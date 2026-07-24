"""Тесты метрик бэктеста (src/backtest/metrics.py)."""
from __future__ import annotations

from datetime import datetime

from src.backtest.engine import TradeResult
from src.backtest.metrics import compute_metrics, format_report
from src.backtest.signals import Signal
from src.backtest.structure import Zone

T0 = datetime(2026, 1, 1, 12, 0)


def _result(r_multiple: float) -> TradeResult:
    zone = Zone("FVG", "down", top=1.22, bottom=1.20, formed_at_idx=0)
    signal = Signal(
        symbol="EURUSD",
        trade_direction="buy",
        trigger="H1_absorption",
        signal_time=T0,
        entry_price=1.10,
        entry_type="market",
        stop_price=1.05,
        fta=zone,
        target=zone,
        rr_to_fta=1.0,
        rr_to_target=2.0,
    )
    reason = "target" if r_multiple > 0 else ("be" if r_multiple == 0 else "stop")
    return TradeResult(signal=signal, fill_time=T0, exit_time=T0, exit_reason=reason, r_multiple=r_multiple)


def test_win_rate_excludes_breakeven_from_denominator():
    """Win rate = TP/(TP+SL): БУ — не победа и не поражение (та же конвенция,
    что в /setup). 1 тейк, 1 стоп, 2 БУ -> 50%, а не 25%."""
    metrics = compute_metrics([_result(2.0), _result(-1.0), _result(0.0), _result(0.0)])
    assert metrics.n_wins == 1
    assert metrics.n_losses == 1
    assert metrics.n_be == 2
    assert metrics.win_rate_pct == 50.0
    assert metrics.total_r == 1.0


def test_win_rate_none_when_only_breakevens():
    metrics = compute_metrics([_result(0.0), _result(0.0)])
    assert metrics.win_rate_pct is None
    assert metrics.n_be == 2


def test_report_shows_profit_in_percent_of_deposit():
    """+3R при риске 1% на сделку от $10,000 = +3% = +300$ (без реинвеста)."""
    metrics = compute_metrics([_result(2.0), _result(2.0), _result(-1.0)])
    report = format_report(metrics, "EURUSD", assumptions=[], deposit_usd=10_000.0, risk_pct=1.0)
    assert "+3.00R" in report
    assert "+3.00%" in report
    assert "+300$" in report
