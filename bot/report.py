"""Отчёт «факт демо ↔ коридор бэктеста»: раннее предупреждение о деградации эджа.

Чистая аналитика поверх:
  • закрытых сделок из истории MT5 (bot/stats.ClosedTrade) — исходы, WR, PF;
  • лога проскальзывания (bot/execlog) — план↔факт по входам;
  • коридоров бэктеста (bot/expectations).

Ввод-вывод (MT5, файлы, Telegram) — в run.py. Здесь только расчёты и рендер,
поэтому логика классификации исходов и вердиктов покрыта тестами.

Классификация исхода сделки (по P&L относительно планового риска 1R):
  win  — прибыль ≥ +0.5R (дошли к тейку);
  stop — убыток ≤ −0.5R (полный стоп);
  be   — между ними (стоп в безубытке; для CRT это ~2/3 исходов — норма).
1R берётся из лога проскальзывания (plan_risk_amount) по position_id; если
записи нет (старая история), 1R оценивается медианой убытков стратегии.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from statistics import median
from typing import Dict, List, Optional, Tuple

from .expectations import Band, Expectations, StrategyExpectation
from .slippage import pip_size

# Инструменты-индексы: у них главный вопрос по проскальзыванию рыночных ордеров.
INDEX_SYMBOLS = {"GER40", "NAS100", "US30", "SPX500", "GER30m", "USTECH100m"}

OK, WARN, PROB, THIN = "OK", "ВНИМАНИЕ", "ПРОБЛЕМА", "мало данных"
_EMOJI = {OK: "🟢", WARN: "🟡", PROB: "🔴", THIN: "⏳"}
_SEVERITY = {THIN: 0, OK: 1, WARN: 2, PROB: 3}

BE_FRACTION = 0.5   # порог win/stop относительно 1R


# ── Классификация исходов ─────────────────────────────────────────────────────
def _one_r(strategy_trades: list, exec_index: dict) -> Dict[int, float]:
    """position_id → 1R (плановый риск). Из лога, иначе медиана убытков."""
    per_pos: Dict[int, float] = {}
    losses = []
    for t in strategy_trades:
        row = exec_index.get(t.position_id)
        r = None
        if row:
            try:
                r = abs(float(row.get("plan_risk_amount") or 0)) or None
            except (TypeError, ValueError):
                r = None
        if r:
            per_pos[t.position_id] = r
        if t.profit < 0:
            losses.append(abs(t.profit))
    fallback = median(losses) if losses else None
    if fallback:
        for t in strategy_trades:
            per_pos.setdefault(t.position_id, fallback)
    return per_pos


def classify(profit: float, one_r: Optional[float]) -> str:
    """win / be / stop по P&L относительно 1R (см. модуль-докстринг)."""
    if not one_r:                      # нет масштаба риска — только знак
        return "win" if profit > 0 else "stop"
    if profit >= BE_FRACTION * one_r:
        return "win"
    if profit <= -BE_FRACTION * one_r:
        return "stop"
    return "be"


def classify_trades(trades: list, exec_index: dict) -> List[Tuple[object, str]]:
    """Каждой сделке — исход. trades предполагаются в порядке закрытия."""
    r_by_pos = _one_r(trades, exec_index)
    return [(t, classify(t.profit, r_by_pos.get(t.position_id))) for t in trades]


def stop_streaks(outcomes: List[str]) -> Tuple[int, int]:
    """(макс серия стопов подряд, текущая серия на конце). BE/win её рвут."""
    mx = cur = 0
    for o in outcomes:
        if o == "stop":
            cur += 1
            mx = max(mx, cur)
        else:
            cur = 0
    return mx, cur


def _months_span(trades: list) -> int:
    """Число календарных месяцев от первой до последней сделки (>=1)."""
    if not trades:
        return 1
    ts = [t.close_time for t in trades]
    a, b = min(ts), max(ts)
    return max(1, (b.year - a.year) * 12 + (b.month - a.month) + 1)


def _monthly_net_pct(trades: list, ref_equity: float) -> Dict[str, float]:
    """YYYY-MM → чистый P&L месяца как % от опорного equity."""
    by_month: Dict[str, float] = defaultdict(float)
    for t in trades:
        by_month[t.close_time.strftime("%Y-%m")] += t.profit
    if ref_equity <= 0:
        return {}
    return {m: round(v / ref_equity * 100.0, 2) for m, v in by_month.items()}


# ── Вердикты факт ↔ коридор ───────────────────────────────────────────────────
def _v_higher_better(value: float, band: Band, tol: float) -> str:
    if value >= band.lo:
        return OK
    if value >= band.lo - tol:
        return WARN
    return PROB


def _v_floor(value: float, floor: float) -> str:
    # value и floor обычно отрицательные; хуже = более отрицательное
    if value >= floor:
        return OK
    if value >= floor * 1.5:
        return WARN
    return PROB


def _v_ceiling(value: int, cap: int) -> str:
    if value <= cap:
        return OK
    if value <= cap + 1:
        return WARN
    return PROB


def _v_info(value: float, band: Band) -> str:
    return OK if band.contains(value) else WARN


def _band_str(band: Optional[Band], pct: bool = False, mul: float = 1.0) -> str:
    if band is None:
        return "—"
    lo, hi = band.lo * mul, band.hi * mul
    return f"{lo:.0f}–{hi:.0f}%" if pct else f"{lo:.2f}–{hi:.2f}"


# ── Отчёт по стратегии ────────────────────────────────────────────────────────
@dataclass
class MetricRow:
    name: str
    fact: str
    corridor: str
    verdict: str          # OK | ВНИМАНИЕ | ПРОБЛЕМА | мало данных


@dataclass
class StrategyReport:
    label: str
    n: int
    enough: bool
    wins: int = 0
    be: int = 0
    stops: int = 0
    net: float = 0.0
    win_rate: Optional[float] = None
    profit_factor: object = None         # float | "∞" | None
    be_share: Optional[float] = None
    max_stop_streak: int = 0
    cur_stop_streak: int = 0
    trades_per_month: Optional[float] = None
    worst_month_pct: Optional[float] = None
    rows: List[MetricRow] = field(default_factory=list)
    note: str = ""

    def worst_verdict(self) -> str:
        vs = [r.verdict for r in self.rows] or [THIN]
        return max(vs, key=lambda v: _SEVERITY.get(v, 0))


def build_strategy_report(label: str, trades: list, exec_index: dict,
                          exp: Optional[StrategyExpectation], min_sample: int,
                          ref_equity: float) -> StrategyReport:
    n = len(trades)
    enough = n >= min_sample
    rep = StrategyReport(label=label, n=n, enough=enough)
    if n == 0:
        rep.note = "нет сделок за период"
        return rep

    outcomes = [o for _, o in classify_trades(trades, exec_index)]
    rep.wins = sum(1 for o in outcomes if o == "win")
    rep.be = sum(1 for o in outcomes if o == "be")
    rep.stops = sum(1 for o in outcomes if o == "stop")
    rep.net = round(sum(t.profit for t in trades), 2)
    rep.win_rate = rep.wins / n
    rep.be_share = rep.be / n
    gross_win = sum(t.profit for t in trades if t.profit > 0)
    gross_loss = abs(sum(t.profit for t in trades if t.profit < 0))
    rep.profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else "∞"
    rep.max_stop_streak, rep.cur_stop_streak = stop_streaks(outcomes)
    rep.trades_per_month = round(n / _months_span(trades), 2)
    monthly = _monthly_net_pct(trades, ref_equity)
    rep.worst_month_pct = min(monthly.values()) if monthly else None

    if exp is None:
        rep.note = "нет коридора в expectations.yaml"
        return rep

    def verdict(v: str) -> str:
        # при малой выборке выводов не делаем — честно скромничаем
        return THIN if not enough else v

    pf_val = rep.profit_factor if isinstance(rep.profit_factor, (int, float)) else None
    rep.rows = [
        MetricRow("Winrate", f"{rep.win_rate*100:.0f}% ({rep.wins}/{n})",
                  _band_str(exp.win_rate, pct=True, mul=100),
                  verdict(_v_higher_better(rep.win_rate, exp.win_rate, 0.03))
                  if exp.win_rate else "—"),
        MetricRow("Profit Factor",
                  ("∞" if rep.profit_factor == "∞" else f"{pf_val:.2f}"),
                  _band_str(exp.profit_factor),
                  verdict(_v_higher_better(pf_val, exp.profit_factor, 0.15))
                  if (exp.profit_factor and pf_val is not None) else
                  (verdict(OK) if rep.profit_factor == "∞" and exp.profit_factor else "—")),
        MetricRow("Сделок/мес", f"{rep.trades_per_month:.1f}",
                  _band_str(exp.trades_per_month),
                  verdict(_v_info(rep.trades_per_month, exp.trades_per_month))
                  if exp.trades_per_month else "—"),
        MetricRow("Доля БУ", f"{rep.be_share*100:.0f}% ({rep.be}/{n})",
                  _band_str(exp.be_share, pct=True, mul=100),
                  verdict(_v_info(rep.be_share, exp.be_share))
                  if exp.be_share else "—"),
        MetricRow("Макс серия стопов", f"{rep.max_stop_streak}",
                  (str(exp.max_stop_streak) if exp.max_stop_streak is not None else "—"),
                  verdict(_v_ceiling(rep.max_stop_streak, exp.max_stop_streak))
                  if exp.max_stop_streak is not None else "—"),
        MetricRow("Худший месяц",
                  (f"{rep.worst_month_pct:.1f}%" if rep.worst_month_pct is not None else "—"),
                  (f"{exp.worst_month_pct:.1f}%" if exp.worst_month_pct is not None else "—"),
                  verdict(_v_floor(rep.worst_month_pct, exp.worst_month_pct))
                  if (exp.worst_month_pct is not None and rep.worst_month_pct is not None)
                  else "—"),
    ]
    if not enough:
        rep.note = (f"мало данных (n={n} < {min_sample}) — выводы преждевременны, "
                    f"цифры справочно")
    return rep


# ── Сводка проскальзывания ────────────────────────────────────────────────────
@dataclass
class SlipRow:
    symbol: str
    n: int
    avg_pips: float
    worst_pips: float
    avg_risk_delta_pp: float
    worst_risk_delta_pp: float
    adverse: int          # сколько входов проскользнули В МИНУС (риск вверх)


def _f(row: dict, key: str) -> Optional[float]:
    v = (row.get(key) or "").strip()
    if v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def slippage_summary(exec_rows: List[dict]) -> Dict[str, List[SlipRow]]:
    """Сводка по символам, разнесённая на индексы и FX. Только реальные филы
    рыночных ордеров (dry_run=0, есть slip и это market)."""
    by_sym: Dict[str, List[dict]] = defaultdict(list)
    for r in exec_rows:
        if (r.get("dry_run") or "0").strip() not in ("0", ""):
            continue
        if (r.get("order_kind") or "").strip() != "market":
            continue
        if _f(r, "slip_pips") is None:
            continue
        by_sym[(r.get("symbol_tv") or "?").strip()].append(r)

    idx, fx = [], []
    for sym, rows in sorted(by_sym.items()):
        pips = [abs(_f(r, "slip_pips")) for r in rows if _f(r, "slip_pips") is not None]
        deltas = [_f(r, "risk_delta_pp") for r in rows if _f(r, "risk_delta_pp") is not None]
        adverse = sum(1 for r in rows if (r.get("adverse") or "0").strip() == "1")
        sr = SlipRow(
            symbol=sym, n=len(rows),
            avg_pips=round(sum(pips) / len(pips), 1) if pips else 0.0,
            worst_pips=round(max(pips), 1) if pips else 0.0,
            avg_risk_delta_pp=round(sum(deltas) / len(deltas), 2) if deltas else 0.0,
            worst_risk_delta_pp=round(max(deltas), 2) if deltas else 0.0,
            adverse=adverse,
        )
        (idx if sym in INDEX_SYMBOLS else fx).append(sr)
    return {"indices": idx, "fx": fx}


# ── Живой мониторинг серии стопов (для мгновенной тревоги) ─────────────────────
def current_stop_streaks(trades: list, exec_index: dict) -> Dict[str, int]:
    """label → текущая серия стопов на конце истории (для edge-тревоги)."""
    by_strat: Dict[str, list] = defaultdict(list)
    for t in trades:
        by_strat[t.strategy].append(t)
    out: Dict[str, int] = {}
    for label, ts in by_strat.items():
        ts.sort(key=lambda t: t.close_time)
        outcomes = [o for _, o in classify_trades(ts, exec_index)]
        _, cur = stop_streaks(outcomes)
        out[label] = cur
    return out


# ── Рендер ────────────────────────────────────────────────────────────────────
def render_markdown(month_label: str, per_strategy: List[StrategyReport],
                    slip: Dict[str, List[SlipRow]], generated_at: datetime,
                    month_trades: list, min_sample: int) -> str:
    L: List[str] = []
    L.append(f"# Отчёт эджа · {month_label}")
    L.append("")
    L.append(f"_Сформирован {generated_at.strftime('%Y-%m-%d %H:%M')}. "
             f"Факт демо-форвард-теста Just2Trade против коридоров бэктеста "
             f"(`expectations.yaml`). Пометки: 🟢 OK · 🟡 ВНИМАНИЕ · 🔴 ПРОБЛЕМА · "
             f"⏳ мало данных (n<{min_sample})._")
    L.append("")

    for rep in per_strategy:
        L.append(f"## {rep.label}")
        if rep.n == 0:
            L.append(f"_{rep.note or 'нет сделок'}._")
            L.append("")
            continue
        head = (f"Сделок: **{rep.n}** · net **{rep.net:+.0f}** · "
                f"W/BE/L = {rep.wins}/{rep.be}/{rep.stops}")
        if rep.note:
            head += f"  \n⏳ _{rep.note}_" if not rep.enough else ""
        L.append(head)
        L.append("")
        L.append("| Метрика | Факт | Коридор | |")
        L.append("|---|---|---|---|")
        for r in rep.rows:
            em = _EMOJI.get(r.verdict, "")
            L.append(f"| {r.name} | {r.fact} | {r.corridor} | {em} {r.verdict} |")
        L.append("")

    L.append("## Проскальзывание (рыночные ордера)")
    L.append("")
    for group, title in (("indices", "Индексы (главный вопрос)"), ("fx", "FX")):
        rows = slip.get(group, [])
        L.append(f"**{title}.**")
        if not rows:
            L.append("_нет исполнений за период._")
            L.append("")
            continue
        L.append("| Символ | Входов | Ср. \\|slip\\| | Худший slip | "
                 "Ср. Δриск | Худший Δриск | Adverse |")
        L.append("|---|---|---|---|---|---|---|")
        for s in rows:
            L.append(f"| {s.symbol} | {s.n} | {s.avg_pips} п | {s.worst_pips} п | "
                     f"{s.avg_risk_delta_pp:+.2f} п.п. | {s.worst_risk_delta_pp:+.2f} п.п. | "
                     f"{s.adverse}/{s.n} |")
        L.append("")

    L.append(f"## Сделки за {month_label}")
    L.append("")
    if not month_trades:
        L.append("_за этот месяц закрытых сделок нет._")
    else:
        L.append("| Закрыта | Стратегия | Символ | Сторона | P&L |")
        L.append("|---|---|---|---|---|")
        for t in month_trades:
            L.append(f"| {t.close_time.strftime('%m-%d %H:%M')} | {t.strategy} | "
                     f"{t.symbol} | {t.side} | {t.profit:+.0f} |")
    L.append("")
    return "\n".join(L)


def render_telegram(month_label: str, per_strategy: List[StrategyReport],
                    slip: Dict[str, List[SlipRow]], min_sample: int) -> str:
    L: List[str] = [f"📈 Отчёт эджа · {month_label}"]
    for rep in per_strategy:
        if rep.n == 0:
            L.append(f"• {rep.label}: нет сделок")
            continue
        em = _EMOJI.get(rep.worst_verdict(), "")
        wr = f"{rep.win_rate*100:.0f}%" if rep.win_rate is not None else "—"
        pf = ("∞" if rep.profit_factor == "∞"
              else (f"{rep.profit_factor:.2f}" if rep.profit_factor is not None else "—"))
        tail = f" ⏳n={rep.n}" if not rep.enough else ""
        L.append(f"{em} {rep.label}: n={rep.n} WR {wr} PF {pf} "
                 f"net {rep.net:+.0f}{tail}")
    # худшее проскальзывание по индексам — то, что тревожит на market-ордерах
    idx = slip.get("indices", [])
    if idx:
        worst = max(idx, key=lambda s: s.worst_risk_delta_pp)
        L.append(f"Слиппедж (индексы): худш. Δриск {worst.worst_risk_delta_pp:+.2f} п.п. "
                 f"({worst.symbol})")
    L.append("Полный отчёт — logs/reports/.")
    return "\n".join(L)
