"""Резервный источник сигналов: pull из облачного store-and-forward буфера.

Работает ПАРАЛЛЕЛЬНО быстрому CDP-каналу. Убирает главный узел отказа: CDP видит
алерт, только пока бот запущен и TradingView жив; облачный буфер (Cloudflare
Worker + D1) принимает webhook от TradingView серверно и хранит ≥7 дней, а бот его
ОПРАШИВАЕТ (pull) — никаких входящих портов на домашнем ПК.

Курсор ``after=<id>`` персистится в State: после простоя бот добирает ровно то,
что пришло, пока он стоял. Протухшие входы отсекает гейт свежести в движке
(``Engine._fresh_enough``) — здесь мы только доставляем.

Живость: критерий — БЫЛ ЛИ НЕДАВНО УСПЕШНЫЙ ОПРОС (пустой ответ = норма, буфер
часто пуст), а не «пришли ли сигналы». Нет успешного опроса дольше stale_sec —
авария (резервный канал недоступен), по принципу «молчание = авария».
"""
from __future__ import annotations

import logging
import threading
import time
from typing import List, Optional

import requests

from .base import SignalSource

log = logging.getLogger("bot")

_CURSOR_KEY = "webhook_cursor"


class WebhookPullSource(SignalSource):
    name = "webhook"

    def __init__(self, pull_url: str, token: str, state, poll_interval_sec: int = 12,
                 pull_limit: int = 100, request_timeout: float = 10.0):
        super().__init__()
        self.base = pull_url.rstrip("/")
        self.token = token
        self.state = state
        self.poll_interval = max(3, int(poll_interval_sec))
        self.pull_limit = max(1, int(pull_limit))
        self.timeout = request_timeout
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._cursor = 0
        self._cursor_inited = False
        # Мониторинг живости.
        self._last_ok_poll_ts = 0.0
        self._last_error = ""
        self._started_at = 0.0
        self._connect_grace = 45.0

    # ── HTTP (отдельные методы — их подменяют тесты) ──────────────────────────
    def _pull(self, after: int) -> List[dict]:
        r = requests.get(f"{self.base}/pull/{self.token}",
                         params={"after": after, "limit": self.pull_limit},
                         timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []

    def _head(self) -> int:
        r = requests.get(f"{self.base}/head/{self.token}", timeout=self.timeout)
        r.raise_for_status()
        return int((r.json() or {}).get("max_id") or 0)

    # ── Логика доставки (чистая, тестируется отдельно) ────────────────────────
    def _load_cursor(self) -> int:
        raw = self.state.get_meta(_CURSOR_KEY) if self.state else None
        try:
            return int(raw) if raw is not None else -1
        except (TypeError, ValueError):
            return -1

    def _save_cursor(self, value: int) -> None:
        self._cursor = value
        if self.state:
            self.state.set_meta(_CURSOR_KEY, str(value))

    def _init_cursor(self) -> None:
        """Первый старт — начать с текущего максимума (не переигрывать буфер).
        Повторный — продолжить с сохранённого курсора (добрать простой)."""
        stored = self._load_cursor()
        if stored >= 0:
            self._cursor = stored
        else:
            self._cursor = self._head()
            self._save_cursor(self._cursor)
            log.info("webhook: первый старт, курсор инициализирован max_id=%d "
                     "(история буфера не переигрывается)", self._cursor)
        self._cursor_inited = True

    def _ingest(self, records: List[dict]) -> int:
        """Положить записи в очередь по возрастанию id, продвинуть и сохранить
        курсор. Возвращает число доставленных."""
        n = 0
        for rec in sorted(records, key=lambda r: int(r.get("id", 0))):
            rid = int(rec.get("id", 0))
            if rid <= self._cursor:
                continue
            body = rec.get("body") or ""
            if body:
                ts = rec.get("ts")
                self._put(body, received_ts=(float(ts) if ts is not None else None),
                          ext_id=str(rid))
                n += 1
            self._save_cursor(rid)
        if n:
            log.info("webhook: доставлено %d сигнал(ов) из буфера (курсор=%d)",
                     n, self._cursor)
        return n

    def _poll_once(self, now: Optional[float] = None) -> int:
        now = time.time() if now is None else now
        if not self._cursor_inited:
            self._init_cursor()
        records = self._pull(self._cursor)
        n = self._ingest(records)
        self._last_ok_poll_ts = now   # успех даже при пустом ответе = канал жив
        self._last_error = ""
        return n

    # ── Основной цикл ─────────────────────────────────────────────────────────
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as e:  # сеть/облако недоступны — не падаем, повторим
                self._last_error = str(e)
                log.warning("webhook: не удалось опросить буфер (%s). Повтор через "
                            "%d с…", e, self.poll_interval)
            self._stop.wait(self.poll_interval)

    def start(self) -> None:
        self._started_at = time.time()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ── Мониторинг живости ────────────────────────────────────────────────────
    _REMEDY = ("Проверь, что облачный приёмник (Cloudflare Worker) доступен и есть "
               "интернет. URL/токен — WEBHOOK_PULL_URL/WEBHOOK_TOKEN в .env. "
               "Быстрая проверка: python run.py webhook-selftest.")

    def health(self, now: float, stale_sec: float):
        if not self._last_ok_poll_ts:
            # Ещё ни одного успешного опроса — даём фору на старте.
            if self._started_at and (now - self._started_at) < self._connect_grace:
                return (True, "webhook-буфер опрашивается…", "")
            why = f" ({self._last_error})" if self._last_error else ""
            return (False,
                    f"резервный webhook-буфер недоступен — нет успешного опроса{why}",
                    self._REMEDY)
        age = now - self._last_ok_poll_ts
        if age > stale_sec:
            why = f" ({self._last_error})" if self._last_error else ""
            return (False,
                    f"резервный webhook-буфер не опрашивается {int(age // 60)} мин{why}",
                    self._REMEDY)
        return (True, f"webhook-буфер жив, опрошен {int(age)} с назад", "")
