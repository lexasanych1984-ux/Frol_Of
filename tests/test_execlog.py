"""Append-only лог проскальзывания: запись, чтение, индекс по позиции."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import execlog


def _rec(**over):
    base = dict(
        ts="2026-07-20T16:14:56", strategy="smc", symbol_tv="EURUSD",
        mt5_symbol="EURUSD", side="short", order_kind="market", ticket=247,
        position_id=247, plan_entry=1.14222, sl=1.14495, tp=1.13403, plan_rr=3.0,
        target_risk_pct=1.0, plan_lots=3.66, plan_risk_amount=1000.0,
        fill_price=1.14115, fill_lots=3.66, actual_rr=2.55, actual_risk_pct=1.39,
        actual_risk_amount=1391.0, slip_price=-0.00107, slip_pips=-10.7,
        slip_pct_of_sl=39.2, risk_delta_pp=0.39, adverse=1, equity=100000.0,
        dry_run=0)
    base.update(over)
    return execlog.ExecutionRecord(**base)


def test_append_and_read_roundtrip(tmp_path):
    p = tmp_path / "exec.csv"
    execlog.append(p, _rec())
    execlog.append(p, _rec(ticket=248, position_id=248, symbol_tv="GER40"))
    rows = execlog.read(p)
    assert len(rows) == 2
    assert rows[0]["symbol_tv"] == "EURUSD"
    assert rows[0]["fill_price"] == "1.14115"
    assert rows[1]["position_id"] == "248"


def test_header_written_once(tmp_path):
    p = tmp_path / "exec.csv"
    execlog.append(p, _rec())
    execlog.append(p, _rec(position_id=248))
    text = p.read_text(encoding="utf-8-sig")
    assert text.count("position_id") == 1     # заголовок один раз


def test_index_by_position(tmp_path):
    p = tmp_path / "exec.csv"
    execlog.append(p, _rec(position_id=247))
    execlog.append(p, _rec(position_id=248, plan_risk_amount=500.0))
    idx = execlog.index_by_position(p)
    assert set(idx) == {247, 248}
    assert idx[248]["plan_risk_amount"] == "500"


def test_read_missing_file_is_empty(tmp_path):
    assert execlog.read(tmp_path / "nope.csv") == []
    assert execlog.index_by_position(tmp_path / "nope.csv") == {}


def test_read_month_filters(tmp_path):
    p = tmp_path / "exec.csv"
    execlog.append(p, _rec(ts="2026-07-20T16:00:00"))
    execlog.append(p, _rec(ts="2026-06-15T10:00:00", position_id=200))
    jul = execlog.read_month(p, 2026, 7)
    assert len(jul) == 1
    assert jul[0]["ts"].startswith("2026-07")


def test_migrate_header_adds_new_columns(tmp_path):
    """Старый лог без tick_value дописывается без сдвига колонок."""
    import csv

    from bot import execlog as el
    p = tmp_path / "executions.csv"
    old_header = [f for f in el._HEADER if f not in ("tick_value", "tick_size")]
    with open(p, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(old_header)
        w.writerow(["x"] * len(old_header))

    rec = _rec()
    rec.tick_value, rec.tick_size = 0.115246, 0.1
    el.append(p, rec)

    rows = el.read(p)
    assert len(rows) == 2
    with open(p, encoding="utf-8-sig") as f:
        assert f.readline().strip("\r\n").split(";") == el._HEADER
    assert rows[0]["tick_value"] == ""          # старая строка — пустые новые поля
    assert rows[0]["ts"] == "x"                 # и не поехала
    assert rows[1]["tick_value"] == "0.115246"  # новая строка на своём месте
    assert rows[1]["mt5_symbol"] == rec.mt5_symbol
