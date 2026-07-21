"""Слияние нескольких источников сигналов в одну очередь для главного цикла.

Быстрый CDP и надёжный webhook-буфер работают ПАРАЛЛЕЛЬНО: оба кладут конверты в
общую очередь, главный цикл забирает их одним poll(). Дедуп между каналами делает
движок по хэшу содержимого (State.seen). Живость каждого канала проверяется
отдельно (HealthMonitor), поэтому композит нужен только для доставки.
"""
from __future__ import annotations

from typing import List

from .base import SignalSource


class CompositeSource(SignalSource):
    name = "composite"

    def __init__(self, children: List[SignalSource]):
        super().__init__()
        self.children = [c for c in children if c is not None]
        # Все дети пишут в ОБЩУЮ очередь композита (их _put использует self.q).
        for c in self.children:
            c.q = self.q

    def start(self) -> None:
        for c in self.children:
            c.start()

    def stop(self) -> None:
        for c in self.children:
            try:
                c.stop()
            except Exception:
                pass
