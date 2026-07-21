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

# Magic-номер кодирует стратегию: он навсегда сохраняется в истории сделок MT5,
# по нему run.py stats считает статистику раздельно по стратегиям.
STRATEGY_MAGIC = {"smc": 770001, "crt": 770002, "asweep": 770003}
DEFAULT_MAGIC = 770077  # сигнал без распознанной стратегии

def magic_for(strategy: Optional[str]) -> int:
    return STRATEGY_MAGIC.get((strategy or "").lower(), DEFAULT_MAGIC)


@dataclass
class Account:
    login: int
    equity: float
    balance: float
    currency: str
    server: str


@dataclass
class Mt5Health:
    """Снимок для мониторинга живости (см. bot/health.py).

    Одним обращением снимаем всё, что нужно проверкам MT5, устойчиво к None.
    """
    initialized: bool                 # есть ли живая сессия со счётом
    login: Optional[int]              # account_info().login
    trade_allowed: Optional[bool]     # terminal_info().trade_allowed (кнопка алго)
    trade_expert: Optional[bool]      # account_info().trade_expert (сервер)
    error: str = ""


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
    AUTOTRADING_HINT = (
        "в терминале MT5 ВЫКЛЮЧЕНА «Алго-торговля» — ордера отклоняются "
        "(retcode 10027). Включи кнопку «Алго-торговля» на панели терминала "
        "(или Сервис → Настройки → Советники → Разрешить алгоритмическую "
        "торговлю) — сигнал будет потерян, пока она выключена"
    )

    def autotrading_enabled(self) -> Optional[bool]:
        """Состояние кнопки «Алго-торговля» в терминале (None — терминал молчит).

        Сервер может разрешать торговлю советниками (account_info.trade_expert),
        но локальный тумблер терминала всё равно рубит ордера.
        """
        ti = self.mt5.terminal_info()
        return None if ti is None else bool(ti.trade_allowed)

    def health_snapshot(self) -> Mt5Health:
        """Единый снимок для мониторинга живости: связь со счётом, логин,
        кнопка алго-торговли (терминал) и серверное разрешение советникам.

        Никогда не бросает из-за None — возвращает то, что удалось снять, а
        неизвестное оставляет None, чтобы health-модуль решил сам.
        """
        if self.mt5 is None:
            return Mt5Health(False, None, None, None,
                             "MT5 не инициализирован (connect не вызывался)")
        ti = self.mt5.terminal_info()
        trade_allowed = None if ti is None else bool(ti.trade_allowed)
        ai = self.mt5.account_info()
        if ai is None:
            return Mt5Health(False, None, trade_allowed, None,
                             f"нет связи со счётом: {self.mt5.last_error()}")
        return Mt5Health(
            initialized=True,
            login=ai.login,
            trade_allowed=trade_allowed,
            trade_expert=bool(getattr(ai, "trade_expert", False)),
        )

    def _wrong_account(self) -> Optional[str]:
        """Предполётная проверка перед каждой торговой операцией.

        1) Терминал мог быть вручную переключён на другой счёт — тогда ордера
           улетели бы на него, поэтому сверяем логин.
        2) Могла быть выключена «Алго-торговля» — ловим это ДО отправки, чтобы
           в логе была понятная причина, а не голый retcode.
        """
        i = self.mt5.account_info()
        if i is None:
            return f"нет связи со счётом: {self.mt5.last_error()}"
        if self.creds.login and i.login != self.creds.login:
            return (f"в терминале активен ДРУГОЙ счёт ({i.login} @ {i.server}), "
                    f"ожидался {self.creds.login} — операция отменена")
        if self.autotrading_enabled() is False:
            return self.AUTOTRADING_HINT
        return None

    def current_price(self, symbol: str, side: Side) -> Optional[float]:
        """Цена, по которой прямо сейчас исполнится рыночный ордер.

        Покупка идёт по ask, продажа — по bid. Нужна для сайзинга: лот обязан
        считаться от реальной цены исполнения, иначе при уходе рынка от цены
        из сигнала фактический риск не совпадёт с заданным процентом.
        """
        tick = self.mt5.symbol_info_tick(symbol)
        if tick is None:
            return None
        price = tick.ask if side is Side.LONG else tick.bid
        return float(price) or None

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
        err = self._wrong_account()
        if err:
            return OrderResult(False, err)
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
            "magic": magic_for(sig.strategy),
            "comment": f"bot:{(sig.strategy or 'unknown')}",
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

    def modify_sl_to_entry(self, symbol: str, side: Optional[Side] = None) -> OrderResult:
        """Перенести стоп в безубыток (к цене открытия) для позиции по символу.

        side=None — сторона в алерте не указана. Так шлёт CRT: её алерт
        безубытка выглядит как "CRT ВЫХОД GER40 (после БУ)", без long/short.
        Тогда двигаем стоп у всех позиций по символу — при
        guards.max_open_positions_per_symbol=1 она там всё равно одна.
        """
        err = self._wrong_account()
        if err:
            return OrderResult(False, err)
        mt5 = self.mt5
        done, failed = [], []
        for p in self.positions():
            if p.symbol != symbol or (side is not None and p.side != side):
                continue
            req = {
                "action": mt5.TRADE_ACTION_SLTP,
                "position": p.ticket,
                "symbol": symbol,
                "sl": p.price_open,
                "tp": p.tp,
            }
            res = mt5.order_send(req)
            if res is not None and res.retcode == mt5.TRADE_RETCODE_DONE:
                done.append(p.ticket)
            else:
                failed.append(f"{p.ticket}:{getattr(res, 'retcode', None)}")

        where = f"{symbol}" + (f" {side.value}" if side else "")
        if not done and not failed:
            return OrderResult(False, f"нет позиции по {where}")
        if failed:
            return OrderResult(False, f"BE {where}: перенесено {done}, ошибки {failed}")
        return OrderResult(True, f"BE {where}: стоп в безубыток, позиций {len(done)}")

    def close_position(self, symbol: str, side: Optional[Side] = None) -> OrderResult:
        """side=None — закрыть позицию по символу независимо от стороны
        (алерты выхода CRT тоже идут без long/short)."""
        err = self._wrong_account()
        if err:
            return OrderResult(False, err)
        mt5 = self.mt5
        for p in self.positions():
            if p.symbol == symbol and (side is None or p.side == side):
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
        return OrderResult(False, "нет позиции по " + symbol + (f" {side.value}" if side else ""))
