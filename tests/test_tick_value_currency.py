"""Стоимость тика не-USD инструмента должна быть в валюте счёта.

Регрессия на июль 2026: GER30m (contract=1, currency_profit=EUR) — MT5 отдал
trade_tick_value=0.1, то есть сырое tick_size × contract_size в EUR, без
конвертации. Бот сайзил по $1.00/пункт вместо ~$1.15 и вместо целевого 1.00%
риска взял 1.17% (docs/just2trade-actual-costs.md).
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.broker_mt5 import MT5Broker
from bot.config import MT5Creds
from bot.sizing import SymbolSpec, compute_lots

EURUSD_RATE = 1.15


def _symbol_info(name, tick_size, tick_value, contract, profit_ccy, digits=1):
    return types.SimpleNamespace(
        name=name, volume_min=1.0, volume_max=300.0, volume_step=1.0,
        trade_tick_size=tick_size, point=tick_size,
        trade_tick_value=tick_value, trade_tick_value_loss=tick_value,
        trade_contract_size=contract, currency_profit=profit_ccy, digits=digits)


# Символы нового счёта 246113, значения сняты с живого терминала 2026-08-03.
SYMBOLS = {
    "GER30m": _symbol_info("GER30m", 0.1, 0.1, 1.0, "EUR"),
    "USTECH100m": _symbol_info("USTECH100m", 0.1, 0.1, 1.0, "USD"),
    # FX: MT5 конвертирует сам — 100 JPY за тик отдаёт как 0.638 USD
    "GBPJPY": _symbol_info("GBPJPY", 0.001, 0.6381, 100000.0, "JPY", digits=3),
    "EURUSD": _symbol_info("EURUSD", 0.00001, 1.0, 100000.0, "USD", digits=5),
    "EURUSD_rate": None,
}


class _FakeMt5:
    """Минимальный MT5: счёт в USD и котировка EURUSD для кросс-курса."""

    def __init__(self):
        self.selected = []

    def symbol_select(self, name, enable=True):
        self.selected.append(name)
        return name in SYMBOLS or name == "EURUSD"

    def symbol_info(self, name):
        return SYMBOLS.get(name)

    def symbol_info_tick(self, name):
        if name == "EURUSD":
            return types.SimpleNamespace(bid=EURUSD_RATE - 0.00005,
                                         ask=EURUSD_RATE + 0.00005)
        return None

    def account_info(self):
        return types.SimpleNamespace(currency="USD", equity=100000.0)


def _broker():
    b = MT5Broker(MT5Creds(login=0, password="", server="", terminal_path=""))
    b.mt5 = _FakeMt5()
    return b


def test_ger30m_tick_value_converted_to_account_currency():
    spec = _broker().symbol_spec("GER30m")
    # 0.1 EUR за тик × 1.15 = 0.115 USD за тик = $1.15 за пункт
    assert abs(spec.tick_value - 0.115) < 1e-9
    assert abs(spec.tick_value / spec.tick_size - 1.15) < 1e-9


def test_ger30m_sizing_hits_target_risk():
    """Проверка ТЗ на GER30m, с поправкой на дискретность лота.

    ТЗ просит риск 1.00% ± 0.01 п.п. На GER30m это недостижимо ни при какой
    реализации: volume_step=1.0, и один лот при SL 37 пунктов стоит 0.043 п.п.
    риска (при SL 120 — 0.138 п.п.). Сайзинг округляет лот ВНИЗ, чтобы никогда
    не превысить заданный риск, поэтому корректный критерий здесь такой:
    недобор, и меньше одного шага лота. Допуск ±0.01 проверяется на FX ниже.
    """
    spec = _broker().symbol_spec("GER30m")
    equity, target = 100000.0, 1.0
    # SL 37 пунктов — как в июльской сделке GER30m (25727 → 25764)
    r = compute_lots(equity, target, entry=25727.0, sl=25764.0, spec=spec)
    assert r.ok
    actual_pct = r.lots * r.loss_per_lot / equity * 100.0
    step_pct = r.loss_per_lot * spec.volume_step / equity * 100.0
    assert actual_pct <= target, actual_pct           # перебора нет
    assert target - actual_pct < step_pct, actual_pct  # ближе не встать
    # для протокола: 23 лота, 0.979% — против 1.17% на июльском багe
    assert abs(actual_pct - 0.97865) < 1e-4, actual_pct


def test_fx_sizing_within_tz_tolerance():
    """Допуск ТЗ ±0.01 п.п. — там, где шаг лота 0.01 его позволяет."""
    spec = _broker().symbol_spec("EURUSD")
    equity, target = 100000.0, 1.0
    r = compute_lots(equity, target, entry=1.14355, sl=1.14105, spec=spec)
    assert r.ok
    actual_pct = r.lots * r.loss_per_lot / equity * 100.0
    assert abs(actual_pct - target) <= 0.01, actual_pct


def test_july_bug_reproduces_without_conversion():
    """Без конвертации тот же вход даёт заметный перебор риска."""
    broken = SymbolSpec("GER30m", volume_min=1.0, volume_max=300.0,
                        volume_step=1.0, tick_size=0.1, tick_value=0.1)
    equity = 100000.0
    r = compute_lots(equity, 1.0, entry=25727.0, sl=25764.0, spec=broken)
    # лот посчитан по $1.00/пункт, а стоить будет $1.15/пункт
    real_pct = r.lots * (r.loss_per_lot * 1.15) / equity * 100.0
    assert real_pct > 1.10, real_pct


def test_usd_symbol_untouched():
    spec = _broker().symbol_spec("USTECH100m")
    assert abs(spec.tick_value - 0.1) < 1e-12


def test_already_converted_fx_untouched():
    """GBPJPY: MT5 уже сконвертировал (0.638 ≠ сырых 100 JPY) — не трогаем."""
    spec = _broker().symbol_spec("GBPJPY")
    assert abs(spec.tick_value - 0.6381) < 1e-12
