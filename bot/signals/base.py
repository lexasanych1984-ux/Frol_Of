"""Общий интерфейс источника сигналов.

Источник выдаёт сырые строки alert_message. Разбором и исполнением занимается
движок — так любой источник взаимозаменяем.
"""
from __future__ import annotations

import queue
from typing import Iterator


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
