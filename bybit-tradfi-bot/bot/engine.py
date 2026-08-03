"""Главный движок: сигнал → парсинг → дедуп → возраст → риск → исполнение."""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Optional

from . import execlog, slippage
from .broker_mt5 import MT5Broker, OrderResult, magic_for
from .config import Config, FreshnessCfg
from .model import Action, OrderKind, Side, Signal
from .parser import parse
from .risk import Decision, RiskManager
from .sizing import SymbolSpec
from .state import State

log = logging.getLogger("bot")


class Engine:
    def __init__(self, cfg: Config, broker: MT5Broker, state: State, metrics=None,
                 notifier=None, exec_log_path=None,
                 risk_overshoot_pp: Optional[float] = None):
        self.cfg = cfg
        self.broker = broker
        self.state = state
        self.risk = RiskManager(cfg, state)
        self.exits = cfg.exits or {}
        # Опциональные счётчики для суточной сводки мониторинга (bot/health.py).
        self.metrics = metrics
        # Мгновенные тревоги (риск выше заданного) — тот же Telegram, что у health.
        self.notifier = notifier
        # Append-only лог проскальзывания: план (алерт) ↔ факт (исполнение).
        self.exec_log_path = exec_log_path or cfg.exec_log_path()
        # Порог тревоги «риск выше заданного» в проц. пунктах.
        self.risk_overshoot_pp = (risk_overshoot_pp
                                  if risk_overshoot_pp is not None else 0.3)
        # Защита протухших сигналов (общая для всех источников). getattr —
        # чтобы движок работал и со «скелетным» Config из юнит-тестов.
        self.freshness: FreshnessCfg = getattr(cfg, "freshness", None) or FreshnessCfg()
        # Тексты нераспознанных алертов, о которых уже предупреждали (анти-шум).
        self._unparsed_seen: set[str] = set()

    def handle_raw(self, raw_message: str, *, received_ts: Optional[float] = None,
                   source: Optional[str] = None, ext_id: Optional[str] = None) -> None:
        """Обработать одно сырое alert_message.

        received_ts — epoch, когда сигнал сработал/принят «наверху» (для гейта
        свежести); source/ext_id — канал и внешний id (для лога/трассировки).
        Вызов только со строкой (dry-run, тесты) остаётся валидным.
        """
        sig = parse(raw_message)
        if sig is None:
            # Рисованные алерты пользователя (Liq/FVG/IDM) тоже имеют webhook и
            # долетают сюда десятками — WARNING на каждый повтор топит лог, и в
            # этом шуме теряется настоящая поломка (сломанный алерт стратегии
            # выглядит так же). Первый раз для каждого текста — WARNING, повторы
            # того же текста за сессию — DEBUG.
            key = raw_message[:200]
            if key in self._unparsed_seen:
                log.debug("Не распознан алерт (повтор): %r", key)
            else:
                self._unparsed_seen.add(key)
                log.warning("Не распознан алерт: %r — бот исполняет только "
                            "сигналы стратегий", key)
            return
        tag = f" [{source}{'#' + ext_id if ext_id else ''}]" if source else ""
        log.info("СИГНАЛ%s: %s", tag, sig)
        if self.metrics is not None:
            self.metrics.on_signal()

        if self.risk.is_duplicate(sig):
            log.info("  ↳ дубликат, пропуск (%s)", sig.dedup_key())
            return

        try:
            if not self._fresh_enough(sig, received_ts, source):
                return
            if sig.action is Action.ENTRY:
                self._handle_entry(sig)
            elif sig.action is Action.BREAKEVEN:
                self._handle_breakeven(sig)
            elif sig.action is Action.EXIT:
                self._handle_exit(sig)
            elif sig.action is Action.CANCEL:
                self._handle_cancel(sig)
        finally:
            self.state.mark_seen(sig.dedup_key())

    # ── Защита протухших сигналов ─────────────────────────────────────────────
    def _fresh_enough(self, sig: Signal, received_ts: Optional[float],
                      source: Optional[str]) -> bool:
        """False → сигнал слишком стар, исполнять нельзя (уведомляем, помечаем seen).

        Вход старше entry_max_age (цена устарела) не исполняется. БУ/выход — с
        бОльшим лимитом manage_max_age: позиция уже открыта, управлять ею и с
        задержкой корректно. received_ts=None → возраст неизвестен, не блокируем.
        """
        if received_ts is None:
            return True
        age = time.time() - received_ts
        if age <= 0:
            return True  # часы «в будущем» — не наша забота блокировать
        if sig.action is Action.ENTRY:
            # Отложенный ордер (limit/stop) стоит на фиксированном уровне с
            # фиксированными SL/TP — его риск не зависит от возраста сигнала, он
            # просто ждёт цену. Гейт свежести нужен только market-входу (иначе
            # ушедший рынок раздувает риск). Отмену CRT-идеи ловит Action.CANCEL.
            if sig.order_kind is not OrderKind.MARKET:
                return True
            limit, kind = self.freshness.entry_max_age(sig.strategy), "вход"
        else:
            limit, kind = self.freshness.manage_max_age_sec, "управление позицией"
        if age <= limit:
            return True
        self._alert_stale(sig, age, limit, source, kind)
        return False

    def _alert_stale(self, sig: Signal, age: float, limit: float,
                     source: Optional[str], kind: str) -> None:
        am, lm = int(age // 60), int(limit // 60)
        src = f" (source={source})" if source else ""
        msg = (f"⏳ Пропущен по возрасту · {kind.upper()} "
               f"{(sig.strategy or '?').upper()} {sig.symbol_tv or ''}\n"
               f"Возраст {am} мин > лимит {lm} мин{src}. "
               f"Сигнал НЕ исполнен (цена устарела).")
        log.warning("  ↳ %s", " | ".join(p.strip() for p in msg.splitlines()))
        if self.notifier is not None:
            try:
                self.notifier.send(msg)
            except Exception as e:
                log.warning("Не удалось отправить тревогу о возрасте: %s", e)

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
            # План всё равно фиксируем (факт — оценочный, dry_run=1).
            self._record_execution(sig, decision, spec, account, market_price,
                                   res=None, dry=True)
            return

        res = self.broker.place_entry(sig, decision.lots, decision.mt5_symbol)
        if res.ok:
            log.info("  ↳ ✅ ОРДЕР ОТПРАВЛЕН ticket=%s (%s)", res.ticket, res.detail)
            if self.metrics is not None:
                self.metrics.on_order()
            self._notify_entry(sig, decision, account, res)
        else:
            log.error("  ↳ ❌ ОШИБКА ОРДЕРА: %s", res.detail)
            return

        # Систематическая запись плана↔факта + мгновенная тревога, если реальный
        # риск исполнения ушёл выше заданного (рынок сместился до отправки/фила).
        self._record_execution(sig, decision, spec, account, market_price, res=res)

    # ── Лог проскальзывания (план ↔ факт) ──────────────────────────────────────
    def _record_execution(self, sig: Signal, decision: Decision, spec: SymbolSpec,
                           account, market_price: Optional[float],
                           res: Optional[OrderResult], dry: bool = False) -> None:
        """Собрать запись плана↔факта, дописать в лог и проверить риск.

        Для рыночного ордера факт (цена/объём) известен сразу; для limit/stop он
        появится при срабатывании — тогда факт остаётся пустым и досчитывается
        отчётом из истории MT5 по position_id.
        """
        target = self.cfg.risk_pct(sig.strategy)
        equity = account.equity
        plan_risk_amount = round(equity * target / 100.0, 2)
        is_market = sig.order_kind is OrderKind.MARKET

        # Факт исполнения
        fill_price = fill_lots = None
        ticket = position_id = None
        if res is not None:
            ticket, position_id = res.ticket, res.position_id
            fill_lots = res.fill_volume or decision.lots
            if is_market:
                fill_price = res.fill_price or market_price or sig.entry
        elif dry and is_market:
            # dry-run: лучшая оценка исполнения — текущая цена (или цена алерта)
            fill_price = market_price or sig.entry
            fill_lots = decision.lots

        slip = slippage.compute_slippage(
            sig.side.value if sig.side else "", sig.entry, fill_price, sig.sl, spec)
        actual = None
        if fill_price is not None and fill_lots:
            actual = slippage.compute_actual_risk(
                fill_price, sig.sl, sig.tp, fill_lots, spec, equity)

        risk_delta = (round(actual.risk_pct - target, 3)
                      if actual is not None else None)
        rec = execlog.ExecutionRecord(
            ts=datetime.now().isoformat(timespec="seconds"),
            strategy=(sig.strategy or ""),
            symbol_tv=(sig.symbol_tv or ""),
            mt5_symbol=decision.mt5_symbol or spec.name,
            side=sig.side.value if sig.side else "",
            order_kind=sig.order_kind.value,
            ticket=ticket, position_id=position_id,
            plan_entry=sig.entry, sl=sig.sl, tp=sig.tp, plan_rr=sig.rr,
            target_risk_pct=target, plan_lots=decision.lots,
            plan_risk_amount=plan_risk_amount,
            fill_price=fill_price, fill_lots=fill_lots,
            actual_rr=(actual.rr if actual else None),
            actual_risk_pct=(actual.risk_pct if actual else None),
            actual_risk_amount=(actual.risk_amount if actual else None),
            slip_price=(slip.slip_price if slip else None),
            slip_pips=(slip.slip_pips if slip else None),
            slip_pct_of_sl=(slip.slip_pct_of_sl if slip else None),
            risk_delta_pp=risk_delta,
            adverse=(1 if (slip and slip.adverse) else 0),
            equity=round(equity, 2),
            dry_run=1 if dry else 0,
        )
        try:
            execlog.append(self.exec_log_path, rec)
        except Exception as e:  # лог проскальзывания не должен ронять исполнение
            log.warning("Не удалось записать лог проскальзывания: %s", e)

        if slip is not None and slip.slip_pct_of_sl is not None:
            log.info("  ↳ проскальзывание: %s п (%.1f%% дистанции до SL) | "
                     "риск факт %.2f%% (цель %.2f%%)",
                     slip.slip_pips, slip.slip_pct_of_sl,
                     actual.risk_pct if actual else float("nan"), target)

        # Мгновенная тревога — только на реальном исполнении (не dry-run).
        if not dry and risk_delta is not None and risk_delta > self.risk_overshoot_pp:
            self._alert_risk_overshoot(sig, decision, target, actual, slip)

    def _alert_risk_overshoot(self, sig, decision, target, actual, slip) -> None:
        slip_txt = ""
        if slip is not None:
            slip_txt = (f" Проскальзывание {slip.slip_pips} п "
                        f"(алерт {sig.entry} → факт).")
        msg = (f"⚠️ РИСК ВЫШЕ ЗАДАННОГО · {(sig.strategy or '?').upper()} "
               f"{(sig.side.value if sig.side else '')} {sig.symbol_tv}\n"
               f"Факт {actual.risk_pct:.2f}% vs цель {target:.2f}% "
               f"(+{actual.risk_pct - target:.2f} п.п., порог "
               f"{self.risk_overshoot_pp} п.п.).{slip_txt}\n"
               f"Лот {decision.lots}, риск {actual.risk_amount:.0f} "
               f"вместо {decision.sizing.risk_amount:.0f}.")
        log.warning("  ↳ %s", " | ".join(p.strip() for p in msg.splitlines()))
        if self.notifier is not None:
            try:
                self.notifier.send(msg)
            except Exception as e:
                log.warning("Не удалось отправить тревогу о риске: %s", e)

    # ── Уведомления о сделках (Telegram) ────────────────────────────────────────
    def _notify(self, msg: str) -> None:
        """Отправить уведомление в Telegram (если notifier настроен). Никогда не
        роняет движок: ошибка доставки лишь пишется в лог."""
        if self.notifier is None:
            return
        try:
            self.notifier.send(msg)
        except Exception as e:
            log.warning("Не удалось отправить уведомление о сделке: %s", e)

    def _notify_entry(self, sig: Signal, decision: Decision, account,
                      res: OrderResult) -> None:
        """Сообщение об открытом ордере: сторона, символ, лот, уровни, риск.
        Для рыночного ордера показываем фактическую цену исполнения (res.fill_price),
        для отложенного — цену из алерта."""
        s = decision.sizing
        risk_pct = (s.risk_amount / account.equity * 100.0) if account.equity else 0.0
        side_val = sig.side.value if sig.side else ""
        emoji = {"long": "🟢", "short": "🔴"}.get(side_val, "📈")
        fill = res.fill_price if (res and res.fill_price) else sig.entry
        levels = f"вход {fill}"
        if sig.sl:
            levels += f" · SL {sig.sl}"
        if sig.tp:
            levels += f" · TP {sig.tp}"
        tail = f"Риск {s.risk_amount:.0f} {account.currency} ({risk_pct:.2f}%)"
        if sig.rr:
            tail += f" · RR {sig.rr}"
        if res and res.ticket:
            tail += f" · ticket {res.ticket}"
        self._notify(
            f"{emoji} ВХОД {side_val.upper()} · {(sig.strategy or '?').upper()} · "
            f"{sig.symbol_tv or decision.mt5_symbol}\n"
            f"Лот {decision.lots} ({sig.order_kind.value}) · {levels}\n{tail}")

    # ── Безубыток ───────────────────────────────────────────────────────────────
    def _handle_breakeven(self, sig: Signal) -> None:
        if self.exits.get("on_breakeven", "move_sl_to_entry") != "move_sl_to_entry":
            log.info("  ↳ БУ проигнорирован (config exits.on_breakeven)")
            return
        mt5_symbol = self.cfg.mt5_symbol(sig.symbol_tv)
        if not mt5_symbol:
            log.warning("  ↳ БУ: нет карты символа для %s", sig.symbol_tv)
            return
        # Сторона не обязательна: CRT шлёт "CRT ВЫХОД GER40 (после БУ)" без
        # long/short. Раньше такой алерт молча отбрасывался, и стоп у CRT
        # никогда не уезжал в безубыток. По символу позиция всё равно одна
        # (guards.max_open_positions_per_symbol=1).
        where = f"{mt5_symbol} {sig.side.value}" if sig.side else mt5_symbol
        if self.cfg.dry_run:
            log.info("  ↳ [DRY_RUN] перенёс бы SL в безубыток %s", where)
            return
        res = self.broker.modify_sl_to_entry(mt5_symbol, sig.side,
                                             magic=magic_for(sig.strategy))
        log.info("  ↳ БУ %s: %s", "✅" if res.ok else "⚠️", res.detail)
        if res.ok:
            self._notify(f"🟡 БУ · стоп в безубыток · {(sig.strategy or '?').upper()} · "
                         f"{sig.symbol_tv or mt5_symbol}")

    # ── Выход ─────────────────────────────────────────────────────────────────
    def _handle_exit(self, sig: Signal) -> None:
        mode = self.exits.get("on_exit_alert", "ignore")
        if mode != "close":
            log.info("  ↳ выход проигнорирован — SL/TP уже на ордере (exits.on_exit_alert=ignore)")
            return
        mt5_symbol = self.cfg.mt5_symbol(sig.symbol_tv)
        if not mt5_symbol:
            log.warning("  ↳ выход: нет карты символа для %s", sig.symbol_tv)
            return
        # Сторона тоже необязательна — см. комментарий в _handle_breakeven.
        if self.cfg.dry_run:
            log.info("  ↳ [DRY_RUN] закрыл бы позицию %s %s", mt5_symbol,
                     sig.side.value if sig.side else "(любую)")
            return
        res = self.broker.close_position(mt5_symbol, sig.side,
                                         magic=magic_for(sig.strategy))
        log.info("  ↳ закрытие %s: %s", "✅" if res.ok else "⚠️", res.detail)
        if res.ok:
            self._notify(f"⏹ ВЫХОД · закрытие позиции · {(sig.strategy or '?').upper()} · "
                         f"{sig.symbol_tv or mt5_symbol}")

    # ── Отмена отложенного ордера (инвалидация идеи) ────────────────────────────
    def _handle_cancel(self, sig: Signal) -> None:
        """Снять неисполненный отложенный ордер: CRT-идея инвалидирована (закол за
        экстремум манипуляции или конец дня). Отложка сама не GTC-отменится —
        снимаем её здесь. Сторона необязательна (алерт CRT её не несёт)."""
        mt5_symbol = self.cfg.mt5_symbol(sig.symbol_tv)
        if not mt5_symbol:
            log.warning("  ↳ отмена: нет карты символа для %s", sig.symbol_tv)
            return
        if self.cfg.dry_run:
            log.info("  ↳ [DRY_RUN] снял бы отложку %s %s", mt5_symbol,
                     sig.side.value if sig.side else "(любую)")
            return
        res = self.broker.cancel_pending(mt5_symbol, sig.side,
                                         magic=magic_for(sig.strategy))
        log.info("  ↳ отмена отложки %s: %s", "✅" if res.ok else "⚠️", res.detail)
        # Уведомляем только когда реально что-то сняли (detail начинается с
        # "отмена"), а не на холостой no-op ("нет отложек …") — чтобы не спамить.
        if res.ok and res.detail.startswith("отмена"):
            self._notify(f"🚫 ОТМЕНА · снят отложенный ордер · "
                         f"{(sig.strategy or '?').upper()} · {sig.symbol_tv or mt5_symbol}")
