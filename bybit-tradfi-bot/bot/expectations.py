"""Загрузка коридоров бэктеста (expectations.yaml).

Отдаёт по-стратегийные ожидания в удобных для сравнения структурах. Логика
сравнения «факт ↔ коридор» и вердикты OK/ВНИМАНИЕ/ПРОБЛЕМА живут в bot/report.py
(они уже про отчёт, а не про загрузку).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Band:
    """Коридор [lo, hi]. Оба конца включительно."""
    lo: float
    hi: float

    def contains(self, x: float) -> bool:
        return self.lo <= x <= self.hi

    def width(self) -> float:
        return self.hi - self.lo


@dataclass
class StrategyExpectation:
    key: str                          # smc | crt | asweep
    label: str                        # SMC | CRT | ASIA (как в истории MT5)
    instruments: list
    risk_pct: float
    win_rate: Optional[Band]
    profit_factor: Optional[Band]
    trades_per_month: Optional[Band]
    be_share: Optional[Band]
    max_stop_streak: Optional[int]
    worst_month_pct: Optional[float]


@dataclass
class Expectations:
    min_sample: int
    risk_overshoot_pp: float
    by_label: dict            # LABEL -> StrategyExpectation
    by_key: dict              # key   -> StrategyExpectation

    def for_label(self, label: str) -> Optional[StrategyExpectation]:
        return self.by_label.get(label)


def _band(v) -> Optional[Band]:
    if not v:
        return None
    try:
        lo, hi = float(v[0]), float(v[1])
        return Band(min(lo, hi), max(lo, hi))
    except (TypeError, ValueError, IndexError):
        return None


def expectations_path(path: Optional[str] = None) -> Path:
    p = Path(path or (ROOT / "expectations.yaml"))
    return p if p.is_absolute() else (ROOT / p)


def load(path: Optional[str] = None) -> Expectations:
    p = expectations_path(path)
    with open(p, "r", encoding="utf-8") as f:
        y = yaml.safe_load(f) or {}

    meta = y.get("meta", {}) or {}
    by_label: dict = {}
    by_key: dict = {}
    for key, s in (y.get("strategies", {}) or {}).items():
        s = s or {}
        exp = StrategyExpectation(
            key=key,
            label=str(s.get("label", key)).strip(),
            instruments=list(s.get("instruments", []) or []),
            risk_pct=float(s.get("risk_pct", 0) or 0),
            win_rate=_band(s.get("win_rate")),
            profit_factor=_band(s.get("profit_factor")),
            trades_per_month=_band(s.get("trades_per_month")),
            be_share=_band(s.get("be_share")),
            max_stop_streak=(int(s["max_stop_streak"])
                             if s.get("max_stop_streak") is not None else None),
            worst_month_pct=(float(s["worst_month_pct"])
                             if s.get("worst_month_pct") is not None else None),
        )
        by_key[key] = exp
        by_label[exp.label] = exp

    return Expectations(
        min_sample=int(meta.get("min_sample", 20)),
        risk_overshoot_pp=float(meta.get("risk_overshoot_pp", 0.3)),
        by_label=by_label,
        by_key=by_key,
    )
