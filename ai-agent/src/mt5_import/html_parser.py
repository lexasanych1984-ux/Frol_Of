"""Парсер MT5 History Report (ReportHistory-*.html), экспортируемого из
клиентского терминала: История -> правый клик -> Отчёт -> HTML.

Формат проверен на реальных экспортах с FundingPips (терминал MT5,
client terminal report). Особенности:
  - файл сохраняется в кодировке UTF-16 (не UTF-8!);
  - в одной HTML-таблице подряд идут три секции: "Позиции", "Ордера",
    "Сделки" — нас интересует "Позиции", т.к. там уже склеены open+close
    для каждой закрытой сделки;
  - "скрытая" колонка Comment (colspan=8) в секции "Позиции" не содержит
    полезных данных построчно (это разметка под печать) и пропускается.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from bs4 import BeautifulSoup

from .models import Trade

POSITIONS_ROW_LEN = 14  # число <td> в строке секции "Позиции"


def _read_mt5_html(path: Path) -> str:
    """MT5 сохраняет отчёт в UTF-16 (с BOM) — обычная utf-8 декодировка даст мусор."""
    raw = path.read_bytes()
    return raw.decode("utf-16")


def _parse_account_login(soup: BeautifulSoup) -> str:
    text = soup.get_text(" ", strip=True)
    m = re.search(r"Торговый счет:\s*(\d+)", text)
    return m.group(1) if m else "unknown"


def _parse_dt(s: str) -> Optional[datetime]:
    s = s.strip()
    if not s:
        return None
    return datetime.strptime(s, "%Y.%m.%d %H:%M:%S")


def _parse_float(s: str) -> Optional[float]:
    s = s.strip().replace("\xa0", "").replace(" ", "")
    if not s or s == "-":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _find_section_header(soup: BeautifulSoup, title: str):
    return next(
        (th for th in soup.find_all("th") if title in th.get_text()),
        None,
    )


def parse_positions(path: str | Path) -> List[Trade]:
    """Возвращает список закрытых позиций из секции "Позиции" отчёта."""
    path = Path(path)
    html = _read_mt5_html(path)
    soup = BeautifulSoup(html, "lxml")

    account_login = _parse_account_login(soup)

    section_header = _find_section_header(soup, "Позиции")
    if section_header is None:
        raise ValueError(f"Секция 'Позиции' не найдена в {path}")

    section_tr = section_header.find_parent("tr")
    column_header_tr = section_tr.find_next_sibling("tr")  # строка с названиями колонок
    row = column_header_tr.find_next_sibling("tr")

    trades: List[Trade] = []
    while row is not None:
        if row.find("th") is not None:
            break  # дошли до следующей секции ("Ордера")

        cells = row.find_all("td")
        texts = [c.get_text(strip=True) for c in cells]
        if len(texts) < POSITIONS_ROW_LEN:
            break  # пустая строка-разделитель между секциями

        open_time_s, position_id, symbol, direction = texts[0:4]
        # texts[4] — скрытая колонка "Comment" (colspan=8), данных не несёт
        volume, open_price, sl, tp, close_time_s, close_price, commission, swap = texts[5:13]
        profit = texts[13]

        trades.append(
            Trade(
                account_login=account_login,
                position_id=position_id,
                symbol=symbol,
                direction=direction,
                volume=_parse_float(volume) or 0.0,
                open_time=_parse_dt(open_time_s),
                open_price=_parse_float(open_price) or 0.0,
                close_time=_parse_dt(close_time_s),
                close_price=_parse_float(close_price),
                sl=_parse_float(sl),
                tp=_parse_float(tp),
                commission=_parse_float(commission) or 0.0,
                swap=_parse_float(swap) or 0.0,
                profit=_parse_float(profit) or 0.0,
                source_file=path.name,
            )
        )
        row = row.find_next_sibling("tr")

    return trades


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Использование: python -m src.mt5_import.html_parser <ReportHistory.html>")
        sys.exit(1)

    parsed = parse_positions(sys.argv[1])
    print(f"Найдено закрытых позиций: {len(parsed)}")
    for t in parsed[:5]:
        print(t.to_dict())
