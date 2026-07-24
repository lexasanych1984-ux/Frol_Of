"""Прогресс-логи бэктеста — в stderr, чтобы не мешать текстовому отчёту
в stdout (`python -m src.cli backtest ... > report.txt` останется чистым)."""
from __future__ import annotations

import sys
from datetime import datetime


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", file=sys.stderr, flush=True)
