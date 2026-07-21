"""Коридоры бэктеста загружаются из боевого expectations.yaml без сюрпризов.

Метки стратегий должны совпадать с именами из bot/stats (SMC/CRT/ASIA), иначе
факт демо не сматчится с коридором.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import expectations as expmod
from bot import stats as st


def test_loads_three_strategies():
    exp = expmod.load()
    assert set(exp.by_key) == {"smc", "crt", "asweep"}
    assert exp.min_sample == 20
    assert exp.risk_overshoot_pp == 0.3


def test_labels_match_stats_names():
    exp = expmod.load()
    labels = {e.label for e in exp.by_key.values()}
    # именно этими метками stats.py помечает сделки бота
    assert labels <= set(st.MAGIC_NAME.values())
    assert exp.by_label.keys() >= {"SMC", "CRT", "ASIA"}


def test_bands_parsed():
    exp = expmod.load()
    crt = exp.for_label("CRT")
    assert crt.win_rate.lo == 0.18 and crt.win_rate.hi == 0.25
    assert crt.max_stop_streak == 7
    assert crt.worst_month_pct == -3.6
    assert "GER40" in crt.instruments
