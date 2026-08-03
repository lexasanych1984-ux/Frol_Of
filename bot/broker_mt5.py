"""Обёртка над MetaTrader5 для счёта Bybit TradFi.

Импортирует MetaTrader5 лениво — модуль можно загрузить и без установленного
пакета (например, для парсер-тестов), пока не вызван connect().
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import List, Optional

from .config import MT5Creds
from .model import OrderKind, Side, Signal
from .sizing import SymbolSpec

log = logging.getLogger("bot")

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
    # Факт исполнения (для лога проскальзывания). Для рыночного ордера MT5
    # возвращает реальные цену и объём; для отложенного — заполнится при
    # срабатывании (из истории MT5), здесь остаётся None.
    fill_price: Optional[float] = None
    fill_volume: Optional[float] = None
    position_id: Optional[int] = None


class MT5Broker:
    def __init__(self, creds: MT5Creds):
        self.creds = creds
        self.mt5 = None  # заполняется в connect()
        # Политика экспирации отложенных ордеров (задаётся из config в run.py):
        # gtc — живёт до отмены; day — сгорает в конце торгового дня брокера;
        # window — экспирация в конце окна CRT (pending_window_end/tz).
        self.pending_expire = "gtc"
        self.pending_window_end = "2300"
        self.pending_window_tz = "Europe/Moscow"

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

    def positions(self, magic: Optional[int] = None) -> List[Position]:
        """magic задан — только позиции этой стратегии (ручные и чужие не наши)."""
        raw = self.mt5.positions_get() or []
        out = []
        for p in raw:
            if magic is not None and getattr(p, "magic", None) != magic:
                continue
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
        tick_size = s.trade_tick_size or s.point
        # Для сайзинга нужна стоимость тика в УБЫТОК: у части символов брокер
        # разводит profit/loss (разные стороны конвертации). Если поля нет —
        # откатываемся на общий trade_tick_value.
        tick_value = getattr(s, "trade_tick_value_loss", 0.0) or s.trade_tick_value
        tick_value = self._tick_value_in_account_currency(s, tick_size, tick_value)
        return SymbolSpec(
            name=s.name,
            volume_min=s.volume_min,
            volume_max=s.volume_max,
            volume_step=s.volume_step,
            tick_size=tick_size,
            tick_value=tick_value,
        )

    def _tick_value_in_account_currency(self, s, tick_size: float,
                                        tick_value: float) -> float:
        """Стоимость тика в валюте счёта — с конвертацией, если MT5 её не сделал.

        MT5 обязан отдавать trade_tick_value уже в валюте счёта, и для FX
        Just2Trade так и делает (GBPJPY: 100 JPY за тик → 0.638 USD). Но для
        индексных мини (GER30m: currency_profit=EUR, contract=1) он отдаёт
        0.1 — это ровно tick_size × contract_size, то есть СЫРОЕ значение в EUR,
        без конвертации. На этом в июле 2026 бот и погорел: GER30m сайзился по
        $1.00/пункт вместо фактических ~$1.15 → риск 1.17% вместо 1.00%
        (docs/just2trade-actual-costs.md).

        Отличаем одно от другого по признаку «значение равно сырому»: если MT5
        сконвертировал, оно от сырого отличается. Если курс и так ≈1, обе ветки
        дают один ответ, так что ложное срабатывание безвредно.
        """
        profit_ccy = (getattr(s, "currency_profit", "") or "").upper()
        acc = self.mt5.account_info()
        acc_ccy = (acc.currency if acc else "").upper()
        if not profit_ccy or not acc_ccy or profit_ccy == acc_ccy:
            return tick_value

        raw = tick_size * (getattr(s, "trade_contract_size", 0.0) or 0.0)
        if raw <= 0 or abs(tick_value - raw) > raw * 1e-6:
            return tick_value  # MT5 уже сконвертировал — не трогаем

        rate = self._cross_rate(profit_ccy, acc_ccy)
        if rate is None:
            log.error("%s: MT5 отдал стоимость тика в %s (%.6g) и кросс-курса "
                      "%s%s в терминале нет — сайзинг посчитает риск НЕВЕРНО",
                      s.name, profit_ccy, tick_value, profit_ccy, acc_ccy)
            return tick_value

        converted = tick_value * rate
        log.warning("%s: MT5 отдал стоимость тика без конвертации (%.6g %s); "
                    "пересчитал по %s%s=%.5f → %.6g %s",
                    s.name, tick_value, profit_ccy, profit_ccy, acc_ccy,
                    rate, converted, acc_ccy)
        return converted

    def _cross_rate(self, frm: str, to: str) -> Optional[float]:
        """Сколько единиц `to` в одной единице `frm` по текущему рынку."""
        if frm == to:
            return 1.0
        for name, invert in ((f"{frm}{to}", False), (f"{to}{frm}", True)):
            if not self.mt5.symbol_select(name, True):
                continue
            tick = self.mt5.symbol_info_tick(name)
            if tick is None:
                continue
            # середина рынка: конвертация не сделка, сторону закладывать не за что
            mid = (tick.bid + tick.ask) / 2.0 if tick.ask else tick.bid
            if not mid:
                continue
            return (1.0 / mid) if invert else mid
        return None

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
            # Отложка сама не отменяется (GTC). Бэкстоп на случай пропущенной
            # отмены-инвалидации: экспирация на стороне MT5 (см. self.pending_*).
            type_time, expiration = self._pending_type_time(symbol)
            req["type_time"] = type_time
            if expiration:
                req["expiration"] = expiration

        res = mt5.order_send(req)
        if res is None:
            return OrderResult(False, f"order_send=None {mt5.last_error()}", raw=req)
        ok = res.retcode == mt5.TRADE_RETCODE_DONE
        # Реальная цена/объём исполнения: у рыночного ордера MT5 кладёт их в
        # результат (res.price/res.volume). У отложенного они появятся при
        # срабатывании — тогда fill_price=0/None, и лог возьмёт плановую цену.
        fill_price = getattr(res, "price", None) or None
        fill_volume = getattr(res, "volume", None) or None
        # id позиции = тикет открывающего ордера (в MT5 они совпадают).
        position_id = getattr(res, "order", None) or None
        return OrderResult(ok, f"retcode={res.retcode} {res.comment}",
                           ticket=getattr(res, "order", None), raw=res,
                           fill_price=fill_price, fill_volume=fill_volume,
                           position_id=position_id)

    def _window_end_epoch(self) -> Optional[float]:
        """Ближайший конец окна CRT (напр. 23:00 Europe/Moscow) как real-unix epoch.
        None — если таймзона/формат не разобрались."""
        try:
            from datetime import datetime, timedelta
            from zoneinfo import ZoneInfo
            hhmm = str(self.pending_window_end)
            h, m = int(hhmm[:2]), int(hhmm[2:4])
            z = ZoneInfo(self.pending_window_tz)
            now = datetime.now(z)
            target = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if target <= now:
                target = target + timedelta(days=1)
            return target.timestamp()
        except Exception:
            return None

    def _pending_type_time(self, symbol: str):
        """(type_time, expiration_server_epoch|0) для отложенного ордера.

        Безопасно деградирует к GTC: если брокер/символ не поддерживает нужный
        режим экспирации или расчётное время истечения уже почти в прошлом —
        ставим GTC (ордер просто живёт, его снимет отмена-инвалидация), но НИКОГДА
        не ставим ордер, который истечёт мгновенно (это была бы новая тихая потеря).
        """
        mt5 = self.mt5
        mode = (self.pending_expire or "gtc").lower()
        if mode == "gtc":
            return mt5.ORDER_TIME_GTC, 0
        info = mt5.symbol_info(symbol)
        flags = getattr(info, "expiration_mode", 0) if info else 0
        supports_day = bool(flags & mt5.SYMBOL_EXPIRATION_DAY)
        supports_spec = bool(flags & mt5.SYMBOL_EXPIRATION_SPECIFIED)
        if mode == "window":
            real_exp = self._window_end_epoch()
            tick = mt5.symbol_info_tick(symbol)
            if real_exp is not None and supports_spec and tick is not None and tick.time:
                # MT5 хранит время в серверной зоне: переводим real→server через
                # смещение (server_now − real_now).
                server_exp = int(real_exp + (tick.time - time.time()))
                if server_exp > tick.time + 300:      # не ближе 5 мин — иначе GTC
                    return mt5.ORDER_TIME_SPECIFIED, server_exp
            # фоллбэк window → day → gtc
            return (mt5.ORDER_TIME_DAY, 0) if supports_day else (mt5.ORDER_TIME_GTC, 0)
        # mode == "day"
        return (mt5.ORDER_TIME_DAY, 0) if supports_day else (mt5.ORDER_TIME_GTC, 0)

    def cancel_pending(self, symbol: str, side: Optional[Side] = None,
                       magic: Optional[int] = None) -> OrderResult:
        """Снять неисполненные отложенные ордера по символу (инвалидация идеи).

        side=None — снять любую сторону (CRT-отмена приходит и без long/short).
        magic — снимать только ордера этой стратегии (страховка: не тронуть чужие
        отложки на том же символе). Идемпотентно: нет отложки → мягкий no-op.
        """
        err = self._wrong_account()
        if err:
            return OrderResult(False, err)
        mt5 = self.mt5
        long_types = {mt5.ORDER_TYPE_BUY_LIMIT, mt5.ORDER_TYPE_BUY_STOP,
                      mt5.ORDER_TYPE_BUY_STOP_LIMIT}
        short_types = {mt5.ORDER_TYPE_SELL_LIMIT, mt5.ORDER_TYPE_SELL_STOP,
                       mt5.ORDER_TYPE_SELL_STOP_LIMIT}
        done, failed = [], []
        for o in (mt5.orders_get(symbol=symbol) or []):
            if magic is not None and getattr(o, "magic", None) != magic:
                continue
            is_long = o.type in long_types
            is_short = o.type in short_types
            if not (is_long or is_short):
                continue  # не отложка (не должно случаться в orders_get, но на всякий)
            if side is Side.LONG and not is_long:
                continue
            if side is Side.SHORT and not is_short:
                continue
            res = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket})
            if res is not None and res.retcode == mt5.TRADE_RETCODE_DONE:
                done.append(o.ticket)
            else:
                failed.append(f"{o.ticket}:{getattr(res, 'retcode', None)}")
        where = f"{symbol}" + (f" {side.value}" if side else "")
        if not done and not failed:
            return OrderResult(True, f"нет отложек по {where} (уже снята/исполнена)")
        if failed:
            return OrderResult(False, f"отмена {where}: снято {done}, ошибки {failed}")
        return OrderResult(True, f"отмена {where}: снято отложек {len(done)} {done}")

    def modify_sl_to_entry(self, symbol: str, side: Optional[Side] = None,
                           magic: Optional[int] = None) -> OrderResult:
        """Перенести стоп в безубыток (к цене открытия) для позиции по символу.

        side=None — сторона в алерте не указана. Так шлёт CRT: её алерт
        безубытка выглядит как "CRT ВЫХОД GER40 (после БУ)", без long/short.
        Тогда двигаем стоп у всех позиций по символу — при
        guards.max_open_positions_per_symbol=1 она там всё равно одна.

        magic задан — трогаем ТОЛЬКО свою позицию этой стратегии. Иначе БУ-алерт
        (а этот путь включён по умолчанию) переставил бы стоп и у позиции,
        открытой руками на том же счёте по тому же символу.
        """
        err = self._wrong_account()
        if err:
            return OrderResult(False, err)
        mt5 = self.mt5
        done, failed = [], []
        for p in self.positions(magic=magic):
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

    def close_position(self, symbol: str, side: Optional[Side] = None,
                       magic: Optional[int] = None) -> OrderResult:
        """side=None — закрыть позицию по символу независимо от стороны
        (алерты выхода CRT тоже идут без long/short).

        magic задан — трогаем ТОЛЬКО свою позицию этой стратегии. Без фильтра
        exit-алерт закрыл бы любую позицию по символу, включая открытую руками на
        том же счёте (у cancel_pending фильтр по magic был, здесь — нет)."""
        err = self._wrong_account()
        if err:
            return OrderResult(False, err)
        mt5 = self.mt5
        for p in self.positions(magic=magic):
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
