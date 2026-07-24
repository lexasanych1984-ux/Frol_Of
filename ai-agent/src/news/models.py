"""Модель события экономического календаря (ForexFactory weekly feed)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class NewsEvent:
    title: str
    country: str          # код валюты в терминах FF: USD, EUR, GBP, CAD, JPY...
    event_time_utc: datetime  # aware, UTC
    impact: str           # High | Medium | Low | Holiday
    forecast: str
    previous: str

    @property
    def week_key(self) -> str:
        """Ключ недели FF (неделя начинается с воскресенья) по дате UTC.

        Событие "воскресенье 21:00 ET" в UTC уже понедельник — попадает в ту же
        неделю, что и остальные события недели, так что UTC-даты достаточно.
        """
        d = self.event_time_utc.date()
        sunday = d - timedelta(days=(d.weekday() + 1) % 7)
        return sunday.isoformat()

    def local_time(self, utc_offset_hours: int) -> datetime:
        return self.event_time_utc.astimezone(timezone(timedelta(hours=utc_offset_hours)))
