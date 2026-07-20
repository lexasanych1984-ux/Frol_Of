"""Локальное чтение сработавших алертов TradingView Desktop через CDP :9222.

Как это работает (проверено вживую 2026-07-20):
TradingView-страница держит websocket на ``wss://pushstream.tradingview.com``.
Когда алерт срабатывает, сервер шлёт в канал ``pricealerts`` сообщение
``m: "alert_fired"``, и в нём — УЖЕ РАЗВЁРНУТЫЙ текст alert_message
(``{{strategy.order.alert_message}}`` подставлен реальными значениями).

Мы не лезем в DOM и ничего не мутируем на странице: подключаемся к CDP,
включаем домен ``Network`` и просто слушаем кадры websocket
(``Network.webSocketFrameReceived``). Ничего не уходит наружу.

Форма полезного кадра::

    {"method":"Network.webSocketFrameReceived","params":{"response":{"payloadData":
      "{\\"text\\":{\\"channel\\":\\"pricealerts\\",\\"content\\":{
         \\"m\\":\\"alert_fired\\",
         \\"p\\":{\\"message\\":\\"LONG EURUSD | вход ...\\",
                 \\"alert_id\\":5146158866,\\"fire_id\\":53853815890,
                 \\"fire_time\\":\\"2026-07-20T10:14:47Z\\"}}}}"}}}

``fire_id`` — уникальный номер срабатывания, идеальный ключ дедупликации.

⚠️ ВАЖНО: история срабатываний недоступна. Endpoint ``/list_fires`` удалён на
стороне TradingView (отвечает ``no_such_endpoint``), а панель «Журнал» в
приложении наполняется только живым push-потоком. Значит, бот видит ТОЛЬКО те
алерты, которые сработали, пока он запущен. Пропущенные за время простоя
сигналы восстановить нельзя — при старте мы лишь показываем, какие алерты
срабатывали недавно (по ``last_fire_time`` из ``list_alerts``), чтобы было
видно, что было пропущено.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Optional

import requests
import websocket  # websocket-client

from .base import SignalSource

log = logging.getLogger("bot")

# Сколько последних fire_id помнить, чтобы не обработать одно срабатывание дважды
# (переподключение к CDP заново кадры не проигрывает, но подстрахуемся).
_SEEN_MAX = 500


class CdpSignalSource(SignalSource):
    def __init__(self, port: int = 9222, reconnect_sec: float = 5.0):
        super().__init__()
        self.port = port
        self.reconnect_sec = reconnect_sec
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._seen: list[str] = []
        self._seen_set: set[str] = set()
        self._msg_id = 0
        self._connected = False

    # ── Подключение к вкладке TradingView ─────────────────────────────────────
    def _find_target_ws(self) -> str:
        r = requests.get(f"http://127.0.0.1:{self.port}/json", timeout=5)
        targets = r.json()
        for t in targets:
            url = (t.get("url") or "").lower()
            if t.get("type") == "page" and "tradingview" in url:
                return t["webSocketDebuggerUrl"]
        for t in targets:
            if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                return t["webSocketDebuggerUrl"]
        raise RuntimeError("не найдена вкладка TradingView на CDP-порту "
                           f"{self.port} (приложение запущено с отладкой?)")

    def _connect(self):
        # suppress_origin обязателен: Chrome/Electron отклоняет CDP-хендшейк с
        # заголовком Origin (403 «Rejected an incoming WebSocket connection»),
        # а websocket-client шлёт его по умолчанию.
        ws = websocket.create_connection(self._find_target_ws(), timeout=15,
                                         suppress_origin=True,
                                         max_size=16 * 1024 * 1024)
        self._msg_id += 1
        ws.send(json.dumps({"id": self._msg_id, "method": "Network.enable"}))
        ws.settimeout(2)
        return ws

    # ── Разбор кадров ─────────────────────────────────────────────────────────
    def _mark_seen(self, fire_id: str) -> bool:
        """True — впервые видим это срабатывание."""
        if fire_id in self._seen_set:
            return False
        self._seen.append(fire_id)
        self._seen_set.add(fire_id)
        if len(self._seen) > _SEEN_MAX:
            self._seen_set.discard(self._seen.pop(0))
        return True

    def _handle_frame(self, payload: str) -> None:
        if "alert_fired" not in payload:
            return
        try:
            outer = json.loads(payload)
        except ValueError:
            return
        text = outer.get("text")
        if not isinstance(text, dict) or text.get("channel") != "pricealerts":
            return
        content = text.get("content") or {}
        if content.get("m") != "alert_fired":
            return
        p = content.get("p") or {}
        message = p.get("message")
        if not message:
            return
        fire_id = str(p.get("fire_id") or f"{p.get('alert_id')}_{p.get('fire_time')}")
        if not self._mark_seen(fire_id):
            return
        log.info("АЛЕРТ СРАБОТАЛ (fire_id=%s, %s): %s",
                 fire_id, p.get("fire_time"), message)
        self.q.put(message)

    # ── Основной цикл ─────────────────────────────────────────────────────────
    def _loop(self):
        ws = None
        while not self._stop.is_set():
            try:
                if ws is None:
                    ws = self._connect()
                    self._connected = True
                    log.info("CDP подключён — слушаю срабатывания алертов "
                             "(pushstream канал pricealerts)")
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            except Exception as e:
                if self._connected:
                    log.warning("CDP-соединение потеряно (%s). Переподключаюсь "
                                "через %.0f с…", e, self.reconnect_sec)
                else:
                    log.warning("Не удалось подключиться к TradingView по CDP "
                                "(%s). Повтор через %.0f с…", e, self.reconnect_sec)
                self._connected = False
                try:
                    if ws:
                        ws.close()
                except Exception:
                    pass
                ws = None
                self._stop.wait(self.reconnect_sec)
                continue

            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if msg.get("method") != "Network.webSocketFrameReceived":
                continue
            payload = ((msg.get("params") or {}).get("response") or {}).get("payloadData")
            if isinstance(payload, str) and payload:
                try:
                    self._handle_frame(payload)
                except Exception as e:  # один битый кадр не должен ронять поток
                    log.exception("Ошибка разбора кадра алерта: %s", e)

        try:
            if ws:
                ws.close()
        except Exception:
            pass

    # ── Диагностика при старте ────────────────────────────────────────────────
    def report_recent_fires(self, hours: int = 24) -> None:
        """Показать, какие алерты срабатывали недавно.

        Живой поток истории не даёт, поэтому это только справка: увидеть, что
        бот пропустил, пока был выключен. В торговлю эти срабатывания НЕ идут —
        текст сообщения в list_alerts хранится шаблоном
        ({{strategy.order.alert_message}}), без подставленных цен.
        """
        js = (
            "fetch('https://pricealerts.tradingview.com/list_alerts',"
            "{credentials:'include'}).then(r=>r.json()).then(d=>JSON.stringify("
            "(d.r||[]).filter(a=>a.last_fire_time).map(a=>({n:a.name||a.message,"
            "t:a.last_fire_time,s:a.symbol}))))"
        )
        try:
            ws = websocket.create_connection(self._find_target_ws(), timeout=15,
                                             suppress_origin=True)
            self._msg_id += 1
            ws.send(json.dumps({"id": self._msg_id, "method": "Runtime.evaluate",
                                "params": {"expression": js, "returnByValue": True,
                                           "awaitPromise": True}}))
            deadline = time.time() + 15
            value = None
            while time.time() < deadline:
                resp = json.loads(ws.recv())
                if resp.get("id") == self._msg_id:
                    value = (resp.get("result", {}).get("result", {}) or {}).get("value")
                    break
            ws.close()
            fires = json.loads(value) if value else []
        except Exception as e:
            log.warning("Не удалось прочитать список алертов: %s", e)
            return

        cutoff = time.time() - hours * 3600
        recent = []
        for f in fires:
            try:
                ts = time.mktime(time.strptime(f["t"], "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
            except Exception:
                continue
            if ts >= cutoff:
                recent.append((f.get("n"), f.get("t")))
        if recent:
            log.info("Срабатывания за последние %d ч (ДО запуска бота, в торговлю "
                     "не идут):", hours)
            for name, t in sorted(recent, key=lambda x: x[1]):
                log.info("   • %s — %s UTC", name, t)
        else:
            log.info("За последние %d ч срабатываний алертов не было.", hours)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
