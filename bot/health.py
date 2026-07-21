"""Мониторинг живости бота. Принцип: молчание = авария.

Каждые N минут снимаем 4 проверки и сравниваем с прошлым состоянием:
  1. ``signal``  — источник сигналов жив (CDP-поток к TradingView получал кадры
                   за последние N минут / http-приёмник поднят);
  2. ``mt5_login`` — MT5 отвечает и активен ОЖИДАЕМЫЙ счёт из .env;
  3. ``terminal_algo`` — кнопка «Алго-торговля» в терминале включена
                   (terminal_info().trade_allowed);
  4. ``expert``  — сервер разрешает торговлю советникам
                   (account_info().trade_expert).

Уведомления (Telegram):
  * НЕМЕДЛЕННО при переходе проверки OK→FAIL и FAIL→OK;
  * анти-спам: одна и та же авария повторно напоминает не чаще раза в
    ``antispam_sec`` (по умолчанию 30 мин);
  * раз в сутки в фиксированное время — сводка;
  * старт и штатная остановка — короткое сообщение.

Логика переходов (``HealthMonitor.evaluate``) отделена от ввода-вывода и покрыта
тестами (``tests/test_health.py``).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, List, Optional

log = logging.getLogger("bot")

ROOT = Path(__file__).resolve().parent.parent

# Порядок = порядок вывода в сводке. key → человекочитаемый заголовок.
CHECK_TITLES = {
    "signal": "Поток сигналов (TradingView)",
    "mt5_login": "Связь с MT5-счётом",
    "terminal_algo": "Кнопка «Алго-торговля» в MT5",
    "expert": "Серверное разрешение советникам",
}
CHECK_ORDER = list(CHECK_TITLES)

# Опциональные проверки — включаются, только если задан соответствующий источник.
# Держим отдельно от CHECK_TITLES, чтобы CHECK_ORDER (и тесты на «4/4») не менялись.
OPTIONAL_TITLES = {
    "webhook": "Резервный webhook-буфер",
}


@dataclass
class CheckResult:
    """Итог одной проверки за один цикл."""
    key: str
    ok: bool
    detail: str = ""       # человекочитаемая причина (для FAIL)
    remedy: str = ""       # что сделать, чтобы починить


@dataclass
class _CheckState:
    """Внутреннее состояние проверки между циклами (для машины переходов)."""
    status: Optional[str] = None            # None | "ok" | "fail"
    detail: str = ""
    fail_since: float = 0.0
    announced: bool = False                 # отправляли ли FAIL по текущему эпизоду
    # -inf, чтобы ПЕРВАЯ авария всегда прошла анти-спам-окно и ушла сразу
    last_fail_sent: float = float("-inf")


# ── Счётчики для суточной сводки ──────────────────────────────────────────────
class HealthMetrics:
    """Потокобезопасные счётчики: сигналов принято, ордеров отправлено.

    Движок инкрементит из своего потока, health читает/сбрасывает из своего —
    поэтому под локом.
    """

    def __init__(self, clock: Callable[[], float] = time.time):
        self._lock = threading.Lock()
        self._clock = clock
        self.started_at = clock()
        self.signals_total = 0
        self.orders_total = 0
        self.signals_today = 0
        self.orders_today = 0

    def on_signal(self) -> None:
        with self._lock:
            self.signals_total += 1
            self.signals_today += 1

    def on_order(self) -> None:
        with self._lock:
            self.orders_total += 1
            self.orders_today += 1

    def take_daily(self) -> tuple[int, int]:
        """Вернуть суточные счётчики и обнулить их (вызывается при отправке сводки)."""
        with self._lock:
            s, o = self.signals_today, self.orders_today
            self.signals_today = 0
            self.orders_today = 0
            return s, o

    def uptime_sec(self) -> float:
        return self._clock() - self.started_at


def _human_uptime(sec: float) -> str:
    sec = int(max(0, sec))
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    if d:
        return f"{d}д {h}ч {m}м"
    if h:
        return f"{h}ч {m}м"
    return f"{m}м"


class HealthMonitor:
    """Собирает проверки, гоняет машину переходов, шлёт Telegram.

    Проверки выполняются в ГЛАВНОМ потоке бота (через ``maybe_tick``), поэтому
    вызовы MT5 не пересекаются с исполнением ордеров — отдельный поток и лок не
    нужны.
    """

    def __init__(self, notifier, cfg, broker=None, source=None,
                 metrics: Optional[HealthMetrics] = None, webhook_source=None,
                 clock: Callable[[], float] = time.time):
        self.notifier = notifier
        self.cfg = cfg
        self.broker = broker
        self.source = source
        self.webhook_source = webhook_source
        self.metrics = metrics or HealthMetrics(clock=clock)
        self.clock = clock

        h = getattr(cfg, "health", None)
        self.interval = getattr(h, "check_interval_sec", 300)
        self.cdp_stale = getattr(h, "cdp_stale_sec", 600)
        self.antispam = getattr(h, "antispam_sec", 1800)
        self.daily_at = getattr(h, "daily_summary_at", "09:00")
        self.webhook_stale = getattr(getattr(cfg, "webhook", None), "stale_sec", 90)

        # Порядок и заголовки проверок — инстансные: 4 базовых + webhook, если он
        # задан. Когда webhook выключен, всё идентично прежнему (тесты «4/4» целы).
        self.check_order = list(CHECK_ORDER)
        self.titles = dict(CHECK_TITLES)
        if webhook_source is not None:
            self.check_order.append("webhook")
            self.titles["webhook"] = OPTIONAL_TITLES["webhook"]

        self._states = {k: _CheckState() for k in self.check_order}
        self._last_ok_count = 0
        self._last_tick = 0.0
        self._last_summary_day: Optional[str] = None
        self._summary_inited = False

    # ── Машина переходов (чистая логика, тестируется отдельно) ─────────────────
    def evaluate(self, results: List[CheckResult], now: float) -> List[str]:
        """Обновить состояние по свежим ``results`` и вернуть сообщения к отправке.

        Возвращает список готовых текстов (без префикса — его добавит notifier).
        Здесь же живёт анти-спам, поэтому именно этот метод покрыт тестами.
        """
        msgs: List[str] = []
        ok_count = 0
        for r in results:
            if r.ok:
                ok_count += 1
            st = self._states.setdefault(r.key, _CheckState())
            msgs.extend(self._process(st, r, now))
        self._last_ok_count = ok_count
        return msgs

    def _process(self, st: _CheckState, r: CheckResult, now: float) -> List[str]:
        title = self.titles.get(r.key, r.key)
        out: List[str] = []
        if r.ok:
            if st.status == "fail" and st.announced:
                downtime = _human_uptime(now - st.fail_since) if st.fail_since else ""
                tail = f" (была не в норме {downtime})" if downtime else ""
                out.append(f"🟢 Восстановлено · {title}{tail}")
            st.status = "ok"
            st.detail = ""
            st.fail_since = 0.0
            st.announced = False
            # last_fail_sent НЕ сбрасываем: быстрый повторный сбой (флап) в
            # пределах анти-спам-окна не должен долбить заново.
            return out

        # r.ok is False
        if st.status != "fail":
            st.status = "fail"
            st.fail_since = now
            st.announced = False
        st.detail = r.detail
        if now - st.last_fail_sent >= self.antispam:
            if st.announced:  # уже сообщали — это напоминание о длящейся аварии
                mins = int((now - st.fail_since) / 60)
                out.append(f"🔴 Всё ещё авария ({mins} мин) · {title}\n{r.detail}")
            else:             # первый сигнал об этом эпизоде — переход OK→FAIL
                body = f"🔴 АВАРИЯ · {title}\n{r.detail}"
                if r.remedy:
                    body += f"\nЧто делать: {r.remedy}"
                out.append(body)
            st.last_fail_sent = now
            st.announced = True
        return out

    # ── Сбор реальных проверок (ввод-вывод) ───────────────────────────────────
    def collect(self, now: Optional[float] = None) -> List[CheckResult]:
        now = self.clock() if now is None else now
        results = [
            self._check_signal(now),
            *self._check_mt5(now),
        ]
        if self.webhook_source is not None:
            results.append(self._check_webhook(now))
        return results

    def _check_webhook(self, now: float) -> CheckResult:
        try:
            ok, detail, remedy = self.webhook_source.health(now, self.webhook_stale)
        except Exception as e:
            return CheckResult("webhook", False,
                               f"ошибка опроса webhook-буфера: {e}",
                               "Проверь бота в консоли — исключение в webhook-источнике.")
        return CheckResult("webhook", ok, detail, remedy)

    def _check_signal(self, now: float) -> CheckResult:
        if self.source is None:
            return CheckResult("signal", True, "источник сигналов не задан")
        try:
            ok, detail, remedy = self.source.health(now, self.cdp_stale)
        except Exception as e:
            return CheckResult("signal", False,
                               f"ошибка опроса источника сигналов: {e}",
                               "Проверь бота в консоли — исключение в источнике сигналов.")
        return CheckResult("signal", ok, detail, remedy)

    def _check_mt5(self, now: float) -> List[CheckResult]:
        expected = getattr(getattr(self.cfg, "mt5", None), "login", None)
        if self.broker is None:
            unknown = "брокер MT5 не подключён"
            return [CheckResult(k, True, unknown) for k in
                    ("mt5_login", "terminal_algo", "expert")]
        try:
            snap = self.broker.health_snapshot()
        except Exception as e:
            fail = CheckResult("mt5_login", False,
                               f"ошибка обращения к MT5: {e}",
                               "Проверь, что терминал MT5 запущен и залогинен.")
            # если MT5 недоступен — остальные MT5-проверки тоже не снять
            return [fail,
                    CheckResult("terminal_algo", True, "MT5 недоступен — проверка отложена"),
                    CheckResult("expert", True, "MT5 недоступен — проверка отложена")]
        return _mt5_results(snap, expected)

    # ── Отправка с логированием ────────────────────────────────────────────────
    def _emit(self, msg: str) -> bool:
        """Отправить сообщение в Telegram и записать факт в bot.log.

        Успех — INFO (видно, что и когда ушло), провал доставки — WARNING
        (потерянный алерт важно видеть). Многострочные сообщения схлопываем
        в одну строку лога.
        """
        ok = self.notifier.send(msg)
        oneline = " | ".join(p.strip() for p in msg.splitlines() if p.strip())
        if ok:
            log.info("health → Telegram: %s", oneline)
        else:
            log.warning("health: сообщение НЕ доставлено в Telegram: %s", oneline)
        return ok

    def _status_line(self) -> str:
        parts = []
        for k in self.check_order:
            st = self._states[k].status or "?"
            parts.append(f"{k}={'OK' if st == 'ok' else st.upper()}")
        return f"проверки {self._last_ok_count}/{len(self.check_order)}: " + " ".join(parts)

    # ── Тик: собрать → оценить → отправить + суточная сводка ───────────────────
    def tick(self, now: Optional[float] = None) -> None:
        now = self.clock() if now is None else now
        try:
            results = self.collect(now)
            msgs = self.evaluate(results, now)
            for m in msgs:
                self._emit(m)
            if not msgs:
                # Ничего не изменилось — heartbeat на DEBUG, чтобы INFO-лог
                # оставался чистым (только события), но при уровне DEBUG было
                # видно, что монитор реально тикает.
                log.debug("health: %s", self._status_line())
        except Exception as e:  # мониторинг НИКОГДА не должен ронять бота
            log.exception("Сбой в health-tick: %s", e)
        self._last_tick = now
        self._maybe_daily_summary(now)

    def maybe_tick(self, now: Optional[float] = None) -> None:
        now = self.clock() if now is None else now
        if now - self._last_tick >= self.interval:
            self.tick(now)

    def _summary_time(self) -> tuple[int, int]:
        try:
            hh, mm = (int(x) for x in self.daily_at.split(":"))
            return hh, mm
        except Exception:
            return 9, 0

    def _maybe_daily_summary(self, now: float) -> None:
        dt = datetime.fromtimestamp(now)
        today = dt.date().isoformat()
        hh, mm = self._summary_time()
        passed = (dt.hour, dt.minute) >= (hh, mm)

        # Первый заход: если время сводки СЕГОДНЯ уже прошло на старте — помечаем
        # день отправленным, чтобы не дампить сводку за едва наблюдавшийся день;
        # первая настоящая сводка уйдёт завтра в срок. Если ещё не прошло —
        # оставляем как есть, сводка уйдёт сегодня при пересечении времени.
        if not self._summary_inited:
            self._summary_inited = True
            if passed:
                self._last_summary_day = today

        if self._last_summary_day == today:
            return
        if passed:
            self._emit(self._summary_text(now))
            self._last_summary_day = today

    def _summary_text(self, now: float) -> str:
        signals, orders = self.metrics.take_daily()
        total = len(self.check_order)
        ok = self._last_ok_count
        failing = [self.titles[k] for k in self.check_order
                   if self._states[k].status == "fail"]
        line = (f"📊 Жив. Аптайм {_human_uptime(self.metrics.uptime_sec())}. "
                f"Проверки {ok}/{total}. "
                f"Сигналов за сутки: {signals}, ордеров: {orders}.")
        if failing:
            line += "\n⚠️ Не в норме: " + ", ".join(failing)
        return line

    # ── Жизненный цикл ────────────────────────────────────────────────────────
    def hello(self, mode: str, login=None) -> None:
        where = f"счёт {login} " if login else ""
        self._emit(f"▶️ Бот запущен ({where}{mode}). "
                   f"Мониторинг живости включён, проверки каждые "
                   f"{self.interval // 60} мин.")

    def goodbye(self) -> None:
        self._emit("⏹ Бот остановлен штатно. "
                   "Сигналы за время простоя обработаны не будут.")

    def start_first_check(self, now: Optional[float] = None) -> None:
        """Немедленная первая проверка на старте — не ждать 5 минут, чтобы
        поймать уже существующую аварию (напр. алго-торговля выключена)."""
        self.tick(now)
        # Базлайн в лог на INFO: видно исходное состояние всех проверок на старте
        # (дальше при отсутствии изменений тик пишет heartbeat только на DEBUG).
        log.info("health: старт мониторинга — %s", self._status_line())


def _mt5_results(snap, expected) -> List[CheckResult]:
    """Разложить снимок MT5 (broker.health_snapshot) в 3 CheckResult."""
    # 1) связь со счётом + ожидаемый логин
    if not snap.initialized or snap.login is None:
        login_res = CheckResult(
            "mt5_login", False,
            snap.error or "нет связи со счётом MT5",
            f"Открой терминал Just2Trade MT5 и залогинься в демо-счёт "
            f"{expected if expected else ''}".rstrip())
    elif expected and snap.login != expected:
        login_res = CheckResult(
            "mt5_login", False,
            f"в терминале активен ДРУГОЙ счёт {snap.login}, ожидался {expected}",
            "Переключи терминал MT5 на нужный демо-счёт — иначе ордера уходят "
            "не туда.")
    else:
        login_res = CheckResult("mt5_login", True,
                                f"счёт {snap.login} на связи")

    # 2) кнопка «Алго-торговля» (терминал)
    if snap.trade_allowed is True:
        algo_res = CheckResult("terminal_algo", True, "алго-торговля включена")
    elif snap.trade_allowed is False:
        algo_res = CheckResult(
            "terminal_algo", False,
            "в терминале MT5 ВЫКЛЮЧЕНА кнопка «Алго-торговля» — каждый ордер "
            "отклоняется (retcode 10027), сигнал теряется",
            "Включи кнопку «Алго-торговля» на панели терминала (или Сервис → "
            "Настройки → Советники → Разрешить алгоритмическую торговлю).")
    else:
        algo_res = CheckResult("terminal_algo", False,
                               "терминал MT5 не отвечает о состоянии алго-торговли",
                               "Проверь, что терминал MT5 запущен.")

    # 3) серверное разрешение советникам
    if snap.trade_expert is True:
        exp_res = CheckResult("expert", True, "сервер разрешает советников")
    elif snap.trade_expert is False:
        exp_res = CheckResult(
            "expert", False,
            "сервер запретил торговлю советникам "
            "(account_info().trade_expert=False) — ордера бота будут отклоняться",
            "Проверь тип/настройки счёта у брокера; на демо это обычно "
            "переоткрытие счёта с разрешённой алго-торговлей.")
    else:
        exp_res = CheckResult("expert", False,
                              "нет данных о серверном разрешении советникам",
                              "Проверь связь терминала MT5 с сервером.")
    return [login_res, algo_res, exp_res]


# ── pid-файл для внешнего watchdog ────────────────────────────────────────────
def pid_path(cfg) -> Path:
    rel = getattr(getattr(cfg, "health", None), "pid_file", "logs/bot.pid")
    p = Path(rel)
    return p if p.is_absolute() else (ROOT / p)


def write_pid_file(cfg) -> Path:
    p = pid_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(os.getpid()), encoding="utf-8")
    return p


def remove_pid_file(cfg) -> None:
    try:
        pid_path(cfg).unlink()
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning("Не удалось удалить pid-файл: %s", e)
