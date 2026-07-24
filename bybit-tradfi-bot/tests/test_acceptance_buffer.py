"""ПРИЁМКА (из ТЗ): облачный буфер + защита возраста, сквозной прогон.

Сценарий: бот был выключен 30 минут. За это время в облачный буфер пришли 2
сигнала — ВХОД (сработал 30 мин назад) и БУ (5 мин назад). После старта бот
добирает оба из буфера через WebhookPullSource:
  • входной сигнал старше max_age (10 мин) → ПРОПУЩЕН с уведомлением, ордер не шлётся;
  • сигнал БУ → ИСПОЛНЕН (позиция уже открыта, лимит управления 6 ч).

Полностью оффлайн: сеть (pull) эмулируем прямой подачей записей в источник,
брокер фейковый, MT5/Telegram не трогаются.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.broker_mt5 import OrderResult
from bot.config import Config, FreshnessCfg
from bot.engine import Engine
from bot.fakebroker import FakeBroker
from bot.signals.webhook_source import WebhookPullSource, _CURSOR_KEY
from bot.state import State

ENTRY = "LONG EURUSD | вход 1.14355 | SL 1.14100 | TP 1.14900 | RR 2.00"
BREAKEVEN = "ВЫХОД long (после БУ) EURUSD"


class SpyBroker(FakeBroker):
    def __init__(self, equity=100000.0):
        super().__init__(equity=equity)
        self.entries, self.be = [], []

    def place_entry(self, sig, lots, symbol, comment=""):
        self.entries.append(symbol)
        return OrderResult(True, "[SPY]", ticket=1, fill_price=sig.entry,
                           fill_volume=lots, position_id=1)

    def modify_sl_to_entry(self, symbol, side):
        self.be.append(symbol)
        return OrderResult(True, "[SPY] BE")


class Notifier:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)
        return True


def _cfg():
    c = Config.__new__(Config)
    c.dry_run = False
    c.risk = {"risk_pct_per_trade": 1.0, "skip_if_below_min_lot": True,
              "max_lot_per_trade": 25.0}
    c.guards = {"dedup_window_sec": 900}
    c.trading_hours = []
    c.symbol_map = {"EURUSD": "EURUSD"}
    c.exits = {"on_breakeven": "move_sl_to_entry", "on_exit_alert": "close"}
    c.freshness = FreshnessCfg(entry_max_age_sec=600, manage_max_age_sec=21600)
    return c


def test_outage_then_recover_entry_skipped_breakeven_executed(tmp_path):
    state = State(":memory:")
    # Эмуляция прошлого запуска: курсор уже сохранён (бот раньше работал).
    state.set_meta(_CURSOR_KEY, "100")

    broker = SpyBroker()
    notifier = Notifier()
    engine = Engine(_cfg(), broker, state, notifier=notifier,
                    exec_log_path=str(tmp_path / "exec.csv"))

    # За «простой» в буфер (id > 100) попали 2 сигнала.
    now = time.time()
    src = WebhookPullSource("https://x.example", "tok", state)
    src._cursor_inited = True
    src._cursor = 100
    src._ingest([
        {"id": 101, "ts": now - 1800, "body": ENTRY},      # вход 30 мин назад
        {"id": 102, "ts": now - 300, "body": BREAKEVEN},   # БУ 5 мин назад
    ])

    # Бот после старта разгребает очередь буфера (как главный цикл run.py).
    while True:
        rs = src.poll(0.01)
        if rs is None:
            break
        engine.handle_raw(rs.raw, received_ts=rs.received_ts,
                          source=rs.source, ext_id=rs.ext_id)

    # Вход протух (30 мин > 10 мин) — не исполнен, ушло уведомление.
    assert broker.entries == []
    assert any("Пропущен по возрасту" in m and "ВХОД" in m for m in notifier.sent)
    # БУ исполнен (5 мин ≪ 6 ч).
    assert broker.be == ["EURUSD"]
    # Курсор продвинулся до последнего обработанного id.
    assert state.get_meta(_CURSOR_KEY) == "102"
