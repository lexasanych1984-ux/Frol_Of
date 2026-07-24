"""Создание строк Trading Journal в Notion — общий слой для /sync и /trade.

Строка журнала ссылается на справочники relation-полями (Pairs, Direction,
Account), поэтому при СОЗДАНИИ нужно резолвить название в id связанной страницы.
Карты «название→id» тянутся один раз и кешируются (JournalResolvers).

ВАЖНО (память notion-changes-require-walkthrough): сюда попадаем только ПОСЛЕ
явного подтверждения трейдера — предпросмотр и кнопка подтверждения живут выше
(в боте). Здесь только механика записи.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from src.matching.matcher import normalize_symbol
from src.notion_sync import schema
from src.notion_sync.client import NotionClient


def _direction_name(direction: Optional[str]) -> Optional[str]:
    """MT5 buy/sell или рус./англ. слово -> название страницы Direction (Long/Short)."""
    d = (direction or "").strip().lower()
    if d in ("buy", "long", "лонг", schema.DIRECTION_LONG.lower()):
        return schema.DIRECTION_LONG
    if d in ("sell", "short", "шорт", schema.DIRECTION_SHORT.lower()):
        return schema.DIRECTION_SHORT
    return None


class JournalResolvers:
    """Ленивые кеши карт «название→page_id» для relation-справочников журнала.

    Справочник может быть НЕ расшарен интеграции Notion (тогда его запрос даёт
    404). Такой справочник помечается недоступным (unavailable), соответствующее
    поле просто не проставляется — команда не падает, а честно предупреждает.
    Проверено 2026-07-22: журнал/Pairs/Account интеграции доступны, Direction —
    нет (расшарить: открыть базу Direction → Connections → Trading AI)."""

    def __init__(self, client: NotionClient, accounts_config: Optional[dict] = None):
        self.client = client
        self._pairs: Optional[dict[str, str]] = None
        self._directions: Optional[dict[str, str]] = None
        self._accounts: Optional[dict[str, str]] = None
        self._acc_cfg = accounts_config
        self.unavailable: set[str] = set()  # имена справочников, что не расшарены

    def _titles(self, data_source_id: str, title_prop: str, label: str) -> dict[str, str]:
        try:
            return self.client.list_data_source_titles(data_source_id, title_prop)
        except Exception:
            self.unavailable.add(label)
            return {}

    def pair_id(self, symbol: str) -> Optional[str]:
        if self._pairs is None:
            raw = self._titles(schema.PAIRS_DATA_SOURCE_ID, "Name", "Pairs")
            self._pairs = {normalize_symbol(k): v for k, v in raw.items()}
        return self._pairs.get(normalize_symbol(symbol))

    def direction_id(self, direction: str) -> Optional[str]:
        if self._directions is None:
            self._directions = self._titles(
                schema.DIRECTION_DATA_SOURCE_ID, "Direction", "Direction"
            )
        name = _direction_name(direction)
        return self._directions.get(name) if name else None

    def account_id(self, mt5_login: str) -> Optional[str]:
        cfg = self._accounts_config().get(str(mt5_login))
        if cfg is None or not cfg.notion_account_name:
            return None
        if self._accounts is None:
            self._accounts = self._titles(schema.ACCOUNT_DATA_SOURCE_ID, "Name", "Account")
        return self._accounts.get(cfg.notion_account_name)

    def _accounts_config(self) -> dict:
        if self._acc_cfg is None:
            from src.risk.limits import load_accounts_config

            self._acc_cfg = load_accounts_config()
        return self._acc_cfg


def _iso(date: datetime | str) -> str:
    return date.isoformat() if isinstance(date, datetime) else str(date)


def default_title(symbol: Optional[str], direction: Optional[str], date: datetime | str) -> str:
    label = {schema.DIRECTION_LONG: "Long", schema.DIRECTION_SHORT: "Short"}.get(
        _direction_name(direction), ""
    )
    day = date.strftime("%d.%m") if isinstance(date, datetime) else ""
    return " ".join(x for x in [symbol or "", label, day] if x).strip()


def build_trade_properties(
    resolvers: JournalResolvers,
    *,
    symbol: Optional[str],
    direction: Optional[str],
    date: datetime | str,
    entry_label: Optional[str] = None,
    mt5_login: Optional[str] = None,
    pnl: Optional[float] = None,
    title: Optional[str] = None,
) -> tuple[dict, list[str]]:
    """Собирает properties строки журнала в формате Notion API.

    Возвращает (properties, warnings). Нерезолвленные relation НЕ ставятся (строка
    всё равно создаётся), но попадают в warnings, чтобы предпросмотр/карточка
    честно сказали трейдеру, что осталось незаполненным.
    """
    props: dict = {}
    warnings: list[str] = []

    props[schema.TradingJournal.TITLE] = {
        "title": [{"text": {"content": title or default_title(symbol, direction, date)}}]
    }
    props[schema.TradingJournal.DATE] = {"date": {"start": _iso(date)}}

    pid = resolvers.pair_id(symbol) if symbol else None
    if pid:
        props[schema.TradingJournal.PAIRS] = {"relation": [{"id": pid}]}
    else:
        warnings.append(f"инструмент «{symbol}» не найден в справочнике Pairs — поле не проставлено")

    if direction:
        did = resolvers.direction_id(direction)
        if did:
            props[schema.TradingJournal.DIRECTION] = {"relation": [{"id": did}]}
        elif "Direction" in resolvers.unavailable:
            warnings.append(
                "поле Direction не проставлено: база Direction не расшарена интеграции Notion "
                "(расшарь её: Connections → Trading AI — и Direction начнёт заполняться)"
            )
        else:
            warnings.append(f"направление «{direction}» не резолвится — Direction не проставлен")

    if entry_label:
        props[schema.TradingJournal.ENTRY] = {"select": {"name": entry_label}}

    if mt5_login is not None:
        aid = resolvers.account_id(mt5_login)
        if aid:
            props[schema.TradingJournal.ACCOUNT] = {"relation": [{"id": aid}]}
        else:
            warnings.append(f"счёт {mt5_login} не сопоставлен со справочником Account — поле не проставлено")

    if pnl is not None:
        props[schema.TradingJournal.RR_PNL] = {"number": round(pnl, 2)}

    return props, warnings


def create_journal_row(
    client: NotionClient, properties: dict, body_note: Optional[str] = None
) -> dict:
    children = None
    if body_note:
        children = [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": body_note}}]},
            }
        ]
    return client.create_page(properties, children=children)
