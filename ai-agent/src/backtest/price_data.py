"""OHLC-бары для бэктеста — через пакет `MetaTrader5` (Windows-only, нужен
запущенный и залогиненный терминал; см. src/mt5_import/live_export.py, тот
же паттерн задокументирован там для будущего /sync).

Кэш (`bars_cache.py`) — по границам диапазона, а не по дырам внутри: если
запрошенный [date_from, date_to] не покрыт целиком, докачивается и
перезаписывается весь диапазон заново. Для бэктеста на исторических данных
(не live-стриминг) это простое и достаточно дешёвое поведение — избегает
багов сшивки частичных диапазонов ценой отдельных повторных докачек.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from . import bars_cache
from .progress import log

TIMEFRAMES = {
    "MN1": "TIMEFRAME_MN1",
    "D1": "TIMEFRAME_D1",
    "H4": "TIMEFRAME_H4",
    "H1": "TIMEFRAME_H1",
    "M15": "TIMEFRAME_M15",
}


def _mt5_timeframe(timeframe: str):
    import MetaTrader5 as mt5  # импорт внутри функции: пакет Windows-only,

    # не должен ломать импорт модуля на других ОС / в тестах без MT5.
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Неизвестный таймфрейм: {timeframe!r} (ожидались {list(TIMEFRAMES)})")
    return getattr(mt5, TIMEFRAMES[timeframe])


# MT5 copy_rates_range молча возвращает None ("Invalid params"), если запрошенный
# диапазон отдаёт слишком много баров за раз (совпадает с terminal_info().maxbars
# ~100000) — обнаружено на реальном запросе 3-летнего диапазона M15 (~115K баров).
# Чтобы не гадать точный лимит на разных таймфреймах, режем запрос на чанки по
# датам фиксированного размера вне зависимости от ТФ — для D1/H4/H1 это просто
# лишние (дешёвые) запросы, для M15 — единственный способ не упереться в лимит.
_CHUNK = timedelta(days=60)


def _fetch_from_mt5(symbol: str, timeframe: str, date_from: datetime, date_to: datetime) -> pd.DataFrame:
    import MetaTrader5 as mt5

    if not mt5.initialize():
        raise RuntimeError(
            f"Не удалось подключиться к терминалу MT5: {mt5.last_error()}. "
            "Терминал должен быть запущен и залогинен на этой машине."
        )
    try:
        chunks: list[pd.DataFrame] = []
        chunk_start = date_from
        total_days = max((date_to - date_from).days, 1)
        n_chunks = -(-total_days // _CHUNK.days)  # ceil
        chunk_no = 0
        while chunk_start < date_to:
            chunk_no += 1
            chunk_end = min(chunk_start + _CHUNK, date_to)
            log(f"MT5: качаю {symbol}/{timeframe} чанк {chunk_no}/{n_chunks} [{chunk_start:%Y-%m-%d}..{chunk_end:%Y-%m-%d}]")
            rates = mt5.copy_rates_range(symbol, _mt5_timeframe(timeframe), chunk_start, chunk_end)
            if rates is None:
                raise RuntimeError(
                    f"copy_rates_range вернул None для {symbol}/{timeframe} "
                    f"[{chunk_start}, {chunk_end}]: {mt5.last_error()}"
                )
            if len(rates):
                chunks.append(pd.DataFrame(rates))
            chunk_start = chunk_end
    finally:
        mt5.shutdown()

    if not chunks:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "tick_volume"])
    df = pd.concat(chunks, ignore_index=True)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df = df.drop_duplicates(subset="time").sort_values("time").reset_index(drop=True)
    return df[["time", "open", "high", "low", "close", "tick_volume"]]


def fetch_bars(symbol: str, timeframe: str, date_from: datetime, date_to: datetime) -> pd.DataFrame:
    """Возвращает бары за [date_from, date_to] — из кэша, докачивая недостающее через MT5."""
    conn = bars_cache.connect()
    covered = bars_cache.covered_range(conn, symbol, timeframe)

    # небольшой запас на границах — copy_rates_range у MT5 иногда отдаёт
    # на бар меньше на самой границе диапазона, если запросить впритык.
    pad = timedelta(days=1)
    fully_covered = covered is not None and covered[0] <= date_from + pad and covered[1] >= date_to - pad

    if not fully_covered:
        fresh = _fetch_from_mt5(symbol, timeframe, date_from - pad, date_to + pad)
        bars_cache.upsert_bars(conn, symbol, timeframe, fresh)
    else:
        log(f"кэш: {symbol}/{timeframe} [{date_from:%Y-%m-%d}..{date_to:%Y-%m-%d}] уже есть локально")

    return bars_cache.get_bars(conn, symbol, timeframe, date_from, date_to)
