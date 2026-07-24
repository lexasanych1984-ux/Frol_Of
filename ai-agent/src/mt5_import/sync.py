"""Оркестрация команды /sync: закрытые сделки ручного терминала MT5 -> строки
Notion-журнала, с дедупом против уже существующих записей.

Дедуп — это ровно matching-модуль (src/matching/matcher.py): сводим MT5-сделки
с записями журнала по инструменту+дате; сделки, для которых пары в журнале НЕТ,
и есть кандидаты на создание. Уже сматченные пропускаем — так /sync не плодит
дубли уже занесённых вручную сделок.

Запись идёт только после подтверждения трейдера в Telegram (кнопка) — здесь
build_sync_preview() ничего не пишет, apply_sync() вызывается отдельно.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from src.matching.matcher import match_trades
from src.mt5_import.live_export import fetch_positions
from src.mt5_import.models import Trade
from src.notion_sync.client import NotionClient
from src.notion_sync.journal_writer import (
    JournalResolvers,
    build_trade_properties,
    create_journal_row,
)


@dataclass
class SyncPreview:
    account_login: Optional[str]
    candidates: list[Trade] = field(default_factory=list)  # к созданию (нет пары в журнале)
    n_mt5_total: int = 0
    n_already_in_journal: int = 0
    account_in_config: bool = True


def build_sync_preview(
    terminal_path: str,
    client: NotionClient,
    date_from: datetime,
    date_to: datetime,
    accounts_config: Optional[dict] = None,
) -> SyncPreview:
    """Подключиться к ручному терминалу, свести с журналом, вернуть кандидатов.
    НИЧЕГО не пишет в Notion."""
    trades, account_login = fetch_positions(terminal_path, date_from, date_to)

    pages = list(client.query_trading_journal(date_from=date_from, date_to=date_to))
    results = match_trades(trades, pages, client)
    candidates = [m.trade for m in results if m.trade is not None and m.notion_page is None]
    n_matched = sum(1 for m in results if m.matched)

    if accounts_config is None:
        from src.risk.limits import load_accounts_config

        accounts_config = load_accounts_config()
    account_in_config = str(account_login) in accounts_config

    return SyncPreview(
        account_login=account_login,
        candidates=candidates,
        n_mt5_total=len(trades),
        n_already_in_journal=n_matched,
        account_in_config=account_in_config,
    )


def _pnl_str(trade: Trade) -> str:
    pnl = trade.net_pnl
    sign = "+" if pnl >= 0 else "−"
    return f"{sign}{abs(pnl):.2f}$"


def format_preview(preview: SyncPreview) -> str:
    lines = [f"🔄 /sync — ручной счёт {preview.account_login}"]
    lines.append(f"Закрытых сделок в MT5 за период: {preview.n_mt5_total}")
    lines.append(f"Уже есть в журнале (пропущу): {preview.n_already_in_journal}")
    lines.append(f"К созданию: {len(preview.candidates)}")

    if not preview.account_in_config:
        lines.append(
            f"\n⚠️ Счёт {preview.account_login} не описан в config.yaml — "
            "поле Account у новых строк проставлено не будет."
        )

    if not preview.candidates:
        lines.append("\nВсё уже в журнале — создавать нечего.")
        return "\n".join(lines)

    lines.append("")
    for i, t in enumerate(preview.candidates, 1):
        lines.append(
            f"  {i}. {t.symbol}  {t.open_time:%d.%m}  {t.direction:4s}  {_pnl_str(t)}"
        )
    lines.append(
        f"\nСоздать {len(preview.candidates)} строк? Заполню Date/Pairs/Direction/Account/PnL. "
        "Result и сетап (Entry) оставлю пустыми — их проставишь сам."
    )
    return "\n".join(lines)


def apply_sync(
    client: NotionClient,
    candidates: list[Trade],
    account_login: Optional[str],
    resolvers: Optional[JournalResolvers] = None,
) -> tuple[int, list[str]]:
    """Создаёт строки журнала для кандидатов. Вызывать ТОЛЬКО после подтверждения.
    Возвращает (сколько создано, уникальные предупреждения)."""
    resolvers = resolvers or JournalResolvers(client)
    created = 0
    warnings: set[str] = set()
    for t in candidates:
        props, warns = build_trade_properties(
            resolvers,
            symbol=t.symbol,
            direction=t.direction,
            date=t.open_time,
            mt5_login=account_login,
            pnl=t.net_pnl,
        )
        create_journal_row(client, props)
        created += 1
        warnings.update(warns)
    return created, sorted(warnings)
