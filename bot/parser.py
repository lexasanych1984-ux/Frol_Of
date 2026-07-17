"""Разбор alert_message из TradingView в Signal.

Поддерживает два формата:
  1. JSON (рекомендуется для реальных денег) — явные поля.
  2. Текущий человекочитаемый текст стратегий SMC / CRT / Asia Sweep.

Примеры текстового формата (из Pine alert_message):
    LONG EURUSD | вход 1.14355 | SL 1.14100 | TP 1.14900 | RR 2.00
    SHORT NAS100 | вход 20100 | SL 20180 | TP 19900 | RR 2.5
    CRT LONG GER40 | вход 24500 | SL 24400 | TP 24800 | RR 3.0
    CRT SHORT (лимит) GER40 | вход 24500 | SL 24620 | TP 24100 | RR 3.0
    ВЫХОД long EURUSD
    CRT ВЫХОД short GER40
    ВЫХОД long (после БУ) EURUSD
"""
from __future__ import annotations

import json
import re
from typing import Optional

from .model import Action, OrderKind, Side, Signal

# Слова, которые не являются тикером, хотя и в верхнем регистре.
_KEYWORDS = {"LONG", "SHORT", "CRT", "RR", "SL", "TP", "SMC", "AS"}
_SYMBOL_RE = re.compile(r"[A-Z]{2,}[0-9]{0,4}")
_NUM_RE = r"([0-9]+(?:\.[0-9]+)?)"


def parse(message: str) -> Optional[Signal]:
    """Разобрать сырой alert_message. Вернуть None, если не распознано."""
    if not message:
        return None
    text = message.strip()
    if not text:
        return None
    if text[0] in "{[":
        try:
            return _parse_json(json.loads(text))
        except (ValueError, KeyError):
            return None
    return _parse_text(text)


def _parse_json(obj: dict) -> Optional[Signal]:
    action = str(obj.get("action", "entry")).lower()
    if action in ("exit", "close"):
        act = Action.EXIT
    elif action in ("be", "breakeven"):
        act = Action.BREAKEVEN
    else:
        act = Action.ENTRY

    side_raw = str(obj.get("side", "")).lower()
    side = Side.LONG if side_raw in ("long", "buy") else (
        Side.SHORT if side_raw in ("short", "sell") else None)

    kind_raw = str(obj.get("order_kind", obj.get("type", "market"))).lower()
    kind = {"limit": OrderKind.LIMIT, "stop": OrderKind.STOP}.get(kind_raw, OrderKind.MARKET)

    def num(key):
        v = obj.get(key)
        return float(v) if v is not None and v != "" else None

    return Signal(
        action=act,
        side=side,
        symbol_tv=(obj.get("symbol") or obj.get("ticker") or "").split(":")[-1] or None,
        entry=num("entry"),
        sl=num("sl"),
        tp=num("tp"),
        rr=num("rr"),
        order_kind=kind,
        strategy=obj.get("strategy"),
        raw=json.dumps(obj, ensure_ascii=False),
        signal_id=obj.get("id"),
    )


def _extract_symbol(segment: str) -> Optional[str]:
    """Тикер = последний токен в верхнем регистре, не являющийся ключевым словом."""
    tokens = [t for t in _SYMBOL_RE.findall(segment) if t not in _KEYWORDS]
    return tokens[-1] if tokens else None


def _parse_text(text: str) -> Optional[Signal]:
    is_crt = "CRT" in text
    strategy = "crt" if is_crt else None

    # ── Выход / безубыток ────────────────────────────────────────────────────
    if "ВЫХОД" in text.upper():
        low = text.lower()
        side = Side.LONG if re.search(r"\blong\b", low) else (
            Side.SHORT if re.search(r"\bshort\b", low) else None)
        act = Action.BREAKEVEN if ("БУ" in text or "безубыт" in low) else Action.EXIT
        # символ ищем во всём тексте (после стороны идёт тикер)
        symbol = _extract_symbol(text)
        return Signal(action=act, side=side, symbol_tv=symbol,
                      strategy=strategy, raw=text)

    # ── Вход ─────────────────────────────────────────────────────────────────
    up = text.upper()
    if "LONG" in up:
        side = Side.LONG
    elif "SHORT" in up:
        side = Side.SHORT
    else:
        return None

    # Тип ордера
    if is_crt:
        kind = OrderKind.LIMIT if "лимит" in text.lower() else OrderKind.STOP
    else:
        kind = OrderKind.MARKET

    head = text.split("|", 1)[0]           # "CRT SHORT (лимит) GER40"
    symbol = _extract_symbol(head) or _extract_symbol(text)

    def grab(label):
        m = re.search(label + r"\s+" + _NUM_RE, text)
        return float(m.group(1)) if m else None

    entry = grab("вход")
    sl = grab("SL")
    tp = grab("TP")
    rr = grab("RR")

    if side is None or symbol is None or sl is None:
        return None  # без стороны/символа/стопа исполнять нельзя

    return Signal(
        action=Action.ENTRY, side=side, symbol_tv=symbol,
        entry=entry, sl=sl, tp=tp, rr=rr,
        order_kind=kind, strategy=strategy, raw=text,
    )
