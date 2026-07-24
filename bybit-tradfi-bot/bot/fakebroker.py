"""Фейковый брокер для dry-run без MT5 (демонстрация парсинга + сайзинга)."""
from __future__ import annotations

from typing import List

from .broker_mt5 import Account, OrderResult, Position
from .model import Side, Signal
from .sizing import SymbolSpec

# Приблизительные параметры инструментов (для оффлайн-демо; на реале берутся из MT5).
# Значения сняты с реальных symbol_info демо-сервера Just2Trade-MT5 (2026-07-18).
_SPECS = {
    "EURUSD":     SymbolSpec("EURUSD", 0.01, 1000.0, 0.01, 0.00001, 1.0),
    "GBPJPY":     SymbolSpec("GBPJPY", 0.01, 1000.0, 0.01, 0.001, 0.62),
    "GER30m":     SymbolSpec("GER30m", 1.0, 300.0, 1.0, 0.1, 0.1),
    "USTECH100m": SymbolSpec("USTECH100m", 1.0, 300.0, 1.0, 0.1, 0.1),
}


class FakeBroker:
    def __init__(self, equity: float = 10000.0, currency: str = "USD"):
        self._equity = equity
        self._currency = currency
        self._positions: List[Position] = []

    def account(self) -> Account:
        return Account(0, self._equity, self._equity, self._currency, "DEMO-FAKE")

    def positions(self) -> List[Position]:
        return list(self._positions)

    def symbol_spec(self, symbol: str):
        return _SPECS.get(symbol, SymbolSpec(symbol, 0.01, 100.0, 0.01, 0.00001, 1.0))

    def current_price(self, symbol: str, side: Side):
        # Оффлайн-демо: живой котировки нет. None → движок считает лот от sig.entry
        # (ветка market_price в Engine._handle_entry корректно это обрабатывает).
        return None

    def place_entry(self, sig: Signal, lots: float, symbol: str, comment="") -> OrderResult:
        return OrderResult(True, f"[FAKE] {sig.side.value} {lots} {symbol}", ticket=1)

    def modify_sl_to_entry(self, symbol: str, side: Side) -> OrderResult:
        return OrderResult(True, "[FAKE] BE")

    def close_position(self, symbol: str, side: Side) -> OrderResult:
        return OrderResult(True, "[FAKE] close")

    def cancel_pending(self, symbol: str, side=None, magic=None) -> OrderResult:
        return OrderResult(True, "[FAKE] cancel")
