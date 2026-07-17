"""Настройка логирования: консоль + файл."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def setup(level: str = "INFO", file: str = "logs/bot.log") -> logging.Logger:
    logger = logging.getLogger("bot")
    if logger.handlers:
        return logger
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Windows-консоль по умолчанию не UTF-8 — иначе кириллица ломается.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    if file:
        fpath = ROOT / file
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(fpath, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    return logger
