"""Приоритет 2: чек-лист перед входом — поиск похожих прошлых сделок.

Трейдер перед входом описывает сетап свободным текстом (`/setup ...`),
бот сверяет его с уже сматченными сделками (MT5 + Notion) и отдаёт
статистику по факту — сколько раз похожий сетап давал TP/SL/BE. Это не
сигнал на вход и не предсказание рынка: только сверка с собственной
историей трейдера, чтобы притормозить импульсивный вход.

Похожесть определяется по двум полям Notion Trading Journal:
  - Setup (relation)  — общая категория сетапа
  - Entry (select)    — конкретный паттерн входа (QM+OB, FVG 1H, ...)
Оба поля — фиксированный небольшой словарь значений, поэтому сопоставление
свободного текста с ними — обычный fuzzy-match по строкам, без похода в
Claude: словарь маленький и закрытый, LLM-классификация здесь была бы
дороже и менее предсказуема, чем сравнение строк.

Не учитывает пока Pairs/Direction/Sessions (сузили бы редкую выборку ещё
сильнее) — см. NEXT_STEPS.md, можно добавить как опциональные фильтры
позже, если понадобится более точная сверка.
"""
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional

from src.matching.matcher import MatchResult
from src.notion_sync import schema
from src.notion_sync.client import NotionClient
from src.notion_sync.dedupe import is_sub_trade


def resolve_setup_name(page: dict, notion_client: NotionClient, cache: dict[str, str]) -> Optional[str]:
    """Резолвит relation "Setup" в название — тот же паттерн, что pair_symbol() в matcher.py."""
    relations = page.get("properties", {}).get(schema.TradingJournal.SETUP, {}).get("relation", [])
    if not relations:
        return None
    related_id = relations[0]["id"]
    if related_id not in cache:
        cache[related_id] = notion_client.resolve_relation_title(related_id)
    return cache[related_id] or None


def entry_pattern(page: dict) -> Optional[str]:
    """Entry — обычный select, без резолва relation."""
    select = page.get("properties", {}).get(schema.TradingJournal.ENTRY, {}).get("select")
    return select.get("name") if select else None


def result_of(page: dict) -> Optional[str]:
    select = page.get("properties", {}).get(schema.TradingJournal.RESULT, {}).get("select")
    return select.get("name") if select else None


@dataclass
class SetupStats:
    query: str
    matched_label: Optional[str]
    ambiguous_candidates: tuple[str, ...] = ()
    n_total: int = 0
    n_tp: int = 0
    n_sl: int = 0
    n_be: int = 0
    n_missed: int = 0
    # записи с заполненным Main Trade — та же сделка на другом счёте;
    # в подсчёт не входят, иначе одна идея раздувает выборку в 2-4 раза
    n_duplicates_skipped: int = 0

    @property
    def n_decided(self) -> int:
        """TP+SL — сделки с однозначным исходом. BE и Missed не считаем ни победой, ни поражением."""
        return self.n_tp + self.n_sl

    @property
    def win_rate_pct(self) -> Optional[float]:
        if self.n_decided == 0:
            return None
        return round(self.n_tp / self.n_decided * 100, 1)


def find_matching_labels(query: str, known_labels: list[str], threshold: float = 0.4) -> list[str]:
    """Находит известные названия Setup/Entry, похожие на свободный текст трейдера.

    Сначала пробует подстроку в любую сторону (трейдер часто пишет само
    название сетапа внутри более длинного описания), затем — похожесть
    строк для опечаток и сокращений.

    Возвращает СПИСОК кандидатов, а не один лучший — например, запрос
    "поглощение манипуляции" подстрокой входит и в "1Н поглощение
    манипуляции", и в "5m поглощение манипуляции" одновременно. Раньше
    здесь молча выбирался один вариант (самый длинный / случайный при
    равной длине) — сделки по отброшенному варианту тихо пропадали из
    статистики, что для инструмента, который должен быть честным про
    данные, недопустимо. Решение неоднозначности — забота вызывающего
    кода (спросить трейдера), а не угадывание здесь.
    """
    if not query.strip() or not known_labels:
        return []

    query_lower = query.lower()
    substring_hits = sorted({lbl for lbl in known_labels if lbl.lower() in query_lower or query_lower in lbl.lower()})
    if substring_hits:
        return substring_hits

    best, best_score = None, 0.0
    for lbl in known_labels:
        score = SequenceMatcher(None, query_lower, lbl.lower()).ratio()
        if score > best_score:
            best, best_score = lbl, score
    return [best] if best is not None and best_score >= threshold else []


def collect_setup_stats(
    query: str,
    matches: list[MatchResult],
    notion_client: NotionClient,
    setup_cache: Optional[dict[str, str]] = None,
) -> SetupStats:
    if setup_cache is None:
        setup_cache = {}

    resolved = [
        (m, resolve_setup_name(m.notion_page, notion_client, setup_cache), entry_pattern(m.notion_page))
        for m in matches
        if m.matched
    ]
    known_labels = sorted({lbl for _, setup, entry in resolved for lbl in (setup, entry) if lbl})
    candidates = find_matching_labels(query, known_labels)

    if not candidates:
        return SetupStats(query=query, matched_label=None)
    if len(candidates) > 1:
        return SetupStats(query=query, matched_label=None, ambiguous_candidates=tuple(candidates))

    matched_label = candidates[0]
    relevant = [m for m, setup, entry in resolved if matched_label in (setup, entry)]
    unique = [m for m in relevant if not is_sub_trade(m.notion_page)]
    results = [result_of(m.notion_page) for m in unique]
    return SetupStats(
        query=query,
        matched_label=matched_label,
        n_total=len(unique),
        n_tp=results.count("TP"),
        n_sl=results.count("SL"),
        n_be=results.count("BE"),
        n_missed=results.count("Missed"),
        n_duplicates_skipped=len(relevant) - len(unique),
    )


def build_setup_report(stats: SetupStats, min_sample_size: int = 30) -> str:
    if stats.ambiguous_candidates:
        options = ", ".join(stats.ambiguous_candidates)
        return (
            f"По запросу «{stats.query}» нашёл несколько похожих сетапов: {options}. "
            "Уточни, какой именно — иначе один вариант молча потеряется из статистики."
        )
    if stats.matched_label is None:
        return (
            f"Не нашёл в истории сетап, похожий на «{stats.query}». "
            "Проверь название (как в Notion Setup/Entry) или заведи новый сетап в журнале."
        )

    lines = [
        f"Похоже на сетап: {stats.matched_label} (по запросу «{stats.query}»)",
        f"Похожих сделок в истории: {stats.n_total} "
        f"(TP {stats.n_tp} / SL {stats.n_sl} / BE {stats.n_be} / Missed {stats.n_missed})",
    ]
    if stats.n_duplicates_skipped:
        lines.append(
            f"Дубли той же сделки на других счетах не считаю: {stats.n_duplicates_skipped} шт."
        )
    if stats.win_rate_pct is not None:
        lines.append(f"Win rate (TP из TP+SL): {stats.win_rate_pct}%")
    else:
        lines.append("Win rate: нет сделок с однозначным исходом (TP/SL) для расчёта.")

    if stats.n_total < min_sample_size:
        lines.append(
            f"\nВыборка маленькая ({stats.n_total} < {min_sample_size}) — это не статистика, "
            "а ориентир по твоей же истории."
        )
    lines.append("\nЭто не сигнал на вход и не предсказание рынка — сверка с твоей же историей сделок.")
    return "\n".join(lines)
