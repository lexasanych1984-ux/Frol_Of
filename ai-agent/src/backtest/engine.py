"""Движок бэктеста — проводит каждый Signal через M15-бары вперёд по
времени до срабатывания стопа/BE/тейка, результат — в R (риск сделки = 1R).

Упрощение (внутрибарная неопределённость): если один M15-бар одновременно
затрагивает и стоп, и тейк (широкий бар), считаем, что сработал СНАЧАЛА
стоп — консервативное допущение без тиковых данных, стандартная практика
в bar-based бэктестах. BE-триггер (цена дошла до FTA) проверяется внутри
бара ДО проверки стоп/тейк — то есть если в одном баре сначала логически
"достигается" FTA, а потом стоп, исполнение — по уже подтянутому в БУ стопу.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Optional

import pandas as pd

from .signals import Signal

ExitReason = Literal["target", "be", "stop", "unfilled", "open_at_data_end", "skipped_position_open"]

PENDING_ORDER_TIMEOUT = timedelta(days=4)


@dataclass
class TradeResult:
    signal: Signal
    fill_time: Optional[datetime]
    exit_time: Optional[datetime]
    exit_reason: ExitReason
    r_multiple: Optional[float]  # None для unfilled/open_at_data_end — не входит в метрики

    @property
    def is_trade(self) -> bool:
        return self.r_multiple is not None


def _touches(bar: "pd.Series", level: float, trade_direction: str, side: Literal["stop", "target"]) -> bool:
    if trade_direction == "buy":
        return bar["low"] <= level if side == "stop" else bar["high"] >= level
    else:
        return bar["high"] >= level if side == "stop" else bar["low"] <= level


def _fill_pending(signal: Signal, m15: pd.DataFrame, start_idx: int) -> Optional[int]:
    deadline = signal.signal_time + PENDING_ORDER_TIMEOUT
    for j in range(start_idx, len(m15)):
        bar = m15.iloc[j]
        if bar["time"] > deadline:
            return None
        touched_entry = bar["low"] <= signal.entry_price <= bar["high"]
        if touched_entry:
            return j
        # инвалидация: цена ушла к тейку или пробила стоп раньше, чем добралась до отложки
        if _touches(bar, _zone_price(signal), signal.trade_direction, "target"):
            return None
        if _touches(bar, signal.stop_price, signal.trade_direction, "stop"):
            return None
    return None


def _zone_price(signal: Signal) -> float:
    return signal.target.bottom if signal.trade_direction == "buy" else signal.target.top


def _fta_price(signal: Signal) -> float:
    return signal.fta.bottom if signal.trade_direction == "buy" else signal.fta.top


def simulate_trade(signal: Signal, m15: pd.DataFrame) -> TradeResult:
    # side="left": бар, чьё время РАВНО signal_time (сам сигнальный бар),
    # должен быть включён — со "right" он бы пропускался при точном совпадении.
    start_idx = int(m15["time"].searchsorted(signal.signal_time, side="left"))

    if signal.entry_type == "pending":
        fill_idx = _fill_pending(signal, m15, start_idx)
        if fill_idx is None:
            return TradeResult(signal=signal, fill_time=None, exit_time=None, exit_reason="unfilled", r_multiple=None)
    else:
        fill_idx = start_idx

    target_price = _zone_price(signal)
    fta_price = _fta_price(signal)
    stop = signal.stop_price
    be_triggered = False

    for k in range(fill_idx, len(m15)):
        bar = m15.iloc[k]

        if not be_triggered and _touches(bar, fta_price, signal.trade_direction, "target"):
            be_triggered = True
            stop = signal.entry_price

        hit_target = _touches(bar, target_price, signal.trade_direction, "target")
        hit_stop = _touches(bar, stop, signal.trade_direction, "stop")

        if hit_stop and hit_target:
            hit_target = False  # консервативное допущение: при неоднозначности внутри бара считаем, что стоп сработал первым

        if hit_stop:
            r = 0.0 if be_triggered else -1.0
            reason: ExitReason = "be" if be_triggered else "stop"
            return TradeResult(signal=signal, fill_time=m15["time"].iloc[fill_idx], exit_time=bar["time"], exit_reason=reason, r_multiple=r)
        if hit_target:
            return TradeResult(
                signal=signal,
                fill_time=m15["time"].iloc[fill_idx],
                exit_time=bar["time"],
                exit_reason="target",
                r_multiple=signal.rr_to_target,
            )

    return TradeResult(signal=signal, fill_time=m15["time"].iloc[fill_idx], exit_time=None, exit_reason="open_at_data_end", r_multiple=None)


def run_backtest(signals: list[Signal], m15: pd.DataFrame) -> list[TradeResult]:
    """Одна открытая позиция за раз: пока предыдущая сделка не закрылась,
    новые сигналы пропускаются (exit_reason="skipped_position_open", в
    метрики не входят). Без этого правила сигналы по одному и тому же
    движению шли пачками и каждый считался отдельной сделкой — на годовом
    прогоне EURUSD 4 одинаковых sell за один день учитывались как 4 сделки
    по -1R, раздувая просадку двойным счётом одной торговой идеи.

    Блокирует только исполнившийся вход (fill): неисполнившаяся отложка
    позицию не занимает. Сделка, оставшаяся открытой к концу данных,
    блокирует все сигналы после себя."""
    results: list[TradeResult] = []
    busy_until: Optional[datetime] = None
    for signal in sorted(signals, key=lambda s: s.signal_time):
        if busy_until is not None and signal.signal_time < busy_until:
            results.append(
                TradeResult(signal=signal, fill_time=None, exit_time=None, exit_reason="skipped_position_open", r_multiple=None)
            )
            continue
        result = simulate_trade(signal, m15)
        results.append(result)
        if result.fill_time is not None:
            if result.exit_time is not None:
                busy_until = result.exit_time
            else:  # open_at_data_end — позиция занята до конца данных
                busy_until = m15["time"].iloc[-1] + timedelta(minutes=15)
    return results
