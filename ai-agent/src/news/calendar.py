"""Загрузка недельного календаря ForexFactory.

Официальный JSON-фид (nfs.faireconomy.media) отдаёт события ТЕКУЩЕЙ недели FF
(неделя = воскресенье-суббота, переключается на новую в воскресенье):
title / country / date (с таймзоной) / impact / forecast / previous.
Поля actual в фиде НЕТ — факт выхода добирается через web search в момент
интерпретации (src/news/analyst.py). Фида следующей недели не существует
(проверено 2026-07-08: ff_calendar_nextweek.* -> 404), поэтому дайджест
на предстоящую неделю возможен не раньше воскресенья.
"""
from __future__ import annotations

from datetime import datetime, timezone

import requests

from .models import NewsEvent

FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


def fetch_feed(url: str = FEED_URL, timeout: int = 30) -> list[dict]:
    resp = requests.get(url, timeout=timeout, headers={"User-Agent": "trading-discipline-ai/1.0"})
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise ValueError(f"Неожиданный формат фида: {type(data)}")
    return data


def parse_events(raw: list[dict]) -> list[NewsEvent]:
    events = []
    for item in raw:
        # дата вида "2026-07-05T21:00:00-04:00" — fromisoformat разбирает смещение
        dt = datetime.fromisoformat(item["date"]).astimezone(timezone.utc)
        events.append(
            NewsEvent(
                title=item["title"].strip(),
                country=item["country"].strip().upper(),
                event_time_utc=dt,
                impact=item.get("impact", "").strip(),
                forecast=(item.get("forecast") or "").strip(),
                previous=(item.get("previous") or "").strip(),
            )
        )
    return events


def filter_events(
    events: list[NewsEvent],
    currencies: list[str],
    impacts: list[str],
) -> list[NewsEvent]:
    cur = {c.upper() for c in currencies}
    imp = {i.lower() for i in impacts}
    return sorted(
        (e for e in events if e.country in cur and e.impact.lower() in imp),
        key=lambda e: e.event_time_utc,
    )
