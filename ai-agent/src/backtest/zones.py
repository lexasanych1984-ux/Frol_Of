"""Объединение FVG/OB/фрактал-ликвидности в общий пул зон и поиск FTA/тейка.

Конвенция направления зоны (см. structure.py): "up" — зона работает как
поддержка/разворот вверх (типично формируется во время восходящего
движения), "down" — как сопротивление/разворот вниз. Для BUY-сделки
"противоположная навстречу" зона (FTA) — это ближайшая "down"-зона выше
цены входа; для SELL — ближайшая "up"-зона ниже цены входа."""
from __future__ import annotations

from typing import Literal, Optional

import pandas as pd

from .structure import Zone, find_fractal_liquidity_zones, find_fractals, find_fvg, find_order_blocks

TradeDirection = Literal["buy", "sell"]


def gather_zones(df: pd.DataFrame, fractals: Optional[pd.DataFrame] = None) -> list[Zone]:
    if fractals is None:
        fractals = find_fractals(df)
    return find_fvg(df) + find_order_blocks(df, fractals) + find_fractal_liquidity_zones(fractals)


def filter_significant_zones(zones: list[Zone], df: pd.DataFrame, min_width_multiplier: float = 2.0) -> list[Zone]:
    """ДОПУЩЕНИЕ, добавлено по итогам верификации на реальных данных
    (не подтверждено трейдером буквально, см. STRATEGY_1H_MANIPULATION.md):

    Без фильтра `gather_zones()` находит зону практически на каждом баре
    (например, 1430 зон на 1281 H4-баре EURUSD за полгода, медианная ширина
    зоны — 0.00158, МЕНЬШЕ типичного размаха одной H4-свечи 0.00224) —
    то есть подавляющее большинство технически валидных FVG/OB — это
    ценовой шум, а не то, что трейдер реально разметил бы на графике как
    значимую зону. Из-за этого FTA всегда оказывался в паре пунктов от
    входа, и правило "RR до FTA >= 1:1" не проходило вообще никогда — не
    единичный случай, а системная блокировка всех сигналов.

    Фильтр: зона проходит, только если её ширина >= `min_width_multiplier`
    типичных H4-баров (медианный high-low). FRACTAL_LIQUIDITY-зоны имеют
    нулевую ширину по построению и этим фильтром исключаются из FTA/тейка
    полностью — что логично: у точечного фрактала нет "ширины", чтобы
    быть содержательной целью для тейка, а как СИГНАЛ (снятие ликвидности)
    он по-прежнему используется отдельно, до сборки зон."""
    if df.empty:
        return zones
    typical_range = float((df["high"] - df["low"]).median())
    min_width = typical_range * min_width_multiplier
    return [z for z in zones if (z.top - z.bottom) >= min_width]


def zones_as_of(zones: list[Zone], as_of_idx: int) -> list[Zone]:
    """Только зоны, уже известные (confirmed) к моменту as_of_idx — без забегания вперёд."""
    return [z for z in zones if z.formed_at_idx <= as_of_idx]


def nearest_opposite_zone(zones: list[Zone], trade_direction: TradeDirection, entry_price: float, as_of_idx: int) -> Optional[Zone]:
    """Ближайшая зона, способная развернуть цену НАВСТРЕЧУ сделке (используется
    и для FTA, и как кандидат на дальний тейк — см. STRATEGY_1H_MANIPULATION.md)."""
    candidates = zones_as_of(zones, as_of_idx)
    opposite_direction = "down" if trade_direction == "buy" else "up"

    if trade_direction == "buy":
        ahead = [z for z in candidates if z.direction == opposite_direction and z.bottom > entry_price]
        if not ahead:
            return None
        return min(ahead, key=lambda z: z.bottom)
    else:
        ahead = [z for z in candidates if z.direction == opposite_direction and z.top < entry_price]
        if not ahead:
            return None
        return min(ahead, key=lambda z: entry_price - z.top)


def take_profit_zone(zones: list[Zone], trade_direction: TradeDirection, entry_price: float, fta: Zone, as_of_idx: int) -> Zone:
    """Тейк = ближайшая ЗНАЧИМАЯ противоположная зона ДАЛЬШЕ FTA по ходу
    сделки; если такой отдельно от FTA нет — тейк = сама FTA (упрощение
    v1, см. STRATEGY_1H_MANIPULATION.md, раздел "Известное упрощение
    модели" — точный алгоритм выбора "именно той" зоны трейдер описывает
    как визуальный/дискреционный процесс)."""
    fta_edge = fta.bottom if trade_direction == "buy" else fta.top
    candidates = zones_as_of(zones, as_of_idx)
    opposite_direction = "down" if trade_direction == "buy" else "up"

    if trade_direction == "buy":
        beyond = [z for z in candidates if z.direction == opposite_direction and z.bottom > fta_edge]
        if not beyond:
            return fta
        return min(beyond, key=lambda z: z.bottom)
    else:
        beyond = [z for z in candidates if z.direction == opposite_direction and z.top < fta_edge]
        if not beyond:
            return fta
        return max(beyond, key=lambda z: z.top)
