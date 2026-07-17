"""Загрузка конфигурации из .env + config.yaml."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def _bool(v: str, default: bool = False) -> bool:
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class MT5Creds:
    login: Optional[int]
    password: str
    server: str
    terminal_path: str


@dataclass
class Config:
    # env
    env: str                       # demo | live
    dry_run: bool
    signal_source: str             # cdp | http
    cdp_port: int
    http_host: str
    http_port: int
    http_secret: str
    mt5: MT5Creds
    # yaml
    symbol_map: dict = field(default_factory=dict)
    risk: dict = field(default_factory=dict)
    guards: dict = field(default_factory=dict)
    exits: dict = field(default_factory=dict)
    trading_hours: list = field(default_factory=list)
    logging_cfg: dict = field(default_factory=dict)

    @property
    def is_live(self) -> bool:
        return self.env.lower() == "live"

    def mt5_symbol(self, tv_ticker: Optional[str]) -> Optional[str]:
        if not tv_ticker:
            return None
        return self.symbol_map.get(tv_ticker) or self.symbol_map.get(tv_ticker.upper())


def load(env_path: Optional[str] = None, yaml_path: Optional[str] = None) -> Config:
    load_dotenv(env_path or (ROOT / ".env"))

    login_raw = os.getenv("MT5_LOGIN", "").strip()
    mt5 = MT5Creds(
        login=int(login_raw) if login_raw.isdigit() else None,
        password=os.getenv("MT5_PASSWORD", ""),
        server=os.getenv("MT5_SERVER", ""),
        terminal_path=os.getenv("MT5_TERMINAL_PATH", ""),
    )

    ypath = Path(yaml_path or (ROOT / "config.yaml"))
    if not ypath.exists():
        ypath = ROOT / "config.example.yaml"
    with open(ypath, "r", encoding="utf-8") as f:
        y = yaml.safe_load(f) or {}

    return Config(
        env=os.getenv("BYBIT_ENV", "demo"),
        dry_run=_bool(os.getenv("DRY_RUN"), True),
        signal_source=os.getenv("SIGNAL_SOURCE", "cdp"),
        cdp_port=int(os.getenv("CDP_PORT", "9222")),
        http_host=os.getenv("HTTP_HOST", "127.0.0.1"),
        http_port=int(os.getenv("HTTP_PORT", "8787")),
        http_secret=os.getenv("HTTP_SHARED_SECRET", ""),
        mt5=mt5,
        symbol_map=y.get("symbol_map", {}) or {},
        risk=y.get("risk", {}) or {},
        guards=y.get("guards", {}) or {},
        exits=y.get("exits", {}) or {},
        trading_hours=y.get("trading_hours", []) or [],
        logging_cfg=y.get("logging", {}) or {},
    )
