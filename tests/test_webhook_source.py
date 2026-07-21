"""WebhookPullSource: порядок доставки, персист курсора, инициализация, health.

Сеть замокана (подменяем _pull/_head на инстансе) — тесты детерминированы и
оффлайн.
"""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.signals.webhook_source import WebhookPullSource, _CURSOR_KEY
from bot.state import State


def _src(state=None, **kw):
    return WebhookPullSource("https://x.example", "tok",
                             state or State(":memory:"), **kw)


def _drain(src):
    out = []
    while True:
        rs = src.poll(0.01)
        if rs is None:
            break
        out.append(rs)
    return out


# ── Порядок доставки ──────────────────────────────────────────────────────────
def test_ingest_delivers_in_ascending_id_order():
    src = _src()
    src._cursor_inited = True
    src._cursor = 0
    src._ingest([{"id": 3, "ts": 300, "body": "C"},
                 {"id": 1, "ts": 100, "body": "A"},
                 {"id": 2, "ts": 200, "body": "B"}])
    got = _drain(src)
    assert [r.raw for r in got] == ["A", "B", "C"]
    assert [r.ext_id for r in got] == ["1", "2", "3"]
    assert [r.received_ts for r in got] == [100.0, 200.0, 300.0]
    assert all(r.source == "webhook" for r in got)


def test_ingest_advances_and_persists_cursor():
    state = State(":memory:")
    src = _src(state)
    src._cursor_inited = True
    src._cursor = 0
    src._ingest([{"id": 6, "ts": 1, "body": "x"}, {"id": 7, "ts": 2, "body": "y"}])
    assert src._cursor == 7
    assert state.get_meta(_CURSOR_KEY) == "7"


def test_ingest_skips_ids_at_or_below_cursor():
    src = _src()
    src._cursor_inited = True
    src._cursor = 5
    n = src._ingest([{"id": 5, "ts": 1, "body": "old"},
                     {"id": 6, "ts": 2, "body": "new"}])
    assert n == 1
    assert [r.raw for r in _drain(src)] == ["new"]


# ── Инициализация курсора ─────────────────────────────────────────────────────
def test_first_start_inits_cursor_to_head_no_replay():
    state = State(":memory:")
    src = _src(state)
    src._head = lambda: 10           # в буфере уже есть история до id=10
    src._init_cursor()
    assert src._cursor == 10         # начинаем с текущего максимума
    assert state.get_meta(_CURSOR_KEY) == "10"


def test_restart_resumes_from_stored_cursor_without_head():
    state = State(":memory:")
    state.set_meta(_CURSOR_KEY, "42")
    src = _src(state)

    def _boom():
        raise AssertionError("head не должен вызываться при сохранённом курсоре")

    src._head = _boom
    src._init_cursor()
    assert src._cursor == 42         # продолжаем добор с сохранённого места


# ── Один опрос ────────────────────────────────────────────────────────────────
def test_poll_once_marks_alive_even_on_empty():
    src = _src()
    src._cursor_inited = True
    src._pull = lambda after: []      # буфер пуст — это норма
    assert src._poll_once(now=1000.0) == 0
    assert src._last_ok_poll_ts == 1000.0


def test_poll_once_ingests_and_advances():
    state = State(":memory:")
    src = _src(state)
    src._cursor_inited = True
    src._cursor = 0
    src._pull = lambda after: [{"id": 1, "ts": 5, "body": "sig"}]
    assert src._poll_once(now=2000.0) == 1
    assert src._cursor == 1
    assert [r.raw for r in _drain(src)] == ["sig"]


# ── Живость ───────────────────────────────────────────────────────────────────
def test_health_startup_grace_then_fail():
    src = _src()
    src._started_at = 1000.0
    ok, _, _ = src.health(now=1010.0, stale_sec=90)      # в форе
    assert ok is True
    ok, detail, remedy = src.health(now=1100.0, stale_sec=90)  # фора прошла, опроса нет
    assert ok is False
    assert "недоступен" in detail and remedy


# ── Потокобезопасность State (webhook-поток ≠ поток создания State) ───────────
def test_state_meta_usable_from_another_thread():
    """Регрессия: webhook-опрос идёт в отдельном потоке и пишет курсор в State,
    созданный в главном потоке (sqlite check_same_thread). Не должно падать."""
    state = State(":memory:")
    errors = []

    def worker():
        try:
            state.set_meta(_CURSOR_KEY, "42")
            assert state.get_meta(_CURSOR_KEY) == "42"
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert errors == []
    assert state.get_meta(_CURSOR_KEY) == "42"


def test_ingest_from_worker_thread_persists_cursor():
    """Полный путь курсора из чужого потока: _ingest пишет в State без ошибок."""
    state = State(":memory:")
    src = _src(state)
    src._cursor_inited = True
    src._cursor = 0
    errors = []

    def worker():
        try:
            src._ingest([{"id": 1, "ts": 5, "body": "sig"}])
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert errors == []
    assert state.get_meta(_CURSOR_KEY) == "1"


def test_health_recent_poll_ok_then_stale():
    src = _src()
    src._last_ok_poll_ts = 1000.0
    ok, _, _ = src.health(now=1050.0, stale_sec=90)      # опрос 50 с назад
    assert ok is True
    ok, detail, _ = src.health(now=1200.0, stale_sec=90)  # 200 с — просрочено
    assert ok is False
    assert "не опрашивается" in detail
