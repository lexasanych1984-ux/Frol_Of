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
class ReportCfg:
    """Отчёт факт↔коридор + мгновенные тревоги о деградации эджа."""
    expectations_file: str = "expectations.yaml"   # коридоры бэктеста
    exec_log: str = "logs/executions.csv"          # append-only лог проскальзывания
    reports_dir: str = "logs/reports"              # markdown-отчёты
    history_days: int = 400                        # глубина истории MT5 для отчёта
    # Мгновенная тревога: факт.риск сделки выше заданного больше чем на столько
    # проц. пунктов. None → берётся из expectations.yaml (meta.risk_overshoot_pp);
    # env RISK_OVERSHOOT_PP имеет приоритет над обоими.
    risk_overshoot_pp: Optional[float] = None
    # Как часто опрашивать историю MT5 на предмет серии стопов (сек).
    edge_check_interval_sec: int = 900


@dataclass
class WebhookCfg:
    """Облачный store-and-forward буфер (Cloudflare Worker + D1) — резервный,
    надёжный канал сигналов параллельно быстрому CDP. Бот его ОПРАШИВАЕТ (pull),
    входящих портов на домашнем ПК не открывается.

    Включён, только если заданы и pull_url, и token (см. .env WEBHOOK_*).
    """
    pull_url: str = ""                 # база, напр. https://<worker>.workers.dev
    token: str = ""                    # длинный случайный секрет (в пути URL)
    poll_interval_sec: int = 12        # как часто опрашивать буфер
    pull_limit: int = 100              # сколько записей забирать за раз
    stale_sec: int = 90                # нет успешного опроса дольше — авария (health)
    request_timeout: float = 10.0      # таймаут HTTP-запроса к облаку

    @property
    def enabled(self) -> bool:
        return bool(self.pull_url and self.token)


@dataclass
class NotionCfg:
    """Автосинхронизация закрытых сделок в Notion-базу «Сделки бота».

    Бот сам пишет каждую закрытую сделку строкой в базу (идемпотентно по
    position_id), чтобы дашборд доходности не пустел молча. Включён, только если
    задан NOTION_TOKEN (секрет internal-интеграции Notion); database_id по
    умолчанию указывает на существующую базу.

    Токен нельзя переиспользовать из Telegram — нужна отдельная internal-
    интеграция Notion, которой расшарена страница «Торговый бот — статистика».
    """
    token: str = ""                    # secret internal-интеграции Notion
    database_id: str = ""              # id базы «Сделки бота»
    charts_page_id: str = ""          # страница для PNG-графиков (пусто → без графиков)
    sync_interval_sec: int = 60        # как часто досинхронизировать (сек)
    history_days: int = 7              # глубина истории MT5 для добора простоя
    request_timeout: float = 15.0      # таймаут HTTP-запроса к Notion
    charts_timeout: float = 30.0       # таймаут заливки картинок (крупнее)
    alert_after_fails: int = 3         # столько подряд провалов → тревога в Telegram

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.database_id)

    @property
    def charts_enabled(self) -> bool:
        return bool(self.token and self.charts_page_id)


@dataclass
class FreshnessCfg:
    """Защита протухших сигналов. Возраст = now − received_ts (когда сигнал
    сработал/принят «наверху»). Применяется ко ВСЕМ источникам.

    Вход старше entry_max_age исполнять нельзя (цена устарела) — пропускаем с
    уведомлением. Управление позицией (БУ/выход) допустимо и позже: позиция уже
    открыта. per_strategy — переопределение entry_max_age по стратегии (smc/crt/…).
    """
    entry_max_age_sec: int = 600           # 10 мин — вход
    manage_max_age_sec: int = 21600        # 6 ч — БУ/выход
    per_strategy: dict = field(default_factory=dict)

    def entry_max_age(self, strategy: Optional[str]) -> int:
        if strategy:
            ov = self.per_strategy.get(strategy) or self.per_strategy.get(strategy.lower())
            if isinstance(ov, dict) and ov.get("entry_max_age_sec") is not None:
                return int(ov["entry_max_age_sec"])
        return self.entry_max_age_sec


@dataclass
class DemoAccountCfg:
    """Срок жизни демо-счёта — под напоминание о переезде (bot/demolife.py).

    opened пустой = напоминание выключено (например, на реальном счёте).
    """
    opened: str = ""               # ISO-дата открытия счёта, YYYY-MM-DD
    lifetime_days: int = 14
    warn_from_day: int = 10
    urgent_from_day: int = 12

    def opened_date(self):
        from datetime import date
        try:
            return date.fromisoformat(self.opened.strip()) if self.opened else None
        except ValueError:
            return None


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
    report: ReportCfg = field(default_factory=ReportCfg)
    webhook: WebhookCfg = field(default_factory=WebhookCfg)
    notion: NotionCfg = field(default_factory=NotionCfg)
    freshness: FreshnessCfg = field(default_factory=FreshnessCfg)
    demo_account: DemoAccountCfg = field(default_factory=DemoAccountCfg)
    # yaml
    symbol_map: dict = field(default_factory=dict)
    risk: dict = field(default_factory=dict)
    guards: dict = field(default_factory=dict)
    exits: dict = field(default_factory=dict)
    pending_orders: dict = field(default_factory=dict)
    trading_hours: list = field(default_factory=list)
    logging_cfg: dict = field(default_factory=dict)

    @property
    def is_live(self) -> bool:
        return self.env.lower() == "live"

    def mt5_symbol(self, tv_ticker: Optional[str]) -> Optional[str]:
        if not tv_ticker:
            return None
        return self.symbol_map.get(tv_ticker) or self.symbol_map.get(tv_ticker.upper())

    def risk_pct(self, strategy: Optional[str] = None) -> float:
        """% equity на сделку с учётом переопределения по стратегии.

        Риск обязан совпадать с riskPct в самой стратегии TradingView, иначе
        форвард-тест не сойдётся с бэктестом: SMC задеплоен на 2%, CRT Day и
        Asia Sweep — на 1%. Ключ per_strategy — как в magic-карте (smc/crt/asweep).
        """
        risk = self.risk or {}
        if strategy:
            per = risk.get("per_strategy") or {}
            ov = per.get(strategy) or per.get(strategy.lower())
            if isinstance(ov, dict) and ov.get("risk_pct_per_trade") is not None:
                return float(ov["risk_pct_per_trade"])
        return float(risk.get("risk_pct_per_trade", 1.0))

    def _abs(self, rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else (ROOT / p)

    def exec_log_path(self) -> Path:
        return self._abs(self.report.exec_log)

    def reports_dir_path(self) -> Path:
        return self._abs(self.report.reports_dir)

    def expectations_path(self) -> Path:
        return self._abs(self.report.expectations_file)


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
    overshoot_raw = (os.getenv("RISK_OVERSHOOT_PP") or "").strip()
    report = ReportCfg(
        expectations_file=(os.getenv("EXPECTATIONS_FILE") or "expectations.yaml").strip(),
        exec_log=(os.getenv("EXEC_LOG_FILE") or "logs/executions.csv").strip(),
        reports_dir=(os.getenv("REPORTS_DIR") or "logs/reports").strip(),
        history_days=_int(os.getenv("REPORT_HISTORY_DAYS"), 400),
        risk_overshoot_pp=(float(overshoot_raw) if overshoot_raw else None),
        edge_check_interval_sec=_int(os.getenv("EDGE_CHECK_INTERVAL_SEC"), 900),
    )
    health = HealthCfg(
        check_interval_sec=_int(os.getenv("HEALTH_CHECK_INTERVAL_SEC"), 300),
        cdp_stale_sec=_int(os.getenv("HEALTH_CDP_STALE_SEC"), 600),
        antispam_sec=_int(os.getenv("HEALTH_ANTISPAM_SEC"), 1800),
        daily_summary_at=(os.getenv("HEALTH_DAILY_SUMMARY_AT") or "09:00").strip(),
        pid_file=(os.getenv("HEALTH_PID_FILE") or "logs/bot.pid").strip(),
    )
    webhook = WebhookCfg(
        pull_url=(os.getenv("WEBHOOK_PULL_URL") or "").strip().rstrip("/"),
        token=(os.getenv("WEBHOOK_TOKEN") or "").strip(),
        poll_interval_sec=_int(os.getenv("WEBHOOK_POLL_SEC"), 12),
        pull_limit=_int(os.getenv("WEBHOOK_PULL_LIMIT"), 100),
        stale_sec=_int(os.getenv("WEBHOOK_STALE_SEC"), 90),
    )
    notion = NotionCfg(
        token=(os.getenv("NOTION_TOKEN") or "").strip(),
        # id базы «Сделки бота»; при желании переопределяется NOTION_DB_ID
        database_id=(os.getenv("NOTION_DB_ID")
                     or "ef22e9a2100f4803beff954a7aedd0e2").strip(),
        charts_page_id=(os.getenv("NOTION_CHARTS_PAGE_ID") or "").strip(),
        sync_interval_sec=_int(os.getenv("NOTION_SYNC_SEC"), 60),
        history_days=_int(os.getenv("NOTION_HISTORY_DAYS"), 7),
    )
    # Свежесть: дефолты из .env, per-strategy — из config.yaml (signal_freshness).
    fresh_yaml = y.get("signal_freshness", {}) or {}
    freshness = FreshnessCfg(
        entry_max_age_sec=_int(os.getenv("WEBHOOK_ENTRY_MAX_AGE_SEC"),
                               _int(fresh_yaml.get("entry_max_age_sec"), 600)),
        manage_max_age_sec=_int(os.getenv("WEBHOOK_MANAGE_MAX_AGE_SEC"),
                                _int(fresh_yaml.get("manage_max_age_sec"), 21600)),
        per_strategy=fresh_yaml.get("per_strategy", {}) or {},
    )
    demo_yaml = y.get("demo_account", {}) or {}
    demo_account = DemoAccountCfg(
        opened=str(demo_yaml.get("opened") or ""),
        lifetime_days=_int(str(demo_yaml.get("lifetime_days") or ""), 14),
        warn_from_day=_int(str(demo_yaml.get("warn_from_day") or ""), 10),
        urgent_from_day=_int(str(demo_yaml.get("urgent_from_day") or ""), 12),
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
        report=report,
        webhook=webhook,
        notion=notion,
        freshness=freshness,
        demo_account=demo_account,
        symbol_map=y.get("symbol_map", {}) or {},
        risk=y.get("risk", {}) or {},
        guards=y.get("guards", {}) or {},
        exits=y.get("exits", {}) or {},
        pending_orders=y.get("pending_orders", {}) or {},
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
