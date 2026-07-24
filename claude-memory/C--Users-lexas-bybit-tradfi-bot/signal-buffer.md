---
name: signal-buffer
description: Облачный store-and-forward буфер сигналов (Cloudflare Worker + D1) + WebhookPullSource параллельно CDP + защита возраста max_age. Убирает потерю сигналов при простое.
metadata: 
  node_type: memory
  type: project
  originSessionId: 1be92d48-3826-4be0-b38d-f7d871ef1a05
  modified: 2026-07-21T19:15:33.065Z
---

Облачный **store-and-forward буфер сигналов** (добавлен 2026-07-21). Убирает главный
оставшийся узел отказа: CDP видел алерт только пока бот запущен и TV жив, история
недоступна (`/list_fires` удалён) — простой = потеря сигнала навсегда
([[silent-failures-history]] п.2, теперь СМЯГЧЁН).

**РАЗВЁРНУТО и активно с 2026-07-21.** Worker: `https://bybit-signals.lexas.workers.dev`
(аккаунт lexasanych1984, поддомен lexas.workers.dev, D1 `bybit-signals`
id 08256f5f-445b-4401-9cf3-d281620ed8b9, cron очистки 17 3 * * *). Токен — секрет
`HOOK_TOKEN` в Worker + `WEBHOOK_TOKEN` в `.env` бота (НЕ в git). Цепочка проверена
`run.py webhook-selftest` (round-trip ok) и дедуп (повтор тела → stored:false).

**Облако** — `cloud/` (Cloudflare Worker + D1 SQLite, free tier $0). Роуты (токен в пути
URL, сверка в постоянном времени, 404 при несовпадении): `POST /hook/<token>` (приём,
мгновенный 200, дедуп ретраев по хэшу тело+минута), `GET /pull/<token>?after=<id>&limit`
(курсор по возрастанию id), `GET /head/<token>` (max_id для инициализации). Ретенция 7
дней (cron 03:17 UTC + оппортунистически). Схема `cloud/schema.sql`, конфиг
`cloud/wrangler.toml` (секрет `HOOK_TOKEN` через `wrangler secret put`, НЕ в git). Деплой
и smoke — `cloud/README.md`.

**Бот** — новый источник `bot/signals/webhook_source.py::WebhookPullSource` работает
ПАРАЛЛЕЛЬНО CDP (`bot/signals/composite.py::CompositeSource` сливает очереди), опрос каждые
~12 с. Курсор `after=<id>` персистится в `state.db` (таблица `meta`, ключ `webhook_cursor`,
`State.get_meta/set_meta`). Первый старт: курсор = текущий max_id (не переигрывать буфер);
рестарт: с сохранённого (добор простоя). Провайдер-независимо: боту нужны только
`WEBHOOK_PULL_URL`+`WEBHOOK_TOKEN` в `.env` (иначе канал молча выключен).

**Конверт сигнала** — `bot/signals/base.py::RawSignal(raw, received_ts, source, ext_id)`;
очередь источников переведена со `str` на конверт. CDP кладёт `received_ts`=fire_time→epoch,
`ext_id`=fire_id; webhook — `ts` из облака, `ext_id`=id.

**Защита возраста (max_age)** — `Engine.handle_raw(raw, *, received_ts, source, ext_id)`
(старый вызов со строкой совместим). Гейт `_fresh_enough`: ВХОД старше `entry_max_age_sec`
(600 с/10 мин) НЕ исполняется → лог + Telegram «⏳ Пропущен по возрасту» + mark_seen; БУ/выход
— лимит `manage_max_age_sec` (21600 с/6 ч, позиция уже открыта). `received_ts=None` → свежий.
Конфиг: `config.yaml signal_freshness` (+ `per_strategy` override) и `.env WEBHOOK_*_MAX_AGE_SEC`
(`bot/config.py::FreshnessCfg/WebhookCfg`). **Дедуп CDP↔webhook** — существующий по хэшу
содержимого (`Signal.dedup_key`); `guards.dedup_window_sec` поднят 120→900 с (покрыть разъезд
каналов).

**Health** — 5-я проверка «Резервный webhook-буфер» (`HealthMonitor(webhook_source=...)`,
опциональная: без источника всё как было, 4/4). Критерий — был ли недавно УСПЕШНЫЙ опрос
(пустой ответ = норма), stale=90 с. Проверка цепочки без MT5: `python run.py webhook-selftest`.

**Пересоздание 6 алертов TradingView — СДЕЛАНО 2026-07-21.** Generic-скрипт
`tradingview-mcp/scripts/recreate-alert-with-webhook.cjs` (берёт ЖИВЫЕ inputs из
`watchdog/etalons.json` — шаблоны create-smc/create-crt устарели, дрейф!) добавил `web_hook`,
сохранив `mobile_push`. Новые alert_id (старые удалены): SMC EURUSD 15m 5198786079, SMC GBPJPY
15m 5198805822, SMC GBPJPY 1H 5198806498, CRT GER40 1H 5198807214, Asia GER40 5m 5198807488,
Asia NAS100 5m 5198837287. Манифест `STREAMS` в `watchdog.mjs` обновлён на новые id, эталон
пересобран (`--baseline` → `--dry` = 6/6). **Урок:** webhook в TradingView требует включённой
2FA на аккаунте (`webhook_requires_2fa`), и после включения 2FA нужно ПЕРЕЛОГИНИТЬСЯ в TV
Desktop, иначе `create_alert` даёт `unauthorized`. Успех create_alert = `{"s":"ok"}` (id алерта
в `r`, не в верхнем `id`); сырой list_alerts поле зовётся `alert_id`, не `id`.

Тесты (все 97 зелёные): `tests/test_{freshness,webhook_source,acceptance_buffer}.py`. Приёмка
= симуляция «бот выключен 30 мин»: вход 30-мин пропущен по возрасту, БУ 5-мин исполнен.
Доки: `docs/BUFFER.md`. Связано: [[monitoring-health]], [[silent-failures-history]],
[[runtime-facts]].
