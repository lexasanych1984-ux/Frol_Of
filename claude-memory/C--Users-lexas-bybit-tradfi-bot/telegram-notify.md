---
name: telegram-notify
description: "Telegram-уведомления переиспользуют бота из D:\\MY\\Crypto\\AI agent, префикс [BOT]."
metadata: 
  node_type: memory
  type: reference
  originSessionId: 4227871c-d4f3-4f60-ad39-be9015a6311d
  modified: 2026-07-21T09:49:46.114Z
---

Telegram-уведомления мониторинга ([[monitoring-health]]) **переиспользуют
существующего бота** из проекта `D:\MY\Crypto\AI agent` (там `TELEGRAM_BOT_TOKEN`
и `TELEGRAM_CHAT_ID` в его `.env`, шлёт через `python-telegram-bot`).

Чтобы НЕ дублировать секреты, в `bybit-tradfi-bot/.env` задан
`TELEGRAM_ENV_FILE=D:\MY\Crypto\AI agent\.env` — `bot/config._resolve_telegram()`
берёт токен/чат оттуда (приоритет у локальных `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`,
если заданы). Ко всем сообщениям добавляется префикс `TELEGRAM_PREFIX=[BOT]`,
чтобы отличать источник в общем чате.

Наш слой (`bot/notify.py`) — намеренно простой синхронный POST на Bot API через
`requests` (не python-telegram-bot): health-модулю нужно лишь надёжно доставить
короткое сообщение и НИКОГДА не уронить бота, если Telegram недоступен. Тот же
`send_telegram()` зовёт внешний watchdog.

Доставка проверена вживую 2026-07-21 (тестовое сообщение дошло).
