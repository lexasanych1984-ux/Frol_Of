"""Тесты новостного модуля: парсинг/фильтрация фида, хранилище, расписание
дайджеста и логика догона. LLM-вызовы не тестируются (мокается аналитик там,
где он нужен), сеть не используется.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.news import store
from src.news.calendar import filter_events, parse_events
from src.news.models import NewsEvent
from src.news.prompts import build_digest_prompt, build_interpretation_prompt, format_week_schedule
from src.news.service import NewsConfig, current_week_key, digest_due, process_due_events, upcoming_today

# --- фид ---------------------------------------------------------------------

RAW_FEED = [
    {
        "title": "Non-Farm Employment Change",
        "country": "USD",
        "date": "2026-07-10T08:30:00-04:00",
        "impact": "High",
        "forecast": "110K",
        "previous": "139K",
    },
    {
        "title": "BOE Gov Bailey Speaks",
        "country": "GBP",
        "date": "2026-07-07T10:00:00-04:00",
        "impact": "High",
        "forecast": "",
        "previous": "",
    },
    {
        "title": "ANZ Commodity Prices m/m",
        "country": "NZD",
        "date": "2026-07-05T21:00:00-04:00",
        "impact": "Low",
        "forecast": "",
        "previous": "0.7%",
    },
    {
        "title": "German Final CPI m/m",
        "country": "EUR",
        "date": "2026-07-08T02:00:00-04:00",
        "impact": "Medium",
        "forecast": "0.2%",
        "previous": "0.2%",
    },
]

CFG = NewsConfig()


def test_parse_events_converts_to_utc():
    events = parse_events(RAW_FEED)
    nfp = next(e for e in events if "Non-Farm" in e.title)
    assert nfp.event_time_utc == datetime(2026, 7, 10, 12, 30, tzinfo=timezone.utc)


def test_filter_keeps_only_tracked_currencies_and_impacts():
    events = filter_events(parse_events(RAW_FEED), CFG.currencies, CFG.impacts)
    titles = [e.title for e in events]
    assert "Non-Farm Employment Change" in titles
    assert "BOE Gov Bailey Speaks" in titles
    assert "ANZ Commodity Prices m/m" not in titles  # NZD не отслеживается
    assert "German Final CPI m/m" not in titles      # Medium, а не High
    # отсортировано по времени
    assert titles[0] == "BOE Gov Bailey Speaks"


def test_week_key_is_sunday_of_week():
    events = parse_events(RAW_FEED)
    nfp = next(e for e in events if "Non-Farm" in e.title)
    # 10.07.2026 — пятница; неделя FF началась в воскресенье 05.07
    assert nfp.week_key == "2026-07-05"


# --- хранилище ---------------------------------------------------------------


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    store.ensure_schema(c)
    return c


def _tracked_events():
    return filter_events(parse_events(RAW_FEED), CFG.currencies, CFG.impacts)


def test_upsert_idempotent_and_updates_forecast(conn):
    events = _tracked_events()
    store.upsert_events(conn, events)
    store.upsert_events(conn, events)
    assert conn.execute("SELECT COUNT(*) FROM news_events").fetchone()[0] == len(events)

    updated = NewsEvent(
        title=events[0].title,
        country=events[0].country,
        event_time_utc=events[0].event_time_utc,
        impact=events[0].impact,
        forecast="123K",
        previous=events[0].previous,
    )
    store.upsert_events(conn, [updated])
    row = conn.execute(
        "SELECT forecast FROM news_events WHERE title = ?", (events[0].title,)
    ).fetchone()
    assert row[0] == "123K"


def test_due_events_split_fresh_vs_stale_and_respects_delay(conn):
    store.upsert_events(conn, _tracked_events())
    nfp_time = datetime(2026, 7, 10, 12, 30, tzinfo=timezone.utc)

    # за 5 минут до release_delay после NFP: NFP ещё не готов, BOE (3 дня назад) устарел
    now = nfp_time + timedelta(minutes=5)
    fresh, stale = store.due_events(conn, now, release_delay_minutes=10, catchup_hours=24)
    assert [e.title for e in fresh] == []
    assert [e.title for e in stale] == ["BOE Gov Bailey Speaks"]

    # через 15 минут после NFP — готов к разбору
    now = nfp_time + timedelta(minutes=15)
    fresh, _ = store.due_events(conn, now, release_delay_minutes=10, catchup_hours=24)
    assert [e.title for e in fresh] == ["Non-Farm Employment Change"]


def test_mark_interpreted_removes_from_due(conn):
    store.upsert_events(conn, _tracked_events())
    now = datetime(2026, 7, 10, 13, 0, tzinfo=timezone.utc)
    fresh, _ = store.due_events(conn, now, 10, 24)
    for e in fresh:
        store.mark_interpreted(conn, e, "разбор", now)
    fresh_again, stale_again = store.due_events(conn, now, 10, 24)
    assert fresh_again == []
    # stale уже помечать — забота process_due_events; здесь просто не падаем
    for e in stale_again:
        store.mark_interpreted(conn, e, store.SKIPPED_OFFLINE, now)
    assert store.due_events(conn, now, 10, 24) == ([], [])


# --- расписание дайджеста ----------------------------------------------------


def test_digest_due_only_until_cutoff(conn):
    sunday = datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)
    assert current_week_key(sunday) == "2026-07-05"
    # воскресенье: пора
    assert digest_due(conn, CFG, sunday) == "2026-07-05"
    # вторник: ещё догоняем
    assert digest_due(conn, CFG, sunday + timedelta(days=2)) == "2026-07-05"
    # среда (cutoff=3 дня): уже поздно
    assert digest_due(conn, CFG, sunday + timedelta(days=3)) is None
    # после отправки — не дублируем
    store.mark_digest_sent(conn, "2026-07-05", sunday)
    assert digest_due(conn, CFG, sunday) is None


# --- оркестрация с моком аналитика --------------------------------------------


class FakeAnalyst:
    def __init__(self):
        self.interpreted: list[str] = []

    def weekly_digest(self, events, instruments, utc_offset_hours):
        return f"дайджест по {len(events)} событиям"

    def interpret_event(self, event, instruments, utc_offset_hours):
        self.interpreted.append(event.title)
        return f"разбор: {event.title}"


def test_process_due_events_interprets_fresh_and_skips_stale(conn):
    store.upsert_events(conn, _tracked_events())
    analyst = FakeAnalyst()
    now = datetime(2026, 7, 10, 13, 0, tzinfo=timezone.utc)  # NFP вышел 30 мин назад
    results = process_due_events(conn, CFG, analyst, now)
    assert [e.title for e, _ in results] == ["Non-Farm Employment Change"]
    assert analyst.interpreted == ["Non-Farm Employment Change"]
    # устаревший BOE помечен пропущенным без LLM
    row = conn.execute(
        "SELECT interpretation FROM news_events WHERE title = 'BOE Gov Bailey Speaks'"
    ).fetchone()
    assert row[0] == store.SKIPPED_OFFLINE
    # повторный вызов ничего не дублирует
    assert process_due_events(conn, CFG, analyst, now) == []


def test_upcoming_today_lists_future_events_of_local_day(conn):
    store.upsert_events(conn, _tracked_events())
    # утро пятницы 10.07 UTC — NFP (12:30 UTC) ещё впереди
    now = datetime(2026, 7, 10, 6, 0, tzinfo=timezone.utc)
    titles = [e.title for e in upcoming_today(conn, CFG, now)]
    assert titles == ["Non-Farm Employment Change"]


# --- промпты -----------------------------------------------------------------


def test_week_schedule_groups_by_local_day_with_offset():
    events = _tracked_events()
    schedule = format_week_schedule(events, utc_offset_hours=3)
    # NFP: 12:30 UTC = 15:30 UTC+3, пятница 10.07
    assert "Пятница 10.07" in schedule
    assert "15:30 USD — Non-Farm Employment Change" in schedule
    assert "прогноз 110K" in schedule


def test_prompts_carry_honesty_rules_and_event_data():
    events = _tracked_events()
    system, user = build_digest_prompt(events, CFG.instruments, 3)
    assert "НЕ торговые сигналы" in system
    assert "Non-Farm Employment Change" in user

    nfp = next(e for e in events if "Non-Farm" in e.title)
    system_i, user_i = build_interpretation_prompt(nfp, CFG.instruments, 3)
    assert "НЕ торговые сигналы" in system_i
    assert "Прогноз: 110K" in user_i
    assert "actual" in user_i
