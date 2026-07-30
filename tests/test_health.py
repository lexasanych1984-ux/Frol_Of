"""Мониторинг живости: машина переходов OK↔FAIL и анти-спам.

Логика уведомлений — самая ответственная часть (от неё зависит, узнаем ли мы об
аварии), поэтому тестируется в отрыве от ввода-вывода: ``HealthMonitor.evaluate``
принимает готовые результаты проверок и возвращает список сообщений к отправке.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.broker_mt5 import Mt5Health
from bot.health import (CheckResult, HealthMetrics, HealthMonitor, _mt5_results,
                        _human_uptime)


# ── Минимальные фейки конфига/нотификатора ────────────────────────────────────
class _H:
    check_interval_sec = 300
    cdp_stale_sec = 600
    antispam_sec = 1800          # 30 минут
    daily_summary_at = "09:00"
    pid_file = "logs/bot.pid"


class _Mt5:
    login = 245169


class FakeCfg:
    health = _H()
    mt5 = _Mt5()


class DummyNotifier:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)
        return True


class _FakeState:
    """meta-таблица State: в ней живёт день последней суточной сводки."""

    def __init__(self):
        self.meta = {}

    def get_meta(self, key):
        return self.meta.get(key)

    def set_meta(self, key, value):
        self.meta[key] = value


def mk(state=None):
    return HealthMonitor(DummyNotifier(), FakeCfg(), broker=None, source=None,
                         state=state)


def R(key, ok, detail="причина", remedy="что делать"):
    return CheckResult(key, ok, detail, remedy)


# ── Переходы OK↔FAIL ──────────────────────────────────────────────────────────
def test_all_ok_is_silent():
    m = mk()
    msgs = m.evaluate([R("signal", True), R("mt5_login", True),
                       R("terminal_algo", True), R("expert", True)], now=0)
    assert msgs == []


def test_ok_to_fail_notifies_immediately_with_remedy():
    m = mk()
    assert m.evaluate([R("terminal_algo", True)], now=1000) == []
    msgs = m.evaluate([R("terminal_algo", False, "алго выключена", "включи кнопку")],
                      now=1300)
    assert len(msgs) == 1
    assert "АВАРИЯ" in msgs[0]
    assert "алго выключена" in msgs[0]
    assert "включи кнопку" in msgs[0]


def test_fail_to_ok_notifies_recovery():
    m = mk()
    f = [R("terminal_algo", False, "off", "on")]
    ok = [R("terminal_algo", True)]
    assert len(m.evaluate(f, now=0)) == 1
    msgs = m.evaluate(ok, now=300)
    assert len(msgs) == 1
    assert "Восстановлено" in msgs[0]
    # дальше тишина, пока снова не сломается
    assert m.evaluate(ok, now=600) == []


def test_first_fail_fires_regardless_of_clock():
    # last_fail_sent = -inf, поэтому первая авария проходит анти-спам при любом now
    m = mk()
    msgs = m.evaluate([R("signal", False, "поток молчит", "проверь TV")], now=5)
    assert len(msgs) == 1


# ── Анти-спам ─────────────────────────────────────────────────────────────────
def test_repeated_fail_suppressed_until_window_then_reminds():
    m = mk()  # antispam = 1800 c
    f = [R("terminal_algo", False, "off", "on")]
    assert len(m.evaluate(f, now=0)) == 1        # первая авария — ушла
    assert m.evaluate(f, now=300) == []          # +5 мин — молчим
    assert m.evaluate(f, now=1500) == []         # +25 мин — молчим
    msgs = m.evaluate(f, now=1800)               # +30 мин — напоминание
    assert len(msgs) == 1
    assert "Всё ещё авария" in msgs[0]


def test_reminder_not_more_than_once_per_window():
    m = mk()
    f = [R("mt5_login", False, "нет связи", "залогинься")]
    m.evaluate(f, now=0)                          # авария
    reminders = 0
    for t in range(300, 1801, 300):              # каждые 5 мин полчаса
        reminders += len(m.evaluate(f, now=t))
    assert reminders == 1                         # ровно одно напоминание за 30 мин


def test_flap_after_recovery_is_throttled():
    m = mk()
    f = [R("terminal_algo", False, "off", "on")]
    ok = [R("terminal_algo", True)]
    assert len(m.evaluate(f, now=0)) == 1        # авария
    assert len(m.evaluate(ok, now=300)) == 1     # восстановление
    # снова сломалось быстро — в пределах анти-спам-окна: FAIL подавлен
    assert m.evaluate(f, now=600) == []
    # и восстановление тоже молчит, раз об аварии не сообщали
    assert m.evaluate(ok, now=900) == []


# ── Независимость проверок ────────────────────────────────────────────────────
def test_checks_are_independent():
    m = mk()
    # signal падает, expert падает, остальные ок — два разных сообщения
    msgs = m.evaluate([R("signal", False, "молчит", "тв"),
                       R("mt5_login", True),
                       R("terminal_algo", True),
                       R("expert", False, "сервер запретил", "к брокеру")], now=0)
    assert len(msgs) == 2
    joined = "\n".join(msgs)
    assert "Поток сигналов" in joined
    assert "Серверное разрешение" in joined
    # только signal чинится
    msgs = m.evaluate([R("signal", True),
                       R("mt5_login", True),
                       R("terminal_algo", True),
                       R("expert", False, "сервер запретил", "к брокеру")], now=300)
    assert len(msgs) == 1
    assert "Восстановлено" in msgs[0] and "Поток сигналов" in msgs[0]


# ── Приёмка: алго-торговля off → on через полный конвейер collect→evaluate ─────
class FakeBroker:
    def __init__(self, snap):
        self.snap = snap

    def health_snapshot(self):
        return self.snap


class FakeSource:
    def health(self, now, stale_sec):
        return True, "ok", ""


def test_acceptance_algo_off_then_on_end_to_end():
    cfg = FakeCfg()
    broker = FakeBroker(Mt5Health(True, 245169, True, True))
    m = HealthMonitor(DummyNotifier(), cfg, broker=broker, source=FakeSource())
    assert m.evaluate(m.collect(1000), 1000) == []          # всё ок

    broker.snap = Mt5Health(True, 245169, trade_allowed=False, trade_expert=True)
    msgs = m.evaluate(m.collect(1300), 1300)                # выключили кнопку
    assert any("Алго-торговля" in x and "10027" in x for x in msgs)

    broker.snap = Mt5Health(True, 245169, True, True)
    msgs = m.evaluate(m.collect(1600), 1600)                # включили обратно
    assert any("Восстановлено" in x for x in msgs)


# ── Раскладка снимка MT5 в человекочитаемые причины ───────────────────────────
def test_mt5_results_login_mismatch():
    d = {r.key: r for r in _mt5_results(
        Mt5Health(True, 999, True, True), expected=245169)}
    assert d["mt5_login"].ok is False
    assert "999" in d["mt5_login"].detail and "245169" in d["mt5_login"].detail
    assert d["terminal_algo"].ok is True
    assert d["expert"].ok is True


def test_mt5_results_algo_off_mentions_retcode():
    d = {r.key: r for r in _mt5_results(
        Mt5Health(True, 245169, False, True), expected=245169)}
    assert d["mt5_login"].ok is True
    assert d["terminal_algo"].ok is False
    assert "10027" in d["terminal_algo"].detail
    assert d["expert"].ok is True


def test_mt5_results_expert_off():
    d = {r.key: r for r in _mt5_results(
        Mt5Health(True, 245169, True, False), expected=245169)}
    assert d["expert"].ok is False
    assert "trade_expert" in d["expert"].detail


def test_mt5_results_no_connection():
    d = {r.key: r for r in _mt5_results(
        Mt5Health(False, None, None, None, "нет связи со счётом"), expected=245169)}
    assert d["mt5_login"].ok is False
    assert "нет связи" in d["mt5_login"].detail


# ── Счётчики суточной сводки ──────────────────────────────────────────────────
def test_metrics_daily_reset_keeps_totals():
    mx = HealthMetrics(clock=lambda: 1000.0)
    mx.on_signal(); mx.on_signal(); mx.on_order()
    assert mx.take_daily() == (2, 1)
    assert mx.take_daily() == (0, 0)        # после сброса — нули
    assert mx.signals_total == 2            # общий счётчик не трогается
    assert mx.orders_total == 1


def test_human_uptime_formats():
    assert _human_uptime(90) == "1м"
    assert _human_uptime(3660) == "1ч 1м"
    assert _human_uptime(90000) == "1д 1ч 0м"


# ── Суточная сводка в фиксированное время ─────────────────────────────────────
import datetime as _dt


def _ts(y, mo, d, h, mi):
    """Epoch для ЛОКАЛЬНОГО времени — так тест не зависит от таймзоны машины."""
    return _dt.datetime(y, mo, d, h, mi, 0).timestamp()


def test_daily_summary_fires_at_time_once_per_day():
    m = mk()  # daily_at = 09:00
    n = m.notifier
    m._maybe_daily_summary(_ts(2026, 7, 21, 8, 0))    # рано
    assert n.sent == []
    m._maybe_daily_summary(_ts(2026, 7, 21, 8, 55))   # ещё рано
    assert n.sent == []
    m.evaluate([R("signal", True), R("mt5_login", True),
                R("terminal_algo", True), R("expert", True)], now=_ts(2026, 7, 21, 8, 59))
    m._maybe_daily_summary(_ts(2026, 7, 21, 9, 0))    # время — сводка
    assert len(n.sent) == 1 and "Жив" in n.sent[0] and "4/4" in n.sent[0]
    m._maybe_daily_summary(_ts(2026, 7, 21, 9, 5))    # тот же день — не дублирует
    assert len(n.sent) == 1
    m._maybe_daily_summary(_ts(2026, 7, 22, 9, 0))    # следующий день — снова
    assert len(n.sent) == 2


# ── Пятая проверка «webhook» (опциональная) ───────────────────────────────────
class _DownWebhook:
    def health(self, now, stale_sec):
        return (False, "буфер недоступен", "проверь worker")


def test_webhook_check_absent_by_default():
    # Без webhook_source — ровно 4 проверки, поведение прежнее.
    m = mk()
    assert m.check_order == ["signal", "mt5_login", "terminal_algo", "expert"]


def test_webhook_check_added_when_source_present():
    cfg = FakeCfg()
    broker = FakeBroker(Mt5Health(True, 245169, True, True))
    m = HealthMonitor(DummyNotifier(), cfg, broker=broker, source=FakeSource(),
                      webhook_source=FakeSource())
    keys = [r.key for r in m.collect(1000)]
    assert keys == ["signal", "mt5_login", "terminal_algo", "expert", "webhook"]
    assert m.evaluate(m.collect(1000), 1000) == []          # всё ок — тихо
    # суточная сводка отражает 5/5
    assert "5/5" in m._summary_text(1000)


def test_webhook_failure_notifies_with_its_title():
    cfg = FakeCfg()
    broker = FakeBroker(Mt5Health(True, 245169, True, True))
    m = HealthMonitor(DummyNotifier(), cfg, broker=broker, source=FakeSource(),
                      webhook_source=_DownWebhook())
    msgs = m.evaluate(m.collect(2000), 2000)
    assert any("Резервный webhook-буфер" in x and "буфер недоступен" in x for x in msgs)


def test_daily_summary_sent_on_start_after_missed_time():
    """Старт после времени сводки — сводка уходит сразу, а не пропадает.

    Бот живёт 09:30–23:20, время сводки 09:00 приходится на простой. Раньше
    стартовый заход помечал день отправленным и следующее 09:00 бот уже не видел:
    за неделю не ушло ни одной сводки, и неделя нулевых сигналов прошла молча.
    """
    state = _FakeState()
    m = mk(state)
    n = m.notifier
    m._maybe_daily_summary(_ts(2026, 7, 21, 10, 0))   # старт уже после 09:00
    assert len(n.sent) == 1 and "Жив" in n.sent[0]
    m._maybe_daily_summary(_ts(2026, 7, 21, 23, 0))   # тот же день — без повтора
    assert len(n.sent) == 1
    m._maybe_daily_summary(_ts(2026, 7, 22, 9, 30))   # следующий день — снова
    assert len(n.sent) == 2


def test_daily_summary_not_duplicated_after_restart_same_day():
    """Несколько запусков за сутки не должны дублировать сводку (день в State)."""
    state = _FakeState()
    m1 = mk(state)
    m1._maybe_daily_summary(_ts(2026, 7, 21, 10, 0))
    assert len(m1.notifier.sent) == 1

    m2 = mk(state)                                    # рестарт в тот же день
    m2._maybe_daily_summary(_ts(2026, 7, 21, 14, 0))
    assert m2.notifier.sent == []


def test_goodbye_reports_session_counters():
    """Сообщение об останове несёт итог сессии — иначе тихий день не видно."""
    m = mk(_FakeState())
    m.metrics.on_signal()
    m.metrics.on_order()
    m.goodbye()
    assert len(m.notifier.sent) == 1
    assert "сигналов: 1" in m.notifier.sent[0] and "ордеров: 1" in m.notifier.sent[0]
