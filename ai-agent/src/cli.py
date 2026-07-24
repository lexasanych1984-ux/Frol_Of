"""Точка входа CLI.

    python -m src.cli import-mt5 data/raw/ReportHistory-11620556.html
    python -m src.cli risk 11620556
    python -m src.cli dedupe-journal [--apply]
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta

from src.backtest import price_data
from src.backtest.engine import run_backtest
from src.backtest.metrics import compute_metrics, format_report
from src.backtest.progress import log as backtest_log
from src.backtest.signals import generate_signals
from src.mt5_import.html_parser import parse_positions
from src.risk.limits import load_accounts_config, load_ai_review_config
from src.risk.monitor import build_risk_report
from src.storage.db import connect, get_trades, upsert_trades

# Известные допущения формализации (см. STRATEGY_1H_MANIPULATION.md и план
# structured-toasting-rainbow.md) — печатаются в каждом отчёте, чтобы не
# выдавать интерпретацию за буквально подтверждённое трейдером правило.
BACKTEST_ASSUMPTIONS = {
    "1h-manipulation": [
        "Календарь новостей не учитывается — трейдер не торгует только важные ('красные') новости по классификации FundingPips, но фильтр не смоделирован (нет источника исторического календаря с разметкой важности).",
        "Фильтр месячного фрактала: 'снятие' = ЗАКРЫТИЕ D1-бара за уровнем, разблокировка = D1 FVG до уровня в ЛЮБУЮ сторону (оба уточнения подтверждены трейдером 2026-07-02), блокируются все входы по инструменту.",
        "H1 'поглощение манипуляции' формализовано как прокол ближайшего противоположного фрактала тенью и закрытие ЭТОЙ ЖЕ свечи обратно за уровень — трейдер подтвердил только сам факт снятия ликвидности, не точный свечной критерий.",
        "M15-слом структуры считается только внутри той же H4-зоны, что и H1-путь — трейдер описал H1/M15 как независимые альтернативы, но не уточнил, привязан ли M15-слом к зоне.",
        "Тейк = ближайшая значимая противоположная зона за FTA; если такой отдельно от FTA нет — тейк = сама FTA. Точный визуальный выбор 'той самой' зоны трейдер не формализовал.",
        "Несколько сигналов одновременно на разных парах не отбираются по силе/слабости валют — каждая пара тестируется независимо.",
    ]
}


def cmd_import_mt5(args: argparse.Namespace) -> None:
    trades = parse_positions(args.file)
    conn = connect()
    n = upsert_trades(conn, trades)
    print(f"Импортировано/обновлено сделок: {n} (счёт {trades[0].account_login if trades else '?'})")


def cmd_risk(args: argparse.Namespace) -> None:
    accounts = load_accounts_config()
    config = accounts.get(args.mt5_login)
    if config is None:
        print(f"Счёт {args.mt5_login} не найден в config/config.yaml", file=sys.stderr)
        sys.exit(1)

    conn = connect()
    trades = get_trades(conn, account_login=args.mt5_login)
    print(build_risk_report(config, trades))


def cmd_dedupe_journal(args: argparse.Namespace) -> None:
    """Найти в Notion-журнале дубли одной сделки на разных счетах и связать
    их с главной записью через Main Trade (см. src/notion_sync/dedupe.py).
    Без --apply только показывает план, ничего не пишет."""
    from dotenv import load_dotenv

    load_dotenv()

    from src.notion_sync.client import NotionClient
    from src.notion_sync.dedupe import apply_dedupe, build_dedupe_plan, format_dedupe_report

    client = NotionClient()
    pages = list(client.query_trading_journal())
    plan = build_dedupe_plan(pages)
    print(format_dedupe_report(plan, client))

    if plan.n_to_link == 0:
        return
    if args.apply:
        n = apply_dedupe(client, plan)
        print(f"Записано в Notion: {n} дублям проставлен Main Trade.")
    else:
        print("Прогон без записи. Запусти с --apply, чтобы записать связи в Notion.")


def cmd_backtest(args: argparse.Namespace) -> None:
    if args.setup not in BACKTEST_ASSUMPTIONS:
        print(f"Неизвестный сетап {args.setup!r}. Доступные: {list(BACKTEST_ASSUMPTIONS)}", file=sys.stderr)
        sys.exit(1)

    date_from = datetime.strptime(args.date_from, "%Y-%m-%d")
    date_to = datetime.strptime(args.date_to, "%Y-%m-%d")

    signals = generate_signals(args.symbol, date_from, date_to)
    backtest_log(f"{args.symbol}: генерация сигналов завершена — {len(signals)} шт. Качаю M15 для симуляции...")
    # запас на конце диапазона, чтобы у сделок, открытых ближе к date_to,
    # было куда развиваться в симуляции (иначе искусственно много "open_at_data_end")
    m15 = price_data.fetch_bars(args.symbol, "M15", date_from, date_to + timedelta(days=10))
    backtest_log(f"{args.symbol}: симулирую {len(signals)} сигналов через движок...")
    results = run_backtest(signals, m15)
    backtest_log(f"{args.symbol}: симуляция готова, считаю метрики.")

    ai_review_cfg = load_ai_review_config()
    metrics = compute_metrics(results, min_sample_size=ai_review_cfg.get("min_sample_size", 30))
    print(
        format_report(
            metrics,
            args.symbol,
            BACKTEST_ASSUMPTIONS[args.setup],
            deposit_usd=args.deposit,
            risk_pct=args.risk_pct,
        )
    )

    if results:
        print("\nСписок сигналов:")
        for r in results:
            outcome = f"R={r.r_multiple}" if r.is_trade else r.exit_reason
            print(f"  {r.signal.signal_time}  {r.signal.trade_direction:4s}  {r.signal.trigger:14s}  вход={r.signal.entry_price:.5f} ({r.signal.entry_type})  -> {outcome}")


def cmd_news_calendar(args: argparse.Namespace) -> None:
    """Скачать календарь недели и показать отфильтрованное расписание (без LLM)."""
    from src.news import service as news_service
    from src.news.prompts import format_week_schedule

    cfg = news_service.load_news_config()
    conn = news_service.open_db()
    events = news_service.refresh_calendar(conn, cfg)
    if not events:
        print(f"Красных новостей по {', '.join(cfg.currencies)} на этой неделе в фиде нет.")
        return
    print(f"Красные новости недели (время UTC+{cfg.utc_offset_hours}):\n")
    print(format_week_schedule(events, cfg.utc_offset_hours))


def cmd_news_digest(args: argparse.Namespace) -> None:
    """Построить недельный дайджест через Claude (web search) и напечатать.
    Отметку 'дайджест отправлен' НЕ ставит — это делает только бот."""
    from dotenv import load_dotenv

    load_dotenv()

    from src.news import service as news_service
    from src.news.analyst import NewsAnalyst

    cfg = news_service.load_news_config()
    conn = news_service.open_db()
    now = news_service.store.now_utc()
    news_service.refresh_calendar(conn, cfg)
    week = news_service.current_week_key(now)
    print(news_service.build_digest(conn, cfg, NewsAnalyst(), week, now, mark_sent=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Trading Discipline AI — CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_import = sub.add_parser("import-mt5", help="Импортировать ReportHistory-*.html в локальную базу")
    p_import.add_argument("file")
    p_import.set_defaults(func=cmd_import_mt5)

    p_risk = sub.add_parser("risk", help="Показать риск-отчёт по счёту")
    p_risk.add_argument("mt5_login")
    p_risk.set_defaults(func=cmd_risk)

    p_dedupe = sub.add_parser(
        "dedupe-journal",
        help="Связать дубли одной сделки на разных счетах в Notion-журнале (Main Trade)",
    )
    p_dedupe.add_argument("--apply", action="store_true", help="Записать связи в Notion (без флага — только показать план)")
    p_dedupe.set_defaults(func=cmd_dedupe_journal)

    p_backtest = sub.add_parser("backtest", help="Бэктест формализованного сетапа на исторических данных MT5")
    p_backtest.add_argument("setup", choices=list(BACKTEST_ASSUMPTIONS))
    p_backtest.add_argument("symbol")
    p_backtest.add_argument("--from", dest="date_from", required=True, help="YYYY-MM-DD")
    p_backtest.add_argument("--to", dest="date_to", required=True, help="YYYY-MM-DD")
    p_backtest.add_argument("--deposit", type=float, default=10_000.0, help="Депозит для пересчёта R в деньги/проценты (по умолчанию $10,000)")
    p_backtest.add_argument("--risk-pct", type=float, default=1.0, help="Риск на сделку в %% от депозита, фиксированный без реинвеста (по умолчанию 1%%)")
    p_backtest.set_defaults(func=cmd_backtest)

    p_news_cal = sub.add_parser("news-calendar", help="Показать красные новости недели из ForexFactory (без LLM)")
    p_news_cal.set_defaults(func=cmd_news_calendar)

    p_news_digest = sub.add_parser("news-digest", help="Недельный новостной дайджест через Claude (web search)")
    p_news_digest.set_defaults(func=cmd_news_digest)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
