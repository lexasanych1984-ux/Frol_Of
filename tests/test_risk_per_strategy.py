"""Риск на сделку берётся по стратегии, а не один на всех.

Расхождение найдено 2026-07-27: в TradingView три SMC-потока задеплоены на 2%
(in_0=2), а бот сайзил ВСЕ сделки по единственному risk.risk_pct_per_trade = 1%.
Подтверждение из боя: SMC GBPJPY закрылся −976.79 USD при equity 99224.58, т.е.
1R вместо 2R. У CRT Day и Asia Sweep 1% совпадал и так — их поведение меняться
не должно.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import Config
from bot.model import Action, OrderKind, Side, Signal
from bot.risk import RiskManager
from bot.sizing import SymbolSpec

EUR = SymbolSpec("EURUSD", volume_min=0.01, volume_max=100, volume_step=0.01,
                 tick_size=0.00001, tick_value=1.0)

ENTRY, SL = 1.14222, 1.14495
SMC_2PCT = {"smc": {"risk_pct_per_trade": 2.0}}


class FakeAccount:
    equity = 100000.0
    currency = "USD"


class FakeState:
    def start_of_day_equity(self, equity):
        return equity

    def is_duplicate(self, key, window):
        return False


def _config(per_strategy=None):
    cfg = Config.__new__(Config)
    cfg.risk = {"risk_pct_per_trade": 1.0, "skip_if_below_min_lot": True,
                "max_lot_per_trade": 25.0}
    if per_strategy is not None:
        cfg.risk["per_strategy"] = per_strategy
    cfg.guards = {}
    cfg.trading_hours = []
    cfg.symbol_map = {"EURUSD": "EURUSD"}
    return cfg


def _signal(strategy):
    return Signal(action=Action.ENTRY, side=Side.SHORT, symbol_tv="EURUSD",
                  entry=ENTRY, sl=SL, tp=1.13403, rr=3.0,
                  order_kind=OrderKind.MARKET, strategy=strategy, raw="")


def _risk_usd(lots):
    """Реальный риск в USD: дистанция в тиках * стоимость тика * лот."""
    return abs(SL - ENTRY) / EUR.tick_size * EUR.tick_value * lots


def _lots(cfg, strategy):
    rm = RiskManager(cfg, FakeState())
    d = rm.evaluate_entry(_signal(strategy), FakeAccount(), [], EUR,
                          market_price=ENTRY)
    assert d.allow, d.reason
    return d.lots


def test_smc_sized_at_two_percent():
    risk = _risk_usd(_lots(_config(SMC_2PCT), "smc"))
    assert abs(risk - 2000.0) <= 10.0, f"риск {risk:.0f} USD вместо ~2000"


def test_crt_and_asweep_keep_base_one_percent():
    """Переопределение SMC не должно задевать соседние потоки."""
    cfg = _config(SMC_2PCT)
    for strategy in ("crt", "asweep"):
        risk = _risk_usd(_lots(cfg, strategy))
        assert abs(risk - 1000.0) <= 10.0, f"{strategy}: {risk:.0f} USD вместо ~1000"


def test_resolver_handles_case_and_unknown_keys():
    cfg = _config(SMC_2PCT)
    assert cfg.risk_pct("smc") == 2.0
    assert cfg.risk_pct("SMC") == 2.0        # JSON-алерты могут прислать в верхнем
    assert cfg.risk_pct("crt") == 1.0
    assert cfg.risk_pct("unknown") == 1.0
    assert cfg.risk_pct(None) == 1.0         # стратегию не распознали → база


def test_without_per_strategy_behaviour_unchanged():
    """Конфиг без секции per_strategy работает как раньше — всё по базовому."""
    cfg = _config()
    assert cfg.risk_pct("smc") == 1.0
    assert abs(_risk_usd(_lots(cfg, "smc")) - 1000.0) <= 10.0
