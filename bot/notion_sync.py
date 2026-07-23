"""Автосинхронизация закрытых сделок в Notion-базу «Сделки бота».

Зачем: раньше дашборд доходности в Notion наполнялся вручную (импорт CSV), и
если про это забыть — база молча оставалась пустой, хотя сделки закрывались и
даже уходили в Telegram. Это ровно тот «молчаливый отказ», который у проекта в
запрете. Теперь бот сам пишет КАЖДУЮ закрытую сделку строкой в базу.

Как: тик в главном цикле бота (как ``PositionCloseWatcher``) — на каждом тике
берём закрытые сделки из истории MT5 (``stats.collect_closed``), считаем ту же
кривую equity, что и CSV (``stats.equity_curve``), и досоздаём в Notion строки,
которых там ещё нет. Идемпотентность — по ``Позиция ID`` (уникальный id позиции
MT5): существующие id вычитываются из базы и пропускаются, поэтому повторный тик
и добор за простой не плодят дублей.

Если Notion недоступен/отвечает ошибкой — бот НЕ падает: несколько провалов
подряд поднимают ОДНУ тревогу в Telegram (журнал отстаёт), дальше повтор на
следующих тиках; при восстановлении шлётся отбой.

Обращения к MT5 идут в главном потоке через ``maybe_tick()`` и не пересекаются с
исполнением ордеров (та же дисциплина, что у closewatch/edgewatch).
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional

import requests

from . import stats

log = logging.getLogger("bot")

_API = "https://api.notion.com/v1"
_VERSION = "2022-06-28"


class NotionError(RuntimeError):
    """Notion ответил не 2xx или сеть отвалилась — обрабатывается вызывающим."""


class NotionJournal:
    """Тонкий REST-клиент базы «Сделки бота» (только query + create page)."""

    def __init__(self, token: str, database_id: str, timeout: float = 15.0,
                 session: Optional[requests.Session] = None):
        self.token = (token or "").strip()
        self.database_id = (database_id or "").strip()
        self.timeout = timeout
        self._s = session or requests.Session()

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": _VERSION,
            "Content-Type": "application/json",
        }

    def existing_position_ids(self) -> set:
        """Все значения свойства «Позиция ID», уже лежащие в базе.

        Постранично (Notion отдаёт по 100). При ошибке бросает NotionError."""
        ids: set = set()
        cursor = None
        url = f"{_API}/databases/{self.database_id}/query"
        while True:
            body: dict = {"page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            try:
                r = self._s.post(url, headers=self._headers(), json=body,
                                 timeout=self.timeout)
            except requests.RequestException as e:
                raise NotionError(f"query сеть: {e}") from e
            if r.status_code != 200:
                raise NotionError(f"query HTTP {r.status_code}: {r.text[:200]}")
            data = r.json() or {}
            for page in data.get("results", []):
                prop = (page.get("properties") or {}).get("Позиция ID") or {}
                num = prop.get("number")
                if num is not None:
                    ids.add(int(num))
            if data.get("has_more"):
                cursor = data.get("next_cursor")
            else:
                break
        return ids

    def create_trade(self, trade, equity: Optional[float], cum: float) -> None:
        """Создать одну строку сделки. При ошибке бросает NotionError."""
        props = {
            "Сделка": {"title": [{"text": {"content": _title(trade)}}]},
            "Стратегия": {"select": {"name": trade.strategy}},
            "Символ": {"select": {"name": trade.symbol}},
            "Сторона": {"select": {"name": trade.side}},
            "Лот": {"number": trade.volume},
            "Вход": {"number": trade.price_open},
            "Выход": {"number": trade.price_close},
            "P&L": {"number": round(trade.profit, 2)},
            "Кумул. P&L стратегии": {"number": cum},
            "Позиция ID": {"number": int(trade.position_id)},
            "Открыта": {"date": {"start": _iso(trade.open_time)}},
            "Закрыта": {"date": {"start": _iso(trade.close_time)}},
        }
        if equity is not None:
            props["Equity"] = {"number": equity}
        body = {
            "parent": {"database_id": self.database_id},
            "icon": {"type": "emoji", "emoji": _icon(trade.profit)},
            "properties": props,
        }
        try:
            r = self._s.post(f"{_API}/pages", headers=self._headers(), json=body,
                             timeout=self.timeout)
        except requests.RequestException as e:
            raise NotionError(f"create сеть: {e}") from e
        if r.status_code not in (200, 201):
            raise NotionError(f"create HTTP {r.status_code}: {r.text[:200]}")


def _title(t) -> str:
    return (f"{t.strategy} {t.symbol} {t.side} · "
            f"{t.close_time.strftime('%d.%m %H:%M')}")


def _iso(dt) -> str:
    """Время закрытия/открытия как ISO с ЛОКАЛЬНЫМ смещением (напр. +04:00).

    collect_closed отдаёт наивное локальное время (как в trades.csv). Notion
    хранит момент в UTC и рисует его в tz воркспейса; без смещения REST-API
    примет наивное время за UTC и сдвинет отображение (поздняя сделка уедет на
    соседний день, ломая график «по дням»). astimezone() навешивает локальное
    смещение машины — Notion покажет ту же настенную дату/время, что и CSV, если
    воркспейс в том же поясе, что торговый ПК."""
    return dt.astimezone().isoformat(timespec="seconds")


def _icon(profit: float) -> str:
    if profit > 1e-9:
        return "✅"
    if profit < -1e-9:
        return "❌"
    return "➖"


class NotionSyncWatcher:
    """Тикает в главном цикле; досинхронизирует новые закрытия в Notion."""

    def __init__(self, journal: NotionJournal, broker, notifier=None,
                 symbol_alias: Optional[dict] = None, interval_sec: int = 60,
                 history_days: int = 7, alert_after_fails: int = 3,
                 charts=None, clock: Callable[[], float] = time.time):
        self.journal = journal
        self.broker = broker
        self.notifier = notifier
        self.symbol_alias = symbol_alias or {}
        self.interval = max(10, int(interval_sec))
        self.history_days = max(1, int(history_days))
        self.alert_after_fails = max(1, int(alert_after_fails))
        self.charts = charts                # NotionCharts | None (PNG-графики)
        self.clock = clock
        self._last_tick = 0.0
        self._known: Optional[set] = None   # position_id, уже лежащие в Notion
        self._charts_built = False          # графики хоть раз собраны за сессию
        self._fail_streak = 0
        self._alerted = False               # тревога уже поднята — не спамить

    def maybe_tick(self, now: Optional[float] = None) -> None:
        now = self.clock() if now is None else now
        if now - self._last_tick >= self.interval:
            self.tick(now)

    def tick(self, now: Optional[float] = None) -> int:
        """Один проход: вернуть число ЗАПИСАННЫХ на этом тике строк.

        Никогда не бросает — сбой логируется и (после серии) тревожит в Telegram,
        но бот продолжает работать и торговать."""
        self._last_tick = self.clock() if now is None else now
        try:
            trades = stats.collect_closed(self.broker.mt5, days=self.history_days,
                                          symbol_alias=self.symbol_alias)
            if self._known is None:
                self._known = self.journal.existing_position_ids()

            acc = self.broker.account()
            start_balance = round((acc.balance or 0.0)
                                  - sum(t.profit for t in trades), 2)

            written = 0
            for t, equity, cum in stats.equity_curve(trades, start_balance):
                if int(t.position_id) in self._known:
                    continue
                self.journal.create_trade(t, equity, cum)
                self._known.add(int(t.position_id))
                written += 1
                log.info("notion ← %s %s %s P&L %+.2f (pos %s) записана",
                         t.strategy, t.symbol, t.side, t.profit, t.position_id)

            # Графики пересобираем при новой строке или один раз при старте —
            # незачем перезаливать картинки на каждом «пустом» тике.
            if self.charts is not None and (written > 0 or not self._charts_built):
                self._rebuild_charts(trades, start_balance)
                self._charts_built = True

            self._on_success(written)
            return written
        except Exception as e:  # синк НИКОГДА не роняет бота
            return self._on_failure(e)

    def _rebuild_charts(self, trades, start_balance) -> None:
        from datetime import datetime
        from . import charts as chmod          # ленивый импорт (matplotlib тяжёлый)
        imgs = chmod.render_all(trades, start_balance)
        if not imgs:
            return
        last_eq = None
        for _t, eq, _cum in stats.equity_curve(trades, start_balance):
            last_eq = eq
        eq_txt = f" · equity {last_eq:,.0f}$" if last_eq is not None else ""
        header = (f"Обновлено {datetime.now():%Y-%m-%d %H:%M} · "
                  f"сделок: {len(trades)}{eq_txt}")
        n = self.charts.rebuild(imgs, header)
        log.info("notion ← графики пересобраны (%d картинок)", n)

    def _on_success(self, written: int) -> None:
        if self._alerted:   # был сбой, теперь ожил — дать отбой
            log.info("Автосинк Notion восстановлен.")
            self._notify("✅ Журнал Notion снова обновляется — синхронизация "
                         "восстановлена.")
        self._fail_streak = 0
        self._alerted = False

    def _on_failure(self, e: Exception) -> int:
        self._fail_streak += 1
        # частичный сбой мог оставить кэш неполным — перечитаем базу на след. тике
        self._known = None
        log.warning("Сбой автосинка Notion (%d подряд): %s", self._fail_streak, e)
        if self._fail_streak >= self.alert_after_fails and not self._alerted:
            self._notify("⚠️ Журнал Notion НЕ обновляется: сделки закрываются, но "
                         f"в базу не пишутся ({e}). Проверь NOTION_TOKEN/доступ "
                         "интеграции к базе.")
            self._alerted = True
        return 0

    def _notify(self, text: str) -> None:
        if self.notifier is not None:
            try:
                self.notifier.send(text)
            except Exception as ex:
                log.warning("Не удалось отправить тревогу автосинка: %s", ex)
