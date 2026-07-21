"""Общий интерфейс источника сигналов.

Источник выдаёт сырые строки alert_message. Разбором и исполнением занимается
движок — так любой источник взаимозаменяем.
"""
from __future__ import annotations

import queue
from typing import Iterator, Optional, Tuple


class SignalSource:
    """Базовый класс. start() запускает получение, stream() отдаёт сообщения."""

    def __init__(self):
        self.q: "queue.Queue[str]" = queue.Queue()

    def start(self) -> None:  # pragma: no cover - переопределяется
        raise NotImplementedError

    def stop(self) -> None:  # pragma: no cover
        pass

    def stream(self) -> Iterator[str]:
        """Блокирующий генератор сырых alert_message."""
        while True:
            yield self.q.get()

    def poll(self, timeout: float) -> Optional[str]:
        """Неблокирующе-с-таймаутом: вернуть сообщение или None по истечении.

        Главный цикл использует poll вместо stream, чтобы регулярно просыпаться
        и запускать проверки живости даже когда сигналов нет.
        """
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None

    def health(self, now: float, stale_sec: float) -> Tuple[bool, str, str]:
        """Живость источника: (ok, причина, что делать). По умолчанию — «жив»."""
        return True, "источник сигналов активен", ""
