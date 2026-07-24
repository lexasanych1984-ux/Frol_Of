"""Заливка PNG-графиков в Notion и пересборка страницы графиков.

Бесплатный тариф Notion разрешает лишь 1 нативный чарт на воркспейс, поэтому
графики доходности бот рисует картинками (bot/charts.py) и кладёт их на
отдельную страницу Notion как image-блоки (картинки лимитом не считаются).

Notion File Upload API (три шага): создать file_upload → отправить байты на
upload_url → приложить блоком по file_upload.id. На каждом обновлении страница
графиков ПОЛНОСТЬЮ пересобирается (старые блоки удаляются) — она целиком
принадлежит боту, поэтому конфликтов с ручным контентом нет. Держи на этой
странице только авто-графики.

Ошибки не глушим внутри — их ловит вызывающий (NotionSyncWatcher) и, как со
строками, поднимает тревогу в Telegram (молчание = авария).
"""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import requests

from .notion_sync import NotionError, _API, _VERSION

log = logging.getLogger("bot")


class NotionCharts:
    def __init__(self, token: str, page_id: str, timeout: float = 30.0,
                 session: Optional[requests.Session] = None):
        self.token = (token or "").strip()
        self.page_id = (page_id or "").strip()
        self.timeout = timeout
        self._s = session or requests.Session()

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self.token}",
                "Notion-Version": _VERSION}

    def _json_headers(self) -> dict:
        return {**self._auth(), "Content-Type": "application/json"}

    # ── File Upload API ──────────────────────────────────────────────────────
    def _upload_png(self, filename: str, data: bytes) -> str:
        """Загрузить один PNG, вернуть file_upload.id (приложить в течение часа)."""
        try:
            r = self._s.post(f"{_API}/file_uploads", headers=self._json_headers(),
                             json={"filename": filename, "content_type": "image/png"},
                             timeout=self.timeout)
        except requests.RequestException as e:
            raise NotionError(f"file_uploads сеть: {e}") from e
        if r.status_code not in (200, 201):
            raise NotionError(f"file_uploads HTTP {r.status_code}: {r.text[:200]}")
        info = r.json() or {}
        fid, upload_url = info.get("id"), info.get("upload_url")
        if not fid or not upload_url:
            raise NotionError(f"file_uploads без id/upload_url: {str(info)[:200]}")
        try:
            r2 = self._s.post(upload_url, headers=self._auth(),
                              files={"file": (filename, data, "image/png")},
                              timeout=self.timeout)
        except requests.RequestException as e:
            raise NotionError(f"upload send сеть: {e}") from e
        if r2.status_code not in (200, 201):
            raise NotionError(f"upload send HTTP {r2.status_code}: {r2.text[:200]}")
        return fid

    # ── Пересборка страницы ──────────────────────────────────────────────────
    def _child_block_ids(self) -> List[str]:
        ids: List[str] = []
        cursor = None
        while True:
            params = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            try:
                r = self._s.get(f"{_API}/blocks/{self.page_id}/children",
                                headers=self._auth(), params=params,
                                timeout=self.timeout)
            except requests.RequestException as e:
                raise NotionError(f"list children сеть: {e}") from e
            if r.status_code != 200:
                raise NotionError(f"list children HTTP {r.status_code}: {r.text[:200]}")
            data = r.json() or {}
            ids += [b["id"] for b in data.get("results", [])]
            if data.get("has_more"):
                cursor = data.get("next_cursor")
            else:
                break
        return ids

    def _delete_block(self, block_id: str) -> None:
        try:
            r = self._s.delete(f"{_API}/blocks/{block_id}", headers=self._auth(),
                               timeout=self.timeout)
        except requests.RequestException as e:
            raise NotionError(f"delete block сеть: {e}") from e
        if r.status_code != 200:
            raise NotionError(f"delete block HTTP {r.status_code}: {r.text[:200]}")

    def _append(self, children: list) -> None:
        try:
            r = self._s.patch(f"{_API}/blocks/{self.page_id}/children",
                              headers=self._json_headers(),
                              json={"children": children}, timeout=self.timeout)
        except requests.RequestException as e:
            raise NotionError(f"append children сеть: {e}") from e
        if r.status_code != 200:
            raise NotionError(f"append children HTTP {r.status_code}: {r.text[:200]}")

    def rebuild(self, images: List[Tuple[str, str, bytes]], header: str) -> int:
        """Полностью пересобрать страницу графиков. Вернуть число картинок.

        Порядок важен: сначала чистим, затем грузим PNG (получаем file_upload.id)
        и одним PATCH прикладываем заголовок + image-блоки."""
        if not self.page_id:
            return 0
        for bid in self._child_block_ids():
            self._delete_block(bid)
        children: list = [{
            "object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text",
                          "text": {"content": header}}]},
        }]
        for filename, caption, data in images:
            fid = self._upload_png(filename, data)
            children.append({
                "object": "block", "type": "image",
                "image": {"type": "file_upload", "file_upload": {"id": fid},
                          "caption": [{"type": "text", "text": {"content": caption}}]},
            })
        self._append(children)
        return len(images)
