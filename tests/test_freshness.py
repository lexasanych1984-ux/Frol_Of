"""Защита протухших сигналов (max_age) + дедуп между каналами.

Из ТЗ: вход старше max_age НЕ исполняется (логируется + Telegram «по возрасту»);
БУ/выход исполняются и с бОльшим возрастом (позиция уже открыта). Дедуп: один и
тот же сигнал, пришедший двумя каналами, исполняется один раз.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.broker_mt5 import OrderResult
from bot.config import Config, FreshnessCfg
from bot.engine import Engine
from bot.fakebroker import FakeBroker
from bot.state import State

ENTRY = "LONG EURUSD | вход 1.14355 | SL 1.14100 | TP 1.14900 | RR 2.00"
CRT_ENTRY = "CRT LONG GER40 | вход 24500 | SL 24400 | TP 24800 | RR 3.0"
BREAKEVEN = "ВЫХОД long (после БУ) EURUSD"
CANCEL = "CRT ОТМЕНА short GER40"


class SpyBroker(FakeBroker):
    """FakeBroker + запись вызовов исполнения (specs/цены берём у FakeBroker)."""
    def __init__(self, equity=100000.0):
        super().__init__(equity=equity)
        self.entries = []
        self.be = []
        self.closes = []
        self.cancels = []

    def place_entry(self, sig, lots, symbol, comment=""):
        self.entries.append((symbol, lots, sig.side.value))
        return OrderResult(True, "[SPY]", ticket=1, fill_price=sig.entry,
                           fill_volume=lots, position_id=1)

    def modify_sl_to_entry(self, symbol, side):
        self.be.append(symbol)
        return OrderResult(True, "[SPY] BE")

    def close_position(self, symbol, side):
        self.closes.append(symbol)
        return OrderResult(True, "[SPY] close")

    def cancel_pending(self, symbol, side=None, magic=None):
        self.cancels.append((symbol, side.value if side else None, magic))
        return OrderResult(True, f"отмена {symbol}: снято отложек 1 [1]")


class Notifier:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)
        return True


def _cfg(freshness=None):
    c = Config.__new__(Config)
    c.dry_run = False
    c.risk = {"risk_pct_per_trade": 1.0, "skip_if_below_min_lot": True,
              "max_lot_per_trade": 25.0}
    c.guards = {"dedup_window_sec": 120}
    c.trading_hours = []
    c.symbol_map = {"EURUSD": "EURUSD", "GER40": "GER30m"}
    c.exits = {"on_breakeven": "move_sl_to_entry", "on_exit_alert": "close"}
    c.freshness = freshness or FreshnessCfg(entry_max_age_sec=600,
                                            manage_max_age_sec=21600)
    return c


def _engine(tmp_path, freshness=None):
    broker = SpyBroker()
    notifier = Notifier()
    eng = Engine(_cfg(freshness), broker, State(":memory:"), notifier=notifier,
                 exec_log_path=str(tmp_path / "exec.csv"))
    return eng, broker, notifier


# ── Вход ──────────────────────────────────────────────────────────────────────
def test_stale_entry_skipped(tmp_path):
    eng, broker, notifier = _engine(tmp_path)
    eng.handle_raw(ENTRY, received_ts=time.time() - 1800, source="webhook")  # 30 мин
    assert broker.entries == []                                # НЕ исполнен
    assert any("Пропущен по возрасту" in m for m in notifier.sent)
    assert any("ВХОД" in m for m in notifier.sent)


def test_fresh_entry_executes(tmp_path):
    eng, broker, notifier = _engine(tmp_path)
    eng.handle_raw(ENTRY, received_ts=time.time() - 60, source="webhook")   # 1 мин
    assert len(broker.entries) == 1
    assert not any("Пропущен" in m for m in notifier.sent)


def test_no_timestamp_is_treated_fresh(tmp_path):
    eng, broker, _ = _engine(tmp_path)
    eng.handle_raw(ENTRY, received_ts=None, source="cdp")       # возраст неизвестен
    assert len(broker.entries) == 1


def test_future_timestamp_not_blocked(tmp_path):
    eng, broker, _ = _engine(tmp_path)
    eng.handle_raw(ENTRY, received_ts=time.time() + 120)        # часы «в будущем»
    assert len(broker.entries) == 1


# ── Управление позицией (БУ/выход) — больший лимит ────────────────────────────
def test_breakeven_older_than_entry_limit_still_executes(tmp_path):
    # 30 мин: старше entry_max_age (10 мин), но в пределах manage_max_age (6 ч).
    eng, broker, notifier = _engine(tmp_path)
    eng.handle_raw(BREAKEVEN, received_ts=time.time() - 1800, source="webhook")
    assert broker.be == ["EURUSD"]                             # БУ исполнен
    assert not any("Пропущен" in m for m in notifier.sent)


def test_breakeven_beyond_manage_limit_skipped(tmp_path):
    # 8 ч: старше manage_max_age (6 ч) — не трогаем даже управление.
    eng, broker, notifier = _engine(tmp_path)
    eng.handle_raw(BREAKEVEN, received_ts=time.time() - 8 * 3600, source="webhook")
    assert broker.be == []
    assert any("Пропущен по возрасту" in m for m in notifier.sent)


# ── Отложенный вход не фильтруется по возрасту ────────────────────────────────
def test_pending_entry_ignores_age(tmp_path):
    # CRT-вход отложенный (stop): стоит на фикс. уровне, возраст не важен.
    eng, broker, notifier = _engine(tmp_path)
    eng.handle_raw(CRT_ENTRY, received_ts=time.time() - 3 * 3600, source="webhook")  # 3 ч
    assert len(broker.entries) == 1
    assert broker.entries[0][0] == "GER30m"
    assert not any("Пропущен" in m for m in notifier.sent)


# ── Per-strategy override (действует на market-вход) ──────────────────────────
def test_per_strategy_entry_override(tmp_path):
    fresh = FreshnessCfg(entry_max_age_sec=600, manage_max_age_sec=21600,
                         per_strategy={"smc": {"entry_max_age_sec": 900}})
    eng, broker, _ = _engine(tmp_path, freshness=fresh)
    # 13 мин: старше общего 10 мин, но в пределах smc-override 15 мин → исполняется.
    eng.handle_raw(ENTRY, received_ts=time.time() - 13 * 60, source="webhook")
    assert len(broker.entries) == 1
    assert broker.entries[0][0] == "EURUSD"


# ── Отмена отложенного ордера (инвалидация идеи) ──────────────────────────────
def test_cancel_calls_broker(tmp_path):
    eng, broker, _ = _engine(tmp_path)
    eng.handle_raw(CANCEL, received_ts=time.time() - 60, source="webhook")
    assert len(broker.cancels) == 1
    symbol, side, magic = broker.cancels[0]
    assert symbol == "GER30m"
    assert side == "short"
    assert magic == 770002   # magic_for("crt")


def test_cancel_within_manage_window_executes(tmp_path):
    # Отмена — управление ордером: живёт в пределах manage_max_age (6 ч).
    eng, broker, notifier = _engine(tmp_path)
    eng.handle_raw(CANCEL, received_ts=time.time() - 3 * 3600, source="webhook")
    assert len(broker.cancels) == 1
    assert not any("Пропущен" in m for m in notifier.sent)


# ── Дедуп между каналами (один fire двумя путями = одно исполнение) ───────────
def test_dedup_across_sources(tmp_path):
    eng, broker, _ = _engine(tmp_path)
    now = time.time()
    eng.handle_raw(ENTRY, received_ts=now - 5, source="cdp", ext_id="fire1")
    eng.handle_raw(ENTRY, received_ts=now - 2, source="webhook", ext_id="42")
    assert len(broker.entries) == 1        # второй канал — дубликат по хэшу
