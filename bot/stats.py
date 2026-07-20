"""Статистика по сделкам из истории MT5, раздельно по стратегиям.

Стратегия определяется по magic-номеру ордера (см. broker_mt5.STRATEGY_MAGIC).
Сделки, открытые руками (magic=0) или другими роботами, попадают в "ручные/чужие".
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from .broker_mt5 import DEFAULT_MAGIC, STRATEGY_MAGIC

# Имена стратегий должны совпадать с вариантами select-свойства «Стратегия»
# в Notion-базе «Сделки бота», иначе синхронизация заводит новый вариант и
# кривая стратегии разъезжается на две. Внутренний ключ asweep → «ASIA».
STRATEGY_LABEL = {"smc": "SMC", "crt": "CRT", "asweep": "ASIA"}
MAGIC_NAME = {v: STRATEGY_LABEL.get(k, k.upper()) for k, v in STRATEGY_MAGIC.items()}
MAGIC_NAME[DEFAULT_MAGIC] = "БОТ(?)"


@dataclass
class ClosedTrade:
    position_id: int
    symbol: str
    strategy: str
    side: str
    volume: float
    open_time: datetime
    close_time: datetime
    price_open: float
    price_close: float
    profit: float           # с учётом свопа и комиссии


def _strategy_name(magic: int) -> str:
    return MAGIC_NAME.get(magic, "ручные/чужие")


def collect_closed(mt5, days: int = 365,
                   symbol_alias: Optional[dict] = None) -> List[ClosedTrade]:
    """Собрать закрытые сделки: группируем дилы истории по position_id.

    symbol_alias — обратная карта символов MT5 → тикер TradingView
    (GER30m → GER40, USTECH100m → NAS100). Нужна, чтобы в отчёте и в
    Notion-базе символы назывались так же, как в стратегиях, а не
    брокерскими именами мини-контрактов.
    """
    alias = symbol_alias or {}
    frm = datetime.now() - timedelta(days=days)
    to = datetime.now() + timedelta(days=1)
    deals = mt5.history_deals_get(frm, to) or []

    by_pos: dict[int, list] = {}
    for d in deals:
        if d.position_id and d.symbol:  # пропускаем баланс/депозиты
            by_pos.setdefault(d.position_id, []).append(d)

    out: List[ClosedTrade] = []
    for pid, ds in by_pos.items():
        ins = [d for d in ds if d.entry == mt5.DEAL_ENTRY_IN]
        outs = [d for d in ds if d.entry != mt5.DEAL_ENTRY_IN]
        if not ins or not outs:
            continue  # позиция ещё открыта (или неполная история)
        vol_in = sum(d.volume for d in ins)
        vol_out = sum(d.volume for d in outs)
        if vol_out + 1e-9 < vol_in:
            continue  # закрыта частично — ещё открыта
        e0, x_last = ins[0], max(outs, key=lambda d: d.time)
        profit = sum(d.profit + getattr(d, "swap", 0.0) + getattr(d, "commission", 0.0)
                     + getattr(d, "fee", 0.0) for d in ds)
        out.append(ClosedTrade(
            position_id=pid,
            symbol=alias.get(e0.symbol, e0.symbol),
            strategy=_strategy_name(e0.magic),
            side="long" if e0.type == mt5.DEAL_TYPE_BUY else "short",
            volume=vol_in,
            open_time=datetime.fromtimestamp(e0.time),
            close_time=datetime.fromtimestamp(x_last.time),
            price_open=e0.price,
            price_close=x_last.price,
            profit=round(profit, 2),
        ))
    out.sort(key=lambda t: t.close_time)
    return out


def summarize(trades: List[ClosedTrade]) -> List[dict]:
    """Сводка по каждой стратегии + строка ИТОГО (только сделки бота)."""
    groups: dict[str, List[ClosedTrade]] = {}
    for t in trades:
        groups.setdefault(t.strategy, []).append(t)

    rows = []
    for name, ts in sorted(groups.items()):
        wins = [t for t in ts if t.profit > 0]
        losses = [t for t in ts if t.profit <= 0]
        gross_win = sum(t.profit for t in wins)
        gross_loss = abs(sum(t.profit for t in losses))
        rows.append({
            "стратегия": name,
            "сделок": len(ts),
            "winrate": f"{100.0 * len(wins) / len(ts):.0f}%" if ts else "-",
            "net_pnl": round(sum(t.profit for t in ts), 2),
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else "∞",
            "ср.плюс": round(gross_win / len(wins), 2) if wins else 0.0,
            "ср.минус": round(-gross_loss / len(losses), 2) if losses else 0.0,
            "макс.минус": round(min((t.profit for t in ts), default=0.0), 2),
        })
    bot_trades = [t for t in trades if t.strategy != "ручные/чужие"]
    if bot_trades and len(groups) > 1:
        wins = [t for t in bot_trades if t.profit > 0]
        rows.append({
            "стратегия": "ИТОГО (бот)",
            "сделок": len(bot_trades),
            "winrate": f"{100.0 * len(wins) / len(bot_trades):.0f}%",
            "net_pnl": round(sum(t.profit for t in bot_trades), 2),
            "profit_factor": "",
            "ср.плюс": "",
            "ср.минус": "",
            "макс.минус": "",
        })
    return rows


def write_csv(trades: List[ClosedTrade], path: Path,
              start_balance: Optional[float] = None) -> None:
    """trades должны быть отсортированы по close_time (collect_closed так и отдаёт).
    Если задан start_balance — добавляется колонка Equity (нарастающий баланс),
    по ней строится график доходности (в т.ч. в Notion)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["закрыта", "стратегия", "символ", "сторона", "лот",
                    "открыта", "цена входа", "цена выхода", "P&L (USD)",
                    "Equity (USD)", "кумул P&L стратегии (USD)", "позиция ID"])
        equity = start_balance
        cum_by_strategy: dict[str, float] = {}
        for t in trades:
            if equity is not None:
                equity = round(equity + t.profit, 2)
            cum = round(cum_by_strategy.get(t.strategy, 0.0) + t.profit, 2)
            cum_by_strategy[t.strategy] = cum
            w.writerow([t.close_time.strftime("%Y-%m-%d %H:%M"), t.strategy,
                        t.symbol, t.side, t.volume,
                        t.open_time.strftime("%Y-%m-%d %H:%M"),
                        t.price_open, t.price_close, t.profit,
                        equity if equity is not None else "", cum, t.position_id])


def open_positions(mt5) -> List[dict]:
    """Текущие открытые позиции с плавающим P&L и стратегией."""
    out = []
    for p in mt5.positions_get() or []:
        out.append({
            "символ": p.symbol,
            "стратегия": _strategy_name(p.magic),
            "сторона": "long" if p.type == 0 else "short",
            "лот": p.volume,
            "вход": p.price_open,
            "SL": p.sl, "TP": p.tp,
            "плавающий P&L": round(p.profit + getattr(p, "swap", 0.0), 2),
        })
    return out
