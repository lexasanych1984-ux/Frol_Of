"""Промпты для новостного модуля.

Тот же принцип честности, что и в ai_review/prompts.py: никакого "предсказания
рынка" как торгового сигнала. Дайджест и разборы — макро-контекст и сценарии
("если факт выше прогноза, обычно означает..."), явно помеченные как НЕ сигнал
на вход. Плюс практическая привязка к правилам FundingPips: красные новости —
запрет на торговлю в окне вокруг выхода.
"""
from __future__ import annotations

from collections import defaultdict

from .models import NewsEvent

# Дни недели по-русски для группировки в дайджесте
_WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]

_HONESTY = (
    "Правила честности (обязательны):\n"
    "- Ты даёшь макро-контекст и сценарии, а НЕ торговые сигналы. Никаких 'покупай'/'продавай'.\n"
    "- Сценарии формулируй условно: 'если факт заметно выше прогноза, для валюты X это обычно...'.\n"
    "- Явно разделяй факты (числа, источники) и твою интерпретацию.\n"
    "- Если данных не хватает или источники противоречат друг другу — скажи об этом прямо, не выдумывай.\n"
    "- Помни: реакция рынка на новость часто не совпадает с 'учебной' — упомяни это там, где уместно.\n"
)


def _format_event_line(e: NewsEvent, utc_offset_hours: int) -> str:
    t = e.local_time(utc_offset_hours).strftime("%H:%M")
    parts = [f"{t} {e.country} — {e.title}"]
    if e.forecast:
        parts.append(f"прогноз {e.forecast}")
    if e.previous:
        parts.append(f"пред. {e.previous}")
    return "; ".join(parts)


def format_week_schedule(events: list[NewsEvent], utc_offset_hours: int) -> str:
    """Расписание недели, сгруппированное по дням, время в UTC+offset."""
    by_day: dict[str, list[str]] = defaultdict(list)
    for e in events:
        local = e.local_time(utc_offset_hours)
        day = f"{_WEEKDAYS[local.weekday()].capitalize()} {local.strftime('%d.%m')}"
        by_day[day].append(_format_event_line(e, utc_offset_hours))
    blocks = []
    for day, lines in by_day.items():
        blocks.append(day + "\n" + "\n".join(f"  {line}" for line in lines))
    return "\n\n".join(blocks)


def build_digest_prompt(
    events: list[NewsEvent],
    instruments: list[str],
    utc_offset_hours: int,
) -> tuple[str, str]:
    system = (
        "Ты — макро-аналитик, помогающий дисциплинированному трейдеру проп-счетов FundingPips "
        "подготовиться к торговой неделе. Он торгует редко (1-2 сделки в неделю), инструменты: "
        f"{', '.join(instruments)}. Отвечай по-русски, кратко и структурно — ответ уходит в Telegram.\n\n"
        + _HONESTY
        + "\nПравило FundingPips: в окне вокруг выхода красных (high-impact) новостей торговля запрещена — "
        "твой обзор в первую очередь помогает спланировать, КОГДА на неделе торговать нельзя."
    )
    schedule = format_week_schedule(events, utc_offset_hours)
    user = (
        f"Календарь красных новостей на предстоящую неделю (время UTC+{utc_offset_hours}):\n\n"
        f"{schedule}\n\n"
        "Сделай обзор недели:\n"
        "1) Выжимка: 3-5 главных событий недели и почему именно они значимы сейчас "
        "(используй веб-поиск, чтобы уточнить актуальный макро-фон: ожидания по ставкам, последние данные).\n"
        "2) Макро-сценарии по валютам (USD, EUR, GBP, CAD, JPY): базовое ожидание и что обычно означает "
        "выход заметно выше/ниже прогноза для валюты и моих инструментов.\n"
        "3) Карта 'запретных окон': по дням — когда из-за красных новостей торговать нельзя.\n"
        "Уложись примерно в 3500 символов."
    )
    return system, user


def build_interpretation_prompt(
    event: NewsEvent,
    instruments: list[str],
    utc_offset_hours: int,
) -> tuple[str, str]:
    system = (
        "Ты — макро-аналитик, разбирающий только что вышедшую экономическую новость для трейдера "
        f"проп-счетов FundingPips (инструменты: {', '.join(instruments)}). "
        "Отвечай по-русски, компактно (до ~1500 символов) — ответ уходит в Telegram.\n\n" + _HONESTY
    )
    t_local = event.local_time(utc_offset_hours).strftime("%d.%m %H:%M")
    context = [f"Событие: {event.country} — {event.title}", f"Время выхода: {t_local} (UTC+{utc_offset_hours})"]
    if event.forecast:
        context.append(f"Прогноз: {event.forecast}")
    if event.previous:
        context.append(f"Предыдущее значение: {event.previous}")
    user = (
        "\n".join(context)
        + "\n\nНайди через веб-поиск фактическое вышедшее значение (actual) и разбери:\n"
        "1) Факт: число + источник. Если точный факт найти не удалось — так и напиши, не угадывай.\n"
        "2) Отклонение от прогноза и от предыдущего значения.\n"
        "3) Интерпретация: что такой результат обычно означает для валюты и для моих инструментов, "
        "и как это соотносится с недельным макро-фоном.\n"
        "4) Если рынок уже успел отреагировать и реакция видна в источниках — опиши её; если нет — не выдумывай."
    )
    return system, user
