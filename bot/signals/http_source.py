"""Локальный HTTP-приёмник сигналов на 127.0.0.1 (loopback).

Полностью локальный и детерминированный. Никогда не биндится на публичный
интерфейс по умолчанию. Пригоден для:
  - ручного теста (curl на localhost),
  - будущего варианта с VPS (тогда host меняется и добавляется реверс-прокси/TLS).

Тело запроса = сырой alert_message (text/plain) или JSON {"message": "..."}.
Если задан HTTP_SHARED_SECRET — требуется заголовок X-Bot-Secret.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .base import SignalSource


class HttpSignalSource(SignalSource):
    def __init__(self, host: str = "127.0.0.1", port: int = 8787, secret: str = ""):
        super().__init__()
        self.host, self.port, self.secret = host, port, secret
        self._srv = None
        self._thread = None

    def start(self) -> None:
        q, secret = self.q, self.secret

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # тише в консоли
                pass

            def _reject(self, code, msg):
                self.send_response(code)
                self.end_headers()
                self.wfile.write(msg.encode("utf-8"))

            def do_POST(self):
                if secret and self.headers.get("X-Bot-Secret", "") != secret:
                    return self._reject(403, "forbidden")
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode("utf-8", "replace").strip()
                msg = body
                if body[:1] == "{":
                    try:
                        obj = json.loads(body)
                        msg = obj.get("message", body) if isinstance(obj, dict) else body
                    except ValueError:
                        msg = body
                if msg:
                    q.put(msg)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

        self._srv = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._srv:
            self._srv.shutdown()

    def health(self, now: float, stale_sec: float):
        """(ok, причина, что делать) — жив ли loopback-приёмник."""
        alive = bool(self._srv and self._thread and self._thread.is_alive())
        if alive:
            return (True, f"http-приёмник слушает {self.host}:{self.port}", "")
        return (False,
                f"http-приёмник на {self.host}:{self.port} не поднят",
                "Перезапусти бота — локальный приёмник сигналов не работает.")
