"""Главный движок: сигнал → парсинг → дедуп → риск → исполнение."""
from __future__ import annotations

import logging

from .broker_mt5 import MT5Broker
from .config import Config
from .model import Action, OrderKind, Signal
from .parser import parse
from .risk import RiskManager
from .state import State

log = logging.getLogger("bot")


class Engine:
    def __init__(self, cfg: Config, broker: MT5Broker, state: State):
        self.cfg = cfg
        self.broker = broker
        self.state = state
        self.risk = RiskManager(cfg, state)
        self.exits = cfg.exits or {}

    def handle_raw(self, raw_message: str) -> None:
        """Обработать одно сырое alert_message."""
        sig = parse(raw_message)
        if sig is None:
            log.warning("Не распознан алерт: %r", raw_message[:200])
            return
        log.info("СИГНАЛ: %s", sig)

        if self.risk.is_duplicate(sig):
            log.info("  ↳ дубликат, пропуск (%s)", sig.dedup_key())
            return

        try:
            if sig.action is Action.ENTRY:
                self._handle_entry(sig)
            elif sig.action is Action.BREAKEVEN:
                self._handle_breakeven(sig)
            elif sig.action is Action.EXIT:
                self._handle_exit(sig)
        finally:
            self.state.mark_seen(sig.dedup_key())

    # ── Вход ──────────────────────────────────────────────────────────────────
    def _handle_entry(self, sig: Signal) -> None:
        mt5_symbol = self.cfg.mt5_symbol(sig.symbol_tv)
        if not mt5_symbol:
            log.error("  ↳ нет карты символа для %s (config.yaml → symbol_map)", sig.symbol_tv)
            return

        spec = self.broker.symbol_spec(mt5_symbol)
        if spec is None:
            log.error("  ↳ MT5 не знает символ %s", mt5_symbol)
            return

        account = self.broker.account()
        positions = self.broker.positions()

        # Рыночный ордер исполнится по текущей цене, а не по цене из алерта.
        # Считаем лот от неё, иначе ушедший рынок молча раздувает риск.
        market_price = None
        if sig.order_kind is OrderKind.MARKET and sig.side is not None:
            market_price = self.broker.current_price(mt5_symbol, sig.side)
            if market_price and sig.entry:
                slip = abs(market_price - sig.entry)
                if sig.sl and abs(sig.entry - sig.sl) > 0:
                    slip_pct = slip / abs(sig.entry - sig.sl) * 100.0
                    if slip_pct >= 5.0:
                        log.warning("  ↳ рынок ушёл от сигнала: %s → %s "
                                    "(%.0f%% от дистанции до SL) — лот считаю "
                                    "от текущей цены", sig.entry, market_price,
                                    slip_pct)

        decision = self.risk.evaluate_entry(sig, account, positions, spec,
                                            market_price=market_price)

        if not decision.allow:
            log.info("  ↳ ОТКАЗ риск-менеджера: %s", decision.reason)
            return

        s = decision.sizing
        log.info("  ↳ РАЗРЕШЕНО: %s %.4f лот на %s | риск=%.2f %s | убыток/лот=%.2f",
                 sig.order_kind.value, decision.lots, decision.mt5_symbol,
                 s.risk_amount, account.currency, s.loss_per_lot)

        if self.cfg.dry_run:
            log.info("  ↳ [DRY_RUN] ордер НЕ отправлен")
            return

        res = self.broker.place_entry(sig, decision.lots, decision.mt5_symbol)
        if res.ok:
            log.info("  ↳ ✅ ОРДЕР ОТПРАВЛЕН ticket=%s (%s)", res.ticket, res.detail)
        else:
            log.error("  ↳ ❌ ОШИБКА ОРДЕРА: %s", res.detail)

    # ── Безубыток ───────────────────────────────────────────────────────────────
    def _handle_breakeven(self, sig: Signal) -> None:
        if self.exits.get("on_breakeven", "move_sl_to_entry") != "move_sl_to_entry":
            log.info("  ↳ БУ проигнорирован (config exits.on_breakeven)")
            return
        mt5_symbol = self.cfg.mt5_symbol(sig.symbol_tv)
        if not mt5_symbol or sig.side is None:
            log.warning("  ↳ БУ: нет символа/стороны")
            return
        if self.cfg.dry_run:
            log.info("  ↳ [DRY_RUN] перенёс бы SL в безубыток %s %s", mt5_symbol, sig.side.value)
            return
        res = self.broker.modify_sl_to_entry(mt5_symbol, sig.side)
        log.info("  ↳ БУ %s: %s", "✅" if res.ok else "⚠️", res.detail)

    # ── Выход ─────────────────────────────────────────────────────────────────
    def _handle_exit(self, sig: Signal) -> None:
        mode = self.exits.get("on_exit_alert", "ignore")
        if mode != "close":
            log.info("  ↳ выход проигнорирован — SL/TP уже на ордере (exits.on_exit_alert=ignore)")
            return
        mt5_symbol = self.cfg.mt5_symbol(sig.symbol_tv)
        if not mt5_symbol or sig.side is None:
            log.warning("  ↳ выход: нет символа/стороны")
            return
        if self.cfg.dry_run:
            log.info("  ↳ [DRY_RUN] закрыл бы позицию %s %s", mt5_symbol, sig.side.value)
            return
        res = self.broker.close_position(mt5_symbol, sig.side)
        log.info("  ↳ закрытие %s: %s", "✅" if res.ok else "⚠️", res.detail)
