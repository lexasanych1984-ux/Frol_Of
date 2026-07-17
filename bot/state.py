"""Персистентное состояние: дедуп сигналов и отметка equity на начало дня.

Хранится в sqlite (stdlib), переживает перезапуск бота.
"""
from __future__ import annotations

import sqlite3
import time
from datetime import date
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent


class State:
    def __init__(self, path: Optional[str] = None):
        self.path = str(path or (ROOT / "state.db"))
        self.db = sqlite3.connect(self.path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS seen (key TEXT PRIMARY KEY, ts REAL)")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS daily (day TEXT PRIMARY KEY, start_equity REAL)")
        self.db.commit()

    # ── Дедуп сигналов ────────────────────────────────────────────────────────
    def is_duplicate(self, key: str, window_sec: float) -> bool:
        row = self.db.execute("SELECT ts FROM seen WHERE key=?", (key,)).fetchone()
        if row is None:
            return False
        return (time.time() - row[0]) < window_sec

    def mark_seen(self, key: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO seen(key, ts) VALUES(?, ?)",
                        (key, time.time()))
        self.db.commit()

    def prune(self, older_than_sec: float = 86400) -> None:
        self.db.execute("DELETE FROM seen WHERE ts < ?",
                        (time.time() - older_than_sec,))
        self.db.commit()

    # ── Equity на начало дня (для дневного kill-switch) ───────────────────────
    def start_of_day_equity(self, current_equity: float) -> float:
        today = date.today().isoformat()
        row = self.db.execute(
            "SELECT start_equity FROM daily WHERE day=?", (today,)).fetchone()
        if row is not None:
            return row[0]
        self.db.execute("INSERT INTO daily(day, start_equity) VALUES(?, ?)",
                        (today, current_equity))
        self.db.commit()
        return current_equity

    def close(self) -> None:
        self.db.close()
