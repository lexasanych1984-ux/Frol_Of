"""Локальный кэш сделок (SQLite) — чтобы не парсить HTML и не дёргать
Notion API каждый раз заново. Один файл `trades` на все счета,
дедупликация по (account_login, position_id).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from src.mt5_import.models import Trade

DEFAULT_DB_PATH = "data/trades.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    account_login TEXT NOT NULL,
    position_id   TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    direction     TEXT NOT NULL,
    volume        REAL NOT NULL,
    open_time     TEXT NOT NULL,
    open_price    REAL NOT NULL,
    close_time    TEXT,
    close_price   REAL,
    sl            REAL,
    tp            REAL,
    commission    REAL NOT NULL,
    swap          REAL NOT NULL,
    profit        REAL NOT NULL,
    source_file   TEXT NOT NULL,
    notion_page_id TEXT,
    PRIMARY KEY (account_login, position_id)
);
"""


def connect(path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(SCHEMA)
    conn.commit()
    return conn


def upsert_trades(conn: sqlite3.Connection, trades: Iterable[Trade]) -> int:
    rows = [
        (
            t.account_login,
            t.position_id,
            t.symbol,
            t.direction,
            t.volume,
            t.open_time.isoformat() if t.open_time else None,
            t.open_price,
            t.close_time.isoformat() if t.close_time else None,
            t.close_price,
            t.sl,
            t.tp,
            t.commission,
            t.swap,
            t.profit,
            t.source_file,
        )
        for t in trades
    ]
    conn.executemany(
        """
        INSERT INTO trades (account_login, position_id, symbol, direction, volume,
            open_time, open_price, close_time, close_price, sl, tp, commission, swap, profit, source_file)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(account_login, position_id) DO UPDATE SET
            close_time=excluded.close_time, close_price=excluded.close_price,
            commission=excluded.commission, swap=excluded.swap, profit=excluded.profit,
            source_file=excluded.source_file
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def get_trades(
    conn: sqlite3.Connection,
    account_login: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> list[Trade]:
    query = "SELECT account_login, position_id, symbol, direction, volume, open_time, open_price, close_time, close_price, sl, tp, commission, swap, profit, source_file FROM trades WHERE 1=1"
    params: list = []
    if account_login:
        query += " AND account_login = ?"
        params.append(account_login)
    if date_from:
        query += " AND open_time >= ?"
        params.append(date_from.isoformat())
    if date_to:
        query += " AND open_time <= ?"
        params.append(date_to.isoformat())

    rows = conn.execute(query, params).fetchall()
    trades = []
    for r in rows:
        trades.append(
            Trade(
                account_login=r[0],
                position_id=r[1],
                symbol=r[2],
                direction=r[3],
                volume=r[4],
                open_time=datetime.fromisoformat(r[5]) if r[5] else None,
                open_price=r[6],
                close_time=datetime.fromisoformat(r[7]) if r[7] else None,
                close_price=r[8],
                sl=r[9],
                tp=r[10],
                commission=r[11],
                swap=r[12],
                profit=r[13],
                source_file=r[14],
            )
        )
    return trades
