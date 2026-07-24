"""Тесты структурных примитивов (src/backtest/structure.py) на синтетических
барах — без похода в MT5, только детерминированные сценарии с известным
ожидаемым результатом."""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from src.backtest.structure import (
    detect_structure_break,
    find_fractals,
    find_fvg,
    find_order_blocks,
    structure_legs,
)


def _bars(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    """rows — список (open, high, low, close); время генерируется по часу подряд."""
    t0 = datetime(2026, 1, 1)
    return pd.DataFrame(
        {
            "time": [t0 + timedelta(hours=i) for i in range(len(rows))],
            "open": [r[0] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[2] for r in rows],
            "close": [r[3] for r in rows],
            "tick_volume": [100] * len(rows),
        }
    )


def test_find_fractals_high_and_low():
    # индекс 1 — явный high-фрактал (выше соседей 0 и 2), индекс 3 — low-фрактал.
    df = _bars(
        [
            (1.0, 1.05, 0.98, 1.02),
            (1.02, 1.20, 1.00, 1.10),  # high fractal
            (1.10, 1.08, 0.95, 1.00),
            (1.00, 1.02, 0.80, 0.90),  # low fractal
            (0.90, 1.00, 0.85, 0.95),
        ]
    )
    result = find_fractals(df)
    assert result["is_fractal_high"].tolist() == [False, True, False, False, False]
    assert result["is_fractal_low"].tolist() == [False, False, False, True, False]


def _uptrend_zigzag_rows() -> list[tuple[float, float, float, float]]:
    """Чистый зигзаг: 3 фрактал-хая (idx 1,4,7 = 1.10/1.20/1.30, монотонно
    растут) и 3 фрактал-лоу (idx 2,5,8 = 1.00/1.08/1.16, монотонно растут).
    high/low сконструированы как независимые серии, чтобы не было
    случайных лишних фракталов от "широких" свечей."""
    high = [1.01, 1.10, 1.06, 1.07, 1.20, 1.16, 1.12, 1.30, 1.26, 1.21]
    low = [0.99, 1.02, 1.00, 1.03, 1.09, 1.08, 1.10, 1.20, 1.16, 1.19]
    return [(round((h + l) / 2, 4), h, l, round((h + l) / 2, 4)) for h, l in zip(high, low)]


def test_structure_legs_uptrend():
    df = _bars(_uptrend_zigzag_rows())
    fractals = find_fractals(df)
    assert structure_legs(fractals, as_of_idx=len(df) - 1) == "up"


def test_structure_legs_insufficient_data_returns_none():
    df = _bars([(1.0, 1.05, 0.95, 1.0), (1.0, 1.10, 1.00, 1.05), (1.05, 1.06, 1.00, 1.02)])
    fractals = find_fractals(df)
    assert structure_legs(fractals, as_of_idx=len(df) - 1) is None


def test_detect_structure_break_down_break_in_uptrend():
    # Восходящая структура (как в test_structure_legs_uptrend), затем резкое
    # закрытие ниже последнего фрактал-лоу (1.16) -> слом структуры вниз.
    rows = _uptrend_zigzag_rows() + [(1.19, 1.19, 1.05, 1.10)]
    df = _bars(rows)
    fractals = find_fractals(df)
    break_idx = detect_structure_break(df, fractals, current_direction="up", as_of_idx=len(df) - 1)
    assert break_idx == len(df) - 1


def test_detect_structure_break_none_when_no_break():
    rows = [
        (1.00, 1.01, 0.99, 1.00),
        (1.00, 1.10, 1.00, 1.05),
        (1.05, 1.06, 1.02, 1.03),
        (1.03, 1.07, 1.03, 1.05),
        (1.05, 1.20, 1.05, 1.15),
        (1.15, 1.16, 1.08, 1.10),
        (1.10, 1.12, 1.09, 1.11),
    ]
    df = _bars(rows)
    fractals = find_fractals(df)
    assert detect_structure_break(df, fractals, current_direction="up", as_of_idx=len(df) - 1) is None


def test_find_fvg_bullish_gap():
    # bar0 high=1.05, bar2 low=1.10 -> разрыв (1.05, 1.10), bullish FVG confirmed на индексе 2.
    df = _bars(
        [
            (1.00, 1.05, 0.98, 1.02),
            (1.02, 1.08, 1.01, 1.06),
            (1.10, 1.15, 1.10, 1.12),
        ]
    )
    zones = find_fvg(df)
    assert len(zones) == 1
    z = zones[0]
    assert z.kind == "FVG" and z.direction == "up"
    assert z.bottom == 1.05 and z.top == 1.10
    assert z.formed_at_idx == 2


def test_find_order_blocks_bullish():
    # Фрактал-хай на индексе 1 (1.10), медвежья свеча на индексе 2 (close<open),
    # затем импульсные бычьи свечи пробивают 1.10 закрытием -> OB = свеча индекса 2.
    rows = [
        (1.00, 1.02, 0.99, 1.01),
        (1.01, 1.10, 1.00, 1.05),  # fractal high = 1.10
        (1.05, 1.06, 0.95, 0.97),  # медвежья (OB candidate)
        (0.97, 1.05, 0.96, 1.03),
        (1.03, 1.15, 1.02, 1.12),  # закрытие 1.12 > 1.10 -> пробой
    ]
    df = _bars(rows)
    fractals = find_fractals(df)
    obs = find_order_blocks(df, fractals)
    bullish_obs = [z for z in obs if z.direction == "up"]
    assert len(bullish_obs) == 1
    assert bullish_obs[0].bottom == 0.95 and bullish_obs[0].top == 1.06
