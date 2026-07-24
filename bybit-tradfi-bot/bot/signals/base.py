"""Общий интерфейс источника сигналов.

Источник выдаёт «конверты» :class:`RawSignal` — сырую строку alert_message плюс
метаданные (когда сигнал сработал/принят «наверху», из какого канала, внешний id).
Разбором и исполнением занимается движок — так любой источник взаимозаменяем.

Метка времени ``received_ts`` нужна защите протухших сигналов: вход, доставленный
с большим опозданием (бот стоял, потом добрал сигнал из облачного буфера), нельзя
исполнять по устаревшей цене. См. ``Engine.handle_raw`` (гейт свежести).
"""
from __future__ import annotations

import queue
from dataclasses import dataclass
from typing import Iterator, Optional, Tuple


@dataclass
class RawSignal:
    """Конверт одного сигнала на пути от источника к движку."""
    raw: str                              # сырой alert_message
    received_ts: Optional[float] = None   # epoch: когда сигнал сработал/принят «наверху»
    source: str = ""                      # 'cdp' | 'webhook' | 'http'
    ext_id: Optional[str] = None          # fire_id / id облака — для дедуп-трассировки


class SignalSource:
    """Базовый класс. start() запускает получение, poll()/stream() отдают конверты."""

    #: короткое имя канала, проставляется в RawSignal.source (переопределяется)
    name: str = ""

    def __init__(self):
        self.q: "queue.Queue[RawSignal]" = queue.Queue()

    # ── Внутренний хелпер: положить конверт в очередь ─────────────────────────
    def _put(self, raw: str, received_ts: Optional[float] = None,
             ext_id: Optional[str] = None) -> None:
        self.q.put(RawSignal(raw=raw, received_ts=received_ts,
                             source=self.name, ext_id=ext_id))

    def start(self) -> None:  # pragma: no cover - переопределяется
        raise NotImplementedError

    def stop(self) -> None:  # pragma: no cover
        pass

    def stream(self) -> Iterator[RawSignal]:
        """Блокирующий генератор конвертов."""
        while True:
            yield self.q.get()

    def poll(self, timeout: float) -> Optional[RawSignal]:
        """Неблокирующе-с-таймаутом: вернуть конверт или None по истечении.

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
