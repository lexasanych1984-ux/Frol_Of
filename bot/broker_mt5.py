"""Обёртка над MetaTrader5 для счёта Bybit TradFi.

Импортирует MetaTrader5 лениво — модуль можно загрузить и без установленного
пакета (например, для парсер-тестов), пока не вызван connect().
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .config import MT5Creds
from .model import OrderKind, Side, Signal
from .sizing import SymbolSpec


@dataclass
class Account:
    login: int
    equity: float
    balance: float
    currency: str
    server: str


@dataclass
class Position:
    ticket: int
    symbol: str
    side: Side
    volume: float
    price_open: float
    sl: float
    tp: float


@dataclass
class OrderResult:
    ok: bool
    detail: str
    ticket: Optional[int] = None
    raw: object = None


class MT5Broker:
    def __init__(self, creds: MT5Creds):
        self.creds = creds
        self.mt5 = None  # заполняется в connect()

    # ── Подключение ───────────────────────────────────────────────────────────
    def connect(self) -> None:
        import MetaTrader5 as mt5  # ленивый импорт (только Windows)
        self.mt5 = mt5
        kwargs = {}
        if self.creds.terminal_path:
            kwargs["path"] = self.creds.terminal_path
        if self.creds.login:
            kwargs.update(login=self.creds.login,
                          password=self.creds.password,
                          server=self.creds.server)
        if not mt5.initialize(**kwargs):
            raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
        info = mt5.account_info()
        if info is None:
            raise RuntimeError(f"MT5 нет доступа к счёту: {mt5.last_error()}")

    def shutdown(self) -> None:
        if self.mt5:
            self.mt5.shutdown()

    # ── Чтение ────────────────────────────────────────────────────────────────
    def account(self) -> Account:
        i = self.mt5.account_info()
        return Account(login=i.login, equity=i.equity, balance=i.balance,
                       currency=i.currency, server=i.server)

    def positions(self) -> List[Position]:
        raw = self.mt5.positions_get() or []
        out = []
        for p in raw:
            side = Side.LONG if p.type == self.mt5.POSITION_TYPE_BUY else Side.SHORT
            out.append(Position(ticket=p.ticket, symbol=p.symbol, side=side,
                                volume=p.volume, price_open=p.price_open,
                                sl=p.sl, tp=p.tp))
        return out

    def symbol_spec(self, symbol: str) -> Optional[SymbolSpec]:
        if not self.mt5.symbol_select(symbol, True):
            return None
        s = self.mt5.symbol_info(symbol)
        if s is None:
            return None
        return SymbolSpec(
            name=s.name,
            volume_min=s.volume_min,
            volume_max=s.volume_max,
            volume_step=s.volume_step,
            tick_size=s.trade_tick_size or s.point,
            tick_value=s.trade_tick_value,
        )

    # ── Исполнение ────────────────────────────────────────────────────────────
    def _filling(self, symbol: str):
        """Подобрать поддерживаемый режим исполнения для символа."""
        info = self.mt5.symbol_info(symbol)
        mode = getattr(info, "filling_mode", 0)
        # filling_mode — битовая маска: 1=FOK, 2=IOC
        if mode & 2:
            return self.mt5.ORDER_FILLING_IOC
        if mode & 1:
            return self.mt5.ORDER_FILLING_FOK
        return self.mt5.ORDER_FILLING_RETURN

    def place_entry(self, sig: Signal, lots: float, symbol: str,
                    comment: str = "bybit-tradfi-bot") -> OrderResult:
        mt5 = self.mt5
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return OrderResult(False, f"нет котировки по {symbol}")

        is_long = sig.side is Side.LONG
        req = {
            "symbol": symbol,
            "volume": float(lots),
            "sl": float(sig.sl) if sig.sl else 0.0,
            "tp": float(sig.tp) if sig.tp else 0.0,
            "deviation": 20,
            "magic": 770077,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling(symbol),
        }

        if sig.order_kind is OrderKind.MARKET:
            req["action"] = mt5.TRADE_ACTION_DEAL
            req["type"] = mt5.ORDER_TYPE_BUY if is_long else mt5.ORDER_TYPE_SELL
            req["price"] = tick.ask if is_long else tick.bid
        else:
            req["action"] = mt5.TRADE_ACTION_PENDING
            req["price"] = float(sig.entry)
            if sig.order_kind is OrderKind.LIMIT:
                req["type"] = mt5.ORDER_TYPE_BUY_LIMIT if is_long else mt5.ORDER_TYPE_SELL_LIMIT
            else:  # STOP
                req["type"] = mt5.ORDER_TYPE_BUY_STOP if is_long else mt5.ORDER_TYPE_SELL_STOP

        res = mt5.order_send(req)
        if res is None:
            return OrderResult(False, f"order_send=None {mt5.last_error()}", raw=req)
        ok = res.retcode == mt5.TRADE_RETCODE_DONE
        return OrderResult(ok, f"retcode={res.retcode} {res.comment}",
                           ticket=getattr(res, "order", None), raw=res)

    def modify_sl_to_entry(self, symbol: str, side: Side) -> OrderResult:
        """Перенести стоп в безубыток (к цене открытия) для позиции по символу."""
        mt5 = self.mt5
        for p in self.positions():
            if p.symbol == symbol and p.side == side:
                req = {
                    "action": mt5.TRADE_ACTION_SLTP,
                    "position": p.ticket,
                    "symbol": symbol,
                    "sl": p.price_open,
                    "tp": p.tp,
                }
                res = mt5.order_send(req)
                ok = res is not None and res.retcode == mt5.TRADE_RETCODE_DONE
                return OrderResult(ok, f"BE retcode={getattr(res,'retcode',None)}", raw=res)
        return OrderResult(False, f"нет позиции по {symbol} {side.value}")

    def close_position(self, symbol: str, side: Side) -> OrderResult:
        mt5 = self.mt5
        for p in self.positions():
            if p.symbol == symbol and p.side == side:
                tick = mt5.symbol_info_tick(symbol)
                closing_long = p.side is Side.LONG
                req = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "position": p.ticket,
                    "symbol": symbol,
                    "volume": p.volume,
                    "type": mt5.ORDER_TYPE_SELL if closing_long else mt5.ORDER_TYPE_BUY,
                    "price": tick.bid if closing_long else tick.ask,
                    "deviation": 20,
                    "magic": 770077,
                    "comment": "bybit-tradfi-bot close",
                    "type_filling": self._filling(symbol),
                }
                res = mt5.order_send(req)
                ok = res is not None and res.retcode == mt5.TRADE_RETCODE_DONE
                return OrderResult(ok, f"close retcode={getattr(res,'retcode',None)}", raw=res)
        return OrderResult(False, f"нет позиции по {symbol} {side.value}")
