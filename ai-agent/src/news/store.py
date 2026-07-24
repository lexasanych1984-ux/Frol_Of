"""Хранение календаря новостей и статуса обработки в общей SQLite-базе.

Отдельные таблицы в data/trades.db: news_events (события + интерпретация)
и news_digests (какие недели уже получили воскресный обзор). Ключ события —
(title, country, event_time_utc): при переносе времени события FF появится
новая строка, старая может быть интерпретирована как "пропущенная" — редкий
случай, который догон помечает skipped без похода в LLM.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Iterable

from .models import NewsEvent

SCHEMA = """
CREATE TABLE IF NOT EXISTS news_events (
    title          TEXT NOT NULL,
    country        TEXT NOT NULL,
    event_time_utc TEXT NOT NULL,
    impact         TEXT NOT NULL,
    forecast       TEXT,
    previous       TEXT,
    week_key       TEXT NOT NULL,
    interpreted_at TEXT,
    interpretation TEXT,
    PRIMARY KEY (title, country, event_time_utc)
);
CREATE TABLE IF NOT EXISTS news_digests (
    week_key TEXT PRIMARY KEY,
    sent_at  TEXT NOT NULL
);
"""

SKIPPED_OFFLINE = "skipped-offline"  # событие вышло, пока бот был выключен дольше catchup-окна


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def upsert_events(conn: sqlite3.Connection, events: Iterable[NewsEvent]) -> int:
    rows = [
        (e.title, e.country, e.event_time_utc.isoformat(), e.impact, e.forecast, e.previous, e.week_key)
        for e in events
    ]
    conn.executemany(
        """
        INSERT INTO news_events (title, country, event_time_utc, impact, forecast, previous, week_key)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(title, country, event_time_utc) DO UPDATE SET
            impact=excluded.impact, forecast=excluded.forecast, previous=excluded.previous
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def _row_to_event(r: sqlite3.Row | tuple) -> NewsEvent:
    return NewsEvent(
        title=r[0],
        country=r[1],
        event_time_utc=datetime.fromisoformat(r[2]),
        impact=r[3],
        forecast=r[4] or "",
        previous=r[5] or "",
    )


_SELECT = "SELECT title, country, event_time_utc, impact, forecast, previous FROM news_events"


def get_week_events(conn: sqlite3.Connection, week_key: str) -> list[NewsEvent]:
    rows = conn.execute(
        f"{_SELECT} WHERE week_key = ? ORDER BY event_time_utc", (week_key,)
    ).fetchall()
    return [_row_to_event(r) for r in rows]


def due_events(
    conn: sqlite3.Connection,
    now_utc: datetime,
    release_delay_minutes: int,
    catchup_hours: int,
) -> tuple[list[NewsEvent], list[NewsEvent]]:
    """Неинтерпретированные события, чьё время уже прошло.

    Возвращает (to_interpret, to_skip): интерпретируем только то, что вышло
    не раньше catchup-окна (иначе бот, включённый после долгого простоя,
    зальёт чат разборами устаревших новостей) и не позже release-задержки
    (факту нужно время, чтобы появиться в источниках).
    """
    ready_before = (now_utc - timedelta(minutes=release_delay_minutes)).isoformat()
    rows = conn.execute(
        f"{_SELECT} WHERE interpreted_at IS NULL AND event_time_utc <= ? ORDER BY event_time_utc",
        (ready_before,),
    ).fetchall()
    stale_cutoff = now_utc - timedelta(hours=catchup_hours)
    to_interpret, to_skip = [], []
    for r in rows:
        event = _row_to_event(r)
        (to_interpret if event.event_time_utc >= stale_cutoff else to_skip).append(event)
    return to_interpret, to_skip


def mark_interpreted(
    conn: sqlite3.Connection, event: NewsEvent, interpretation: str, now_utc: datetime
) -> None:
    conn.execute(
        """
        UPDATE news_events SET interpreted_at = ?, interpretation = ?
        WHERE title = ? AND country = ? AND event_time_utc = ?
        """,
        (now_utc.isoformat(), interpretation, event.title, event.country, event.event_time_utc.isoformat()),
    )
    conn.commit()


def digest_sent_at(conn: sqlite3.Connection, week_key: str) -> str | None:
    row = conn.execute("SELECT sent_at FROM news_digests WHERE week_key = ?", (week_key,)).fetchone()
    return row[0] if row else None


def mark_digest_sent(conn: sqlite3.Connection, week_key: str, now_utc: datetime) -> None:
    conn.execute(
        "INSERT INTO news_digests (week_key, sent_at) VALUES (?, ?) "
        "ON CONFLICT(week_key) DO UPDATE SET sent_at=excluded.sent_at",
        (week_key, now_utc.isoformat()),
    )
    conn.commit()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
