"""Замер спреда по символам счёта: семплирование в CSV + сводка.

Зачем отдельный инструмент: тип счёта у Just2Trade spread-only, комиссии нет
(docs/just2trade-actual-costs.md), поэтому круг издержек = спред, и это
единственная величина, которую надо перемерять после каждого переезда на новый
демо-счёт (чек-лист в README, «Переезд на новый демо-счёт»).

Спред плавающий и зависит от сессии: индекс США до открытия США шире, чем
после, поэтому мерить надо в том окне, в котором стратегия реально торгует.

    # 35 минут семплирования раз в 5 с
    python tools\\spread-sample.py --minutes 35 --out logs\\spreads-us.csv

    # сводка по уже снятому файлу
    python tools\\spread-sample.py --report logs\\spreads-us.csv

Стоимость пункта пишется в CSV на каждом семпле (через MT5Broker.symbol_spec,
то есть уже сконвертированная в валюту счёта) — сводка не угадывает её задним
числом и переживает смену кросс-курса.
"""
from __future__ import annotations

import argparse
import csv
import statistics as st
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_SYMBOLS = ["EURUSD", "GBPJPY", "GER30m", "USTECH100m"]
# пунктов цены в одном пипсе (FX) / индексном пункте
POINTS_PER_UNIT = 10
FIELDS = ["ts_utc", "symbol", "bid", "ask", "spread_price", "spread_points",
          "point", "usd_per_unit", "tradable"]


def sample(symbols, minutes: float, interval: float, out_path: Path) -> int:
    from bot import config as cfgmod
    from bot.broker_mt5 import MT5Broker

    cfg = cfgmod.load()
    broker = MT5Broker(cfg.mt5)
    broker.connect()
    mt5 = broker.mt5

    specs = {}
    for s in symbols:
        spec = broker.symbol_spec(s)
        if spec is None:
            print(f"{s}: нет такого символа на счёте — пропускаю", flush=True)
            continue
        info = mt5.symbol_info(s)
        # стоимость пипса/пункта на 1 лот в валюте счёта
        specs[s] = (info.point, spec.tick_value / spec.tick_size * info.point
                    * POINTS_PER_UNIT)
    if not specs:
        raise SystemExit("ни одного символа снять не удалось")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + minutes * 60.0
    rows = 0
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(FIELDS)
        while time.time() < deadline:
            loop_start = time.time()
            for s, (point, usd_per_unit) in specs.items():
                info = mt5.symbol_info(s)
                tick = mt5.symbol_info_tick(s)
                if info is None or tick is None or tick.bid <= 0 or tick.ask <= 0:
                    continue
                spread = tick.ask - tick.bid
                w.writerow([
                    datetime.now(timezone.utc).isoformat(timespec="seconds"), s,
                    f"{tick.bid:.5f}", f"{tick.ask:.5f}", f"{spread:.6f}",
                    round(spread / point, 1), point, round(usd_per_unit, 4),
                    1 if info.trade_mode == mt5.SYMBOL_TRADE_MODE_FULL else 0,
                ])
                rows += 1
            fh.flush()
            time.sleep(max(0.0, interval - (time.time() - loop_start)))
    broker.shutdown()
    return rows


def _pct(xs, q: float) -> float:
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def report(path: Path) -> None:
    """Сводка: медиана/p25/p75/max. Нетоурговые семплы отбрасываются."""
    pts, usd, times = defaultdict(list), {}, defaultdict(list)
    with open(path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("tradable") != "1":
                continue
            pts[row["symbol"]].append(float(row["spread_points"]))
            usd[row["symbol"]] = float(row.get("usd_per_unit") or 0.0)
            times[row["symbol"]].append(row["ts_utc"])

    if not pts:
        raise SystemExit(f"в {path} нет торговых семплов")
    print(f"{'символ':<12}{'n':>5}{'медиана':>9}{'p25':>7}{'p75':>7}{'max':>7}"
          f"{'ед.':>9}{'$/лот круг':>12}  окно UTC")
    for s, xs in pts.items():
        med = st.median(xs)
        print(f"{s:<12}{len(xs):>5}{med:>9.1f}{_pct(xs,.25):>7.1f}"
              f"{_pct(xs,.75):>7.1f}{max(xs):>7.1f}"
              f"{med/POINTS_PER_UNIT:>9.2f}{med/POINTS_PER_UNIT*usd[s]:>12.2f}"
              f"  {times[s][0][11:16]}–{times[s][-1][11:16]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", metavar="CSV", help="только сводка по готовому файлу")
    ap.add_argument("--minutes", type=float, default=35.0)
    ap.add_argument("--interval", type=float, default=5.0, help="секунд между семплами")
    ap.add_argument("--out", default="logs/spreads.csv")
    ap.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    a = ap.parse_args()

    # Windows-консоль по умолчанию не UTF-8 (как в bot/logutil.py): без этого
    # кириллица и стрелки роняют вывод, а под Планировщиком — весь замер.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    if a.report:
        report(Path(a.report))
        return
    out = Path(a.out)
    if not out.is_absolute():
        out = ROOT / out
    print(f"замер {a.minutes:.0f} мин, раз в {a.interval:.0f} с → {out}", flush=True)
    n = sample(a.symbols, a.minutes, a.interval, out)
    print(f"снято {n} семплов", flush=True)
    report(out)


if __name__ == "__main__":
    main()
