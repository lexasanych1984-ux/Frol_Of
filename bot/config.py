"""Загрузка конфигурации из .env + config.yaml."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml
from dotenv import dotenv_values, load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def _bool(v: str, default: bool = False) -> bool:
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _int(v: Optional[str], default: int) -> int:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


@dataclass
class MT5Creds:
    login: Optional[int]
    password: str
    server: str
    terminal_path: str


@dataclass
class HealthCfg:
    check_interval_sec: int = 300      # как часто снимать проверки
    cdp_stale_sec: int = 600           # нет кадров CDP дольше — авария
    antispam_sec: int = 1800           # не напоминать об одной аварии чаще
    daily_summary_at: str = "09:00"    # локальное время суточной сводки
    pid_file: str = "logs/bot.pid"     # для внешнего watchdog


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
    # telegram + мониторинг живости
    telegram_token: str = ""
    telegram_chat_id: str = ""
    telegram_prefix: str = "[BOT]"
    health: HealthCfg = field(default_factory=HealthCfg)
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

    tg_token, tg_chat = _resolve_telegram()
    health = HealthCfg(
        check_interval_sec=_int(os.getenv("HEALTH_CHECK_INTERVAL_SEC"), 300),
        cdp_stale_sec=_int(os.getenv("HEALTH_CDP_STALE_SEC"), 600),
        antispam_sec=_int(os.getenv("HEALTH_ANTISPAM_SEC"), 1800),
        daily_summary_at=(os.getenv("HEALTH_DAILY_SUMMARY_AT") or "09:00").strip(),
        pid_file=(os.getenv("HEALTH_PID_FILE") or "logs/bot.pid").strip(),
    )

    return Config(
        env=os.getenv("BYBIT_ENV", "demo"),
        dry_run=_bool(os.getenv("DRY_RUN"), True),
        signal_source=os.getenv("SIGNAL_SOURCE", "cdp"),
        cdp_port=int(os.getenv("CDP_PORT", "9222")),
        http_host=os.getenv("HTTP_HOST", "127.0.0.1"),
        http_port=int(os.getenv("HTTP_PORT", "8787")),
        http_secret=os.getenv("HTTP_SHARED_SECRET", ""),
        mt5=mt5,
        telegram_token=tg_token,
        telegram_chat_id=tg_chat,
        telegram_prefix=(os.getenv("TELEGRAM_PREFIX") or "[BOT]").strip(),
        health=health,
        symbol_map=y.get("symbol_map", {}) or {},
        risk=y.get("risk", {}) or {},
        guards=y.get("guards", {}) or {},
        exits=y.get("exits", {}) or {},
        trading_hours=y.get("trading_hours", []) or [],
        logging_cfg=y.get("logging", {}) or {},
    )


def _resolve_telegram() -> tuple[str, str]:
    """Токен и chat_id для уведомлений.

    Приоритет — локальные TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID. Если их нет, но
    задан TELEGRAM_ENV_FILE (напр. .env бота из D:\\MY\\Crypto\\AI agent) —
    берём оттуда, НЕ дублируя секреты в этот проект.
    """
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    ext = (os.getenv("TELEGRAM_ENV_FILE") or "").strip()
    if (not token or not chat) and ext and Path(ext).exists():
        vals = dotenv_values(ext)
        token = token or (vals.get("TELEGRAM_BOT_TOKEN") or "").strip()
        chat = chat or (vals.get("TELEGRAM_CHAT_ID") or "").strip()
    return token, chat
