"""Структурные примитивы поверх OHLC-баров — фракталы, FVG, Order Block,
Order Flow (колена структуры), слом структуры (MSS).

Все функции с параметром `as_of_idx` используют только бары СТРОГО ДО этой
точки (плюс сам бар, если явно упомянуто) — чтобы движок бэктеста мог честно
идти вперёд по времени без подглядывания в будущее. `as_of_idx` — это индекс
"текущего" бара в симуляции; фрактал на индексе i confirмируется только
начиная с индекса i+1 (нужен следующий бар, чтобы понять, что i был
локальным экстремумом), поэтому фрактал учитывается, только если
`i + 1 <= as_of_idx`.

Правила (см. STRATEGY_1H_MANIPULATION.md, подтверждено трейдером):
  - Фрактал: классическая 3-свечная формация (high или low).
  - Order Flow: минимум 2 колена одной структуры — 2×(HH+HL) для восходящей,
    2×(LH+LL) для нисходящей.
  - Слом структуры (MSS): цена ЗАКРЫВАЕТСЯ (тело свечи) за ближайшим
    противоположным фракталом.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

import pandas as pd

Direction = Literal["up", "down"]


def find_fractals(df: pd.DataFrame) -> pd.DataFrame:
    """Возвращает df с добавленными колонками is_fractal_high/is_fractal_low.

    Бар i — фрактал-хай, если high[i] строго больше соседей с обеих сторон;
    фрактал-лоу симметрично. Первый и последний бар фракталами быть не могут
    (нет одного из соседей)."""
    out = df.copy()
    high, low = out["high"], out["low"]
    is_high = (high > high.shift(1)) & (high > high.shift(-1))
    is_low = (low < low.shift(1)) & (low < low.shift(-1))
    out["is_fractal_high"] = is_high.fillna(False)
    out["is_fractal_low"] = is_low.fillna(False)
    return out


def _confirmed_fractal_highs(fractals: pd.DataFrame, as_of_idx: int) -> pd.DataFrame:
    """Фракталы-хаи, confirmed к моменту as_of_idx (индекс фрактала + 1 <= as_of_idx)."""
    mask = fractals["is_fractal_high"] & (fractals.index + 1 <= as_of_idx)
    return fractals.loc[mask]


def _confirmed_fractal_lows(fractals: pd.DataFrame, as_of_idx: int) -> pd.DataFrame:
    mask = fractals["is_fractal_low"] & (fractals.index + 1 <= as_of_idx)
    return fractals.loc[mask]


def structure_legs(fractals: pd.DataFrame, as_of_idx: int, legs: int = 2) -> Optional[Direction]:
    """Order Flow: нужно минимум `legs` колен одной структуры (по умолчанию
    2, как подтвердил трейдер для H1). Формализовано как: последние
    `legs`+1 confirmed фрактал-хая монотонно растут (= `legs` повышений =
    `legs` колен HH) И столько же фрактал-лоу тоже монотонно растут =>
    "up". Симметрично для "down". Недостаточно фракталов или несовпадение
    — None (нет чёткого order flow, сделок в этом состоянии не открываем).

    `legs=1` используется для дневного контекста — трейдер подтвердил
    правило "2 колена" явно только для H1 order flow, для дневного
    контекста это ДОПУЩЕНИЕ по итогам верификации на реальных данных: с
    `legs=2` дневной фильтр резолвился в чёткое направление только 17% дней
    за 2.5 года и блокировал 100% сигналов в окне вокруг двух реальных
    сделок трейдера — то есть слишком строг, чтобы быть тем, что трейдер
    reально имеет в виду под "дневным контекстом". Не подтверждено
    трейдером буквально, см. STRATEGY_1H_MANIPULATION.md."""
    n = legs + 1
    highs = _confirmed_fractal_highs(fractals, as_of_idx)["high"].tail(n).tolist()
    lows = _confirmed_fractal_lows(fractals, as_of_idx)["low"].tail(n).tolist()
    if len(highs) < n or len(lows) < n:
        return None

    highs_rising = all(highs[k] < highs[k + 1] for k in range(n - 1))
    lows_rising = all(lows[k] < lows[k + 1] for k in range(n - 1))
    if highs_rising and lows_rising:
        return "up"

    highs_falling = all(highs[k] > highs[k + 1] for k in range(n - 1))
    lows_falling = all(lows[k] > lows[k + 1] for k in range(n - 1))
    if highs_falling and lows_falling:
        return "down"

    return None


def detect_structure_break(df: pd.DataFrame, fractals: pd.DataFrame, current_direction: Direction, as_of_idx: int) -> Optional[int]:
    """Слом структуры (MSS): ближайший противоположный фрактал ДО as_of_idx,
    и первый бар после него, чьё тело (close) закрылось за его уровнем.

    "Противоположный" направлению current_direction: в восходящей
    структуре слом — это закрытие НИЖЕ последнего фрактал-лоу (сигнал
    возможного разворота вниз), и наоборот."""
    if current_direction == "up":
        candidates = _confirmed_fractal_lows(fractals, as_of_idx)
        if candidates.empty:
            return None
        target_idx = candidates.index[-1]
        target_price = candidates["low"].iloc[-1]
        window = df.loc[target_idx + 1 : as_of_idx]
        broken = window[window["close"] < target_price]
    else:
        candidates = _confirmed_fractal_highs(fractals, as_of_idx)
        if candidates.empty:
            return None
        target_idx = candidates.index[-1]
        target_price = candidates["high"].iloc[-1]
        window = df.loc[target_idx + 1 : as_of_idx]
        broken = window[window["close"] > target_price]

    if broken.empty:
        return None
    return int(broken.index[0])


@dataclass(frozen=True)
class Zone:
    kind: Literal["FVG", "OB", "FRACTAL_LIQUIDITY"]
    direction: Direction  # направление, в котором зона действует как разворотная/интересная
    top: float
    bottom: float
    formed_at_idx: int  # индекс бара, на котором зона становится известна (confirmed)


def find_fvg(df: pd.DataFrame) -> list[Zone]:
    """Fair Value Gap по 3 свечам: разрыв между high свечи i-1 и low свечи
    i+1 (bullish), либо между low свечи i-1 и high свечи i+1 (bearish).
    Confirmed на индексе i+1 (нужна третья свеча, чтобы увидеть разрыв)."""
    zones: list[Zone] = []
    high, low = df["high"], df["low"]
    for i in range(1, len(df) - 1):
        prev_high, prev_low = high.iloc[i - 1], low.iloc[i - 1]
        next_high, next_low = high.iloc[i + 1], low.iloc[i + 1]
        if next_low > prev_high:
            zones.append(Zone("FVG", "up", top=float(next_low), bottom=float(prev_high), formed_at_idx=i + 1))
        elif next_high < prev_low:
            zones.append(Zone("FVG", "down", top=float(prev_low), bottom=float(next_high), formed_at_idx=i + 1))
    return zones


def find_order_blocks(df: pd.DataFrame, fractals: pd.DataFrame) -> list[Zone]:
    """Order Block: последняя противоположная по цвету свеча перед импульсным
    движением, которое пробивает (закрытием тела) предыдущий фрактал.

    Bullish OB: последняя медвежья (close<open) свеча перед серией бычьих
    свечей, закрывшихся выше предыдущего фрактал-хая. Симметрично bearish OB.
    Использует ту же механику "слома", что detect_structure_break, но не
    привязано к конкретному as_of_idx — ищет все такие события по всей
    истории df (зоны, найденные позже as_of_idx, просто не будут видны
    коду сигналов, который сам фильтрует по formed_at_idx)."""
    zones: list[Zone] = []
    highs = fractals.index[fractals["is_fractal_high"]].tolist()
    lows = fractals.index[fractals["is_fractal_low"]].tolist()

    for fh in highs:
        level = fractals["high"].loc[fh]
        after = df.loc[fh + 1 :]
        broken = after[after["close"] > level]
        if broken.empty:
            continue
        break_idx = int(broken.index[0])
        ob_idx = _find_impulse_origin(df, break_idx, bullish=True)
        if ob_idx is not None:
            candle = df.loc[ob_idx]
            zones.append(Zone("OB", "up", top=float(candle["high"]), bottom=float(candle["low"]), formed_at_idx=break_idx))

    for fl in lows:
        level = fractals["low"].loc[fl]
        after = df.loc[fl + 1 :]
        broken = after[after["close"] < level]
        if broken.empty:
            continue
        break_idx = int(broken.index[0])
        ob_idx = _find_impulse_origin(df, break_idx, bullish=False)
        if ob_idx is not None:
            candle = df.loc[ob_idx]
            zones.append(Zone("OB", "down", top=float(candle["high"]), bottom=float(candle["low"]), formed_at_idx=break_idx))

    return zones


def _find_impulse_origin(df: pd.DataFrame, break_idx: int, bullish: bool) -> Optional[int]:
    """Идёт назад от break_idx через непрерывный забег свечей "в сторону
    пробоя" (бычьих для bullish-пробоя) и возвращает индекс первой
    противоположной свечи перед этим забегом — это и есть OB-свеча."""
    i = break_idx
    while i >= 0:
        is_same_direction = (df["close"].iloc[i] > df["open"].iloc[i]) == bullish
        if not is_same_direction:
            return i
        i -= 1
    return None


def find_fractal_liquidity_zones(fractals: pd.DataFrame) -> list[Zone]:
    """Зона снятия ликвидности фрактала — сам фрактал-хай/лоу как узкая
    зона (используется как третий тип "старшей зоны" наравне с FVG/OB, как
    подтвердил трейдер).

    Direction — по тому, в какую сторону цена разворачивается ПОСЛЕ снятия
    этой ликвидности (та же конвенция, что у FVG/OB: "up" = зона работает
    как поддержка/разворот вверх, "down" = как сопротивление/разворот вниз).
    Фрактал-хай — здесь отдыхают стопы/лимитки продавцов; снятие (прокол
    вверх) типично ведёт к развороту ВНИЗ -> direction="down". Фрактал-лоу
    симметрично -> direction="up"."""
    zones: list[Zone] = []
    for idx, row in fractals[fractals["is_fractal_high"]].iterrows():
        zones.append(Zone("FRACTAL_LIQUIDITY", "down", top=float(row["high"]), bottom=float(row["high"]), formed_at_idx=int(idx) + 1))
    for idx, row in fractals[fractals["is_fractal_low"]].iterrows():
        zones.append(Zone("FRACTAL_LIQUIDITY", "up", top=float(row["low"]), bottom=float(row["low"]), formed_at_idx=int(idx) + 1))
    return zones
