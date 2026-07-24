"""Собирает risk-чеки в готовый текстовый отчёт — то, что отдаёт /risk в боте."""
from __future__ import annotations

from datetime import date
from typing import Iterable

from src.mt5_import.models import Trade
from .limits import AccountRiskConfig, check_daily_loss, check_overall_drawdown, running_balance_curve


def build_risk_report(config: AccountRiskConfig, trades: list[Trade]) -> str:
    balance_curve = running_balance_curve(trades, config.account_size)
    current_balance = balance_curve[-1]

    trades_today = [t for t in trades if t.close_time and t.close_time.date() == date.today()]
    daily_check = check_daily_loss(config, trades_today, balance_at_day_start=current_balance)
    dd_check = check_overall_drawdown(config, balance_curve)

    lines = [
        f"Риск-отчёт: {config.name} ({config.challenge_type})",
        f"Баланс: {current_balance:,.2f} / старт {config.account_size:,.2f}",
        "",
        _format_check(daily_check),
        _format_check(dd_check),
    ]
    if daily_check.breached or dd_check.breached:
        lines.append("")
        lines.append("НАРУШЕНИЕ ЛИМИТА — остановить торговлю на сегодня.")
    return "\n".join(lines)


def _format_check(check) -> str:
    status = "НАРУШЕН" if check.breached else "ok"
    return f"{check.label}: {check.used_pct}% / лимит {check.limit_pct}% (запас {check.remaining_pct}%) — {status}"
