---
name: monitoring-health
description: "Как устроен мониторинг живости — встроенный health + внешний watchdog, Telegram-алерты."
metadata: 
  node_type: memory
  type: project
  originSessionId: 4227871c-d4f3-4f60-ad39-be9015a6311d
  modified: 2026-07-21T12:08:54.024Z
---

Мониторинг живости (реализован 2026-07-21), принцип **молчание = авария**
([[silent-failures-history]]). Два уровня:

1. **Встроенный health** (`bot/health.py`) — внутри процесса бота, в ГЛАВНОМ
   потоке (главный цикл в `run.py::cmd_trade` перешёл с блокирующего `stream()`
   на `src.poll(timeout)` + `monitor.maybe_tick()`, поэтому вызовы MT5 не
   пересекаются с исполнением — лок не нужен). Каждые 5 мин снимает 4 проверки:
   - `signal` — CDP-поток к TradingView подключён и получал кадры за N мин
     (`CdpSignalSource.health`; поток пишет `_last_frame_ts` на каждый кадр);
   - `mt5_login` — `account_info()` жив и `login == MT5_LOGIN` из .env;
   - `terminal_algo` — `terminal_info().trade_allowed`;
   - `expert` — `account_info().trade_expert`.
     Проверки 2–4 берутся одним `MT5Broker.health_snapshot()` → `Mt5Health`.
   Машина переходов OK↔FAIL с анти-спамом — `HealthMonitor.evaluate(results, now)`
   (чистая, без I/O, покрыта `tests/test_health.py`). Анти-спам: одна авария
   напоминает не чаще `HEALTH_ANTISPAM_SEC` (30 мин); переход OK→FAIL и FAIL→OK —
   немедленно. Суточная сводка в `HEALTH_DAILY_SUMMARY_AT` (09:00).

2. **Внешний watchdog** (`tools/watchdog.py`, задача Планировщика раз в час,
   ставится `tools/install-watchdog-task.ps1`) — ловит падение всего процесса.
   Бот пишет pid-файл `logs/bot.pid` (удаляет при штатной остановке). Watchdog:
   pid-файл есть + процесс мёртв → Telegram-тревога; pid-файла нет → штатная
   остановка, молчим.

Переходы и отправки логируются в `bot.log` через `HealthMonitor._emit` (INFO:
`health → Telegram: ...`; провал доставки — WARNING; heartbeat «ничего не
изменилось» — DEBUG; базлайн при старте — INFO). Раньше монитор молчал в логе,
и было не понять, что он решил.

Telegram — через `bot/notify.py` (простой POST на Bot API, без python-telegram-bot).
Креды — см. [[telegram-notify]]. Счётчики сигналов/ордеров для сводки —
`HealthMetrics`, движок инкрементит (`Engine(..., metrics=...)`).

Документация и приёмка: `docs/MONITORING.md`.
