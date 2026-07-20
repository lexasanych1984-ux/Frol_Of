"""Рыночный ордер должен сайзиться от цены исполнения, а не от цены из алерта.

Реальный случай 2026-07-20: алерт SHORT EURUSD вход 1.14222 SL 1.14495,
исполнение прошло по 1.14115. Лот считался от 1.14222 → дистанция до SL 273
пункта, риск 999 USD (1%). По факту дистанция была 380 пунктов, и риск вышел
1391 USD (1.39%) — на 39% выше заданного, молча.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import Config
from bot.model import OrderKind, Side, Signal, Action
from bot.risk import RiskManager
from bot.sizing import SymbolSpec

EUR = SymbolSpec("EURUSD", volume_min=0.01, volume_max=100, volume_step=0.01,
                 tick_size=0.00001, tick_value=1.0)


class FakeAccount:
    equity = 100000.0
    currency = "USD"


class FakeState:
    def start_of_day_equity(self, equity):
        return equity

    def is_duplicate(self, key, window):
        return False


def _risk_manager():
    cfg = Config.__new__(Config)
    cfg.risk = {"risk_pct_per_trade": 1.0, "skip_if_below_min_lot": True,
                "max_lot_per_trade": 25.0}
    cfg.guards = {}
    cfg.trading_hours = []
    cfg.symbol_map = {"EURUSD": "EURUSD"}
    return RiskManager(cfg, FakeState())


def _signal(kind=OrderKind.MARKET):
    return Signal(action=Action.ENTRY, side=Side.SHORT, symbol_tv="EURUSD",
                  entry=1.14222, sl=1.14495, tp=1.13403, rr=3.0,
                  order_kind=kind, strategy="smc", raw="")


def _actual_risk(lots, entry, sl):
    """Реальный риск в USD: дистанция в тиках * стоимость тика * лот."""
    return abs(sl - entry) / EUR.tick_size * EUR.tick_value * lots


def test_market_order_sized_from_fill_price_keeps_risk_at_one_percent():
    rm = _risk_manager()
    fill = 1.14115  # рынок ушёл вниз, шорт исполнится хуже
    d = rm.evaluate_entry(_signal(), FakeAccount(), [], EUR, market_price=fill)

    assert d.allow
    risk = _actual_risk(d.lots, fill, 1.14495)
    # риск обязан остаться ~1% от 100k, а не уехать к 1.39%
    assert abs(risk - 1000.0) <= 5.0, f"риск {risk:.0f} USD вместо ~1000"


def test_without_market_price_risk_silently_overshoots():
    """Фиксируем поведение старой схемы — сайзинг от цены из алерта."""
    rm = _risk_manager()
    d = rm.evaluate_entry(_signal(), FakeAccount(), [], EUR)  # market_price не передан
    assert d.allow
    # лот посчитан от 1.14222, а исполнение было бы по 1.14115
    risk = _actual_risk(d.lots, 1.14115, 1.14495)
    assert risk > 1300.0  # именно та дыра, которую закрывает market_price


def test_limit_order_still_uses_signal_entry():
    """У limit/stop цена известна заранее — текущая цена не должна её подменять."""
    rm = _risk_manager()
    d_limit = rm.evaluate_entry(_signal(OrderKind.LIMIT), FakeAccount(), [], EUR,
                                market_price=1.14115)
    d_plain = rm.evaluate_entry(_signal(OrderKind.LIMIT), FakeAccount(), [], EUR)
    assert d_limit.lots == d_plain.lots
