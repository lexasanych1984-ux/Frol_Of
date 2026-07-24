"""Риск-лимиты проп-счёта (FundingPips и совместимые).

Приоритет №1 всего проекта: на проп-счетах чаще теряют аккаунт из-за
нарушения лимитов, а не из-за плохой стратегии. Все цифры лимитов —
из config/config.yaml (в Notion этих полей нет, см. schema.py).

Два типа max overall drawdown:
  - "static"   — считается от initial balance, не двигается с профитом
  - "trailing" — считается от пикового equity (плавает вверх по мере роста)
Обязательно уточнить, какой тип у вашего конкретного challenge — это
принципиально разные пороги закрытия счёта.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, Literal, Optional

import yaml

from src.mt5_import.models import Trade


@dataclass
class AccountRiskConfig:
    name: str
    mt5_login: str
    challenge_type: str
    account_size: float
    max_daily_loss_pct: float
    max_overall_drawdown_pct: float
    drawdown_type: Literal["static", "trailing"]
    profit_target_pct: Optional[float] = None
    notion_account_name: Optional[str] = None  # см. matching.filter_pages_by_account


def load_accounts_config(path: str | Path = "config/config.yaml") -> dict[str, AccountRiskConfig]:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    result = {}
    for acc in raw.get("accounts", []):
        cfg = AccountRiskConfig(
            name=acc["name"],
            mt5_login=str(acc["mt5_login"]),
            challenge_type=acc["challenge_type"],
            account_size=float(acc["account_size"]),
            max_daily_loss_pct=float(acc["max_daily_loss_pct"]),
            max_overall_drawdown_pct=float(acc["max_overall_drawdown_pct"]),
            drawdown_type=acc.get("drawdown_type", "static"),
            profit_target_pct=acc.get("profit_target_pct"),
            notion_account_name=acc.get("notion_account_name"),
        )
        result[cfg.mt5_login] = cfg
    return result


def load_ai_review_config(path: str | Path = "config/config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return raw.get("ai_review", {})


@dataclass
class RiskCheck:
    label: str
    limit_pct: float
    used_pct: float
    breached: bool

    @property
    def remaining_pct(self) -> float:
        return max(0.0, self.limit_pct - self.used_pct)


def daily_pnl(trades: Iterable[Trade], day: date) -> float:
    return sum(t.net_pnl for t in trades if t.close_time and t.close_time.date() == day)


def check_daily_loss(config: AccountRiskConfig, trades_today: Iterable[Trade], balance_at_day_start: float) -> RiskCheck:
    pnl = daily_pnl(trades_today, date.today())
    loss_pct = max(0.0, -pnl) / balance_at_day_start * 100 if balance_at_day_start else 0.0
    return RiskCheck(
        label="Daily loss",
        limit_pct=config.max_daily_loss_pct,
        used_pct=round(loss_pct, 2),
        breached=loss_pct >= config.max_daily_loss_pct,
    )


def running_balance_curve(trades: list[Trade], starting_balance: float) -> list[float]:
    """Сортирует сделки по close_time и возвращает кривую баланса (включая старт)."""
    closed = sorted((t for t in trades if t.close_time), key=lambda t: t.close_time)
    curve = [starting_balance]
    balance = starting_balance
    for t in closed:
        balance += t.net_pnl
        curve.append(balance)
    return curve


def check_overall_drawdown(config: AccountRiskConfig, balance_curve: list[float]) -> RiskCheck:
    if not balance_curve:
        return RiskCheck("Overall drawdown", config.max_overall_drawdown_pct, 0.0, False)

    current = balance_curve[-1]
    if config.drawdown_type == "trailing":
        reference = max(balance_curve)
    else:  # static
        reference = config.account_size

    drawdown_pct = max(0.0, (reference - current) / reference * 100) if reference else 0.0
    return RiskCheck(
        label="Overall drawdown",
        limit_pct=config.max_overall_drawdown_pct,
        used_pct=round(drawdown_pct, 2),
        breached=drawdown_pct >= config.max_overall_drawdown_pct,
    )
