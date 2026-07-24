"""Тесты поиска FTA/тейка (src/backtest/zones.py) на синтетических зонах —
без похода в df/MT5, напрямую конструируем Zone."""
from __future__ import annotations

from src.backtest.structure import Zone
from src.backtest.zones import nearest_opposite_zone, take_profit_zone


def test_nearest_opposite_zone_buy_picks_closest_resistance_above():
    entry = 1.10
    zones = [
        Zone("FVG", "down", top=1.20, bottom=1.15, formed_at_idx=0),  # дальше
        Zone("OB", "down", top=1.14, bottom=1.12, formed_at_idx=0),  # ближе -> FTA
        Zone("FVG", "up", top=1.09, bottom=1.05, formed_at_idx=0),  # не та сторона (support, не resistance)
    ]
    fta = nearest_opposite_zone(zones, "buy", entry, as_of_idx=10)
    assert fta is not None and fta.bottom == 1.12


def test_nearest_opposite_zone_sell_picks_closest_support_below():
    entry = 1.10
    zones = [
        Zone("FVG", "up", top=1.05, bottom=1.00, formed_at_idx=0),  # дальше
        Zone("OB", "up", top=1.08, bottom=1.06, formed_at_idx=0),  # ближе -> FTA
    ]
    fta = nearest_opposite_zone(zones, "sell", entry, as_of_idx=10)
    assert fta is not None and fta.top == 1.08


def test_nearest_opposite_zone_none_when_no_candidates():
    assert nearest_opposite_zone([], "buy", 1.10, as_of_idx=10) is None


def test_nearest_opposite_zone_respects_as_of_idx_no_lookahead():
    zones = [Zone("OB", "down", top=1.14, bottom=1.12, formed_at_idx=50)]
    assert nearest_opposite_zone(zones, "buy", 1.10, as_of_idx=10) is None


def test_take_profit_zone_uses_zone_beyond_fta():
    entry = 1.10
    fta = Zone("OB", "down", top=1.14, bottom=1.12, formed_at_idx=0)
    zones = [fta, Zone("FVG", "down", top=1.25, bottom=1.20, formed_at_idx=0)]
    tp = take_profit_zone(zones, "buy", entry, fta, as_of_idx=10)
    assert tp.bottom == 1.20


def test_take_profit_zone_falls_back_to_fta_when_nothing_beyond():
    entry = 1.10
    fta = Zone("OB", "down", top=1.14, bottom=1.12, formed_at_idx=0)
    tp = take_profit_zone([fta], "buy", entry, fta, as_of_idx=10)
    assert tp is fta
