"""Пересборка страницы графиков: чистка старых блоков, заливка PNG (File Upload),
добавление image-блоков одним PATCH."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.notion_charts import NotionCharts


class FakeResp:
    def __init__(self, status=200, data=None, text=""):
        self.status_code = status
        self._data = data if data is not None else {}
        self.text = text

    def json(self):
        return self._data


class FakeSession:
    """Маршрутизирует по URL, записывает вызовы Notion File Upload + blocks API."""
    def __init__(self):
        self.deleted = []
        self.uploaded_files = []      # имена файлов, ушедших на upload_url
        self.appended = None          # children последнего PATCH
        self._fu = 0

    def get(self, url, headers=None, params=None, timeout=None):
        assert "/children" in url
        return FakeResp(200, {"results": [{"id": "b1"}, {"id": "b2"}],
                              "has_more": False})

    def post(self, url, headers=None, json=None, files=None, timeout=None):
        if url.endswith("/file_uploads"):
            self._fu += 1
            fid = f"fu{self._fu}"
            return FakeResp(200, {"id": fid, "upload_url": f"https://up/{fid}"})
        # отправка байтов на upload_url
        assert files and "file" in files
        self.uploaded_files.append(files["file"][0])
        return FakeResp(200, {"status": "uploaded"})

    def delete(self, url, headers=None, timeout=None):
        self.deleted.append(url.rsplit("/", 1)[-1])
        return FakeResp(200, {})

    def patch(self, url, headers=None, json=None, timeout=None):
        assert "/children" in url
        self.appended = json["children"]
        return FakeResp(200, {})


def _imgs():
    return [("equity.png", "Кривая", b"\x89PNG_1"),
            ("pnl.png", "P&L", b"\x89PNG_2")]


def test_rebuild_clears_uploads_and_appends():
    s = FakeSession()
    ch = NotionCharts("tok", "page123", session=s)
    n = ch.rebuild(_imgs(), "Обновлено сегодня")

    assert n == 2
    assert s.deleted == ["b1", "b2"]                 # старые блоки снесены
    assert s.uploaded_files == ["equity.png", "pnl.png"]   # оба PNG залиты
    # заголовок + 2 картинки
    assert len(s.appended) == 3
    assert s.appended[0]["type"] == "paragraph"
    assert [b["type"] for b in s.appended[1:]] == ["image", "image"]
    assert s.appended[1]["image"]["file_upload"]["id"] == "fu1"
    assert s.appended[2]["image"]["file_upload"]["id"] == "fu2"


def test_rebuild_noop_without_page_id():
    s = FakeSession()
    ch = NotionCharts("tok", "", session=s)
    assert ch.rebuild(_imgs(), "x") == 0
    assert s.deleted == [] and s.uploaded_files == []
