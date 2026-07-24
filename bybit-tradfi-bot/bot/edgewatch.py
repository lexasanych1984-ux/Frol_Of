"""Живая тревога о деградации эджа: серия стопов превысила исторический максимум.

Второе из двух немедленных (не месячных) событий-тревог (первое — «риск сделки
выше заданного», живёт в движке). Раз в edge_check_interval_sec опрашиваем
историю MT5, считаем ТЕКУЩУЮ серию стопов по каждой стратегии и сравниваем с
`max_stop_streak` из expectations.yaml. Превышение → Telegram.

Опрос идёт в главном потоке бота (через maybe_tick, как у health-монитора),
поэтому обращения к MT5 не пересекаются с исполнением ордеров.

Анти-спам событийный, не временной: об одной и той же длине серии сообщаем один
раз; серия выросла (ещё стоп) → новое сообщение; серия оборвалась (win/BE) →
состояние сбрасывается, следующее превышение снова прозвучит.
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Dict, Optional

from . import execlog, report, stats

log = logging.getLogger("bot")


class EdgeMonitor:
    def __init__(self, notifier, broker, cfg, expectations,
                 symbol_alias: Optional[dict] = None,
                 clock: Callable[[], float] = time.time):
        self.notifier = notifier
        self.broker = broker
        self.cfg = cfg
        self.exp = expectations
        self.symbol_alias = symbol_alias or {}
        self.clock = clock
        self.interval = getattr(cfg.report, "edge_check_interval_sec", 900)
        self.history_days = getattr(cfg.report, "history_days", 400)
        self.exec_log = cfg.exec_log_path()
        self._last_tick = 0.0
        self._alerted: Dict[str, int] = {}   # label → длина серии, о которой уже сказали

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
            exec_index = execlog.index_by_position(self.exec_log)
            streaks = report.current_stop_streaks(trades, exec_index)
            for label, cur in streaks.items():
                self._evaluate(label, cur)
        except Exception as e:  # мониторинг НИКОГДА не роняет бота
            log.exception("Сбой в edge-tick: %s", e)

    def _evaluate(self, label: str, cur: int) -> None:
        exp = self.exp.for_label(label)
        cap = getattr(exp, "max_stop_streak", None) if exp else None
        if cap is None or cur <= cap:
            # серия в норме (или оборвалась) — сбросить анти-спам-память
            self._alerted.pop(label, None)
            return
        if self._alerted.get(label) == cur:
            return  # об этой длине уже сообщали
        self._alerted[label] = cur
        msg = (f"🔴 СЕРИЯ СТОПОВ ВЫШЕ НОРМЫ · {label}\n"
               f"Подряд стопов: {cur} (исторический максимум бэктеста {cap}).\n"
               f"Это сигнал возможной деградации эджа — проверь стратегию и рынок, "
               f"риск не поднимай.")
        log.warning("edge → Telegram: %s", " | ".join(
            p.strip() for p in msg.splitlines()))
        if self.notifier is not None:
            try:
                self.notifier.send(msg)
            except Exception as e:
                log.warning("Не удалось отправить edge-тревогу: %s", e)
