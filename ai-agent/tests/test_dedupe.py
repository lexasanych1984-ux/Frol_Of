"""Тесты дедупликации журнала (src/notion_sync/dedupe.py) на синтетических
Notion-страницах — без похода в API."""
from __future__ import annotations

from src.notion_sync import schema
from src.notion_sync.dedupe import (
    apply_dedupe,
    build_dedupe_plan,
    duplicate_key,
    format_dedupe_report,
    is_sub_trade,
)


def _page(
    page_id: str,
    date: str = "2026-06-15T12:00:00.000+04:00",
    pair: str = "pair-usdcad",
    entry: str = "QM+OB",
    result: str | None = "TP",
    main: str | None = None,
    created: str = "2026-06-15T13:00:00.000Z",
) -> dict:
    return {
        "id": page_id,
        "created_time": created,
        "properties": {
            schema.TradingJournal.DATE: {"date": {"start": date}},
            schema.TradingJournal.PAIRS: {"relation": [{"id": pair}]},
            schema.TradingJournal.ENTRY: {"select": {"name": entry} if entry else None},
            schema.TradingJournal.RESULT: {"select": {"name": result} if result else None},
            schema.TradingJournal.MAIN_TRADE: {"relation": [{"id": main}] if main else []},
        },
    }


class RecordingClient:
    """update_trade_page() без HTTP — записывает вызовы для проверки."""

    def __init__(self):
        self.updates: list[tuple[str, dict]] = []

    def update_trade_page(self, page_id: str, properties: dict) -> dict:
        self.updates.append((page_id, properties))
        return {}

    def resolve_relation_title(self, page_id: str) -> str:
        return page_id


def test_duplicate_key_requires_date_and_pair():
    page = _page("1")
    page["properties"][schema.TradingJournal.PAIRS] = {"relation": []}
    assert duplicate_key(page) is None
    assert duplicate_key(_page("2")) is not None


def test_same_time_pair_entry_grouped_others_not():
    pages = [
        _page("a", date="2026-06-15T12:00:00.000+04:00"),
        _page("b", date="2026-06-15T12:00:00.000+04:00"),
        # то же всё, но другое время — честный повторный вход, не дубль
        _page("c", date="2026-06-15T15:00:00.000+04:00"),
        # та же дата/время, другой инструмент — не дубль
        _page("d", pair="pair-ger40"),
    ]
    plan = build_dedupe_plan(pages)
    assert len(plan.groups) == 1
    group = plan.groups[0]
    assert {group.main["id"]} | {p["id"] for p in group.to_link} == {"a", "b"}
    assert plan.n_to_link == 1


def test_main_prefers_filled_result_then_earliest_created():
    pages = [
        _page("later", result="TP", created="2026-06-15T14:00:00.000Z"),
        _page("empty", result=None, created="2026-06-15T12:00:00.000Z"),
        _page("earliest", result="TP", created="2026-06-15T13:00:00.000Z"),
    ]
    plan = build_dedupe_plan(pages)
    assert plan.groups[0].main["id"] == "earliest"
    assert {p["id"] for p in plan.groups[0].to_link} == {"later", "empty"}


def test_mixed_result_group_is_skipped_not_guessed():
    pages = [_page("tp", result="TP"), _page("sl", result="SL")]
    plan = build_dedupe_plan(pages)
    assert plan.groups == []
    assert len(plan.mixed_result) == 1
    report = format_dedupe_report(plan)
    assert "разный Result" in report


def test_empty_result_is_not_a_mismatch():
    pages = [_page("filled", result="TP"), _page("blank", result=None)]
    plan = build_dedupe_plan(pages)
    assert len(plan.groups) == 1
    assert plan.groups[0].main["id"] == "filled"


def test_already_linked_duplicates_are_not_touched():
    pages = [_page("main"), _page("dup", main="main")]
    plan = build_dedupe_plan(pages)
    assert plan.n_to_link == 0
    client = RecordingClient()
    assert apply_dedupe(client, plan) == 0
    assert client.updates == []


def test_apply_writes_main_trade_relation():
    pages = [_page("main", created="2026-06-15T12:00:00.000Z"), _page("dup", created="2026-06-15T13:00:00.000Z")]
    plan = build_dedupe_plan(pages)
    client = RecordingClient()
    assert apply_dedupe(client, plan) == 1
    page_id, props = client.updates[0]
    assert page_id == "dup"
    assert props == {schema.TradingJournal.MAIN_TRADE: {"relation": [{"id": "main"}]}}


def test_is_sub_trade():
    assert not is_sub_trade(_page("main"))
    assert is_sub_trade(_page("dup", main="main"))


def test_no_duplicates_report():
    plan = build_dedupe_plan([_page("a"), _page("b", pair="pair-eurusd")])
    assert "не найдено" in format_dedupe_report(plan)
