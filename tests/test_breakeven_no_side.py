"""Безубыток должен срабатывать, когда в алерте НЕТ стороны сделки.

Реальный случай 2026-07-20: CRT шлёт "CRT ВЫХОД GER40 (после БУ)" без
long/short (crt-day-1h-strategy-v1.pine, strategy.exit в ветке удержания
позиции). Движок требовал сторону и молча выходил — стоп у CRT никогда не
переносился в безубыток, хотя стратегия на это рассчитывает.

SMC шлёт "ВЫХОД long (после БУ) EURUSD" — со стороной, там всё работало.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.model import Action, Side
from bot.parser import parse


def test_crt_breakeven_alert_has_no_side_but_is_recognized():
    sig = parse("CRT ВЫХОД GER40 (после БУ)")
    assert sig is not None
    assert sig.action is Action.BREAKEVEN
    assert sig.symbol_tv == "GER40"
    assert sig.strategy == "crt"
    assert sig.side is None          # ровно то, обо что спотыкался движок


def test_smc_breakeven_alert_keeps_side():
    sig = parse("ВЫХОД long (после БУ) EURUSD")
    assert sig.action is Action.BREAKEVEN
    assert sig.side is Side.LONG
    assert sig.symbol_tv == "EURUSD"


class FakePos:
    def __init__(self, ticket, symbol, side, price_open, tp):
        self.ticket = ticket
        self.symbol = symbol
        self.side = side
        self.price_open = price_open
        self.tp = tp
        self.volume = 1.0


class FakeMT5:
    TRADE_ACTION_SLTP = 1
    TRADE_RETCODE_DONE = 10009

    def __init__(self):
        self.sent = []

    def order_send(self, req):
        self.sent.append(req)

        class R:
            retcode = FakeMT5.TRADE_RETCODE_DONE
        return R()


class FakeBrokerBE:
    """Только логика выбора позиций из MT5Broker.modify_sl_to_entry."""

    def __init__(self, positions):
        from bot.broker_mt5 import MT5Broker
        self.mt5 = FakeMT5()
        self._positions = positions
        self.creds = None
        self.modify_sl_to_entry = MT5Broker.modify_sl_to_entry.__get__(self)

    def _wrong_account(self):
        return None

    def positions(self):
        return self._positions


def test_breakeven_without_side_moves_stop_of_symbol_position():
    pos = FakePos(111, "GER30m", Side.SHORT, price_open=24500.0, tp=24100.0)
    broker = FakeBrokerBE([pos])

    res = broker.modify_sl_to_entry("GER30m", None)   # сторона не передана

    assert res.ok, res.detail
    assert len(broker.mt5.sent) == 1
    assert broker.mt5.sent[0]["position"] == 111
    assert broker.mt5.sent[0]["sl"] == 24500.0        # стоп = цена входа
    assert broker.mt5.sent[0]["tp"] == 24100.0        # тейк не трогаем


def test_breakeven_with_side_still_filters():
    longp  = FakePos(1, "EURUSD", Side.LONG,  price_open=1.14, tp=1.15)
    shortp = FakePos(2, "EURUSD", Side.SHORT, price_open=1.13, tp=1.12)
    broker = FakeBrokerBE([longp, shortp])

    res = broker.modify_sl_to_entry("EURUSD", Side.SHORT)

    assert res.ok
    assert len(broker.mt5.sent) == 1
    assert broker.mt5.sent[0]["position"] == 2


def test_breakeven_reports_when_no_position():
    broker = FakeBrokerBE([])
    res = broker.modify_sl_to_entry("GER30m", None)
    assert not res.ok
    assert "нет позиции" in res.detail
