# Project memory index — bybit-tradfi-bot

- [Current direction](project-direction.md) — цель = стабильный форвард-тест на Just2Trade демо; перенос на Bybit-реал ОТЛОЖЕН.
- [Silent-failure history](silent-failures-history.md) — каталог молчаливых отказов (403 CDP, мёртвый /list_fires, AutoTrading off → 10027). Принцип: молчание = авария.
- [Liveness monitoring](monitoring-health.md) — health-модуль в процессе + внешний watchdog в Планировщике, Telegram-алерты.
- [Edge degradation report](edge-degradation-report.md) — expectations.yaml + лог проскальзывания + `run.py report` + 2 мгновенные тревоги; факт демо ↔ коридоры бэктеста.
- [Signal buffer](signal-buffer.md) — облачный store-and-forward (Cloudflare Worker+D1) + WebhookPullSource ∥ CDP + max_age; убирает потерю сигналов при простое.
- [Telegram notifications](telegram-notify.md) — переиспользуем бота из D:\MY\Crypto\AI agent, префикс [BOT].
- [Notion journal](notion-journal.md) — база «Сделки бота» + автосинк из бота (bot/notion_sync.py); раньше молча пустела. Нужен NOTION_TOKEN в .env.
- [Runtime facts](runtime-facts.md) — демо-счёт 245169 Just2Trade-MT5, DRY_RUN=false, источник cdp.
- [CRT invalidation & cancel](crt-invalidation-cancel.md) — CRT-отложка без фильтра свежести; инвалидация (закол/конец дня) → сигнал ОТМЕНА + MT5-экспирация. Нужен ручной пересоздать алерт.
- [Projects backup system](projects-backup-system.md) — единый git-бэкап всех проектов в Frol_Of ветка projects-backup; backup.ps1 + Планировщик (вс→пн 03:30); секрет-скан, .env→.env.example.
- [TV сам глушит алерты](tv-alert-stopped-by-error.md) — «Остановлено — Ошибка расчёта» видно только в DOM панели; перезапуск через alert-restart-button; конфиг-сторож теперь почасовой.
- [Окна торговли и аптайм](trading-windows-uptime.md) — ночь и Азию держать НЕ нужно; окно диапазона ≠ окно торговли; зазоры на краях дня (утро 10:00, хвост CRT до 00:00).
- [«ВЫХОД» = отражение SL/TP](exit-alerts-are-sl-tp-mirrors.md) — досрочных выходов у стратегий нет, on_exit_alert=ignore корректен (проверено по MT5 и Strategy Tester).
- [Время MT5 = время сервера](mt5-time-is-server-time.md) — epoch из MetaTrader5 это UTC+3; fromtimestamp врёт на +1 ч, на этом я уже ошибся.
