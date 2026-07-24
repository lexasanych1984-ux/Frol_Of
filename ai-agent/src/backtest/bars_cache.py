"""Локальный кэш OHLC-баров (SQLite) — тот же паттерн, что src/storage/db.py
для сделок, но отдельный файл (`data/bars.db`), потому что бары и сделки —
независимые по объёму и жизненному циклу данные (баров на порядки больше,
кэш пополняется по диапазонам дат, а не по одной записи за раз)."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

DEFAULT_DB_PATH = "data/bars.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
    symbol       TEXT NOT NULL,
    timeframe    TEXT NOT NULL,
    time         TEXT NOT NULL,
    open         REAL NOT NULL,
    high         REAL NOT NULL,
    low          REAL NOT NULL,
    close        REAL NOT NULL,
    tick_volume  INTEGER NOT NULL,
    PRIMARY KEY (symbol, timeframe, time)
);
"""


def connect(path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(SCHEMA)
    conn.commit()
    return conn


def upsert_bars(conn: sqlite3.Connection, symbol: str, timeframe: str, df: pd.DataFrame) -> int:
    """df — колонки time (datetime), open, high, low, close, tick_volume."""
    if df.empty:
        return 0
    rows = [
        (symbol, timeframe, row.time.isoformat(), row.open, row.high, row.low, row.close, int(row.tick_volume))
        for row in df.itertuples(index=False)
    ]
    conn.executemany(
        """
        INSERT INTO bars (symbol, timeframe, time, open, high, low, close, tick_volume)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(symbol, timeframe, time) DO UPDATE SET
            open=excluded.open, high=excluded.high, low=excluded.low,
            close=excluded.close, tick_volume=excluded.tick_volume
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def get_bars(
    conn: sqlite3.Connection,
    symbol: str,
    timeframe: str,
    date_from: datetime,
    date_to: datetime,
) -> pd.DataFrame:
    rows = conn.execute(
        """
        SELECT time, open, high, low, close, tick_volume FROM bars
        WHERE symbol = ? AND timeframe = ? AND time >= ? AND time <= ?
        ORDER BY time ASC
        """,
        (symbol, timeframe, date_from.isoformat(), date_to.isoformat()),
    ).fetchall()
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "tick_volume"])
    if not df.empty:
        df["time"] = pd.to_datetime(df["time"])
    return df


def covered_range(conn: sqlite3.Connection, symbol: str, timeframe: str) -> tuple[datetime, datetime] | None:
    """Мин/макс время в кэше для (symbol, timeframe) — None, если кэш пуст.

    Не гарантирует отсутствие "дыр" внутри диапазона (докачка всегда идёт по
    границам min/max, а не по проверке каждого бара) — для этого проекта
    (бэктест на исторических данных, не live-стриминг) это приемлемое
    упрощение: диапазоны обычно докачиваются целиком за один вызов."""
    row = conn.execute(
        "SELECT MIN(time), MAX(time) FROM bars WHERE symbol = ? AND timeframe = ?",
        (symbol, timeframe),
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return datetime.fromisoformat(row[0]), datetime.fromisoformat(row[1])
