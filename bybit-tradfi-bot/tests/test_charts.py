"""Рендер PNG-графиков доходности не должен падать и обязан отдавать валидный PNG."""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import charts
from bot.stats import ClosedTrade

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _t(pos, strat, sym, side, o, c, pnl):
    return ClosedTrade(pos, sym, strat, side, 1.0,
                       datetime(2026, 7, 22, o, 0), datetime(2026, 7, 22, c, 0),
                       100.0, 101.0, pnl)


def _trades():
    return [
        _t(1, "SMC", "GBPJPY", "long", 3, 4, -46.08),
        _t(2, "SMC", "GBPJPY", "long", 5, 6, -127.45),
        _t(3, "ASIA", "NAS100", "short", 22, 23, -760.0),
    ]


def test_render_all_returns_three_valid_pngs():
    imgs = charts.render_all(_trades(), 100000.0)
    assert [name for name, _c, _d in imgs] == [
        "equity.png", "pnl_by_strategy.png", "cumulative_by_strategy.png"]
    for _name, _cap, data in imgs:
        assert data[:8] == _PNG_MAGIC
        assert len(data) > 1000        # непустая картинка


def test_render_all_empty_when_no_trades():
    assert charts.render_all([], 100000.0) == []


def test_single_trade_and_no_start_balance_do_not_crash():
    one = [_t(1, "SMC", "GBPJPY", "long", 3, 4, -46.08)]
    assert charts.render_equity(one, None)[:8] == _PNG_MAGIC     # start_balance=None
    assert charts.render_equity(one, 100000.0)[:8] == _PNG_MAGIC
    assert charts.render_pnl_by_strategy(one)[:8] == _PNG_MAGIC
    assert charts.render_cumulative_by_strategy(one)[:8] == _PNG_MAGIC
