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
