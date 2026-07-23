"""Рендер PNG-графиков доходности из закрытых сделок (matplotlib, backend Agg).

Обходной путь для бесплатного тарифа Notion (там всего 1 нативный чарт на весь
воркспейс): бот сам рисует картинки и встраивает их в страницу Notion как image-
блоки — картинки лимитом на чарты не считаются. Источник данных — те же закрытые
сделки и та же кривая equity, что в trades.csv (bot/stats.equity_curve).

Каждая функция возвращает PNG-байты. render_all() отдаёт список
(имя_файла, подпись, png_bytes) для заливки в Notion.
"""
from __future__ import annotations

import io
from typing import List, Optional, Tuple

import matplotlib
matplotlib.use("Agg")  # без дисплея — только файлы; ставить ДО pyplot
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

from . import stats  # noqa: E402

_GREEN = "#2e9b57"
_RED = "#d1495b"
_GRID = "#d7dbe0"


def _fig(width=9.0, height=4.4):
    fig, ax = plt.subplots(figsize=(width, height), dpi=130)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    ax.grid(True, color=_GRID, linewidth=0.8, alpha=0.9)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    return fig, ax


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()


def _fmt_time_axis(ax, times) -> None:
    span_days = (max(times) - min(times)).total_seconds() / 86400 if len(times) > 1 else 0
    if span_days <= 2:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m %H:%M"))
    else:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    for lbl in ax.get_xticklabels():
        lbl.set_rotation(0)
        lbl.set_fontsize(8)


def render_equity(trades, start_balance: Optional[float]) -> bytes:
    """Кривая баланса: старт → equity после каждой закрытой сделки."""
    rows = list(stats.equity_curve(trades, start_balance))
    fig, ax = _fig()
    xs, ys = [], []
    if start_balance is not None and rows:
        xs.append(rows[0][0].open_time)
        ys.append(start_balance)
    for t, equity, _cum in rows:
        if equity is None:
            continue
        xs.append(t.close_time)
        ys.append(equity)
    if len(xs) >= 1:
        ax.plot(xs, ys, marker="o", markersize=4, linewidth=1.8, color=_GREEN)
        if start_balance is not None:
            ax.axhline(start_balance, color="#9aa2ab", linewidth=1.0,
                       linestyle="--", alpha=0.8)
        last = ys[-1]
        ax.annotate(f"{last:,.0f}$", (xs[-1], last), textcoords="offset points",
                    xytext=(6, 6), fontsize=9, fontweight="bold",
                    color=_GREEN if last >= (start_balance or last) else _RED)
        _fmt_time_axis(ax, xs)
    ax.set_title("Кривая доходности (equity после каждой сделки)", fontsize=12,
                 fontweight="bold")
    ax.set_ylabel("Баланс, USD", fontsize=9)
    return _png(fig)


def render_pnl_by_strategy(trades) -> bytes:
    """Столбики: чистый P&L по каждой стратегии (сделки бота)."""
    by: dict[str, float] = {}
    for t in trades:
        by[t.strategy] = round(by.get(t.strategy, 0.0) + t.profit, 2)
    items = sorted(by.items(), key=lambda kv: kv[1])
    fig, ax = _fig(width=8.0, height=4.2)
    if items:
        names = [k for k, _ in items]
        vals = [v for _, v in items]
        colors = [_GREEN if v >= 0 else _RED for v in vals]
        bars = ax.bar(names, vals, color=colors, width=0.6)
        ax.axhline(0, color="#6b7280", linewidth=1.0)
        for b, v in zip(bars, vals):
            ax.annotate(f"{v:+,.0f}$", (b.get_x() + b.get_width() / 2,
                        v), textcoords="offset points",
                        xytext=(0, 4 if v >= 0 else -12), ha="center",
                        fontsize=9, fontweight="bold")
    ax.set_title("Чистый P&L по стратегиям, USD", fontsize=12, fontweight="bold")
    ax.set_ylabel("P&L, USD", fontsize=9)
    return _png(fig)


def render_cumulative_by_strategy(trades) -> bytes:
    """Линии: накопленный P&L внутри каждой стратегии по времени."""
    fig, ax = _fig()
    strategies = sorted({t.strategy for t in trades})
    palette = ["#2e9b57", "#d1495b", "#3b6fb0", "#e0a458", "#8a5cd1"]
    any_line = False
    for i, strat in enumerate(strategies):
        sts = sorted([t for t in trades if t.strategy == strat],
                     key=lambda x: x.close_time)
        if not sts:
            continue
        xs, ys, run = [], [], 0.0
        for t in sts:
            run = round(run + t.profit, 2)
            xs.append(t.close_time)
            ys.append(run)
        ax.plot(xs, ys, marker="o", markersize=3, linewidth=1.6,
                color=palette[i % len(palette)], label=strat)
        any_line = True
    ax.axhline(0, color="#9aa2ab", linewidth=1.0, linestyle="--", alpha=0.8)
    if any_line:
        all_times = [t.close_time for t in trades]
        _fmt_time_axis(ax, all_times)
        ax.legend(fontsize=9, frameon=False)
    ax.set_title("Накопленный P&L по стратегиям, USD", fontsize=12,
                 fontweight="bold")
    ax.set_ylabel("Кумул. P&L, USD", fontsize=9)
    return _png(fig)


def render_all(trades, start_balance: Optional[float]
               ) -> List[Tuple[str, str, bytes]]:
    """(filename, caption, png_bytes) для всех графиков. Пусто, если нет сделок."""
    if not trades:
        return []
    return [
        ("equity.png", "Кривая доходности счёта", render_equity(trades, start_balance)),
        ("pnl_by_strategy.png", "Чистый P&L по стратегиям",
         render_pnl_by_strategy(trades)),
        ("cumulative_by_strategy.png", "Накопленный P&L по стратегиям",
         render_cumulative_by_strategy(trades)),
    ]
