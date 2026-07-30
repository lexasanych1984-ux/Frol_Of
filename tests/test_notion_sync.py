"""Автосинк закрытых сделок в Notion: дедуп по position_id, равенство equity с
CSV, идемпотентность между тиками и громкая тревога при сбое (молчание = авария).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import stats as st
from bot.notion_sync import NotionError, NotionSyncWatcher, _icon, _iso, _title


# ── Фейки MT5/брокера/Notion ─────────────────────────────────────────────────
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
        self.swap = self.commission = self.fee = 0.0


class FakeMT5:
    DEAL_ENTRY_IN = 0
    DEAL_TYPE_BUY = 0

    def __init__(self, deals, window_deals=None):
        self._deals = deals              # полная история (запрос по position=)
        self._window = window_deals      # что попало в окно frm..to (None = всё)

    def history_deals_get(self, frm=None, to=None, position=None):
        if position is not None:
            return [d for d in self._deals if d.position_id == position]
        return list(self._window if self._window is not None else self._deals)


class FakeAccount:
    def __init__(self, balance):
        self.balance = balance
        self.equity = balance


class FakeBroker:
    def __init__(self, deals, balance):
        self.mt5 = FakeMT5(deals)
        self._balance = balance

    def account(self):
        return FakeAccount(self._balance)


class FakeJournal:
    def __init__(self, existing=None, fail=False):
        self.existing = set(existing or [])
        self.fail = fail
        self.created = []          # (position_id, equity, cum)
        self.queries = 0

    def existing_position_ids(self):
        self.queries += 1
        return set(self.existing)

    def create_trade(self, trade, equity, cum):
        if self.fail:
            raise NotionError("boom")
        self.created.append((int(trade.position_id), equity, cum))
        self.existing.add(int(trade.position_id))


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)
        return True


class FakeCharts:
    def __init__(self):
        self.rebuilds = 0

    def rebuild(self, images, header):
        self.rebuilds += 1
        return len(images)


def _ts(y, mo, d, h, mi):
    from datetime import datetime
    # реалистичный (не возле эпохи) epoch — astimezone на Windows у 1970 падает
    return int(datetime(y, mo, d, h, mi).timestamp())


def _two_smc_trades():
    """pos100 (+50, закр. раньше), pos200 (−30, закр. позже), обе SMC EURUSD."""
    return [
        FakeDeal(100, "EURUSD", entry=0, volume=1.0, time=_ts(2026, 7, 22, 3, 29),
                 magic=770001, price=1.1000),
        FakeDeal(100, "EURUSD", entry=1, volume=1.0, time=_ts(2026, 7, 22, 3, 31),
                 magic=770001, profit=50.0, price=1.1050),
        FakeDeal(200, "EURUSD", entry=0, volume=2.0, time=_ts(2026, 7, 22, 4, 9),
                 magic=770001, price=1.2000),
        FakeDeal(200, "EURUSD", entry=1, volume=2.0, time=_ts(2026, 7, 22, 4, 10),
                 magic=770001, profit=-30.0, price=1.1980),
    ]


def _watcher(journal, deals, balance=1020.0, notifier=None, **kw):
    broker = FakeBroker(deals, balance)
    return NotionSyncWatcher(journal, broker, notifier=notifier,
                             clock=lambda: 0.0, **kw)


# ── Тесты ────────────────────────────────────────────────────────────────────
def test_writes_new_trades_with_csv_equity():
    """Записаны обе сделки; equity/cum совпадают со stats.equity_curve (как CSV)."""
    journal = FakeJournal()
    w = _watcher(journal, _two_smc_trades(), balance=1020.0)
    assert w.tick() == 2

    # эталон — тот же расчёт, что пишет CSV: start = balance − сумма P&L = 1000
    trades = st.collect_closed(FakeMT5(_two_smc_trades()), days=30)
    want = [(int(t.position_id), eq, cum)
            for t, eq, cum in st.equity_curve(trades, 1000.0)]
    assert journal.created == want
    assert want == [(100, 1050.0, 50.0), (200, 1020.0, 20.0)]


def test_dedup_skips_existing_position_ids():
    journal = FakeJournal(existing={100})
    w = _watcher(journal, _two_smc_trades())
    assert w.tick() == 1
    assert [c[0] for c in journal.created] == [200]   # pos100 уже в базе


def test_idempotent_across_ticks():
    journal = FakeJournal()
    w = _watcher(journal, _two_smc_trades())
    assert w.tick() == 2
    assert w.tick() == 0                       # второй проход не плодит дублей
    assert journal.queries == 1                # база перечитана лишь однажды (кэш)
    assert len(journal.created) == 2


def test_failure_never_raises_and_alerts_once():
    journal = FakeJournal(fail=True)
    notifier = FakeNotifier()
    w = _watcher(journal, _two_smc_trades(), notifier=notifier, alert_after_fails=2)

    assert w.tick() == 0                        # 1-й сбой — тревоги ещё нет
    assert notifier.sent == []
    assert w._known is None                     # кэш сброшен для перечитки
    assert w.tick() == 0                        # 2-й сбой — одна тревога
    assert len(notifier.sent) == 1
    assert "не обновл" in notifier.sent[0].lower()
    assert w.tick() == 0                        # 3-й сбой — без повторного спама
    assert len(notifier.sent) == 1


def test_recovery_sends_all_clear():
    journal = FakeJournal(fail=True)
    notifier = FakeNotifier()
    w = _watcher(journal, _two_smc_trades(), notifier=notifier, alert_after_fails=1)
    w.tick()
    assert len(notifier.sent) == 1              # тревога поднята
    journal.fail = False
    assert w.tick() == 2                        # ожил, записал обе
    assert len(notifier.sent) == 2
    assert "восстанов" in notifier.sent[1].lower()


def test_charts_rebuilt_on_new_rows_then_skipped():
    journal = FakeJournal()
    fc = FakeCharts()
    w = _watcher(journal, _two_smc_trades(), charts=fc)
    assert w.tick() == 2
    assert fc.rebuilds == 1            # новые строки → графики пересобраны
    assert w.tick() == 0
    assert fc.rebuilds == 1            # пустой тик → повторно не перезаливаем


def test_charts_built_once_even_without_new_rows():
    journal = FakeJournal(existing={100, 200})   # всё уже в базе
    fc = FakeCharts()
    w = _watcher(journal, _two_smc_trades(), charts=fc)
    assert w.tick() == 0              # новых строк нет
    assert fc.rebuilds == 1          # но график построить на старте всё равно надо


def test_row_formatting_helpers():
    t = st.collect_closed(FakeMT5(_two_smc_trades()), days=30)[0]
    assert _title(t).startswith("SMC EURUSD long · ")
    iso = _iso(t.close_time)
    # ISO с локальным смещением: "YYYY-MM-DDTHH:MM:SS±HH:MM" — начинается с
    # настенного времени сделки, дальше tz-смещение (не голое наивное время).
    assert iso[:19] == t.close_time.strftime("%Y-%m-%dT%H:%M:%S")
    assert iso[19] in "+-" and len(iso) == 25
    assert _icon(50.0) == "✅" and _icon(-30.0) == "❌" and _icon(0.0) == "➖"
