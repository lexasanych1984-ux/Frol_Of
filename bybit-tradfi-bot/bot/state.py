"""Персистентное состояние: дедуп сигналов и отметка equity на начало дня.

Хранится в sqlite (stdlib), переживает перезапуск бота.

Потокобезопасность: к одному State обращаются два потока — главный цикл (дедуп,
equity) и поток webhook-опроса (курсор буфера через get_meta/set_meta). Поэтому
соединение открыто с ``check_same_thread=False``, а каждый доступ к БД сериализуется
общим ``threading.Lock`` (операции короткие и редкие — контеншн ничтожен).
"""
from __future__ import annotations

import sqlite3
import threading
import time
from datetime import date
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent


class State:
    def __init__(self, path: Optional[str] = None):
        self.path = str(path or (ROOT / "state.db"))
        self._lock = threading.Lock()
        # check_same_thread=False: соединением пользуются главный поток и поток
        # webhook-опроса. Гонки исключает self._lock вокруг каждой операции.
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        with self._lock:
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS seen (key TEXT PRIMARY KEY, ts REAL)")
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS daily (day TEXT PRIMARY KEY, start_equity REAL)")
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
            self.db.commit()

    # ── Дедуп сигналов ────────────────────────────────────────────────────────
    def is_duplicate(self, key: str, window_sec: float) -> bool:
        with self._lock:
            row = self.db.execute("SELECT ts FROM seen WHERE key=?", (key,)).fetchone()
        if row is None:
            return False
        return (time.time() - row[0]) < window_sec

    def mark_seen(self, key: str) -> None:
        with self._lock:
            self.db.execute("INSERT OR REPLACE INTO seen(key, ts) VALUES(?, ?)",
                            (key, time.time()))
            self.db.commit()

    def prune(self, older_than_sec: float = 86400) -> None:
        with self._lock:
            self.db.execute("DELETE FROM seen WHERE ts < ?",
                            (time.time() - older_than_sec,))
            self.db.commit()

    # ── Произвольные метки (курсор webhook-буфера и т.п.) ─────────────────────
    def get_meta(self, key: str) -> Optional[str]:
        with self._lock:
            row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row is not None else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self.db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
                            (key, str(value)))
            self.db.commit()

    # ── Equity на начало дня (для дневного kill-switch) ───────────────────────
    def start_of_day_equity(self, current_equity: float) -> float:
        today = date.today().isoformat()
        with self._lock:
            row = self.db.execute(
                "SELECT start_equity FROM daily WHERE day=?", (today,)).fetchone()
            if row is not None:
                return row[0]
            self.db.execute("INSERT INTO daily(day, start_equity) VALUES(?, ?)",
                            (today, current_equity))
            self.db.commit()
        return current_equity

    def close(self) -> None:
        with self._lock:
            self.db.close()
