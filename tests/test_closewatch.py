"""Уведомление о закрытии позиции: детектим закрытые сделки в истории MT5.

Позиции закрываются по SL/TP на брокере, поэтому пуш строится на истории сделок,
а не на exit-сигнале. Проверяем: первый старт не переигрывает историю; новое
закрытие шлётся один раз; ручные/чужие сделки игнорируются; курсор персистится.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.closewatch import PositionCloseWatcher


class _Deal:
    def __init__(self, position_id, symbol, entry, volume, time, profit, type_,
                 price, magic):
        self.position_id = position_id
        self.symbol = symbol
        self.entry = entry
        self.volume = volume
        self.time = time
        self.profit = profit
        self.type = type_
        self.price = price
        self.magic = magic
        self.swap = 0.0
        self.commission = 0.0
        self.fee = 0.0


class _FakeMT5:
    DEAL_ENTRY_IN = 0
    DEAL_TYPE_BUY = 0

    def __init__(self, deals, window_deals=None):
        self._deals = deals              # полная история (запрос по position=)
        self._window = window_deals      # что попало в окно frm..to (None = всё)

    def history_deals_get(self, frm=None, to=None, position=None):
        if position is not None:
            return [d for d in self._deals if d.position_id == position]
        return list(self._window if self._window is not None else self._deals)


class _FakeBroker:
    def __init__(self, mt5):
        self.mt5 = mt5


class _RecordingNotifier:
    def __init__(self):
        self.messages = []

    def send(self, text):
        self.messages.append(text)
        return True


class _FakeState:
    def __init__(self):
        self.meta = {}

    def get_meta(self, key):
        return self.meta.get(key)

    def set_meta(self, key, value):
        self.meta[key] = value


def _closed(pid, magic, symbol, close_t, profit, price_in=1.10, price_out=1.20,
            vol=1.0):
    """IN(buy) + OUT дилы одной закрытой позиции. entry: 0=IN, 1=OUT."""
    return [
        _Deal(pid, symbol, 0, vol, close_t - 100, 0.0, 0, price_in, magic),
        _Deal(pid, symbol, 1, vol, close_t, profit, 1, price_out, magic),
    ]


def _watcher(mt5, notifier, state):
    return PositionCloseWatcher(notifier, _FakeBroker(mt5), state, interval_sec=0)


def test_first_tick_no_replay_then_notifies_new_close():
    mt5 = _FakeMT5(_closed(1, 770001, "EURUSD", close_t=1000, profit=50.0))
    notifier, state = _RecordingNotifier(), _FakeState()
    w = _watcher(mt5, notifier, state)

    w.tick(now=0)                       # первый старт — историю не переигрываем
    assert notifier.messages == []

    mt5._deals += _closed(2, 770001, "GBPJPY", close_t=2000, profit=-30.0)
    w.tick(now=1)
    assert len(notifier.messages) == 1
    m = notifier.messages[0]
    assert "ЗАКРЫТИЕ" in m and "GBPJPY" in m and "-30.00" in m and "❌" in m

    w.tick(now=2)                       # идемпотентность — без повтора
    assert len(notifier.messages) == 1


def test_profit_marker_and_strategy_label():
    mt5 = _FakeMT5([])
    notifier, state = _RecordingNotifier(), _FakeState()
    w = _watcher(mt5, notifier, state)
    w.tick(now=0)                       # пустая история → курсор 0

    mt5._deals = _closed(5, 770001, "EURUSD", close_t=3000, profit=123.45)
    w.tick(now=1)
    m = notifier.messages[-1]
    assert "✅" in m and "SMC" in m and "+123.45" in m


def test_foreign_trades_ignored():
    mt5 = _FakeMT5(_closed(1, 0, "EURUSD", close_t=1000, profit=50.0))  # magic 0
    notifier, state = _RecordingNotifier(), _FakeState()
    w = _watcher(mt5, notifier, state)
    w.tick(now=0)
    mt5._deals += _closed(2, 0, "EURUSD", close_t=2000, profit=10.0)
    w.tick(now=1)
    assert notifier.messages == []     # чужие/ручные закрытия не уведомляем


def test_notifies_close_of_long_held_position():
    """Позиция, открытая раньше окна истории, тоже должна дать уведомление.

    Так молча пропали два минуса (28.07 GBPJPY -976 и 30.07 EURUSD -1336): в окно
    closewatch (history_days=3) входной дил не попадал, сделка считалась открытой,
    Telegram молчал, а курсор закрытий стоял на 22.07.
    """
    old = _closed(1, 770001, "EURUSD", close_t=1000, profit=50.0)
    notifier, state = _RecordingNotifier(), _FakeState()

    mt5 = _FakeMT5(list(old))
    w = _watcher(mt5, notifier, state)
    w.tick(now=0)                       # первый старт — курсор на 1000
    assert notifier.messages == []

    # закрытие позиции, открытой задолго до окна: в окне виден только выход
    entry = _Deal(7, "GBPJPY", 0, 1.93, 100, 0.0, 0, 218.353, 770001)
    out = _Deal(7, "GBPJPY", 1, 1.93, 5000, -976.79, 1, 217.519, 770001)
    mt5._deals = old + [entry, out]
    mt5._window = old + [out]

    w.tick(now=1)
    assert len(notifier.messages) == 1
    m = notifier.messages[0]
    assert "ЗАКРЫТИЕ" in m and "GBPJPY" in m and "-976.79" in m
    assert "218.353" in m               # цена входа взята из добранной истории


def test_cursor_persisted_across_restart():
    deals = _closed(1, 770001, "EURUSD", close_t=1000, profit=50.0)
    state = _FakeState()
    # первый прогон инициализирует курсор
    _watcher(_FakeMT5(deals), _RecordingNotifier(), state).tick(now=0)
    assert state.meta.get("close_cursor") is not None

    # новый инстанс (рестарт) с тем же state: старое закрытие НЕ повторяется
    notifier2 = _RecordingNotifier()
    _watcher(_FakeMT5(deals), notifier2, state).tick(now=0)
    assert notifier2.messages == []
