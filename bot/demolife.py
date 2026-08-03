"""Срок жизни демо-счёта: напоминание о переезде, пока счёт ещё жив.

Демо Just2Trade живёт две недели и умирает вместе с историей сделок: с
истёкшего счёта её уже не выгрузить, а комиссия и своп в журналах бота
раздельно не хранятся (см. docs/just2trade-actual-costs.md — июльскую
раскладку издержек пришлось восстанавливать вычитанием). Поэтому напоминание
приходит заранее, а не по факту отвала авторизации.

Чистая функция без зависимостей — её зовёт `run.py stats` (ежедневная задача
«bybit-tradfi-bot daily journal»), она же легко тестируется.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

# уровни по возрастанию срочности
OK, WARN, URGENT, EXPIRED = "ok", "warn", "urgent", "expired"


@dataclass
class DemoStatus:
    day: int            # какой день жизни счёта идёт (день открытия = 1)
    days_left: int      # сколько дней осталось (0 = истекает сегодня)
    level: str          # ok | warn | urgent | expired
    text: str           # готовая строка для лога и Telegram

    @property
    def should_notify(self) -> bool:
        return self.level != OK


def status(opened: date, today: Optional[date] = None, *, lifetime_days: int = 14,
           warn_from_day: int = 10, urgent_from_day: int = 12) -> DemoStatus:
    """Где мы на шкале жизни демо-счёта."""
    today = today or date.today()
    day = (today - opened).days + 1          # день открытия — первый
    days_left = lifetime_days - (today - opened).days

    if days_left <= 0:
        return DemoStatus(day, days_left, EXPIRED,
                          f"❌ ДЕМО-СЧЁТ ИСТЁК (день {day}, срок {lifetime_days} дн.). "
                          f"История уже недоступна. Переезд — чек-лист в README.")
    if day >= urgent_from_day:
        return DemoStatus(day, days_left, URGENT,
                          f"❗ ДЕМО УМРЁТ ЧЕРЕЗ {days_left} ДН. (день {day} из "
                          f"{lifetime_days}). СНЯТЬ ИСТОРИЮ И ПЕРЕЕХАТЬ — "
                          f"чек-лист «Переезд на новый демо-счёт» в README.")
    if day >= warn_from_day:
        return DemoStatus(day, days_left, WARN,
                          f"⚠️ Демо-счёт живёт {day}-й день из {lifetime_days}, "
                          f"осталось {days_left} дн. Пора планировать переезд "
                          f"(чек-лист в README).")
    return DemoStatus(day, days_left, OK,
                      f"Демо-счёт: день {day} из {lifetime_days}, "
                      f"осталось {days_left} дн.")
