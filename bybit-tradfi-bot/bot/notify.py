"""Telegram-уведомления мониторинга живости.

Намеренно простой синхронный слой: один POST на Bot API через ``requests``
(уже зависимость проекта). Никакого python-telegram-bot/async — health-модулю
нужно лишь надёжно доставить короткое сообщение и НИКОГДА не уронить бота, если
Telegram недоступен.

Токен и chat_id можно переиспользовать у бота из ``D:\\MY\\Crypto\\AI agent``
(см. TELEGRAM_ENV_FILE в config). Ко всем сообщениям добавляется префикс
(по умолчанию ``[BOT]``), чтобы отличать этот источник в общем чате.
"""
from __future__ import annotations

import logging

import requests

log = logging.getLogger("bot")

_API = "https://api.telegram.org/bot{token}/sendMessage"


def send_telegram(token: str, chat_id: str, text: str, timeout: float = 10.0) -> bool:
    """Отправить одно сообщение. Возвращает True при успехе.

    Автономна (не зависит от остального пакета) — её же зовёт внешний watchdog.
    Любая ошибка сети/Telegram проглатывается и логируется: уведомление не
    должно ронять ни бота, ни чекер.
    """
    if not token or not chat_id:
        return False
    try:
        r = requests.post(
            _API.format(token=token),
            json={"chat_id": chat_id, "text": text,
                  "disable_web_page_preview": True},
            timeout=timeout,
        )
        if r.status_code != 200:
            log.warning("Telegram ответил %s: %s", r.status_code, r.text[:200])
            return False
        return True
    except Exception as e:  # сеть/таймаут/битый ответ — не наша забота ронять бота
        log.warning("Не удалось отправить Telegram-сообщение: %s", e)
        return False


class TelegramNotifier:
    """Обёртка с префиксом и включаемостью.

    Если токен/chat_id не заданы — ``enabled=False``: методы молча ничего не шлют
    (и один раз предупреждают в лог), чтобы бот работал и без Telegram.
    """

    def __init__(self, token: str = "", chat_id: str = "", prefix: str = "[BOT]"):
        self.token = (token or "").strip()
        self.chat_id = (chat_id or "").strip()
        self.prefix = (prefix or "").strip()
        self.enabled = bool(self.token and self.chat_id)
        self._warned = False

    def send(self, text: str) -> bool:
        if not self.enabled:
            if not self._warned:
                log.warning("Telegram-уведомления ВЫКЛЮЧЕНЫ: не заданы "
                            "TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID (или "
                            "TELEGRAM_ENV_FILE). Мониторинг работает, но молча.")
                self._warned = True
            return False
        body = f"{self.prefix} {text}" if self.prefix else text
        return send_telegram(self.token, self.chat_id, body)
