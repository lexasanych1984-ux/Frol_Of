"""Тесты оркестрации /sync (src/mt5_import/sync.py) на фейковых MT5-сделках и
Notion-страницах — без живого терминала и без похода в Notion API.

Проверяем главное: кандидаты на создание = сделки MT5, которых ещё НЕТ в
журнале (дедуп через matcher по инструменту+дате), и что apply_sync создаёт
строки с объективными полями, оставляя Entry/Result пустыми."""
from __future__ import annotations

from datetime import datetime

from src.mt5_import import sync
from src.mt5_import.models import Trade
from src.notion_sync import schema
from src.notion_sync.journal_writer import JournalResolvers


def _trade(symbol: str, day: int, direction: str = "sell", profit: float = -50.0) -> Trade:
    return Trade(
        account_login="11620556",
        position_id=f"{symbol}-{day}",
        symbol=symbol,
        direction=direction,
        volume=1.0,
        open_time=datetime(2026, 7, day, 10, 5),
        open_price=1.0,
        close_time=datetime(2026, 7, day, 12, 0),
        close_price=1.0,
        sl=None,
        tp=None,
        commission=-2.0,
        swap=0.0,
        profit=profit,
        source_file="MT5 live",
    )


def _journal_page(page_id: str, symbol: str, date_iso: str) -> dict:
    return {
        "id": page_id,
        "properties": {
            schema.TradingJournal.PAIRS: {"relation": [{"id": f"pair-{symbol}"}]},
            schema.TradingJournal.DATE: {"date": {"start": date_iso}},
        },
    }


class FakeReadClient:
    """query_trading_journal + resolve_relation_title без сети. id связанной
    страницы Pairs = 'pair-<SYMBOL>', резолвится обратно в SYMBOL."""

    def __init__(self, pages: list[dict]):
        self._pages = pages

    def query_trading_journal(self, date_from=None, date_to=None):
        return iter(self._pages)

    def resolve_relation_title(self, page_id: str) -> str:
        return page_id.replace("pair-", "")


class RecordingWriteClient:
    """Пишущий клиент без сети: справочники и запись страниц в память."""

    def __init__(self):
        self.created: list[dict] = []

    def list_data_source_titles(self, data_source_id: str, title_prop: str) -> dict[str, str]:
        if data_source_id == schema.PAIRS_DATA_SOURCE_ID:
            return {"GER40": "id-ger40", "EURUSD": "id-eurusd", "USDCAD": "id-usdcad"}
        if data_source_id == schema.DIRECTION_DATA_SOURCE_ID:
            return {"Long": "id-long", "Short": "id-short"}
        if data_source_id == schema.ACCOUNT_DATA_SOURCE_ID:
            return {"FP_10": "id-fp10"}
        return {}

    def create_page(self, properties, data_source_id=schema.TRADING_JOURNAL_DATA_SOURCE_ID, children=None):
        self.created.append({"properties": properties, "children": children})
        return {"id": f"new-{len(self.created)}"}


class _AccCfg:
    def __init__(self, notion_account_name):
        self.notion_account_name = notion_account_name


def test_candidates_exclude_already_journaled(monkeypatch):
    trades = [_trade("GER40", 15), _trade("EURUSD", 16), _trade("USDCAD", 17)]
    pages = [_journal_page("p1", "EURUSD", "2026-07-16T10:00:00")]  # EURUSD уже в журнале
    client = FakeReadClient(pages)
    monkeypatch.setattr(sync, "fetch_positions", lambda *a, **k: (trades, "11620556"))

    preview = sync.build_sync_preview(
        "C:/fake/terminal.exe",
        client,
        datetime(2026, 7, 1),
        datetime(2026, 7, 31),
        accounts_config={"11620556": _AccCfg("FP_10")},
    )

    assert {t.symbol for t in preview.candidates} == {"GER40", "USDCAD"}
    assert preview.n_already_in_journal == 1
    assert preview.n_mt5_total == 3
    assert preview.account_in_config is True


def test_preview_warns_when_account_not_in_config(monkeypatch):
    monkeypatch.setattr(sync, "fetch_positions", lambda *a, **k: ([_trade("GER40", 15)], "99999999"))
    preview = sync.build_sync_preview(
        "C:/fake/terminal.exe",
        FakeReadClient([]),
        datetime(2026, 7, 1),
        datetime(2026, 7, 31),
        accounts_config={"11620556": _AccCfg("FP_10")},
    )
    assert preview.account_in_config is False
    assert "Account" in sync.format_preview(preview)


def test_apply_sync_creates_objective_fields_only():
    candidates = [_trade("GER40", 15, direction="sell", profit=-52.3)]
    client = RecordingWriteClient()
    resolvers = JournalResolvers(client, accounts_config={"11620556": _AccCfg("FP_10")})

    created, warnings = sync.apply_sync(client, candidates, "11620556", resolvers=resolvers)

    assert created == 1
    assert warnings == []
    props = client.created[0]["properties"]
    assert props[schema.TradingJournal.PAIRS] == {"relation": [{"id": "id-ger40"}]}
    assert props[schema.TradingJournal.DIRECTION] == {"relation": [{"id": "id-short"}]}
    assert props[schema.TradingJournal.ACCOUNT] == {"relation": [{"id": "id-fp10"}]}
    # PnL = profit(-52.3) + commission(-2.0) + swap(0) = -54.3
    assert props[schema.TradingJournal.RR_PNL] == {"number": -54.3}
    # Entry и Result /sync не проставляет
    assert schema.TradingJournal.ENTRY not in props
    assert schema.TradingJournal.RESULT not in props


def test_apply_sync_warns_when_symbol_not_in_pairs():
    candidates = [_trade("XAGUSD", 15)]  # нет в фейковом справочнике Pairs
    client = RecordingWriteClient()
    resolvers = JournalResolvers(client, accounts_config={"11620556": _AccCfg("FP_10")})

    created, warnings = sync.apply_sync(client, candidates, "11620556", resolvers=resolvers)

    assert created == 1  # строка всё равно создаётся
    assert any("Pairs" in w for w in warnings)
    props = client.created[0]["properties"]
    assert schema.TradingJournal.PAIRS not in props
