"""Метрики бэктеста в R-мультипликаторах — win rate, profit factor,
expectancy, max drawdown. Честность по размеру выборки — тот же принцип,
что в src/ai_review/prompts.py (min_sample_size), берётся из того же
config.yaml: ai_review.min_sample_size, чтобы не держать два разных
порога честности в проекте."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .engine import TradeResult


@dataclass
class BacktestMetrics:
    n_signals: int
    n_trades: int
    n_unfilled: int
    n_open_at_end: int
    n_skipped: int
    n_wins: int
    n_losses: int
    n_be: int
    win_rate_pct: Optional[float]
    profit_factor: Optional[float]
    expectancy_r: Optional[float]
    max_drawdown_r: Optional[float]
    total_r: Optional[float]
    min_sample_size: int

    @property
    def is_small_sample(self) -> bool:
        return self.n_trades < self.min_sample_size


def compute_metrics(results: list[TradeResult], min_sample_size: int = 30) -> BacktestMetrics:
    trades = [r for r in results if r.is_trade]
    n_unfilled = sum(1 for r in results if r.exit_reason == "unfilled")
    n_open = sum(1 for r in results if r.exit_reason == "open_at_data_end")
    n_skipped = sum(1 for r in results if r.exit_reason == "skipped_position_open")
    r_values = [r.r_multiple for r in trades]
    n = len(r_values)

    wins = [v for v in r_values if v > 0]
    losses = [v for v in r_values if v < 0]
    n_be = n - len(wins) - len(losses)

    # Win rate = TP / (TP + SL): безубыток не считается ни победой, ни
    # поражением — та же конвенция, что в /setup (SetupStats.win_rate_pct).
    n_decided = len(wins) + len(losses)
    win_rate = round(len(wins) / n_decided * 100, 1) if n_decided else None
    if losses:
        profit_factor = round(sum(wins) / abs(sum(losses)), 2) if wins else 0.0
    else:
        profit_factor = None  # нет убыточных сделок — соотношение не определено, а не "бесконечность"
    expectancy = round(sum(r_values) / n, 3) if n else None
    max_dd = _max_drawdown(r_values)
    total_r = round(sum(r_values), 2) if n else None

    return BacktestMetrics(
        n_signals=len(results),
        n_trades=n,
        n_unfilled=n_unfilled,
        n_open_at_end=n_open,
        n_skipped=n_skipped,
        n_wins=len(wins),
        n_losses=len(losses),
        n_be=n_be,
        win_rate_pct=win_rate,
        profit_factor=profit_factor,
        expectancy_r=expectancy,
        max_drawdown_r=max_dd,
        total_r=total_r,
        min_sample_size=min_sample_size,
    )


def _max_drawdown(r_values: list[float]) -> Optional[float]:
    if not r_values:
        return None
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in r_values:
        equity += r
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return round(max_dd, 2)


def format_report(
    metrics: BacktestMetrics,
    symbol: str,
    assumptions: list[str],
    deposit_usd: float = 10_000.0,
    risk_pct: float = 1.0,
) -> str:
    lines = [
        f"Бэктест «1Н поглощение манипуляции» — {symbol}",
        "",
        "Допущения этого прогона (не подтверждены трейдером буквально, см. STRATEGY_1H_MANIPULATION.md):",
        *[f"  - {a}" for a in assumptions],
        "",
        f"Сигналов найдено: {metrics.n_signals}",
        f"  из них сделок (вход исполнился): {metrics.n_trades}",
        f"  отложка не исполнилась (unfilled): {metrics.n_unfilled}",
        f"  сделка не завершена к концу данных: {metrics.n_open_at_end}",
        f"  пропущено (позиция уже открыта): {metrics.n_skipped}",
        "",
    ]

    if metrics.n_trades == 0:
        lines.append("Ни одной завершённой сделки — метрики считать не на чем.")
        return "\n".join(lines)

    win_rate_str = f"{metrics.win_rate_pct}%" if metrics.win_rate_pct is not None else "н/д (нет сделок с исходом TP/SL)"
    lines += [
        f"Исходы: TP {metrics.n_wins} / SL {metrics.n_losses} / BE {metrics.n_be}",
        f"Win rate (TP из TP+SL, BE не считается): {win_rate_str}",
        f"Profit factor: {metrics.profit_factor if metrics.profit_factor is not None else 'н/д (нет убыточных сделок)'}",
        f"Expectancy: {metrics.expectancy_r}R на сделку",
        f"Max drawdown: {metrics.max_drawdown_r}R",
    ]

    if metrics.total_r is not None:
        profit_pct = metrics.total_r * risk_pct
        profit_usd = deposit_usd * profit_pct / 100
        lines += [
            "",
            f"Итог за период: {metrics.total_r:+.2f}R = {profit_pct:+.2f}% от депозита "
            f"(при депозите ${deposit_usd:,.0f} и фиксированном риске {risk_pct}% на сделку, "
            f"без реинвеста: {profit_usd:+,.0f}$)",
        ]

    if metrics.is_small_sample:
        lines.append(
            f"\nВыборка маленькая ({metrics.n_trades} < {metrics.min_sample_size}) — "
            "это не статистически значимый результат, а ориентир. Не делай выводов "
            "\"стратегия рабочая/нерабочая\" на этом объёме."
        )

    return "\n".join(lines)
