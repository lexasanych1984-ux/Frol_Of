"""Модели данных для сделок, импортированных из MT5."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional


@dataclass
class Trade:
    """Одна закрытая позиция из таблицы "Позиции" MT5-отчёта.

    Это основной объект, с которым дальше работает matching-модуль:
    сопоставляется с записью в Notion по (symbol, open_time) с допуском
    в несколько минут (см. config.matching.time_tolerance_minutes).
    """

    account_login: str
    position_id: str
    symbol: str
    direction: str          # "buy" | "sell"
    volume: float
    open_time: datetime
    open_price: float
    close_time: Optional[datetime]
    close_price: Optional[float]
    sl: Optional[float]
    tp: Optional[float]
    commission: float
    swap: float
    profit: float
    source_file: str

    @property
    def is_closed(self) -> bool:
        return self.close_time is not None

    @property
    def net_pnl(self) -> float:
        """Прибыль с учётом комиссии и свопа."""
        return self.profit + self.commission + self.swap

    def to_dict(self) -> dict:
        d = asdict(self)
        d["open_time"] = self.open_time.isoformat() if self.open_time else None
        d["close_time"] = self.close_time.isoformat() if self.close_time else None
        return d
