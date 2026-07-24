"""Тест поиска похожих сделок по сетапу (src/setup_check/finder.py) на
синтетических Notion-страницах — без реального похода в API."""
from __future__ import annotations

from src.matching.matcher import MatchResult
from src.mt5_import.models import Trade
from src.notion_sync import schema
from src.setup_check.finder import build_setup_report, collect_setup_stats, find_matching_labels


class FakeNotionClient:
    """resolve_relation_title() без HTTP — id совпадает с готовым именем."""

    def resolve_relation_title(self, page_id: str) -> str:
        return page_id


def _make_trade(position_id: str) -> Trade:
    from datetime import datetime

    return Trade(
        account_login="12345",
        position_id=position_id,
        symbol="EURUSD",
        direction="buy",
        volume=1.0,
        open_time=datetime(2026, 6, 1, 10, 0),
        open_price=1.1,
        close_time=datetime(2026, 6, 1, 12, 0),
        close_price=1.11,
        sl=1.09,
        tp=1.12,
        commission=0.0,
        swap=0.0,
        profit=100.0,
        source_file="test",
    )


def _make_page(page_id: str, setup_name: str, entry: str, result: str, main_trade: str | None = None) -> dict:
    return {
        "id": page_id,
        "properties": {
            schema.TradingJournal.SETUP: {"relation": [{"id": setup_name}]},
            schema.TradingJournal.ENTRY: {"select": {"name": entry}},
            schema.TradingJournal.RESULT: {"select": {"name": result}},
            schema.TradingJournal.MAIN_TRADE: {"relation": [{"id": main_trade}] if main_trade else []},
        },
    }


def _match(page_id: str, setup_name: str, entry: str, result: str, main_trade: str | None = None) -> MatchResult:
    trade = _make_trade(page_id)
    page = _make_page(page_id, setup_name, entry, result, main_trade)
    return MatchResult(trade=trade, notion_page=page, notion_page_id=page_id, time_delta=None)


def test_find_matching_labels_substring():
    assert find_matching_labels("сегодня взял QM+OB на лондоне", ["QM+OB", "FVG 1H"]) == ["QM+OB"]


def test_find_matching_labels_typo_fuzzy():
    assert find_matching_labels("qm ob", ["QM+OB", "FVG 1H"]) == ["QM+OB"]


def test_find_matching_labels_no_match():
    assert find_matching_labels("совершенно другой текст про новости", ["QM+OB", "FVG 1H"]) == []


def test_find_matching_labels_ambiguous_substring():
    """Запрос-подстрока обоих вариантов не должен молча выбирать один —
    оба идут в список кандидатов, разрешение неоднозначности на вызывающей стороне."""
    labels = ["1Н поглощение манипуляции", "5m поглощение манипуляции"]
    result = find_matching_labels("поглощение манипуляции", labels)
    assert sorted(result) == sorted(labels)


def test_collect_setup_stats_wins_and_losses():
    matches = [
        _match("1", "Retest", "QM+OB", "TP"),
        _match("2", "Retest", "QM+OB", "TP"),
        _match("3", "Retest", "QM+OB", "SL"),
        _match("4", "Retest", "QM+OB", "BE"),
        _match("5", "Other", "FVG 1H", "TP"),  # другой сетап — не должен попасть
    ]
    stats = collect_setup_stats("QM+OB setup", matches, FakeNotionClient())

    assert stats.matched_label == "QM+OB"
    assert stats.n_total == 4
    assert stats.n_tp == 2
    assert stats.n_sl == 1
    assert stats.n_be == 1
    assert stats.win_rate_pct == 66.7  # 2 из (2 TP + 1 SL)


def test_collect_setup_stats_skips_cross_account_duplicates():
    """Одна сделка на двух счетах = две записи журнала; дубль (с заполненным
    Main Trade) не должен раздувать выборку и искажать win rate."""
    matches = [
        _match("main-1", "Retest", "QM+OB", "TP"),
        _match("dup-1", "Retest", "QM+OB", "TP", main_trade="main-1"),
        _match("main-2", "Retest", "QM+OB", "SL"),
    ]
    stats = collect_setup_stats("QM+OB", matches, FakeNotionClient())

    assert stats.n_total == 2
    assert stats.n_tp == 1
    assert stats.n_sl == 1
    assert stats.n_duplicates_skipped == 1
    assert stats.win_rate_pct == 50.0  # а не 66.7, как было бы с дублем

    report = build_setup_report(stats)
    assert "Дубли" in report


def test_collect_setup_stats_no_match():
    matches = [_match("1", "Retest", "QM+OB", "TP")]
    stats = collect_setup_stats("что-то совсем незнакомое про новости", matches, FakeNotionClient())
    assert stats.matched_label is None
    assert stats.n_total == 0


def test_build_setup_report_small_sample_warns():
    matches = [_match("1", "Retest", "QM+OB", "TP")]
    stats = collect_setup_stats("QM+OB", matches, FakeNotionClient())
    report = build_setup_report(stats, min_sample_size=30)
    assert "Выборка маленькая" in report
    assert "не сигнал" in report.lower()


def test_build_setup_report_unknown_setup():
    matches: list[MatchResult] = []
    stats = collect_setup_stats("QM+OB", matches, FakeNotionClient())
    report = build_setup_report(stats)
    assert "Не нашёл" in report


def test_collect_setup_stats_ambiguous_query_does_not_drop_a_variant():
    """Регресс на реальный баг, найденный на живых данных: запрос
    "поглощение манипуляции" подстрокой совпадает и с "1Н...", и с "5m...",
    оба варианта одинаковой длины. Раньше tie-break молча выбирал "1Н" и
    сделка по "5m"-варианту тихо пропадала из статистики."""
    matches = [
        _match("1", "", "1Н поглощение манипуляции", "TP"),
        _match("2", "", "5m поглощение манипуляции", "SL"),
    ]
    stats = collect_setup_stats("поглощение манипуляции", matches, FakeNotionClient())

    assert stats.matched_label is None
    assert set(stats.ambiguous_candidates) == {"1Н поглощение манипуляции", "5m поглощение манипуляции"}
    assert stats.n_total == 0  # ничего не посчитано, пока трейдер не уточнил

    report = build_setup_report(stats)
    assert "1Н поглощение манипуляции" in report
    assert "5m поглощение манипуляции" in report
    assert "уточни" in report.lower()
