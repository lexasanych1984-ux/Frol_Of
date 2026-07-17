"""Фейковый брокер для dry-run без MT5 (демонстрация парсинга + сайзинга)."""
from __future__ import annotations

from typing import List

from .broker_mt5 import Account, OrderResult, Position
from .model import Side, Signal
from .sizing import SymbolSpec

# Приблизительные параметры инструментов (для оффлайн-демо; на реале берутся из MT5).
_SPECS = {
    "EURUSD.s": SymbolSpec("EURUSD.s", 0.01, 100.0, 0.01, 0.00001, 1.0),
    "GBPJPY.s": SymbolSpec("GBPJPY.s", 0.01, 100.0, 0.01, 0.001, 0.68),
    "GER40.s":  SymbolSpec("GER40.s", 0.01, 100.0, 0.01, 0.01, 0.01),
    "NAS100.s": SymbolSpec("NAS100.s", 0.1, 100.0, 0.1, 0.01, 0.01),
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

    def place_entry(self, sig: Signal, lots: float, symbol: str, comment="") -> OrderResult:
        return OrderResult(True, f"[FAKE] {sig.side.value} {lots} {symbol}", ticket=1)

    def modify_sl_to_entry(self, symbol: str, side: Side) -> OrderResult:
        return OrderResult(True, "[FAKE] BE")

    def close_position(self, symbol: str, side: Side) -> OrderResult:
        return OrderResult(True, "[FAKE] close")
