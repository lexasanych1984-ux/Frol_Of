"""Живое уведомление о ЗАКРЫТИИ ботовой позиции (SL/TP/ручное/по сигналу) с P&L.

Позиции закрываются в основном по SL/TP на стороне брокера, а НЕ через движок,
поэтому «пуш по закрытию» строится не на обработке exit-алерта, а на слежении за
историей сделок MT5 (как edge-монитор). Опрос идёт в главном потоке бота через
maybe_tick(), поэтому обращения к MT5 не пересекаются с исполнением ордеров.

Курсор (время последнего учтённого закрытия) персистится в State в ISO:
  • первый старт не переигрывает историю (только новые закрытия после старта);
  • закрытия за время простоя добираются после рестарта (их close_time > курсора).
Сравниваем сами datetime (без обратной конвертации в epoch — на Windows
datetime.timestamp() у самой эпохи бросает OSError).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Callable, List, Optional

from . import stats

log = logging.getLogger("bot")

_CURSOR_KEY = "close_cursor"


class PositionCloseWatcher:
    def __init__(self, notifier, broker, state, symbol_alias: Optional[dict] = None,
                 interval_sec: int = 30, history_days: int = 3,
                 clock: Callable[[], float] = time.time):
        self.notifier = notifier
        self.broker = broker
        self.state = state
        self.symbol_alias = symbol_alias or {}
        self.interval = max(5, int(interval_sec))
        self.history_days = max(1, int(history_days))
        self.clock = clock
        self._last_tick = 0.0
        self._cursor: Optional[datetime] = None   # время последнего учтённого закрытия
        self._cursor_inited = False

    def maybe_tick(self, now: Optional[float] = None) -> None:
        now = self.clock() if now is None else now
        if now - self._last_tick >= self.interval:
            self.tick(now)

    def tick(self, now: Optional[float] = None) -> None:
        now = self.clock() if now is None else now
        self._last_tick = now
        try:
            trades = stats.collect_closed(self.broker.mt5, days=self.history_days,
                                          symbol_alias=self.symbol_alias)
            # только сделки бота; ручные/чужие (magic вне карты стратегий) не трогаем
            bot_trades = [t for t in trades if t.strategy != "ручные/чужие"]

            if not self._cursor_inited:
                self._cursor_inited = True
                self._cursor = self._load_cursor()
                if self._cursor is None:
                    # первый старт вообще — не переигрываем существующую историю
                    if bot_trades:
                        self._cursor = max(t.close_time for t in bot_trades)
                        self._save_cursor(self._cursor)
                    return
                # иначе курсор загружен с прошлого запуска — добираем простой ниже

            cur = self._cursor
            new: List = [t for t in bot_trades
                         if cur is None or t.close_time > cur]
            for t in sorted(new, key=lambda x: x.close_time):
                self._notify_close(t)

            if bot_trades:
                mx = max(t.close_time for t in bot_trades)
                if cur is None or mx > cur:
                    self._cursor = mx
                    self._save_cursor(mx)
        except Exception as e:  # мониторинг НИКОГДА не роняет бота
            log.exception("Сбой в close-tick: %s", e)

    def _notify_close(self, t) -> None:
        sign = "✅" if t.profit > 0 else ("➖" if abs(t.profit) < 1e-9 else "❌")
        msg = (f"{sign} ЗАКРЫТИЕ · {t.strategy} · {t.symbol} {t.side.upper()}\n"
               f"P&L {t.profit:+.2f} USD · {t.volume} лот · "
               f"вход {t.price_open} → выход {t.price_close}")
        log.info("close → Telegram: %s", " | ".join(
            p.strip() for p in msg.splitlines()))
        if self.notifier is not None:
            try:
                self.notifier.send(msg)
            except Exception as e:
                log.warning("Не удалось отправить уведомление о закрытии: %s", e)

    def _load_cursor(self) -> Optional[datetime]:
        raw = self.state.get_meta(_CURSOR_KEY) if self.state else None
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            return None

    def _save_cursor(self, value: datetime) -> None:
        if self.state:
            self.state.set_meta(_CURSOR_KEY, value.isoformat())
