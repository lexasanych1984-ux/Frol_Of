"""Тест на синтетическом мини-отчёте с реальной структурой MT5 (UTF-16,
секции Позиции/Ордера/Сделки) — чтобы регресс парсера ловился без
привязки к чьим-то реальным (приватным) экспортам."""
from __future__ import annotations

from pathlib import Path

from src.mt5_import.html_parser import parse_positions

MINI_REPORT = """<!DOCTYPE html PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN" "http://www.w3.org/TR/html4/loose.dtd">
<html><head><title>99999999: Test</title></head>
<body>
<table>
<tr><th colspan="10" align="right">Торговый счет:</th><th colspan="10">99999999 (USD, Test-SIM, demo, Hedge)</th></tr>
<tr align="center"><th colspan="14"><div><b>Позиции</b></div></th></tr>
<tr align="center" bgcolor="#E5F0FC">
    <td><b>Время</b></td><td><b>Позиция</b></td><td><b>Символ</b></td><td><b>Тип</b></td>
    <td><b>Объем</b></td><td><b>Цена</b></td><td><b>S/L</b></td><td><b>T/P</b></td>
    <td><b>Время</b></td><td><b>Цена</b></td><td><b>Комиссия</b></td><td><b>Своп</b></td><td colspan="2"><b>Прибыль</b></td>
</tr>
<tr bgcolor="#FFFFFF" align="right">
    <td>2026.02.24 20:46:59</td>
    <td>285686212</td>
    <td>EURUSD</td>
    <td>buy</td>
    <td class="hidden" colspan="8">Comment</td>
    <td class="">2.86</td>
    <td class="">1.17792</td>
    <td class="">1.17650</td>
    <td class="">1.18011</td>
    <td class="">2026.02.25 06:54:31</td>
    <td class="">1.17946</td>
    <td class="">-14.30</td>
    <td class="">-23.43</td>
    <td colspan="2">440.44</td>
</tr>
<tr><td style="height: 10px"></td></tr>
<tr align="center"><th colspan="14"><div><b>Ордера</b></div></th></tr>
</table>
</body></html>
"""


def test_parse_positions_single_trade(tmp_path: Path):
    report_path = tmp_path / "ReportHistory-99999999.html"
    report_path.write_bytes(MINI_REPORT.encode("utf-16"))

    trades = parse_positions(report_path)

    assert len(trades) == 1
    t = trades[0]
    assert t.account_login == "99999999"
    assert t.symbol == "EURUSD"
    assert t.direction == "buy"
    assert t.volume == 2.86
    assert t.open_price == 1.17792
    assert t.sl == 1.1765
    assert t.tp == 1.18011
    assert t.close_price == 1.17946
    assert t.commission == -14.30
    assert t.swap == -23.43
    assert t.profit == 440.44
    assert t.open_time.isoformat() == "2026-02-24T20:46:59"
    assert t.close_time.isoformat() == "2026-02-25T06:54:31"
    assert round(t.net_pnl, 2) == round(440.44 - 14.30 - 23.43, 2)
