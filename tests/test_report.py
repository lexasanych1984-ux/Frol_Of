"""Аналитика отчёта: классификация исходов, серии стопов, вердикты, честность.

ПРИЁМКА (из ТЗ): при n<20 отчёт скромничает («мало данных»), а не тревожит.
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import report as rep
from bot.expectations import Band, StrategyExpectation
from bot.stats import ClosedTrade


def _trade(pid, profit, strategy="SMC", when=None, symbol="EURUSD"):
    when = when or datetime(2026, 7, 10, 12, 0)
    return ClosedTrade(position_id=pid, symbol=symbol, strategy=strategy,
                       side="long", volume=1.0, open_time=when, close_time=when,
                       price_open=1.0, price_close=1.0, profit=profit)


# ── Классификация ─────────────────────────────────────────────────────────────
def test_classify_by_r_multiple():
    assert rep.classify(250, one_r=100) == "win"     # +2.5R
    assert rep.classify(-100, one_r=100) == "stop"   # -1R
    assert rep.classify(0.0, one_r=100) == "be"      # ~0R
    assert rep.classify(-10, one_r=100) == "be"      # -0.1R (БУ с гэпом)


def test_classify_without_r_falls_back_to_sign():
    assert rep.classify(5, one_r=None) == "win"
    assert rep.classify(-5, one_r=None) == "stop"


def test_one_r_from_exec_log_takes_priority():
    trades = [_trade(1, -100), _trade(2, 250)]
    exec_index = {1: {"plan_risk_amount": "500"}, 2: {"plan_risk_amount": "500"}}
    # 1R=500 → -100 это -0.2R → BE (а не stop), 250 это +0.5R → win
    outs = dict((t.position_id, o) for t, o in rep.classify_trades(trades, exec_index))
    assert outs[1] == "be"
    assert outs[2] == "win"


# ── Серии стопов ──────────────────────────────────────────────────────────────
def test_stop_streaks_max_and_current():
    outs = ["stop", "stop", "win", "stop", "stop", "stop"]
    mx, cur = rep.stop_streaks(outs)
    assert mx == 3 and cur == 3


def test_be_breaks_streak():
    outs = ["stop", "stop", "be", "stop"]
    mx, cur = rep.stop_streaks(outs)
    assert mx == 2 and cur == 1


def test_current_stop_streaks_per_strategy():
    trades = [_trade(1, -100, "CRT"), _trade(2, -100, "CRT"),
              _trade(3, 250, "SMC")]
    streaks = rep.current_stop_streaks(trades, {})
    assert streaks["CRT"] == 2
    assert streaks["SMC"] == 0


# ── Честность выборки ─────────────────────────────────────────────────────────
def _exp(label="SMC"):
    return StrategyExpectation(
        key="smc", label=label, instruments=["EURUSD"], risk_pct=2.0,
        win_rate=Band(0.27, 0.50), profit_factor=Band(1.4, 1.85),
        trades_per_month=Band(1.0, 3.0), be_share=Band(0.0, 0.05),
        max_stop_streak=6, worst_month_pct=-8.0)


def test_small_sample_stays_humble():
    trades = [_trade(i, 250 if i % 2 else -100) for i in range(5)]
    r = rep.build_strategy_report("SMC", trades, {}, _exp(), min_sample=20,
                                  ref_equity=100000)
    assert r.enough is False
    assert "мало данных" in r.note
    # ни одного тревожного вердикта — только «мало данных»
    assert all(row.verdict == rep.THIN for row in r.rows if row.verdict != "—")


def test_enough_sample_produces_verdicts():
    # 25 сделок: 12 win (+250), 8 stop (-100), 5 be (0) → WR 48%, PF 3.75, BE 20%
    trades = ([_trade(i, 250) for i in range(12)] +
              [_trade(100 + i, -100) for i in range(8)] +
              [_trade(200 + i, 0.0) for i in range(5)])
    r = rep.build_strategy_report("SMC", trades, {}, _exp(), min_sample=20,
                                  ref_equity=100000)
    assert r.enough is True
    assert r.wins == 12 and r.stops == 8 and r.be == 5
    verdicts = {row.name: row.verdict for row in r.rows}
    assert verdicts["Winrate"] == rep.OK        # 48% ≥ 27%
    assert verdicts["Profit Factor"] == rep.OK  # 3.75 ≥ 1.4


def test_max_stop_streak_over_history_is_problem():
    # 20 сделок, из них хвост — 8 стопов подряд при историческом максимуме 6
    trades = [_trade(i, 250) for i in range(12)]
    trades += [_trade(100 + i, -100) for i in range(8)]
    r = rep.build_strategy_report("SMC", trades, {}, _exp(), min_sample=20,
                                  ref_equity=100000)
    row = next(row for row in r.rows if row.name == "Макс серия стопов")
    assert r.max_stop_streak == 8
    assert row.verdict == rep.PROB              # 8 > 6+1


# ── Сводка проскальзывания ────────────────────────────────────────────────────
def test_slippage_summary_splits_index_and_fx():
    rows = [
        {"symbol_tv": "GER40", "order_kind": "market", "dry_run": "0",
         "slip_pips": "5", "risk_delta_pp": "0.4", "adverse": "1"},
        {"symbol_tv": "GER40", "order_kind": "market", "dry_run": "0",
         "slip_pips": "-3", "risk_delta_pp": "-0.1", "adverse": "0"},
        {"symbol_tv": "EURUSD", "order_kind": "market", "dry_run": "0",
         "slip_pips": "2", "risk_delta_pp": "0.05", "adverse": "1"},
        # dry-run и отложенные — игнор
        {"symbol_tv": "EURUSD", "order_kind": "market", "dry_run": "1",
         "slip_pips": "99", "risk_delta_pp": "9", "adverse": "1"},
        {"symbol_tv": "GER40", "order_kind": "limit", "dry_run": "0",
         "slip_pips": "99", "risk_delta_pp": "9", "adverse": "1"},
    ]
    s = rep.slippage_summary(rows)
    idx = {r.symbol: r for r in s["indices"]}
    fx = {r.symbol: r for r in s["fx"]}
    assert idx["GER40"].n == 2
    assert idx["GER40"].worst_pips == 5.0
    assert idx["GER40"].worst_risk_delta_pp == 0.4
    assert idx["GER40"].adverse == 1
    assert fx["EURUSD"].n == 1              # dry-run отброшен
    assert "GER40" not in fx


def test_render_markdown_smoke():
    trades = [_trade(i, 250 if i % 2 else -100) for i in range(4)]
    r = rep.build_strategy_report("SMC", trades, {}, _exp(), min_sample=20,
                                  ref_equity=100000)
    md = rep.render_markdown("2026-07", [r], {"indices": [], "fx": []},
                             datetime(2026, 8, 1, 9, 0), trades, 20)
    assert "# Отчёт эджа · 2026-07" in md
    assert "мало данных" in md
