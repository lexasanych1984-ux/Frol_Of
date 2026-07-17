import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.model import Action, OrderKind, Side
from bot.parser import parse


def test_smc_long_market():
    s = parse("LONG EURUSD | вход 1.14355 | SL 1.14100 | TP 1.14900 | RR 2.00")
    assert s.action is Action.ENTRY
    assert s.side is Side.LONG
    assert s.symbol_tv == "EURUSD"
    assert s.order_kind is OrderKind.MARKET
    assert s.entry == 1.14355
    assert s.sl == 1.14100
    assert s.tp == 1.14900
    assert s.rr == 2.0


def test_asweep_short_market():
    s = parse("SHORT NAS100 | вход 20100 | SL 20180 | TP 19900 | RR 2.5")
    assert s.side is Side.SHORT
    assert s.symbol_tv == "NAS100"
    assert s.order_kind is OrderKind.MARKET
    assert s.sl == 20180


def test_crt_limit_short():
    s = parse("CRT SHORT (лимит) GER40 | вход 24500 | SL 24620 | TP 24100 | RR 3.0")
    assert s.side is Side.SHORT
    assert s.symbol_tv == "GER40"
    assert s.order_kind is OrderKind.LIMIT
    assert s.strategy == "crt"
    assert s.entry == 24500


def test_crt_stop_long():
    s = parse("CRT LONG GER40 | вход 24500 | SL 24400 | TP 24800 | RR 3.0")
    assert s.side is Side.LONG
    assert s.order_kind is OrderKind.STOP
    assert s.symbol_tv == "GER40"


def test_exit_plain():
    s = parse("ВЫХОД long EURUSD")
    assert s.action is Action.EXIT
    assert s.side is Side.LONG
    assert s.symbol_tv == "EURUSD"


def test_exit_breakeven():
    s = parse("ВЫХОД long (после БУ) EURUSD")
    assert s.action is Action.BREAKEVEN
    assert s.side is Side.LONG
    assert s.symbol_tv == "EURUSD"


def test_crt_exit():
    s = parse("CRT ВЫХОД short GER40")
    assert s.action is Action.EXIT
    assert s.side is Side.SHORT
    assert s.symbol_tv == "GER40"


def test_json_entry():
    s = parse('{"action":"entry","side":"long","symbol":"FX:EURUSD",'
              '"order_kind":"market","entry":1.1435,"sl":1.141,"tp":1.149,"id":"x1"}')
    assert s.action is Action.ENTRY
    assert s.side is Side.LONG
    assert s.symbol_tv == "EURUSD"
    assert s.signal_id == "x1"
    assert s.dedup_key() == "id:x1"


def test_garbage_returns_none():
    assert parse("просто текст без сигнала") is None
    assert parse("") is None


def test_entry_without_sl_rejected():
    assert parse("LONG EURUSD | вход 1.14 | TP 1.15") is None
