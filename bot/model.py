"""Единая модель сигнала, которую понимает весь бот."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Action(str, Enum):
    ENTRY = "entry"      # открыть позицию
    EXIT = "exit"        # закрыть / выйти
    BREAKEVEN = "be"     # перенести стоп в безубыток


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"


class OrderKind(str, Enum):
    MARKET = "market"    # исполнить по рынку сейчас
    LIMIT = "limit"      # отложенный лимит на уровне entry
    STOP = "stop"        # отложенный стоп (пробой) на уровне entry


@dataclass
class Signal:
    """Нормализованный сигнал из alert_message."""
    action: Action
    side: Optional[Side] = None
    symbol_tv: Optional[str] = None          # тикер как в TradingView (EURUSD)
    entry: Optional[float] = None
    sl: Optional[float] = None
    tp: Optional[float] = None
    rr: Optional[float] = None
    order_kind: OrderKind = OrderKind.MARKET
    strategy: Optional[str] = None           # smc | crt | asweep | ...
    raw: str = ""
    # Явный id из JSON-алерта (если есть) — для надёжной идемпотентности.
    signal_id: Optional[str] = None
    meta: dict = field(default_factory=dict)

    def dedup_key(self) -> str:
        """Ключ идемпотентности. Если стратегия дала signal_id — используем его."""
        if self.signal_id:
            return f"id:{self.signal_id}"
        basis = "|".join(
            str(x) for x in (
                self.action.value,
                self.side.value if self.side else "",
                self.symbol_tv or "",
                self.entry, self.sl, self.tp,
            )
        )
        return "h:" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]

    def __str__(self) -> str:
        if self.action is Action.ENTRY:
            return (f"{self.side.value.upper()} {self.symbol_tv} "
                    f"[{self.order_kind.value}] entry={self.entry} "
                    f"SL={self.sl} TP={self.tp} RR={self.rr}")
        return f"{self.action.value.upper()} {self.side.value if self.side else ''} {self.symbol_tv}"
