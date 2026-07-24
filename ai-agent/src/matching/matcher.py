"""Сведение объективных данных MT5 (Trade) с субъективными записями в
Notion Trading Journal — по инструменту + календарной дате входа.

Матчим 1:1 (одна MT5-позиция <-> одна запись Notion). Сделки без пары
в обе стороны не отбрасываются, а помечаются как unmatched — это сами
по себе полезный сигнал ("сделка есть в MT5, но не записана в журнал"
или наоборот).

Почему по дате, а не по времени с допуском в минутах: на практике время
в Notion записывается вручную (часто округлено до 5-30 минут от реального
open_time в MT5), допуска в 5 минут не хватало и терялись реальные пары
(например, разница 6-25 минут в один и тот же день). Если на один символ
в один день приходится несколько записей/сделок — выбираем ближайшую по
времени пару как tie-break.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Optional

from src.mt5_import.models import Trade
from src.notion_sync.client import NotionClient, extract_date_start
from src.notion_sync import schema


@dataclass
class MatchResult:
    trade: Optional[Trade]
    notion_page: Optional[dict]
    notion_page_id: Optional[str]
    time_delta: Optional[timedelta]

    @property
    def matched(self) -> bool:
        return self.trade is not None and self.notion_page is not None


def normalize_symbol(symbol: str) -> str:
    """Убирает суффиксы брокера / разделители, чтобы EURUSD == EUR/USD == EURUSD.a."""
    return re.sub(r"[^A-Z0-9]", "", symbol.upper())


def filter_pages_by_account(
    notion_pages: Iterable[dict],
    notion_client: NotionClient,
    notion_account_name: str,
    account_name_cache: Optional[dict[str, str]] = None,
) -> list[dict]:
    """Оставляет только записи журнала, чьё поле Account резолвится в нужное имя
    (см. config.yaml: accounts[].notion_account_name). Без этого фильтра одна
    и та же запись Notion (например, "GER40" в один день) может утянуть на
    себя сделки с разных MT5-счетов, если у них совпал день+символ.

    resolve_relation_title() — отдельный HTTP-запрос на страницу; account_name_cache
    кеширует его по id связанной страницы. Обязательно передавать ОДИН и тот же
    словарь между вызовами (например, в цикле по нескольким MT5-счетам над одним
    и тем же списком notion_pages) — иначе один и тот же account id резолвится
    заново каждый раз: на 39 страницах и 3 счетах это добавляло ~55 секунд,
    хотя различных счетов в Notion было всего 6."""
    if account_name_cache is None:
        account_name_cache = {}
    result = []
    for page in notion_pages:
        account_prop = page.get("properties", {}).get(schema.TradingJournal.ACCOUNT, {})
        relations = account_prop.get("relation", [])
        if not relations:
            continue
        related_id = relations[0]["id"]
        if related_id not in account_name_cache:
            account_name_cache[related_id] = notion_client.resolve_relation_title(related_id)
        if account_name_cache[related_id] == notion_account_name:
            result.append(page)
    return result


def match_trades(
    mt5_trades: Iterable[Trade],
    notion_pages: Iterable[dict],
    notion_client: NotionClient,
    pairs_cache: Optional[dict[str, str]] = None,
) -> list[MatchResult]:
    """
    notion_pages — сырые page-объекты из NotionClient.query_trading_journal().

    Внимание: для сравнения символа приходится резолвить relation "Pairs"
    в реальное название инструмента (лишний API-вызов на страницу) —
    кешируем по page_id связанной страницы, чтобы не дублировать запросы.
    Как и в filter_pages_by_account(), передавайте один и тот же pairs_cache
    между вызовами (например, по нескольким MT5-счетам), чтобы не резолвить
    один и тот же инструмент заново на каждый счёт.
    """
    if pairs_cache is None:
        pairs_cache = {}

    def pair_symbol(page: dict) -> Optional[str]:
        pairs_prop = page.get("properties", {}).get(schema.TradingJournal.PAIRS, {})
        relations = pairs_prop.get("relation", [])
        if not relations:
            return None
        related_id = relations[0]["id"]
        if related_id not in pairs_cache:
            pairs_cache[related_id] = notion_client.resolve_relation_title(related_id)
        return pairs_cache[related_id]

    # индексируем notion-записи по нормализованному символу; записи без Pairs
    # или без Date идут в unresolved_pages — их нельзя сматчить в принципе
    # (не с чем сравнивать), но это не значит, что их надо молча выбросить
    # из результата, иначе "почему не сматчилось" теряет часть картины.
    notion_index: dict[str, list[tuple[dict, datetime]]] = {}
    unresolved_pages: list[dict] = []
    for page in notion_pages:
        symbol = pair_symbol(page)
        entry_time = extract_date_start(page)
        if entry_time is not None and entry_time.tzinfo is not None:
            # Notion отдаёт дату с offset (напр. +04:00), MT5 open_time — naive
            # локальное время терминала. Сравниваем как "настенное" время без tz,
            # иначе abs(naive - aware) падает с TypeError.
            entry_time = entry_time.replace(tzinfo=None)
        if symbol is None or entry_time is None:
            unresolved_pages.append(page)
            continue
        notion_index.setdefault(normalize_symbol(symbol), []).append((page, entry_time))

    used_page_ids: set[str] = set()
    results: list[MatchResult] = []

    for trade in mt5_trades:
        candidates = notion_index.get(normalize_symbol(trade.symbol), [])
        best: Optional[tuple[dict, datetime, timedelta]] = None
        for page, entry_time in candidates:
            if page["id"] in used_page_ids:
                continue
            if trade.open_time.date() != entry_time.date():
                continue
            delta = abs(trade.open_time - entry_time)
            if best is None or delta < best[2]:
                best = (page, entry_time, delta)

        if best:
            page, _, delta = best
            used_page_ids.add(page["id"])
            results.append(MatchResult(trade=trade, notion_page=page, notion_page_id=page["id"], time_delta=delta))
        else:
            results.append(MatchResult(trade=trade, notion_page=None, notion_page_id=None, time_delta=None))

    # notion-записи, для которых не нашлось сделки в MT5
    for symbol_pages in notion_index.values():
        for page, _ in symbol_pages:
            if page["id"] not in used_page_ids:
                results.append(MatchResult(trade=None, notion_page=page, notion_page_id=page["id"], time_delta=None))

    # notion-записи без Pairs/Date — в принципе не участвовали в сведении
    for page in unresolved_pages:
        results.append(MatchResult(trade=None, notion_page=page, notion_page_id=page["id"], time_delta=None))

    return results
