"""Проскальзывание/факт.риск + мгновенная тревога движка «риск выше заданного».

ПРИЁМКА (из ТЗ): симулированная сделка с завышенным риском триггерит тревогу.
Реальный кейс 2026-07-20: SHORT EURUSD, алерт 1.14222 / SL 1.14495, исполнение
по 1.14115 — дистанция до SL выросла с 273 до 380 пунктов, риск 1% → 1.39%.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.broker_mt5 import Account, OrderResult
from bot.config import Config
from bot.engine import Engine
from bot.sizing import SymbolSpec
from bot.slippage import (compute_actual_risk, compute_slippage, pip_size)
from bot.state import State

EUR = SymbolSpec("EURUSD", volume_min=0.01, volume_max=1000, volume_step=0.01,
                 tick_size=0.00001, tick_value=1.0)


# ── чистые функции ────────────────────────────────────────────────────────────
def test_pip_size_fx_vs_index():
    assert pip_size(0.00001) == 0.0001     # EURUSD 5-знак
    assert pip_size(0.001) == 0.01         # JPY 3-знак
    assert pip_size(0.1) == 1.0            # индекс


def test_slippage_adverse_for_short_when_fill_lower():
    s = compute_slippage("short", 1.14222, 1.14115, 1.14495, EUR)
    assert s is not None
    assert s.adverse is True               # шорт исполнился ниже → риск вверх
    assert round(s.slip_pips, 1) == -10.7  # 107 пятизначных пунктов = 10.7 пипса
    assert s.slip_pct_of_sl > 0


def test_slippage_favorable_for_short_when_fill_higher():
    s = compute_slippage("short", 1.14222, 1.14300, 1.14495, EUR)
    assert s.adverse is False


def test_actual_risk_grows_with_worse_fill():
    # лот 3.66 (посчитан на дистанцию 273 пункта, риск ~1%), фил по 1.14115
    r = compute_actual_risk(1.14115, 1.14495, 1.13403, 3.66, EUR, 100000.0)
    assert r is not None
    assert 1.35 < r.risk_pct < 1.45        # ~1.39%, а не 1.0%


# ── интеграция: тревога движка ────────────────────────────────────────────────
class _Notifier:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)
        return True


class _Broker:
    """Минимальный брокер: сайзинг идёт от цены алерта, а фил приходит хуже."""
    def __init__(self, fill_price):
        self.fill = fill_price

    def account(self):
        return Account(0, 100000.0, 100000.0, "USD", "DEMO")

    def positions(self):
        return []

    def symbol_spec(self, symbol):
        return EUR

    def current_price(self, symbol, side):
        return 1.14222                      # сайзинг от цены алерта

    def place_entry(self, sig, lots, symbol, comment=""):
        return OrderResult(True, "[FAKE]", ticket=555, fill_price=self.fill,
                           fill_volume=lots, position_id=555)


def _cfg():
    c = Config.__new__(Config)
    c.dry_run = False
    c.risk = {"risk_pct_per_trade": 1.0, "skip_if_below_min_lot": True,
              "max_lot_per_trade": 25.0}
    c.guards = {}
    c.trading_hours = []
    c.symbol_map = {"EURUSD": "EURUSD"}
    c.exits = {}
    return c


SHORT = "SHORT EURUSD | вход 1.14222 | SL 1.14495 | TP 1.13403 | RR 3.0"


def _engine(tmp_path, fill, notifier):
    return Engine(_cfg(), _Broker(fill), State(":memory:"), notifier=notifier,
                  exec_log_path=str(tmp_path / "exec.csv"), risk_overshoot_pp=0.3)


def test_overshoot_triggers_alert(tmp_path):
    spy = _Notifier()
    _engine(tmp_path, fill=1.14115, notifier=spy).handle_raw(SHORT)
    # кроме обычного пуша о входе приходит отдельная тревога о риске
    overshoot = [m for m in spy.sent if "РИСК ВЫШЕ ЗАДАННОГО" in m]
    assert len(overshoot) == 1
    assert "EURUSD" in overshoot[0]


def test_clean_fill_does_not_alert(tmp_path):
    spy = _Notifier()
    _engine(tmp_path, fill=1.14222, notifier=spy).handle_raw(SHORT)
    # уведомление о входе есть, а тревоги «риск выше заданного» — нет
    assert any("ВХОД" in m for m in spy.sent)
    assert not any("РИСК ВЫШЕ ЗАДАННОГО" in m for m in spy.sent)


def test_execution_logged_with_risk_delta(tmp_path):
    from bot import execlog
    p = tmp_path / "exec.csv"
    _engine(tmp_path, fill=1.14115, notifier=_Notifier()).handle_raw(SHORT)
    rows = execlog.read(p)
    assert len(rows) == 1
    row = rows[0]
    assert row["symbol_tv"] == "EURUSD"
    assert float(row["risk_delta_pp"]) > 0.3
    assert row["adverse"] == "1"
