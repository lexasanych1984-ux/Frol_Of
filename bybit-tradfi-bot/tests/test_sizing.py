import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.sizing import SymbolSpec, compute_lots

# EURUSD-подобный: tick_size 0.00001, tick_value $1.0 за тик на 1.0 лот
EUR = SymbolSpec("EURUSD.s", volume_min=0.01, volume_max=100, volume_step=0.01,
                 tick_size=0.00001, tick_value=1.0)


def test_basic_risk_math():
    # equity 10000, риск 1% = $100. дистанция 0.00250 = 250 тиков.
    # убыток/лот = 250 * 1.0 = $250. лот = 100/250 = 0.4
    r = compute_lots(10000, 1.0, entry=1.14355, sl=1.14105, spec=EUR)
    assert r.ok
    assert abs(r.lots - 0.4) < 1e-9
    assert abs(r.risk_amount - 100.0) < 1e-9
    assert abs(r.loss_per_lot - 250.0) < 1e-6


def test_rounds_down_to_step():
    # дистанция 0.00240 = 240 тиков -> 100/240 = 0.4166 -> вниз к 0.01 = 0.41
    r = compute_lots(10000, 1.0, entry=1.14355, sl=1.14115, spec=EUR)
    assert r.ok
    assert abs(r.lots - 0.41) < 1e-9


def test_skips_when_below_min():
    small = SymbolSpec("X", 1.0, 100, 1.0, 0.00001, 1.0)  # min лот = 1.0
    # риск $1, убыток/лот большой -> лот < 1
    r = compute_lots(100, 1.0, entry=1.1400, sl=1.1300, spec=small,
                     skip_if_below_min=True)
    assert not r.ok
    assert "min" in r.reason


def test_max_lot_cap():
    r = compute_lots(1_000_000, 1.0, entry=1.14355, sl=1.14105, spec=EUR, max_lot=2.0)
    assert r.ok
    assert r.lots == 2.0


def test_zero_sl_distance_rejected():
    r = compute_lots(10000, 1.0, entry=1.14, sl=1.14, spec=EUR)
    assert not r.ok


def test_sl_on_wrong_side_still_uses_distance():
    # для short SL выше входа — важна дистанция, а не знак
    r = compute_lots(10000, 1.0, entry=1.14105, sl=1.14355, spec=EUR)
    assert r.ok
    assert abs(r.lots - 0.4) < 1e-9
