"""Систематический лог исполнений: ПЛАН (из алерта) ↔ ФАКТ (исполнение) + дельта.

На каждый вход бот дописывает сюда одну строку: цена/риск/RR из алерта против
цены/риска/RR фактического исполнения и проскальзывание. Файл append-only —
он переживает перезапуски и не перезаписывается (в отличие от сводного
logs/trades.csv, который `run.py stats/report` строят из истории MT5 заново).

Отсюда `run.py report` берёт сводку проскальзывания и связывает исполнение с
закрытой сделкой из истории MT5 по position_id (чтобы знать плановый риск = 1R).

Чистый CSV-слой без зависимости от MT5 — легко тестируется.
"""
from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import List, Optional


@dataclass
class ExecutionRecord:
    """Одна запись плана↔факта по входу."""
    ts: str                    # ISO-время исполнения (локальное)
    strategy: str              # smc | crt | asweep
    symbol_tv: str             # тикер как в алерте (EURUSD, GER40, ...)
    mt5_symbol: str            # символ брокера (GER30m, USTECH100m, ...)
    side: str                  # long | short
    order_kind: str            # market | limit | stop
    ticket: Optional[int]      # тикет ордера (res.order)
    position_id: Optional[int] # id позиции для связи с историей MT5
    # ── ПЛАН (из алерта / конфига) ──
    plan_entry: Optional[float]
    sl: Optional[float]
    tp: Optional[float]
    plan_rr: Optional[float]
    target_risk_pct: float     # заданный риск (Config.risk_pct — с учётом per_strategy)
    plan_lots: float           # лот, который посчитал сайзинг
    plan_risk_amount: float    # target_risk_pct% от equity, в валюте счёта = 1R
    # ── ФАКТ (исполнение) ──
    fill_price: Optional[float]
    fill_lots: Optional[float]
    actual_rr: Optional[float]
    actual_risk_pct: Optional[float]
    actual_risk_amount: Optional[float]
    # ── ДЕЛЬТА (проскальзывание) ──
    slip_price: Optional[float]     # fill - plan_entry, в цене инструмента (знак: +/-)
    slip_pips: Optional[float]      # то же в «пунктах» (пипсах) инструмента
    slip_pct_of_sl: Optional[float] # |slip| как % от плановой дистанции до SL
    risk_delta_pp: Optional[float]  # actual_risk_pct - target_risk_pct (проц. пункты)
    adverse: Optional[int]          # 1 = проскальзывание УВЕЛИЧИЛО риск, иначе 0
    equity: float                   # equity счёта на момент входа
    dry_run: int = 0                # 1 — запись из dry-run (факт оценочный)
    # Стоимость тика в валюте счёта, по которой считался лот (после конвертации
    # в MT5Broker.symbol_spec). Для не-USD инструментов зависит от кросс-курса,
    # поэтому её дрейф между сделками должен быть виден в журнале, а не угадываться.
    tick_value: Optional[float] = None
    tick_size: Optional[float] = None


_HEADER = [f.name for f in fields(ExecutionRecord)]


def default_path() -> Path:
    return Path(__file__).resolve().parent.parent / "logs" / "executions.csv"


def migrate_header(path) -> bool:
    """Дописать в старый файл колонки, появившиеся в ExecutionRecord позже.

    Файл append-only и переживает версии бота, поэтому набор полей в нём может
    отставать от кода. Без миграции новая запись легла бы под старый заголовок
    со сдвигом — лог молча стал бы мусором, а заметили бы это через месяц по
    кривому отчёту. Старые строки получают пустые значения в новых колонках.

    Возвращает True, если файл переписан.
    """
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return False
    with open(p, "r", newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f, delimiter=";"))
    with open(p, "r", encoding="utf-8-sig") as f:
        old = (f.readline().strip("\r\n").split(";") if f else [])
    if old == _HEADER:
        return False

    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(_HEADER)
        for row in rows:
            w.writerow([row.get(k, "") if row.get(k) is not None else ""
                        for k in _HEADER])
    tmp.replace(p)  # атомарная замена: обрыв на полпути не съест лог
    return True


def append(path, rec: ExecutionRecord) -> None:
    """Дописать строку. Заголовок пишется только при создании файла."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    new = not p.exists() or p.stat().st_size == 0
    if not new:
        migrate_header(p)
    with open(p, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        if new:
            w.writerow(_HEADER)
        d = asdict(rec)
        w.writerow([_fmt(d[k]) for k in _HEADER])


def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, float):
        # без экспоненты и хвостовых нулей; точности хватает и FX, и индексам
        s = f"{v:.6f}".rstrip("0").rstrip(".")
        return "0" if s in ("", "-0") else s
    return v


def read(path) -> List[dict]:
    """Прочитать лог как список dict (значения — строки, приведение — у вызова)."""
    p = Path(path)
    if not p.exists():
        return []
    with open(p, "r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f, delimiter=";"))


def _num(row: dict, key: str) -> Optional[float]:
    v = (row.get(key) or "").strip()
    if v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def index_by_position(path) -> dict:
    """position_id (int) → строка лога. Для связи с закрытой сделкой из MT5."""
    out: dict = {}
    for row in read(path):
        pid = (row.get("position_id") or "").strip()
        if pid:
            try:
                out[int(float(pid))] = row
            except ValueError:
                continue
    return out


def read_month(path, year: int, month: int) -> List[dict]:
    """Записи, чьё ts попадает в указанный календарный месяц."""
    out = []
    for row in read(path):
        ts = (row.get("ts") or "").strip()
        try:
            dt = datetime.fromisoformat(ts)
        except ValueError:
            continue
        if dt.year == year and dt.month == month:
            out.append(row)
    return out
