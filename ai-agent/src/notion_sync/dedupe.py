"""Дедупликация Trading Journal: одна реальная сделка на нескольких проп-счетах.

Трейдер открывает одну и ту же сделку сразу на нескольких счетах FundingPips —
в журнале появляется по записи на каждый счёт, и win rate по журналу
завышает выборку (одна идея = 2-4 строки). Notion сам не умеет "distinct",
поэтому дубли связываются с "главной" записью через self-relation Main Trade:
у главной записи Main Trade пуст, у дублей — ссылка на главную. Дальше любое
вью с фильтром "Main Trade is empty" показывает каждую сделку ровно один раз.

Дублями считаются записи с одинаковыми (Date начало, Pairs, Entry) — по
наблюдению за реальным журналом дубли заносятся с идентичным временем входа.
Записи с разным временем в один день дублями НЕ считаются (может быть
честный повторный вход по тому же сетапу).

Группы, где Result расходится между счетами (например, на одном TP, на
другом SL из-за разного исполнения), автоматически НЕ связываются — какой
исход считать "настоящим" решает трейдер, а не тихий tie-break.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from src.notion_sync import schema
from src.notion_sync.client import NotionClient


def _relation_ids(page: dict, prop: str) -> list[str]:
    return [r["id"] for r in page.get("properties", {}).get(prop, {}).get("relation", [])]


def _select_name(page: dict, prop: str) -> Optional[str]:
    select = page.get("properties", {}).get(prop, {}).get("select")
    return select.get("name") if select else None


def _date_start_raw(page: dict) -> Optional[str]:
    date_prop = page.get("properties", {}).get(schema.TradingJournal.DATE, {}).get("date")
    return date_prop.get("start") if date_prop else None


def is_sub_trade(page: dict) -> bool:
    """Заполненный Main Trade = запись является дублем другой сделки."""
    return bool(_relation_ids(page, schema.TradingJournal.MAIN_TRADE))


def duplicate_key(page: dict) -> Optional[tuple[str, str, Optional[str]]]:
    """(начало Date, id страницы Pairs, Entry) — ключ группы дублей.

    Pairs сравнивается по id связанной страницы, а не по названию — внутри
    одного журнала это одна и та же страница инструмента, и не нужен
    HTTP-резолв названия на каждую запись.
    """
    start = _date_start_raw(page)
    pairs = _relation_ids(page, schema.TradingJournal.PAIRS)
    if start is None or not pairs:
        return None
    return (start, pairs[0], _select_name(page, schema.TradingJournal.ENTRY))


@dataclass
class DedupeGroup:
    key: tuple[str, str, Optional[str]]
    main: dict                                  # остаётся с пустым Main Trade
    to_link: list[dict] = field(default_factory=list)       # получат Main Trade -> main
    already_linked: list[dict] = field(default_factory=list)  # Main Trade уже стоит — не трогаем


@dataclass
class DedupePlan:
    groups: list[DedupeGroup] = field(default_factory=list)
    # группы с расходящимся Result — пропущены, нужно решение трейдера
    mixed_result: list[list[dict]] = field(default_factory=list)

    @property
    def n_to_link(self) -> int:
        return sum(len(g.to_link) for g in self.groups)


def build_dedupe_plan(pages: list[dict]) -> DedupePlan:
    by_key: dict[tuple, list[dict]] = {}
    for page in pages:
        key = duplicate_key(page)
        if key is not None:
            by_key.setdefault(key, []).append(page)

    plan = DedupePlan()
    for key, group in by_key.items():
        if len(group) < 2:
            continue

        results = {_select_name(p, schema.TradingJournal.RESULT) for p in group}
        results.discard(None)  # незаполненный Result — не расхождение, а пробел в журнале
        if len(results) > 1:
            plan.mixed_result.append(group)
            continue

        linked = [p for p in group if is_sub_trade(p)]
        candidates = [p for p in group if not is_sub_trade(p)]
        if len(candidates) < 1:
            continue  # все уже дубли чего-то — связывать нечего
        # главной оставляем запись с заполненным Result, при равенстве —
        # созданную первой (обычно именно её трейдер заполнял, остальные копии)
        candidates.sort(
            key=lambda p: (
                _select_name(p, schema.TradingJournal.RESULT) is None,
                p.get("created_time", ""),
            )
        )
        main, rest = candidates[0], candidates[1:]
        if not rest and not linked:
            continue
        plan.groups.append(DedupeGroup(key=key, main=main, to_link=rest, already_linked=linked))

    return plan


def apply_dedupe(client: NotionClient, plan: DedupePlan) -> int:
    """Проставляет дублям Main Trade -> главная запись. Возвращает число связанных."""
    n = 0
    for group in plan.groups:
        for dup in group.to_link:
            client.update_trade_page(
                dup["id"],
                {schema.TradingJournal.MAIN_TRADE: {"relation": [{"id": group.main["id"]}]}},
            )
            n += 1
    return n


def _page_label(page: dict, client: Optional[NotionClient], pairs_cache: dict[str, str]) -> str:
    """Человекочитаемая строка для отчёта: дата + инструмент + результат."""
    start = _date_start_raw(page) or "без даты"
    pairs = _relation_ids(page, schema.TradingJournal.PAIRS)
    pair = "?"
    if pairs:
        if pairs[0] not in pairs_cache and client is not None:
            pairs_cache[pairs[0]] = client.resolve_relation_title(pairs[0])
        pair = pairs_cache.get(pairs[0], "?")
    result = _select_name(page, schema.TradingJournal.RESULT) or "-"
    return f"{start} {pair} [{result}]"


def format_dedupe_report(plan: DedupePlan, client: Optional[NotionClient] = None) -> str:
    """Отчёт по плану. client нужен только чтобы показать названия инструментов."""
    pairs_cache: dict[str, str] = {}
    lines: list[str] = []

    if not plan.groups and not plan.mixed_result:
        return "Дублей в журнале не найдено — каждая сделка записана один раз."

    for g in plan.groups:
        main_label = _page_label(g.main, client, pairs_cache)
        if g.to_link:
            lines.append(f"{main_label} — главная, дублей к связыванию: {len(g.to_link)}")
        if g.already_linked and not g.to_link:
            lines.append(f"{main_label} — уже связана с {len(g.already_linked)} дублями, пропускаю")

    for group in plan.mixed_result:
        labels = ", ".join(_page_label(p, client, pairs_cache) for p in group)
        lines.append(
            f"ВНИМАНИЕ: у дублей разный Result — не связываю, реши вручную, "
            f"какой исход настоящий: {labels}"
        )

    lines.append(f"\nИтого дублей к связыванию: {plan.n_to_link}")
    return "\n".join(lines)
