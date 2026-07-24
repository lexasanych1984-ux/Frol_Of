"""Отчёт по сделкам должен говорить на языке стратегий, а не брокера.

Имена стратегий и символов из stats.py попадают в select-свойства
Notion-базы «Сделки бота». Любое расхождение молча заводит там новый
вариант, и график по стратегиям/символам разъезжается на две серии —
поэтому фиксируем соответствие тестом.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import stats as st

# Варианты select-свойств в Notion-базе «Сделки бота»
NOTION_STRATEGIES = {"SMC", "CRT", "ASIA", "БОТ(?)", "ручные/чужие"}
NOTION_SYMBOLS = {"EURUSD", "GBPJPY", "GER40", "NAS100"}


class FakeDeal:
    def __init__(self, position_id, symbol, entry, volume, time, magic=0,
                 profit=0.0, price=0.0, type_=0):
        self.position_id = position_id
        self.symbol = symbol
        self.entry = entry
        self.volume = volume
        self.time = time
        self.magic = magic
        self.profit = profit
        self.price = price
        self.type = type_
        self.swap = 0.0
        self.commission = 0.0
        self.fee = 0.0


class FakeMT5:
    DEAL_ENTRY_IN = 0
    DEAL_TYPE_BUY = 0

    def __init__(self, deals):
        self._deals = deals

    def history_deals_get(self, frm, to):
        return self._deals


def test_strategy_names_match_notion_options():
    assert set(st.MAGIC_NAME.values()) <= NOTION_STRATEGIES
    # asweep исторически превращался в "ASWEEP" и не совпадал с "ASIA"
    assert "ASWEEP" not in st.MAGIC_NAME.values()
    assert "ASIA" in st.MAGIC_NAME.values()


def test_symbol_alias_maps_broker_names_to_tv_tickers():
    """Мини-контракты брокера (GER30m) должны стать тикерами стратегий (GER40)."""
    import time as _t
    now = int(_t.time())
    deals = [
        FakeDeal(1, "GER30m", entry=0, volume=2.0, time=now, magic=770002,
                 price=24500.0),
        FakeDeal(1, "GER30m", entry=1, volume=2.0, time=now + 60, profit=150.0,
                 price=24575.0),
    ]
    alias = {"GER30m": "GER40", "USTECH100m": "NAS100"}
    trades = st.collect_closed(FakeMT5(deals), days=5, symbol_alias=alias)

    assert len(trades) == 1
    assert trades[0].symbol == "GER40"
    assert trades[0].symbol in NOTION_SYMBOLS
    assert trades[0].strategy == "CRT"
    assert trades[0].profit == 150.0


def test_symbol_without_alias_is_left_as_is():
    import time as _t
    now = int(_t.time())
    deals = [
        FakeDeal(2, "EURUSD", entry=0, volume=0.5, time=now, magic=770001,
                 price=1.1435),
        FakeDeal(2, "EURUSD", entry=1, volume=0.5, time=now + 60, profit=-40.0,
                 price=1.1410),
    ]
    trades = st.collect_closed(FakeMT5(deals), days=5, symbol_alias={"GER30m": "GER40"})
    assert trades[0].symbol == "EURUSD"
    assert trades[0].strategy == "SMC"
