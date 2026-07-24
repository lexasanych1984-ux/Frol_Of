"""Тонкий клиент над Notion REST API.

Сырые HTTP-вызовы вместо SDK — чтобы явно контролировать заголовок
Notion-Version и работать с data source id (актуальная модель API для
мульти-сорсных баз, к которым относится "Trading Journal").

Нужен Notion-интеграция с доступом к воркспейсу (создаётся на
https://www.notion.so/my-integrations), токен — в NOTION_API_KEY.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Iterator, Optional

import requests

from . import schema

NOTION_API_BASE = "https://api.notion.com/v1"
# С версии 2025-09-03 Notion разделила "database" (контейнер) и "data source"
# (собственно таблица) — наши id из schema.py это id data source, поэтому
# нужна именно эта версия API и эндпоинт /v1/data_sources/{id}/query
# (а не легаси /v1/databases/{id}/query, который на новых id отдаёт 404).
NOTION_VERSION = "2025-09-03"


class NotionClient:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ["NOTION_API_KEY"]
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {self.api_key}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            }
        )

    # --- низкоуровневые вызовы ---

    def _post(self, path: str, json: dict) -> dict:
        resp = self._session.post(f"{NOTION_API_BASE}{path}", json=json)
        resp.raise_for_status()
        return resp.json()

    def _get(self, path: str) -> dict:
        resp = self._session.get(f"{NOTION_API_BASE}{path}")
        resp.raise_for_status()
        return resp.json()

    def _patch(self, path: str, json: dict) -> dict:
        resp = self._session.patch(f"{NOTION_API_BASE}{path}", json=json)
        resp.raise_for_status()
        return resp.json()

    # --- Trading Journal ---

    def query_trading_journal(
        self,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        data_source_id: str = schema.TRADING_JOURNAL_DATA_SOURCE_ID,
    ) -> Iterator[dict]:
        """Возвращает записи Trading Journal, постранично.

        Примечание: ограничение "requires Business plan", в которое упирался
        MCP query-tool (SQL/view режимы), было специфично для того инструмента —
        обычный POST .../data_sources/{id}/query такого ограничения не имеет
        и доступен на любом плане Notion.
        """
        payload: dict[str, Any] = {"page_size": 100}
        if date_from or date_to:
            date_filter: dict[str, Any] = {}
            if date_from:
                date_filter["on_or_after"] = date_from.date().isoformat()
            if date_to:
                date_filter["on_or_before"] = date_to.date().isoformat()
            payload["filter"] = {"property": schema.TradingJournal.DATE, "date": date_filter}

        cursor = None
        while True:
            if cursor:
                payload["start_cursor"] = cursor
            data = self._post(f"/data_sources/{data_source_id}/query", payload)
            for page in data.get("results", []):
                yield page
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")

    def update_trade_page(self, page_id: str, properties: dict) -> dict:
        """properties — словарь {имя_поля: значение_в_notion_api_формате}."""
        return self._patch(f"/pages/{page_id}", {"properties": properties})

    def archive_page(self, page_id: str) -> dict:
        """Отправить страницу в корзину Notion. Обратимо: восстановить можно из
        Trash. Нужно для отмены ошибочно созданной строки и для самоочищающейся
        проверки create_page (создать -> сразу заархивировать)."""
        return self._patch(f"/pages/{page_id}", {"archived": True})

    def create_page(
        self,
        properties: dict,
        data_source_id: str = schema.TRADING_JOURNAL_DATA_SOURCE_ID,
        children: Optional[list] = None,
    ) -> dict:
        """Создать страницу (строку журнала). properties — уже в формате Notion API.

        В API 2025-09-03 родитель мульти-сорсной базы задаётся по data_source_id;
        на некоторых воркспейсах это ещё не включено — тогда падаем на database_id
        контейнера журнала.
        """
        body: dict[str, Any] = {
            "parent": {"type": "data_source_id", "data_source_id": data_source_id},
            "properties": properties,
        }
        if children:
            body["children"] = children
        try:
            return self._post("/pages", body)
        except requests.HTTPError as e:
            if e.response is None or e.response.status_code != 400:
                raise
            body["parent"] = {"type": "database_id", "database_id": schema.TRADING_JOURNAL_DATABASE_ID}
            return self._post("/pages", body)

    def list_data_source_titles(self, data_source_id: str, title_prop: str) -> dict[str, str]:
        """Карта {название страницы: page_id} для справочника relation-поля
        (Pairs/Direction/Account) — чтобы при создании строки резолвить имя в id."""
        result: dict[str, str] = {}
        payload: dict[str, Any] = {"page_size": 100}
        cursor = None
        while True:
            if cursor:
                payload["start_cursor"] = cursor
            data = self._post(f"/data_sources/{data_source_id}/query", payload)
            for page in data.get("results", []):
                title = _extract_title(page, title_prop)
                if title:
                    result[title] = page["id"]
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
        return result

    def resolve_relation_title(self, page_id: str) -> str:
        """Достаёт title связанной страницы (напр. название инструмента из Pairs)."""
        page = self._get(f"/pages/{page_id}")
        for prop in page.get("properties", {}).values():
            if prop.get("type") == "title":
                title_parts = prop.get("title", [])
                return "".join(t.get("plain_text", "") for t in title_parts)
        return ""


def _extract_title(page: dict, prop_name: str) -> str:
    """Собирает plain-text значение title-свойства страницы по его имени."""
    prop = page.get("properties", {}).get(prop_name, {})
    parts = prop.get("title", [])
    return "".join(t.get("plain_text", "") for t in parts).strip()


def extract_date_start(page: dict, prop_name: str = schema.TradingJournal.DATE) -> Optional[datetime]:
    """Хелпер: вытащить datetime начала из date-свойства страницы Notion."""
    date_prop = page.get("properties", {}).get(prop_name, {}).get("date")
    if not date_prop or not date_prop.get("start"):
        return None
    start = date_prop["start"]
    # Notion отдаёт либо дату (YYYY-MM-DD), либо datetime с таймзоной
    try:
        return datetime.fromisoformat(start)
    except ValueError:
        return datetime.strptime(start, "%Y-%m-%d")
