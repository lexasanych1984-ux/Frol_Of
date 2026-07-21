"""Чистая арифметика проскальзывания и фактического риска (без MT5).

Используется движком (мгновенная проверка «риск выше заданного») и отчётом
(сводка проскальзывания). Без побочных эффектов — легко тестируется.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .sizing import SymbolSpec


def pip_size(tick_size: float) -> float:
    """«Пункт» инструмента в человеческом смысле (пипс), а не MT5-point.

    FX (5-знак 0.00001 → 0.0001; JPY 3-знак 0.001 → 0.01): пипс = tick_size*10.
    Индексы (tick_size 0.1 и крупнее): пипс = 1.0 (как в бэктестах, «80 пунктов»).
    Эвристика покрывает боевые инструменты (EURUSD, GBPJPY, GER40, NAS100).
    """
    if tick_size and tick_size < 0.01:
        return tick_size * 10.0
    return 1.0


@dataclass
class Slippage:
    slip_price: float          # fill - plan_entry, знак сохранён
    slip_pips: float           # то же в пунктах инструмента
    slip_pct_of_sl: Optional[float]  # |slip| как % от плановой дистанции до SL
    adverse: bool              # True — проскальзывание увеличило риск


def compute_slippage(side: str, plan_entry: Optional[float],
                     fill_price: Optional[float], sl: Optional[float],
                     spec: SymbolSpec) -> Optional[Slippage]:
    """Проскальзывание входа. None, если нет плановой цены или факта."""
    if plan_entry is None or fill_price is None:
        return None
    slip_price = fill_price - plan_entry
    pip = pip_size(spec.tick_size)
    slip_pips = slip_price / pip if pip else 0.0

    pct = None
    plan_dist = abs(plan_entry - sl) if sl is not None else None
    if plan_dist:
        pct = abs(slip_price) / plan_dist * 100.0

    # «adverse» = fill хуже плановой цены В СТОРОНУ сделки → дистанция до SL
    # выросла → риск выше. Для long хуже = дороже, для short = дешевле.
    if str(side).lower() == "long":
        adverse = slip_price > 0
    else:
        adverse = slip_price < 0
    return Slippage(round(slip_price, 6), round(slip_pips, 1),
                    round(pct, 1) if pct is not None else None, adverse)


@dataclass
class ActualRisk:
    risk_amount: float         # сколько потеряем при SL, в валюте счёта
    risk_pct: float            # то же как % equity
    rr: Optional[float]        # фактический RR = |tp-fill| / |fill-sl|


def compute_actual_risk(fill_price: float, sl: Optional[float],
                        tp: Optional[float], lots: float, spec: SymbolSpec,
                        equity: float) -> Optional[ActualRisk]:
    """Фактический риск и RR от реальной цены исполнения и реального лота."""
    if fill_price is None or sl is None or lots is None:
        return None
    if spec.tick_size <= 0 or spec.tick_value <= 0 or equity <= 0:
        return None
    sl_dist = abs(fill_price - sl)
    loss_per_lot = sl_dist / spec.tick_size * spec.tick_value
    risk_amount = loss_per_lot * lots
    risk_pct = risk_amount / equity * 100.0
    rr = None
    if tp is not None and sl_dist > 0:
        rr = abs(tp - fill_price) / sl_dist
    return ActualRisk(round(risk_amount, 2), round(risk_pct, 3),
                      round(rr, 2) if rr is not None else None)
