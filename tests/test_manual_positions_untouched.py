"""Управляющие сигналы не должны трогать чужие позиции на том же счёте.

БУ и выход искали позицию ТОЛЬКО по символу (и стороне), без фильтра по magic —
в отличие от отмены отложек, где фильтр был. На демо-счёте это значит: алерт
«(после БУ)» по EURUSD переставил бы стоп и у позиции, открытой руками, а режим
exits.on_exit_alert=close закрыл бы её рыночным ордером. Стратегия помечает свои
сделки magic (см. broker_mt5.STRATEGY_MAGIC), по нему и фильтруем.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.broker_mt5 import MT5Broker, magic_for
from bot.model import Side

SMC = magic_for("smc")          # 770001
MANUAL = 0                      # открыто руками в терминале


class FakePos:
    def __init__(self, ticket, symbol, side, magic, price_open=1.14, tp=1.15):
        self.ticket = ticket
        self.symbol = symbol
        self.side = side
        self.magic = magic
        self.price_open = price_open
        self.tp = tp
        self.volume = 1.0


class FakeMT5:
    TRADE_ACTION_SLTP = 1
    TRADE_ACTION_DEAL = 2
    TRADE_RETCODE_DONE = 10009
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1

    def __init__(self):
        self.sent = []

    def symbol_info_tick(self, symbol):
        class T:
            bid = 1.1400
            ask = 1.1401
        return T()

    def order_send(self, req):
        self.sent.append(req)

        class R:
            retcode = FakeMT5.TRADE_RETCODE_DONE
        return R()


class FakeBroker:
    """Реальные методы MT5Broker поверх фейкового MT5 и списка позиций."""

    def __init__(self, positions):
        self.mt5 = FakeMT5()
        self._positions = positions
        self.creds = None
        self.modify_sl_to_entry = MT5Broker.modify_sl_to_entry.__get__(self)
        self.close_position = MT5Broker.close_position.__get__(self)

    def _wrong_account(self):
        return None

    def _filling(self, symbol):
        return 0

    def positions(self, magic=None):
        return [p for p in self._positions if magic is None or p.magic == magic]


def test_breakeven_does_not_touch_manual_position():
    ours = FakePos(1, "EURUSD", Side.LONG, SMC, price_open=1.1400)
    theirs = FakePos(2, "EURUSD", Side.LONG, MANUAL, price_open=1.1300)
    broker = FakeBroker([ours, theirs])

    res = broker.modify_sl_to_entry("EURUSD", None, magic=SMC)

    assert res.ok, res.detail
    assert len(broker.mt5.sent) == 1, "тронули лишнюю позицию"
    assert broker.mt5.sent[0]["position"] == 1


def test_close_does_not_touch_manual_position():
    theirs = FakePos(2, "EURUSD", Side.LONG, MANUAL)
    broker = FakeBroker([theirs])

    res = broker.close_position("EURUSD", None, magic=SMC)

    assert not res.ok
    assert "нет позиции" in res.detail
    assert broker.mt5.sent == [], "закрыли ручную позицию"


def test_close_still_closes_own_position():
    ours = FakePos(3, "GBPJPY", Side.SHORT, SMC)
    broker = FakeBroker([ours])

    res = broker.close_position("GBPJPY", None, magic=SMC)

    assert res.ok, res.detail
    assert len(broker.mt5.sent) == 1
    assert broker.mt5.sent[0]["position"] == 3
    # закрытие шорта = покупка
    assert broker.mt5.sent[0]["type"] == FakeMT5.ORDER_TYPE_BUY


def test_without_magic_behaviour_unchanged():
    """magic=None (старый вызов) — фильтра нет, поведение прежнее."""
    theirs = FakePos(4, "EURUSD", Side.LONG, MANUAL)
    broker = FakeBroker([theirs])
    assert broker.close_position("EURUSD", None).ok
