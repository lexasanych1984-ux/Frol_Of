"""Оркестрация новостного модуля: что и когда делать.

Вызывается из телеграм-бота (фоновый цикл + команды /week и /news) и из CLI.
Вся логика синхронная — бот заворачивает вызовы в asyncio.to_thread.

Расписание и "догон" (ПК трейдера не работает 24/7):
- дайджест на предстоящую неделю возможен только с воскресенья (фид FF
  переключается на новую неделю в воскресенье); если бот был выключен,
  дайджест досылается при первом запуске, но не позже digest_cutoff_days
  от начала недели — дальше он уже не "на предстоящую неделю";
- вышедшие новости интерпретируются не раньше release_delay_minutes после
  выхода (факту нужно время попасть в источники) и не позже catchup_hours —
  более старые молча помечаются пропущенными.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from src.storage.db import connect

from . import calendar, store
from .analyst import NewsAnalyst
from .models import NewsEvent

CONFIG_PATH = "config/config.yaml"


@dataclass
class NewsConfig:
    currencies: list[str] = field(default_factory=lambda: ["USD", "EUR", "GBP", "CAD", "JPY"])
    impacts: list[str] = field(default_factory=lambda: ["High"])
    instruments: list[str] = field(default_factory=lambda: ["EURUSD", "GBPUSD", "USDCAD", "USDJPY", "GER40"])
    utc_offset_hours: int = 3  # platform time FundingPips (UTC+3), см. config.yaml
    poll_minutes: int = 15
    release_delay_minutes: int = 10
    catchup_hours: int = 24
    digest_cutoff_days: int = 3  # воскресенье + пн/вт — дальше дайджест не досылаем
    max_push_per_cycle: int = 3


def load_news_config(path: str | Path = CONFIG_PATH) -> NewsConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    news = raw.get("news") or {}
    cfg = NewsConfig()
    for key in vars(cfg):
        if key in news and news[key] is not None:
            setattr(cfg, key, news[key])
    return cfg


def open_db() -> sqlite3.Connection:
    conn = connect()
    store.ensure_schema(conn)
    return conn


def current_week_key(now_utc: datetime) -> str:
    d = now_utc.date()
    sunday = d - timedelta(days=(d.weekday() + 1) % 7)
    return sunday.isoformat()


def refresh_calendar(conn: sqlite3.Connection, cfg: NewsConfig) -> list[NewsEvent]:
    """Скачать фид, отфильтровать по валютам/важности, сохранить. Возвращает
    отфильтрованные события текущей недели FF."""
    events = calendar.filter_events(calendar.parse_events(calendar.fetch_feed()), cfg.currencies, cfg.impacts)
    store.upsert_events(conn, events)
    return events


def digest_due(conn: sqlite3.Connection, cfg: NewsConfig, now_utc: datetime) -> str | None:
    """Ключ недели, для которой пора отправить дайджест, либо None."""
    week = current_week_key(now_utc)
    if store.digest_sent_at(conn, week) is not None:
        return None
    week_start = datetime.fromisoformat(week).replace(tzinfo=now_utc.tzinfo)
    if now_utc >= week_start + timedelta(days=cfg.digest_cutoff_days):
        return None  # неделя уже в разгаре — обзор "на предстоящую неделю" потерял смысл
    return week


def build_digest(
    conn: sqlite3.Connection,
    cfg: NewsConfig,
    analyst: NewsAnalyst,
    week_key: str,
    now_utc: datetime,
    mark_sent: bool = True,
) -> str:
    events = store.get_week_events(conn, week_key)
    if not events:
        return (
            "На эту неделю в календаре нет красных новостей по отслеживаемым валютам "
            f"({', '.join(cfg.currencies)})."
        )
    text = analyst.weekly_digest(events, cfg.instruments, cfg.utc_offset_hours)
    if mark_sent:
        store.mark_digest_sent(conn, week_key, now_utc)
    return text


def process_due_events(
    conn: sqlite3.Connection,
    cfg: NewsConfig,
    analyst: NewsAnalyst,
    now_utc: datetime,
    limit: int | None = None,
) -> list[tuple[NewsEvent, str]]:
    """Интерпретировать вышедшие новости; устаревшие пометить пропущенными.

    Возвращает [(событие, текст разбора)] для отправки в Telegram. limit
    ограничивает число LLM-вызовов за один проход (остальные догонятся на
    следующем тике цикла).
    """
    to_interpret, to_skip = store.due_events(
        conn, now_utc, cfg.release_delay_minutes, cfg.catchup_hours
    )
    for event in to_skip:
        store.mark_interpreted(conn, event, store.SKIPPED_OFFLINE, now_utc)

    results: list[tuple[NewsEvent, str]] = []
    for event in to_interpret[: limit if limit is not None else cfg.max_push_per_cycle]:
        text = analyst.interpret_event(event, cfg.instruments, cfg.utc_offset_hours)
        store.mark_interpreted(conn, event, text, now_utc)
        results.append((event, text))
    return results


def upcoming_today(conn: sqlite3.Connection, cfg: NewsConfig, now_utc: datetime) -> list[NewsEvent]:
    """Ближайшие события до конца суток (в локальном времени) — для /news,
    когда разбирать пока нечего."""
    week = current_week_key(now_utc)
    local_now = now_utc + timedelta(hours=cfg.utc_offset_hours)
    end_of_day_utc = (
        local_now.replace(hour=23, minute=59, second=59) - timedelta(hours=cfg.utc_offset_hours)
    )
    return [
        e for e in store.get_week_events(conn, week)
        if now_utc <= e.event_time_utc <= end_of_day_utc
    ]
