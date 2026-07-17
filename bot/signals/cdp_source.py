"""Локальное чтение сработавших алертов из TradingView Desktop через CDP :9222.

Тот же механизм, что использует MCP-сервер: подключаемся к отладочному порту
Electron-приложения и выполняем JS в контексте страницы TradingView (same-origin),
чтобы прочитать журнал сработавших алертов. Ничего не уходит наружу.

⚠️ Точный внутренний endpoint журнала «сработавших» алертов TradingView нужно
подтвердить на живом приложении — см. docs/VALIDATION.md. Метод `_fetch_fires_js`
изолирует это место: при необходимости правится только он.
"""
from __future__ import annotations

import json
import threading
import time

import requests
import websocket  # websocket-client

from .base import SignalSource


class CdpSignalSource(SignalSource):
    def __init__(self, port: int = 9222, poll_sec: float = 2.0):
        super().__init__()
        self.port = port
        self.poll_sec = poll_sec
        self._stop = threading.Event()
        self._thread = None
        self._seen_ids: set[str] = set()
        self._msg_id = 0

    # ── Подключение к вкладке TradingView ─────────────────────────────────────
    def _find_target_ws(self) -> str:
        r = requests.get(f"http://127.0.0.1:{self.port}/json", timeout=5)
        targets = r.json()
        for t in targets:
            url = (t.get("url") or "").lower()
            if t.get("type") == "page" and ("tradingview" in url or "chart" in url):
                return t["webSocketDebuggerUrl"]
        # запасной вариант — первая страница
        for t in targets:
            if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                return t["webSocketDebuggerUrl"]
        raise RuntimeError("Не найдена вкладка TradingView на CDP-порту")

    def _eval(self, ws, expression: str):
        self._msg_id += 1
        ws.send(json.dumps({
            "id": self._msg_id,
            "method": "Runtime.evaluate",
            "params": {"expression": expression, "returnByValue": True,
                       "awaitPromise": True},
        }))
        while True:
            resp = json.loads(ws.recv())
            if resp.get("id") == self._msg_id:
                return resp.get("result", {}).get("result", {}).get("value")

    # ── JS, читающий журнал сработавших алертов ───────────────────────────────
    @staticmethod
    def _fetch_fires_js() -> str:
        # Возвращает JSON-массив [{id, message, time}] последних срабатываний.
        # ⚠️ endpoint подтвердить на живом TV (docs/VALIDATION.md).
        return r"""
        (async () => {
          try {
            const r = await fetch('https://pricealerts.tradingview.com/list_fires', {
              method: 'GET', credentials: 'include'
            });
            if (!r.ok) return JSON.stringify({error: 'http ' + r.status});
            const data = await r.json();
            const fires = (data && (data.fires || data.r || data.list)) || [];
            const out = fires.slice(-20).map(f => ({
              id: String(f.id || f.fire_id || (f.alert_id + '_' + f.fire_time)),
              message: f.desc || f.message || f.alert_message || '',
              time: f.fire_time || f.time || 0
            }));
            return JSON.stringify(out);
          } catch (e) { return JSON.stringify({error: String(e)}); }
        })()
        """

    def _loop(self):
        ws = None
        while not self._stop.is_set():
            try:
                if ws is None:
                    ws = websocket.create_connection(self._find_target_ws(), timeout=10)
                    self._eval(ws, "1")  # прогрев
                raw = self._eval(ws, self._fetch_fires_js())
                fires = json.loads(raw) if raw else []
                if isinstance(fires, dict) and fires.get("error"):
                    time.sleep(self.poll_sec * 2)
                else:
                    for f in fires:
                        fid = f.get("id")
                        if fid and fid not in self._seen_ids and f.get("message"):
                            self._seen_ids.add(fid)
                            self.q.put(f["message"])
            except Exception:
                try:
                    if ws:
                        ws.close()
                except Exception:
                    pass
                ws = None
                time.sleep(self.poll_sec * 2)
            self._stop.wait(self.poll_sec)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
