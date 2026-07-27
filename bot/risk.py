"""Риск-менеджер: защиты (guards) + вызов расчёта лота."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .config import Config
from .model import Action, OrderKind, Signal
from .sizing import SizingResult, SymbolSpec, compute_lots
from .state import State


@dataclass
class Decision:
    allow: bool
    reason: str
    lots: float = 0.0
    mt5_symbol: Optional[str] = None
    sizing: Optional[SizingResult] = None


def _within_trading_hours(windows: list, now: Optional[datetime] = None) -> bool:
    if not windows:
        return True
    now = now or datetime.now()
    hm = now.hour * 100 + now.minute
    for w in windows:
        try:
            a, b = w.split("-")
            if int(a) <= hm <= int(b):
                return True
        except ValueError:
            continue
    return False


class RiskManager:
    def __init__(self, cfg: Config, state: State):
        self.cfg = cfg
        self.state = state
        self.g = cfg.guards or {}
        self.r = cfg.risk or {}

    def evaluate_entry(self, sig: Signal, account, positions, spec: SymbolSpec,
                       market_price: Optional[float] = None) -> Decision:
        """account: объект с .equity; positions: список открытых позиций брокера.

        market_price — текущая цена исполнения для РЫНОЧНОГО ордера. Если
        передана, лот считается от неё, а не от цены из сигнала: рынок мог уйти
        с момента срабатывания алерта, и тогда реальное расстояние до SL больше
        заявленного, а риск — выше заданного процента. Для limit/stop ордеров
        цена известна заранее, поэтому там всегда берётся sig.entry.
        """
        mt5_symbol = self.cfg.mt5_symbol(sig.symbol_tv) or spec.name

        allowed = self.g.get("allowed_symbols") or []
        if allowed and sig.symbol_tv not in allowed:
            return Decision(False, f"символ {sig.symbol_tv} не в allowed_symbols")

        if not _within_trading_hours(self.cfg.trading_hours):
            return Decision(False, "вне торговых часов")

        total = len(positions)
        if total >= int(self.g.get("max_open_positions_total", 999)):
            return Decision(False, f"достигнут лимит позиций всего ({total})")

        per_sym = sum(1 for p in positions if p.symbol == mt5_symbol)
        if per_sym >= int(self.g.get("max_open_positions_per_symbol", 999)):
            return Decision(False, f"уже есть позиция по {mt5_symbol}")

        # Дневной kill-switch
        start_eq = self.state.start_of_day_equity(account.equity)
        max_loss_pct = float(self.g.get("daily_max_loss_pct", 0) or 0)
        if max_loss_pct > 0 and start_eq > 0:
            dd_pct = (start_eq - account.equity) / start_eq * 100.0
            if dd_pct >= max_loss_pct:
                return Decision(False,
                                f"дневной стоп: просадка {dd_pct:.2f}% >= {max_loss_pct}%")

        # Размер лота: для рынка — от цены исполнения, для limit/stop — от sig.entry
        entry_price = sig.entry
        if sig.order_kind is OrderKind.MARKET and market_price:
            entry_price = market_price
        res = compute_lots(
            equity=account.equity,
            risk_pct=self.cfg.risk_pct(sig.strategy),
            entry=entry_price,
            sl=sig.sl,
            spec=spec,
            skip_if_below_min=bool(self.r.get("skip_if_below_min_lot", True)),
            max_lot=self.r.get("max_lot_per_trade"),
        )
        if not res.ok:
            return Decision(False, f"сайзинг: {res.reason}", sizing=res, mt5_symbol=mt5_symbol)

        return Decision(True, "ok", lots=res.lots, mt5_symbol=mt5_symbol, sizing=res)

    def is_duplicate(self, sig: Signal) -> bool:
        window = float(self.g.get("dedup_window_sec", 120))
        return self.state.is_duplicate(sig.dedup_key(), window)
