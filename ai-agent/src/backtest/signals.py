"""Генерация сигналов сетапа "1Н поглощение манипуляции" — сборка всех
правил из STRATEGY_1H_MANIPULATION.md в один пайплайн.

ДВА ДОПУЩЕНИЯ (не прямой ответ трейдера, см. STRATEGY_1H_MANIPULATION.md и
план `structured-toasting-rainbow.md`), обе точки явно помечены ниже:
  1. Точное подтверждение "поглощения манипуляции" на H1: прокол (тенью)
     ближайшего противоположного H1-фрактала внутри H4-зоны, закрытие
     ЭТОЙ ЖЕ свечи обратно по другую сторону уровня (sweep + reject в
     одной свече — судя по всему то же самое, что трейдер описывает как
     "поглощение").
  2. M15-слом структуры тоже должен происходить внутри той же H4-зоны
     (а не где угодно на графике) — иначе теряется привязка к контексту.

Часовой пояс баров — платформенное время брокера (см. config.yaml: для
FundingPips это UTC+3, не подстраивается под переход на летнее время).
Сессии (Франкфурт/Лондон/NY) заданы как фиксированные часовые окна в этом
времени — тоже упрощение без подстройки под DST по обе стороны.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal, Optional

import pandas as pd

from . import price_data
from .monthly_filter import blocked_share, is_blocked, monthly_sweep_blocks
from .progress import log
from .structure import Zone, detect_structure_break, find_fractals, structure_legs
from .zones import filter_significant_zones, gather_zones, nearest_opposite_zone, take_profit_zone

TradeDirection = Literal["buy", "sell"]
Trigger = Literal["H1_absorption", "M15_mss"]

MIN_RR_TO_FTA = 1.0
MIN_RR_FOR_MARKET_ENTRY = 2.0

# Франкфурт+Лондон+NY, платформенное время (UTC+3), без подстройки под DST —
# см. докстринг модуля. Объединённое окно почти во весь день, кроме
# "мёртвых" азиатских часов.
WORKING_SESSION_START_HOUR = 10
WORKING_SESSION_END_HOUR = 25  # 25 = 01:00 следующих суток (NY close)


def in_working_session(ts: pd.Timestamp) -> bool:
    hour = ts.hour
    return hour >= WORKING_SESSION_START_HOUR or hour < (WORKING_SESSION_END_HOUR - 24)


@dataclass
class Signal:
    symbol: str
    trade_direction: TradeDirection
    trigger: Trigger
    signal_time: datetime
    entry_price: float
    entry_type: Literal["market", "pending"]
    stop_price: float
    fta: Zone
    target: Zone
    rr_to_fta: float
    rr_to_target: float
    unfiltered_news: bool = field(default=True)


def _as_of_index(higher_tf_time: pd.Series, ts: pd.Timestamp) -> Optional[int]:
    """Индекс последнего бара старшего ТФ с time <= ts — выравнивание
    таймфреймов без забегания вперёд (searchsorted на отсортированной серии)."""
    pos = higher_tf_time.searchsorted(ts, side="right") - 1
    return int(pos) if pos >= 0 else None


def _nearest_h4_fractal_stop(fractals_h4: pd.DataFrame, trade_direction: TradeDirection, as_of_idx: int) -> Optional[float]:
    """Стоп — "за фракталом H4" (подтверждено трейдером, без указания буфера
    сверх самого уровня фрактала — буфер не формализуем, ставим строго на
    цену фрактала)."""
    if trade_direction == "buy":
        lows = fractals_h4.loc[fractals_h4["is_fractal_low"] & (fractals_h4.index + 1 <= as_of_idx)]
        if lows.empty:
            return None
        return float(lows["low"].iloc[-1])
    else:
        highs = fractals_h4.loc[fractals_h4["is_fractal_high"] & (fractals_h4.index + 1 <= as_of_idx)]
        if highs.empty:
            return None
        return float(highs["high"].iloc[-1])


def _compute_rr(entry: float, stop: float, target_price: float, trade_direction: TradeDirection) -> float:
    risk = abs(entry - stop)
    if risk == 0:
        return 0.0
    reward = (target_price - entry) if trade_direction == "buy" else (entry - target_price)
    return reward / risk


def _target_price(zone: Zone, trade_direction: TradeDirection) -> float:
    return zone.bottom if trade_direction == "buy" else zone.top


def _build_trade_plan(
    trade_direction: TradeDirection,
    current_price: float,
    h4_fractals: pd.DataFrame,
    h4_zones: list[Zone],
    h4_as_of_idx: int,
) -> Optional[dict]:
    stop = _nearest_h4_fractal_stop(h4_fractals, trade_direction, h4_as_of_idx)
    if stop is None:
        return None

    fta = nearest_opposite_zone(h4_zones, trade_direction, current_price, h4_as_of_idx)
    if fta is None:
        return None

    rr_to_fta = _compute_rr(current_price, stop, _target_price(fta, trade_direction), trade_direction)
    if rr_to_fta < MIN_RR_TO_FTA:
        return None

    target = take_profit_zone(h4_zones, trade_direction, current_price, fta, h4_as_of_idx)
    target_price = _target_price(target, trade_direction)

    market_rr = _compute_rr(current_price, stop, target_price, trade_direction)
    if market_rr >= MIN_RR_FOR_MARKET_ENTRY:
        entry_price, entry_type = current_price, "market"
    else:
        # Вход_отложка = (Тейк + 2×Стоп) / 3 — цена, где RR ровно 1:2 (см.
        # STRATEGY_1H_MANIPULATION.md). Формула линейна, одинакова для buy/sell.
        entry_price = (target_price + 2 * stop) / 3
        entry_type = "pending"

    return dict(
        entry_price=entry_price,
        entry_type=entry_type,
        stop_price=stop,
        fta=fta,
        target=target,
        rr_to_fta=rr_to_fta,
        rr_to_target=_compute_rr(entry_price, stop, target_price, trade_direction),
    )


def _zone_matches_price(zone: Zone, price: float, trade_direction: TradeDirection) -> bool:
    wanted_direction = "up" if trade_direction == "buy" else "down"
    return zone.direction == wanted_direction and zone.bottom <= price <= zone.top


def generate_signals(symbol: str, date_from: datetime, date_to: datetime, lookback: timedelta = timedelta(days=120)) -> list[Signal]:
    fetch_from = date_from - lookback
    log(f"{symbol}: качаю бары MN1/D1/H4/H1/M15 [{fetch_from:%Y-%m-%d}..{date_to:%Y-%m-%d}]")
    # MN1 — с большим лукбэком: месячному фракталу нужны месяцы истории,
    # 120 дней дали бы всего ~4 свечи.
    mn1 = price_data.fetch_bars(symbol, "MN1", date_from - timedelta(days=5 * 365), date_to)
    d1 = price_data.fetch_bars(symbol, "D1", fetch_from, date_to)
    h4 = price_data.fetch_bars(symbol, "H4", fetch_from, date_to)
    h1 = price_data.fetch_bars(symbol, "H1", fetch_from, date_to)
    m15 = price_data.fetch_bars(symbol, "M15", fetch_from, date_to)
    log(f"{symbol}: бары готовы — MN1={len(mn1)} D1={len(d1)} H4={len(h4)} H1={len(h1)} M15={len(m15)}")

    # Фильтр снятия месячного фрактала (правило трейдера 2026-07-02): после
    # снятия не входим, пока уровень не пройден дневным имбалансом.
    monthly_blocks = monthly_sweep_blocks(mn1, d1)
    share = blocked_share(monthly_blocks, pd.Timestamp(date_from), pd.Timestamp(date_to))
    log(
        f"{symbol}: месячный фильтр — блокировок {len(monthly_blocks)}, "
        f"закрыто {share * 100:.0f}% тестируемого периода"
    )

    log(f"{symbol}: считаю фракталы и зоны (FVG/OB/фрактал-ликвидность)...")
    fractals_d1 = find_fractals(d1)
    fractals_h4 = find_fractals(h4)
    fractals_h1 = find_fractals(h1)
    fractals_m15 = find_fractals(m15)
    # filter_significant_zones: без него зона находится почти на каждом
    # H4-баре (сплошной ценовой шум) — см. ДОПУЩЕНИЕ в zones.py, найдено
    # верификацией на реальных данных, не подтверждено трейдером буквально.
    all_h4_zones = gather_zones(h4, fractals_h4)
    h4_zones = filter_significant_zones(all_h4_zones, h4)
    log(f"{symbol}: зон найдено {len(all_h4_zones)}, значимых (после фильтра) {len(h4_zones)}")

    signals: list[Signal] = []

    def bias_at(ts: pd.Timestamp) -> Optional[tuple[str, int]]:
        """Дневной контекст должен совпадать с H1 order flow (см. правило
        трейдера) — возвращает (направление, h4_as_of_idx) или None."""
        d1_idx = _as_of_index(d1["time"], ts)
        h1_idx = _as_of_index(h1["time"], ts)
        h4_idx = _as_of_index(h4["time"], ts)
        if d1_idx is None or h1_idx is None or h4_idx is None:
            return None
        # legs=1 для дневного контекста — см. ДОПУЩЕНИЕ в structure_legs():
        # с legs=2 (тем же порогом, что для H1) дневной фильтр резолвился
        # только 17% дней и блокировал все сигналы в окне реальных сделок.
        daily_dir = structure_legs(fractals_d1, d1_idx, legs=1)
        h1_dir = structure_legs(fractals_h1, h1_idx, legs=2)
        if daily_dir is None or h1_dir is None or daily_dir != h1_dir:
            return None
        return h1_dir, h4_idx

    # --- H1-путь: поглощение манипуляции ---
    log(f"{symbol}: прохожу H1-путь ({len(h1)} баров)...")
    h1_progress_step = max(len(h1) // 10, 1)
    for i in range(1, len(h1)):
        if i % h1_progress_step == 0:
            log(f"{symbol}: H1-путь {i}/{len(h1)} ({i * 100 // len(h1)}%), сигналов пока: {len(signals)}")
        ts = h1["time"].iloc[i]
        if ts < date_from or ts > date_to or not in_working_session(ts):
            continue
        if is_blocked(ts, monthly_blocks):
            continue
        bias = bias_at(ts)
        if bias is None:
            continue
        direction, h4_idx = bias
        trade_direction: TradeDirection = "buy" if direction == "up" else "sell"

        # ДОПУЩЕНИЕ №1: прокол ближайшего противоположного H1-фрактала
        # тенью и закрытие ЭТОЙ ЖЕ свечи обратно за уровень (sweep+reject).
        opp_fractals = fractals_h1.loc[
            (fractals_h1["is_fractal_low" if trade_direction == "buy" else "is_fractal_high"]) & (fractals_h1.index + 1 <= i)
        ]
        if opp_fractals.empty:
            continue
        level = float(opp_fractals["low" if trade_direction == "buy" else "high"].iloc[-1])
        bar = h1.iloc[i]
        swept_and_rejected = (
            (bar["low"] < level < bar["close"]) if trade_direction == "buy" else (bar["high"] > level > bar["close"])
        )
        if not swept_and_rejected:
            continue

        zone_hit = any(_zone_matches_price(z, bar["close"], trade_direction) for z in h4_zones if z.formed_at_idx <= h4_idx)
        if not zone_hit:
            continue

        plan = _build_trade_plan(trade_direction, float(bar["close"]), fractals_h4, h4_zones, h4_idx)
        if plan is None:
            continue
        signals.append(Signal(symbol=symbol, trade_direction=trade_direction, trigger="H1_absorption", signal_time=ts, **plan))

    # --- M15-путь: слом структуры (MSS) внутри той же H4-зоны (допущение №2) ---
    h1_signals_count = len(signals)
    log(f"{symbol}: H1-путь готов — {h1_signals_count} сигналов. Прохожу M15-путь ({len(m15)} баров)...")
    m15_progress_step = max(len(m15) // 10, 1)
    for j in range(1, len(m15)):
        if j % m15_progress_step == 0:
            log(f"{symbol}: M15-путь {j}/{len(m15)} ({j * 100 // len(m15)}%), сигналов на этом пути пока: {len(signals) - h1_signals_count}")
        ts = m15["time"].iloc[j]
        if ts < date_from or ts > date_to or not in_working_session(ts):
            continue
        if is_blocked(ts, monthly_blocks):
            continue
        bias = bias_at(ts)
        if bias is None:
            continue
        direction, h4_idx = bias
        trade_direction: TradeDirection = "buy" if direction == "up" else "sell"

        # M15 перед сломом должен двигаться ПРОТИВ старшего bias (коррекция),
        # слом = возврат к направлению bias — стандартная механика MSS-входа.
        m15_idx = _as_of_index(m15["time"], ts)
        if m15_idx is None:
            continue
        counter_direction = "down" if direction == "up" else "up"
        m15_dir = structure_legs(fractals_m15, m15_idx)
        if m15_dir != counter_direction:
            continue
        # "Слом произошёл ИМЕННО на этом баре" — а не просто "где-то в прошлом,
        # раньше j" (detect_structure_break возвращает ПЕРВЫЙ слом в окне, а
        # окно от последнего confirmed фрактала до as_of_idx часто уже
        # содержит более ранний слом к тому моменту, как m15_dir успевает
        # устояться в counter_direction — сравниваем состояние на j-1 и j).
        already_broken = detect_structure_break(m15, fractals_m15, counter_direction, m15_idx - 1) is not None
        if already_broken:
            continue
        break_idx = detect_structure_break(m15, fractals_m15, counter_direction, m15_idx)
        if break_idx != j:
            continue

        bar = m15.iloc[j]
        zone_hit = any(_zone_matches_price(z, bar["close"], trade_direction) for z in h4_zones if z.formed_at_idx <= h4_idx)
        if not zone_hit:
            continue

        plan = _build_trade_plan(trade_direction, float(bar["close"]), fractals_h4, h4_zones, h4_idx)
        if plan is None:
            continue
        signals.append(Signal(symbol=symbol, trade_direction=trade_direction, trigger="M15_mss", signal_time=ts, **plan))

    signals.sort(key=lambda s: s.signal_time)
    return signals
