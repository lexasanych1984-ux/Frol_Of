"""Расчёт размера позиции (лота) от риска и дистанции до стопа.

Чистые функции без зависимости от MetaTrader5 — их легко тестировать.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


@dataclass
class SymbolSpec:
    """Параметры инструмента, взятые из MT5 symbol_info."""
    name: str
    volume_min: float
    volume_max: float
    volume_step: float
    tick_size: float        # trade_tick_size — шаг цены
    tick_value: float       # trade_tick_value — стоимость тика на 1.0 лот, в валюте счёта


@dataclass
class SizingResult:
    lots: float
    ok: bool
    reason: str = ""
    risk_amount: float = 0.0
    loss_per_lot: float = 0.0


def _round_step(volume: float, step: float) -> float:
    if step <= 0:
        return volume
    # округляем ВНИЗ к шагу, чтобы не превысить риск
    steps = math.floor(round(volume / step, 8))
    return round(steps * step, 8)


def compute_lots(
    equity: float,
    risk_pct: float,
    entry: float,
    sl: float,
    spec: SymbolSpec,
    *,
    skip_if_below_min: bool = True,
    max_lot: Optional[float] = None,
) -> SizingResult:
    """Сколько лотов взять, чтобы при срабатывании SL потерять risk_pct% equity.

    loss_per_lot = (|entry - sl| / tick_size) * tick_value
    lots = risk_amount / loss_per_lot, округлённые вниз к volume_step.
    """
    if equity <= 0:
        return SizingResult(0.0, False, "equity<=0")
    if entry is None or sl is None:
        return SizingResult(0.0, False, "no entry/sl")

    sl_dist = abs(entry - sl)
    if sl_dist <= 0:
        return SizingResult(0.0, False, "sl_dist<=0")
    if spec.tick_size <= 0 or spec.tick_value <= 0:
        return SizingResult(0.0, False, "bad tick_size/tick_value")

    risk_amount = equity * (risk_pct / 100.0)
    loss_per_lot = (sl_dist / spec.tick_size) * spec.tick_value
    if loss_per_lot <= 0:
        return SizingResult(0.0, False, "loss_per_lot<=0")

    raw_lots = risk_amount / loss_per_lot
    lots = _round_step(raw_lots, spec.volume_step)

    if lots < spec.volume_min:
        if skip_if_below_min:
            return SizingResult(
                0.0, False,
                f"lots {raw_lots:.4f} < min {spec.volume_min} (риск слишком мал)",
                risk_amount, loss_per_lot)
        lots = spec.volume_min

    if lots > spec.volume_max:
        lots = _round_step(spec.volume_max, spec.volume_step)

    if max_lot is not None and lots > max_lot:
        lots = _round_step(max_lot, spec.volume_step)
        if lots < spec.volume_min:
            return SizingResult(0.0, False, "max_lot < volume_min", risk_amount, loss_per_lot)

    if lots <= 0:
        return SizingResult(0.0, False, "lots<=0", risk_amount, loss_per_lot)

    return SizingResult(lots, True, "ok", risk_amount, loss_per_lot)
