"""Фильтр снятия месячного фрактала (правило трейдера, 2026-07-02).

Правило словами трейдера: «цена снимает месячный фрактал — мы не входим в
сделку, пока месячный фрактал не пройдет по дневному имбалансом. После
этого можно входить либо в лонг, либо в шорт».

Формализация (обе детали — ДОПУЩЕНИЯ, трейдер дал правило без точного
алгоритма, см. STRATEGY_1H_MANIPULATION.md):
  - «Снятие» — первый D1-бар, ЗАКРЫВШИЙСЯ за уровнем confirmed месячного
    фрактала. Первая версия считала снятием прокол тенью, но блокировала
    37-66% всего времени (на GER40 — две трети трёхлетнего периода, 1
    сделка за 3 года) — трейдер 2026-07-02 попросил смягчить до закрытия.
  - «Пройден дневным имбалансом» — после снятия на D1 сформировался FVG
    В ЛЮБУЮ СТОРОНУ, чей диапазон дотягивается до уровня фрактала (бычий
    FVG с top >= уровня, либо медвежий с bottom <= уровня). С этого
    момента блокировка снята, торговать можно в обе стороны.
    Направление «любое» подтверждено трейдером 2026-07-02: первая версия
    требовала имбаланс строго в сторону прохода уровня, из-за чего
    V-образный разворот после снятия (GER40, обвал августа 2024) никогда
    не разблокировал инструмент — одна такая блокировка закрывала 2/3
    трёхлетнего периода.
  - Пока прохода нет — ВСЕ входы по инструменту заблокированы (правило
    сформулировано как «не входим в сделку», без привязки к близости
    цены к уровню).
  - Фрактал, чей уровень уже был пройден ЗАКРЫТИЕМ месячной свечи до
    первого доступного D1-бара, не отслеживается: уровень пройден давно,
    у нас просто нет дневных данных того времени, чтобы найти имбаланс.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from .structure import find_fractals, find_fvg

# (начало блокировки, конец блокировки или None = до конца данных)
BlockInterval = tuple[pd.Timestamp, Optional[pd.Timestamp]]


def monthly_sweep_blocks(mn1: pd.DataFrame, d1: pd.DataFrame) -> list[BlockInterval]:
    """Интервалы, в которых входы запрещены из-за снятого, но ещё не
    пройденного дневным имбалансом месячного фрактала."""
    if mn1.empty or d1.empty:
        return []

    fractals = find_fractals(mn1)
    d1_fvgs = find_fvg(d1)
    first_d1_time = d1["time"].iloc[0]
    blocks: list[BlockInterval] = []

    for is_high in (True, False):
        col_flag = "is_fractal_high" if is_high else "is_fractal_low"
        col_level = "high" if is_high else "low"
        for idx in fractals.index[fractals[col_flag]].tolist():
            # Фрактал confirmed, когда следующая месячная свеча ЗАКРЫТА —
            # т.е. с открытия свечи idx+2 (свеча idx+1 закрывается в момент
            # открытия idx+2). Последние фракталы без idx+2 ещё не confirmed.
            if idx + 2 >= len(mn1):
                continue
            level = float(mn1[col_level].iloc[idx])
            confirm_time = mn1["time"].iloc[idx + 2]

            # Уровень пройден закрытием месячной свечи ещё до наших D1-данных
            # — не отслеживаем (см. докстринг модуля).
            older = mn1.loc[(mn1["time"] >= confirm_time) & (mn1["time"] < first_d1_time)]
            if is_high and (older["close"] > level).any():
                continue
            if not is_high and (older["close"] < level).any():
                continue

            scan = d1.loc[d1["time"] >= confirm_time]
            swept = scan[scan["close"] > level] if is_high else scan[scan["close"] < level]
            if swept.empty:
                continue
            sweep_time = swept["time"].iloc[0]

            resolution_time: Optional[pd.Timestamp] = None
            for z in d1_fvgs:
                z_time = d1["time"].iloc[z.formed_at_idx]
                if z_time < sweep_time:
                    continue
                # Имбаланс в ЛЮБУЮ сторону, дотянувшийся до уровня (см.
                # докстринг модуля — подтверждено трейдером 2026-07-02).
                passed = z.top >= level if z.direction == "up" else z.bottom <= level
                if passed:
                    resolution_time = z_time
                    break

            blocks.append((sweep_time, resolution_time))

    blocks.sort(key=lambda b: b[0])
    return blocks


def is_blocked(ts: pd.Timestamp, blocks: list[BlockInterval]) -> bool:
    return any(start <= ts and (end is None or ts < end) for start, end in blocks)


def blocked_share(blocks: list[BlockInterval], date_from: pd.Timestamp, date_to: pd.Timestamp) -> float:
    """Доля периода [date_from, date_to], закрытая блокировками, 0..1 —
    диагностика, чтобы в отчёте было видно, если формализация фильтра
    перекрывает подозрительно много времени (признак ошибки в допущениях)."""
    total = (date_to - date_from).total_seconds()
    if total <= 0:
        return 0.0
    # Объединяем пересекающиеся интервалы, чтобы не посчитать время дважды.
    clipped = []
    for start, end in blocks:
        s = max(start, date_from)
        e = min(end, date_to) if end is not None else date_to
        if s < e:
            clipped.append((s, e))
    clipped.sort()
    merged: list[list[pd.Timestamp]] = []
    for s, e in clipped:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    blocked = sum((e - s).total_seconds() for s, e in merged)
    return blocked / total
