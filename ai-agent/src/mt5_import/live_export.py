"""Живой импорт закрытых сделок из РУЧНОГО терминала MT5 через пакет
`MetaTrader5` — источник для команды /sync.

Терминал ручной торговли (напр. `C:\\Program Files\\MetaTrader 5`) должен быть
запущен и залогинен. Подключаемся строго по ЯВНОМУ пути (`initialize(path=...)`),
чтобы не прицепиться к «ботовскому» терминалу (data-dir в AppData\\Roaming),
который использует бэктест. Пакет `MetaTrader5` держит одно подключение на
процесс, поэтому вся работа обёрнута в initialize→...→shutdown внутри одного
вызова; бот больше нигде MT5 не держит.

Из истории сделок (`history_deals_get`) собираем ПОЗИЦИИ: сделки одной позиции
связаны общим `position_id`, вход помечен DEAL_ENTRY_IN, выход(ы) —
DEAL_ENTRY_OUT. Результат — те же объекты `Trade`, что даёт html_parser, так что
matching-модуль работает без изменений.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Tuple

from .models import Trade


class MT5NotRunningError(RuntimeError):
    """Ручной терминал не запущен / не залогинен / недоступен по указанному пути."""


def _require_mt5():
    try:
        import MetaTrader5 as mt5  # Windows-only, импорт внутри — чтобы модуль
        return mt5                 # импортировался и в тестах без пакета.
    except ImportError as e:  # pragma: no cover - зависит от платформы
        raise MT5NotRunningError(
            "Пакет MetaTrader5 не установлен (нужен Windows + терминал MT5)."
        ) from e


def fetch_positions(
    terminal_path: str,
    date_from: datetime,
    date_to: datetime,
) -> Tuple[List[Trade], Optional[str]]:
    """Закрытые позиции ручного терминала за [date_from, date_to].

    Возвращает (список Trade, логин счёта). Кидает MT5NotRunningError с понятным
    текстом, если терминал недоступен, — молчания быть не должно.
    """
    mt5 = _require_mt5()

    if not mt5.initialize(path=terminal_path):
        raise MT5NotRunningError(
            f"Не удалось подключиться к ручному терминалу MT5 по пути {terminal_path}: "
            f"{mt5.last_error()}. Терминал должен быть запущен и залогинен на счёте FundingPips."
        )
    try:
        info = mt5.account_info()
        if info is None:
            raise MT5NotRunningError(
                "Терминал MT5 запущен, но счёт не залогинен "
                f"(account_info вернул None: {mt5.last_error()})."
            )
        account_login = str(info.login)

        deals = mt5.history_deals_get(date_from, date_to)
        if deals is None:
            raise MT5NotRunningError(
                f"Не удалось получить историю сделок: {mt5.last_error()}."
            )
        trades = _positions_from_deals(deals, mt5, account_login)
    finally:
        mt5.shutdown()

    return trades, account_login


def _positions_from_deals(deals, mt5, account_login: str) -> List[Trade]:
    """Собирает закрытые позиции из плоского списка сделок по position_id."""
    by_position: dict[int, list] = {}
    for d in deals:
        # балансовые/неторговые операции (пополнения) position_id=0, symbol="" — пропускаем
        if not getattr(d, "symbol", "") or d.position_id == 0:
            continue
        by_position.setdefault(d.position_id, []).append(d)

    trades: List[Trade] = []
    for position_id, position_deals in by_position.items():
        position_deals.sort(key=lambda d: d.time)
        entry_deal = next((d for d in position_deals if d.entry == mt5.DEAL_ENTRY_IN), None)
        out_deals = [d for d in position_deals if d.entry == mt5.DEAL_ENTRY_OUT]
        if entry_deal is None or not out_deals:
            continue  # позиция ещё открыта или странная — /sync берёт только закрытые

        last_out = out_deals[-1]
        direction = "buy" if entry_deal.type == mt5.DEAL_TYPE_BUY else "sell"
        profit = sum(d.profit for d in position_deals)
        commission = sum(d.commission for d in position_deals)
        swap = sum(d.swap for d in position_deals)

        trades.append(
            Trade(
                account_login=account_login,
                position_id=str(position_id),
                symbol=entry_deal.symbol,
                direction=direction,
                volume=entry_deal.volume,
                open_time=datetime.fromtimestamp(entry_deal.time),
                open_price=entry_deal.price,
                close_time=datetime.fromtimestamp(last_out.time),
                close_price=last_out.price,
                sl=None,   # в сделках MT5 уровни SL/TP не хранятся построчно
                tp=None,
                commission=commission,
                swap=swap,
                profit=profit,
                source_file=f"MT5 live ({account_login})",
            )
        )

    trades.sort(key=lambda t: t.open_time)
    return trades
