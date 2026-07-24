"""Тесты фильтра снятия месячного фрактала (src/backtest/monthly_filter.py)
на синтетических MN1/D1-барах — без похода в MT5."""
from __future__ import annotations

import pandas as pd

from src.backtest.monthly_filter import blocked_share, is_blocked, monthly_sweep_blocks


def _bars(times: list[str], highs: list[float], lows: list[float], closes: list[float] | None = None) -> pd.DataFrame:
    if closes is None:
        closes = [(h + l) / 2 for h, l in zip(highs, lows)]
    return pd.DataFrame(
        {
            "time": pd.to_datetime(times),
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "tick_volume": [100] * len(times),
        }
    )


# MN1 с фрактал-хаем 1.30 на втором баре (выше соседей 1.20 и 1.25),
# confirmed с открытия 4-го бара (2026-04-01).
MN1 = _bars(
    ["2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01"],
    highs=[1.20, 1.30, 1.25, 1.28],
    lows=[1.00, 1.10, 1.05, 1.08],
    closes=[1.10, 1.20, 1.10, 1.15],
)


def test_sweep_blocks_until_daily_imbalance_passes_level():
    """Снятие фрактал-хая ЗАКРЫТИЕМ D1 -> блок; бычий D1 FVG через уровень -> блок снят."""
    d1 = _bars(
        ["2026-04-01", "2026-04-02", "2026-04-03", "2026-04-06", "2026-04-07"],
        highs=[1.28, 1.32, 1.29, 1.36, 1.40],
        lows=[1.20, 1.25, 1.24, 1.28, 1.32],  # low[4]=1.32 > high[2]=1.29 -> бычий FVG [1.29..1.32], top >= 1.30
        closes=[1.25, 1.31, 1.26, 1.34, 1.38],  # close[1]=1.31 > 1.30 -> снятие закрытием
    )
    blocks = monthly_sweep_blocks(MN1, d1)
    assert len(blocks) == 1
    start, end = blocks[0]
    assert start == pd.Timestamp("2026-04-02")  # первый бар, ЗАКРЫВШИЙСЯ выше 1.30
    assert end == pd.Timestamp("2026-04-07")  # FVG confirmed третьей свечой (formed_at_idx=4)

    assert not is_blocked(pd.Timestamp("2026-04-01 15:00"), blocks)
    assert is_blocked(pd.Timestamp("2026-04-02 00:00"), blocks)
    assert is_blocked(pd.Timestamp("2026-04-06 23:00"), blocks)
    assert not is_blocked(pd.Timestamp("2026-04-07 00:00"), blocks)


def test_wick_beyond_level_does_not_block():
    """Прокол уровня ТЕНЬЮ без закрытия за ним — не снятие (смягчение по
    просьбе трейдера 2026-07-02: первая версия с тенью блокировала 37-66%
    всего времени)."""
    d1 = _bars(
        ["2026-04-01", "2026-04-02", "2026-04-03"],
        highs=[1.28, 1.33, 1.29],  # high[1]=1.33 > 1.30, но закрытие ниже
        lows=[1.20, 1.25, 1.24],
        closes=[1.25, 1.29, 1.26],
    )
    blocks = monthly_sweep_blocks(MN1, d1)
    assert blocks == []


def test_reversal_imbalance_also_unblocks():
    """Разворотный имбаланс тоже снимает запрет (подтверждено трейдером
    2026-07-02): цена закрылась выше месячного фрактал-хая, развернулась
    и оставила МЕДВЕЖИЙ D1 FVG ниже уровня — блокировка снята, ждать
    прохода вверх не нужно."""
    d1 = _bars(
        ["2026-04-01", "2026-04-02", "2026-04-03", "2026-04-06"],
        highs=[1.28, 1.32, 1.31, 1.24],  # high[3]=1.24 < low[1]=1.25 -> медвежий FVG [1.24..1.25]
        lows=[1.20, 1.25, 1.22, 1.15],
        closes=[1.25, 1.31, 1.23, 1.18],  # close[1]=1.31 > 1.30 -> снятие; дальше разворот вниз
    )
    blocks = monthly_sweep_blocks(MN1, d1)
    assert len(blocks) == 1
    start, end = blocks[0]
    assert start == pd.Timestamp("2026-04-02")
    assert end == pd.Timestamp("2026-04-06")  # медвежий FVG confirmed третьей свечой


def test_sweep_without_imbalance_blocks_to_data_end():
    """Фрактал снят закрытием, но дневного имбаланса через уровень так и нет — блок бессрочный."""
    d1 = _bars(
        ["2026-04-01", "2026-04-02", "2026-04-03", "2026-04-06"],
        highs=[1.28, 1.32, 1.29, 1.28],
        lows=[1.20, 1.25, 1.24, 1.22],  # разрывов нет
        closes=[1.25, 1.31, 1.26, 1.25],  # close[1] > 1.30 -> снятие
    )
    blocks = monthly_sweep_blocks(MN1, d1)
    assert len(blocks) == 1
    assert blocks[0][1] is None
    assert is_blocked(pd.Timestamp("2027-01-01"), blocks)


def test_level_already_passed_by_monthly_close_is_not_tracked():
    """Уровень пройден закрытием месячной свечи ещё до первого D1-бара — не блокируем."""
    mn1 = _bars(
        ["2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01"],
        highs=[1.20, 1.30, 1.25, 1.40],
        lows=[1.00, 1.10, 1.05, 1.20],
        closes=[1.10, 1.20, 1.10, 1.35],  # апрельская свеча ЗАКРЫЛАСЬ выше 1.30
    )
    d1 = _bars(
        ["2026-05-01", "2026-05-04"],
        highs=[1.42, 1.43],  # формально high > 1.30, но уровень давно пройден
        lows=[1.36, 1.37],
    )
    blocks = monthly_sweep_blocks(mn1, d1)
    assert blocks == []


def test_blocked_share_merges_overlaps_and_clips():
    blocks = [
        (pd.Timestamp("2026-01-01"), pd.Timestamp("2026-01-11")),
        (pd.Timestamp("2026-01-06"), pd.Timestamp("2026-01-16")),  # перекрывается с первым
    ]
    share = blocked_share(blocks, pd.Timestamp("2026-01-01"), pd.Timestamp("2026-01-31"))
    assert abs(share - 15 / 30) < 1e-9
