"""Тесты движка бэктеста (src/backtest/engine.py) на синтетических
сигналах и M15-барах — без похода в MT5."""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from src.backtest.engine import run_backtest, simulate_trade
from src.backtest.signals import Signal
from src.backtest.structure import Zone

T0 = datetime(2026, 1, 1, 12, 0)


def _m15_bars(rows: list[tuple[float, float, float, float]], start: datetime = T0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "time": [start + timedelta(minutes=15 * i) for i in range(len(rows))],
            "open": [r[0] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[2] for r in rows],
            "close": [r[3] for r in rows],
            "tick_volume": [100] * len(rows),
        }
    )


def _buy_signal(entry_type="market", entry_price=1.10, signal_time=T0) -> Signal:
    fta = Zone("OB", "down", top=1.14, bottom=1.12, formed_at_idx=0)
    target = Zone("FVG", "down", top=1.22, bottom=1.20, formed_at_idx=0)
    return Signal(
        symbol="EURUSD",
        trade_direction="buy",
        trigger="H1_absorption",
        signal_time=signal_time,
        entry_price=entry_price,
        entry_type=entry_type,
        stop_price=1.05,
        fta=fta,
        target=target,
        rr_to_fta=(1.12 - 1.10) / (1.10 - 1.05),
        rr_to_target=(1.20 - 1.10) / (1.10 - 1.05),
    )


def test_market_entry_hits_target():
    signal = _buy_signal()
    bars = _m15_bars(
        [
            (1.10, 1.11, 1.09, 1.10),
            (1.10, 1.13, 1.105, 1.12),  # достигает FTA (1.12) -> BE, но low не задевает новый стоп (1.10)
            (1.12, 1.21, 1.11, 1.20),  # достигает target (1.20)
        ]
    )
    result = simulate_trade(signal, bars)
    assert result.exit_reason == "target"
    assert result.r_multiple == signal.rr_to_target
    assert result.is_trade


def test_market_entry_hits_stop_before_be():
    signal = _buy_signal()
    bars = _m15_bars(
        [
            (1.10, 1.10, 1.05, 1.06),  # сразу пробивает стоп 1.05, FTA не достигнут
        ]
    )
    result = simulate_trade(signal, bars)
    assert result.exit_reason == "stop"
    assert result.r_multiple == -1.0


def test_be_triggered_then_stop_gives_zero_r():
    signal = _buy_signal()
    bars = _m15_bars(
        [
            (1.10, 1.13, 1.09, 1.12),  # достигает FTA -> стоп подтягивается в БУ (1.10)
            (1.12, 1.12, 1.08, 1.09),  # уходит ниже 1.10 -> стоп в БУ сработал
        ]
    )
    result = simulate_trade(signal, bars)
    assert result.exit_reason == "be"
    assert result.r_multiple == 0.0


def test_pending_order_fills_on_touch():
    signal = _buy_signal(entry_type="pending", entry_price=1.08)
    bars = _m15_bars(
        [
            (1.10, 1.10, 1.09, 1.095),  # цена ещё выше отложки
            (1.095, 1.096, 1.075, 1.09),  # затрагивает 1.08 -> исполнение
            (1.09, 1.30, 1.20, 1.25),  # уходит к тейку, low выше нового БУ-стопа (1.08)
        ]
    )
    result = simulate_trade(signal, bars)
    assert result.fill_time is not None
    assert result.exit_reason == "target"


def test_pending_order_unfilled_when_price_runs_to_target_first():
    """Цена уходит сразу к тейку, ни разу не откатившись до цены отложки (1.08,
    ниже рынка) — ордер так и не исполнился, сделки не было."""
    signal = _buy_signal(entry_type="pending", entry_price=1.08)
    bars = _m15_bars(
        [
            (1.10, 1.22, 1.10, 1.20),  # low=1.10 никогда не касается 1.08, high пробивает тейк 1.20
        ]
    )
    result = simulate_trade(signal, bars)
    assert result.exit_reason == "unfilled"
    assert result.r_multiple is None
    assert not result.is_trade


def test_open_at_data_end_when_bars_run_out():
    signal = _buy_signal()
    bars = _m15_bars([(1.10, 1.11, 1.09, 1.10)])  # ничего не срабатывает, бары кончаются
    result = simulate_trade(signal, bars)
    assert result.exit_reason == "open_at_data_end"
    assert result.r_multiple is None


def test_run_backtest_skips_signal_while_position_open():
    """Одна позиция за раз: второй сигнал приходит, пока первая сделка ещё
    открыта — он пропускается, а не считается отдельной сделкой (иначе одна
    торговая идея, дающая сигнал на нескольких соседних барах, учитывается
    в метриках многократно)."""
    first = _buy_signal()  # T0
    duplicate = _buy_signal(signal_time=T0 + timedelta(minutes=15))
    bars = _m15_bars(
        [
            (1.10, 1.11, 1.09, 1.10),
            (1.10, 1.11, 1.09, 1.10),  # дубль приходит здесь — первая сделка ещё открыта
            (1.10, 1.10, 1.05, 1.06),  # стоп первой сделки только на третьем баре
        ]
    )
    results = run_backtest([first, duplicate], bars)
    assert [r.exit_reason for r in results] == ["stop", "skipped_position_open"]
    assert results[1].r_multiple is None
    assert not results[1].is_trade


def test_run_backtest_allows_signal_after_position_closed():
    first = _buy_signal()  # стоп сработает на первом же баре
    later = _buy_signal(signal_time=T0 + timedelta(minutes=30))
    bars = _m15_bars(
        [
            (1.10, 1.10, 1.05, 1.06),  # первая сделка: сразу стоп
            (1.06, 1.07, 1.055, 1.06),
            (1.06, 1.10, 1.05, 1.06),  # вторая сделка: тоже стоп (low=1.05)
        ]
    )
    results = run_backtest([first, later], bars)
    assert [r.exit_reason for r in results] == ["stop", "stop"]
    assert all(r.is_trade for r in results)


def test_run_backtest_unfilled_pending_does_not_block_next_signal():
    """Неисполнившаяся отложка позицию не занимает — следующий сигнал торгуется."""
    pending = _buy_signal(entry_type="pending", entry_price=1.08)  # цена до 1.08 не дойдёт
    later = _buy_signal(signal_time=T0 + timedelta(minutes=15))
    bars = _m15_bars(
        [
            (1.10, 1.22, 1.10, 1.20),  # отложка 1.08 не тронута, high пробивает тейк -> инвалидация
            (1.10, 1.10, 1.05, 1.06),  # вторая (market) сделка: стоп
        ]
    )
    results = run_backtest([pending, later], bars)
    assert [r.exit_reason for r in results] == ["unfilled", "stop"]
