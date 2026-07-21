"""Бот должен слать в Telegram уведомление на КАЖДУЮ сделку: вход, БУ, выход.

Раньше engine.notifier.send() вызывался только в WARNING-ветках (превышение
риска, ошибка ордера), поэтому обычный успешный ордер уходил молча — и пользователь
не видел в Telegram «сигналов бота».
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import Config, FreshnessCfg
from bot.engine import Engine
from bot.fakebroker import FakeBroker
from bot.model import Action, OrderKind, Side, Signal  # noqa: F401  (модель под рукой)


class RecordingNotifier:
    """Ловит отправленные тексты вместо реального Telegram."""
    def __init__(self):
        self.messages = []

    def send(self, text):
        self.messages.append(text)
        return True


class FakeState:
    def is_duplicate(self, key, window):
        return False

    def mark_seen(self, key):
        pass

    def start_of_day_equity(self, equity):
        return equity


def _engine(tmp_path):
    cfg = Config.__new__(Config)
    cfg.risk = {"risk_pct_per_trade": 1.0, "skip_if_below_min_lot": True,
                "max_lot_per_trade": 25.0}
    cfg.guards = {}
    cfg.trading_hours = []
    cfg.symbol_map = {"EURUSD": "EURUSD", "GBPJPY": "GBPJPY"}
    cfg.exits = {"on_exit_alert": "close", "on_breakeven": "move_sl_to_entry"}
    cfg.dry_run = False
    cfg.freshness = FreshnessCfg()
    notifier = RecordingNotifier()
    exec_log = os.path.join(str(tmp_path), "exec.csv")
    eng = Engine(cfg, FakeBroker(equity=100000.0), FakeState(),
                 notifier=notifier, exec_log_path=exec_log)
    return eng, notifier


def test_entry_sends_telegram_notification(tmp_path):
    eng, notifier = _engine(tmp_path)
    eng.handle_raw("LONG GBPJPY | вход 218.400 | SL 217.900 | TP 219.400 | RR 2.00")
    assert notifier.messages, "уведомление о входе не отправлено"
    m = notifier.messages[-1]
    assert "ВХОД" in m and "LONG" in m and "GBPJPY" in m
    assert "Лот" in m and "SL 217.9" in m and "TP 219.4" in m


def test_short_entry_uses_red_marker(tmp_path):
    eng, notifier = _engine(tmp_path)
    eng.handle_raw("SHORT EURUSD | вход 1.14200 | SL 1.14450 | TP 1.13700 | RR 2.0")
    m = notifier.messages[-1]
    assert "ВХОД" in m and "SHORT" in m and "🔴" in m


def test_breakeven_and_exit_notify(tmp_path):
    eng, notifier = _engine(tmp_path)
    eng.handle_raw("ВЫХОД long (после БУ) EURUSD")
    assert any("БУ" in m for m in notifier.messages), "нет уведомления о БУ"
    eng.handle_raw("ВЫХОД long EURUSD")
    assert any("ВЫХОД" in m for m in notifier.messages), "нет уведомления о выходе"


def test_no_notifier_does_not_crash(tmp_path):
    """Без notifier движок работает как раньше (уведомления просто не шлются)."""
    eng, _ = _engine(tmp_path)
    eng.notifier = None
    eng.handle_raw("LONG GBPJPY | вход 218.400 | SL 217.900 | TP 219.400 | RR 2.00")
