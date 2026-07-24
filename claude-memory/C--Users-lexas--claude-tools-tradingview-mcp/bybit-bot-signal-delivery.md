---
name: bybit-bot-signal-delivery
description: "Как сигналы доходят до бота (CDP + облачный webhook-буфер), диагностика, и разбор инцидента «2 дня нет сигналов» 20–21.07.2026"
metadata: 
  node_type: memory
  type: project
  originSessionId: 64f23b1f-2eb1-442e-856e-7dff0e6c6e07
  modified: 2026-07-23T17:17:54.842Z
---

Канал доставки сигналов у [[bybit-tradfi-bot]] (появился после памяти от 20.07):
бот в `run.py trade` слушает **два источника параллельно** через `CompositeSource`:
быстрый локальный **CDP** (`cdp_source.py`, читает pushstream TradingView) + надёжный
**облачный webhook-буфер** (`webhook_source.py`, pull). Дедуп между каналами — в
движке по хэшу содержимого (`State.seen`, окно `dedup_window_sec`=900).

**Облачный буфер** = Cloudflare Worker `bybit-signals.lexas.workers.dev` + D1
(`cloud/worker.js`). Роуты: `POST /hook/<token>` (принять тело алерта),
`GET /pull/<token>?after&limit`, `GET /head/<token>`. Токен = `WEBHOOK_TOKEN` в
`.env` бота; **URL вебхука в TV-алертах = `<WEBHOOK_PULL_URL>/hook/<WEBHOOK_TOKEN>`**.
Дедуп ретраев в Worker — по «тело+минута» (120 с), ретенция 7 дней. Гейт свежести в
движке: вход `entry_max_age_sec`=600, БУ/выход=21600.

**Диагностика:**
- `python run.py webhook-selftest` — round-trip `/head→/hook→/pull`, MT5 НЕ трогает.
- Какой процесс — живой бот: `logs/bot.pid` + `Get-NetTCPConnection -OwningProcess`;
  у живого есть соединения `→ Cloudflare:443` (pull) и `→ 127.0.0.1:9222` (CDP).
- `.venv\Scripts\python.exe run.py trade` — это **лаунчер-заглушка venv** (1 поток,
  0 соединений), реальный интерпретатор = системный `Python312\python.exe`. Это НЕ
  второй трейдер, двойного исполнения нет.

**Инцидент «с начала недели (пн–вт 20–21.07) ни одного алерта/сделки» — разобран
2026-07-22, корень = дыра в ДОСТАВКЕ, не «рынок молчал»:**
1. Боевые strategy-алерты имели `web_hook=null` до ~22:00 21.07 (пересозданы
   скриптом `recreate-alert-with-webhook.cjs` с вебхуком). В буфере `max_id=2` =
   ни одного реального срабатывания стратегии вебхук так и не принял.
2. У бота webhook-pull **падал на каждом опросе**: `SQLite objects created in a
   thread can only be used in that same thread` (State-соединение из чужого потока).
   Чинилось в `state.py` (`check_same_thread=False` + `threading.Lock`, уже в
   коммите `c35ce23`) в 23:23 21.07. Запуски 23:21 и 23:26 висели с мёртвым
   облачным каналом и слали в Telegram «🔴 АВАРИЯ · Резервный webhook-буфер». Текущий
   процесс (старт 23:28) здоров: `webhook=OK`, ошибок нет.

**E2E проверено 2026-07-22:** `POST /hook` с `LONG GBPJPY` → бот вычитал `webhook#4`
→ открыл демо BUY 3.27 лота, ticket 247064917, риск ровно 1.00%, slippage 0 →
затем закрыт (тестовая). Значит цепочка «webhook → буфер → бот → демо-ордер MT5»
рабочая. (Через CDP исполнение доказано ещё 20.07, ticket 247023415.)

**Telegram — разобрано 2026-07-22, ДВЕ отдельные вещи:**
1. **Доставка РАБОТА�ет** (не сломана): прямой тест API дал `getMe`/`sendMessage`
   200, сообщение доставлено. `health._emit` пишет в лог `health → Telegram: …`
   ТОЛЬКО при `send()==True`, значит и вчерашние старт/стоп/«🔴 АВАРИЯ буфер» дошли.
   НО адресат — бот **`@JournalFXFR_bot`** («AI Trade»), личный чат `350346052`
   (Alexey Frolov / `@AlexFrol_01`), переиспользован из `D:\MY\Crypto\AI agent`
   (токен/чат в его `.env`, `TELEGRAM_BOT_TOKEN`/`_CHAT_ID`), префикс `[BOT]`.
   **Пользователь этот чат НЕ смотрит** → для него «уведомления не приходят», хотя
   технически доходят. Если нужен другой бот/чат — сменить `TELEGRAM_ENV_FILE` (или
   задать `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` в `.env` бота).
2. **Уведомления на сделки ДОБАВЛЕНЫ и ЗАКОММИЧЕНЫ 2026-07-22** (commit `25b6d91`,
   107 тестов зелёные): (а) `engine._notify_entry` — пуш на ВХОД (🟢/🔴, стратегия/
   символ/лот/уровни/риск%/ticket) + на БУ (🟡) и exit-сигнал (⏹); (б) новый
   `bot/closewatch.py` `PositionCloseWatcher` — следит за историей сделок MT5 и шлёт
   пуш о ЗАКРЫТИИ позиции (✅/❌ + P&L) по ЛЮБОЙ причине (SL/TP/ручное/сигнал), т.к.
   позиции чаще закрывает брокер, а не движок; курсор времени в State
   (`close_cursor`, ISO), первый старт не переигрывает историю, опрос 30с в главном
   цикле `run.py`. Тесты: `test_trade_notifications.py`, `test_closewatch.py`
   (+ поправлены 2 в `test_slippage.py`). ВАЖНО про exit-сигнал: в проде
   `on_exit_alert: ignore`, но пуш о закрытии всё равно идёт — его даёт closewatch
   по истории, а не обработка exit-алерта. Живой бот перезапущен на новом коде
   (2026-07-22 01:08, PID менялся); проверено вживую: вход GBPJPY → пуш, закрытие →
   `❌ ЗАКРЫТИЕ · SMC · GBPJPY LONG · P&L -172.35`. Адресат — `@JournalFXFR_bot`.
   Перезапуск: стоп текущего `run.py trade` + `Start-Process .venv\Scripts\python.exe
   run.py trade`; НЕ запускать ярлык, пока крутится ручной процесс (будет двойной
   бот). Осталось: `токен.txt` в корне репо untracked — добавить в `.gitignore`.

**КОРЕНЬ ПРОПАЖ СИГНАЛОВ НАЙДЕН 2026-07-23: ноут = Modern Standby (S0), засыпает
по простою** — при этом TV Desktop и бот ЗАМЕРЗАЮТ (сеть глушится, PID жив но данные
не идут), сигналы приходят протухшими и режутся гейтом свежести (вход 10 мин). Так
ПОТЕРЯНА сделка CRT GER40 23.07: fire 07:00:04Z, бот увидел только 08:16Z (проснулся
на касание) — возраст 76 мин > 10 → «Пропущен по возрасту», исполнения нет (по обоим
каналам сразу: cdp# и webhook#7). Это же — вероятный корень «2 дня без сигналов».
Watchdog писал «процесс жив» = только PID, не поток данных. Улики: журнал Windows
System (Power-Troubleshooter/Kernel-Power 42/107/506/507) — ночной сон 22.07 20:08Z→
23.07 05:46Z + дневные «entering Modern Standby: Idle Timeout», просыпается только от
Keyboard/Touchpad/Mouse. **Смягчено (`powercfg`, только AC/розетка):** monitor-timeout-ac
0 (экран не гаснет → нет входа в Modern Standby — это ГЛАВНЫЙ рычаг), hibernate-timeout-ac
0, standby-timeout-ac уже был 0. CONNECTIVITYINSTANDBY на этой машине не выставляется
(«подгруппы не существует») — не нужен, раз standby на AC вообще не наступает. На
батарее спит как раньше (осознанно). Плюс крышка на AC=«ничего не делать» (скрытую
LIDACTION раскрыл `powercfg -attributes 4f971e89-… 5ca83367-… -ATTRIB_HIDE`, затем
SETACVALUEINDEX …0; DC=сон оставлен). **ДУРАБЛ-фикс СДЕЛАН 23.07:** wake-lock в
`bot/keepawake.py` (`SetThreadExecutionState(ES_CONTINUOUS|ES_SYSTEM_REQUIRED)`, no-op
вне Windows/при ошибке), подключён в `run.py` `cmd_trade`: `keep_system_awake(log)`
перед циклом, `release_system_awake()` в finally. Проверено: компилируется, возвращает
True. **Активен со СЛЕДУЮЩЕГО рестарта бота** (текущий процесс без него, но powercfg
уже прикрывает). Проверка в работе: `powercfg /requests` → SYSTEM покажет python.exe.
Стратегический задел: always-on хост (мини-ПК/VPS). Гейт свежести
(10 мин) НЕ трогать — он отработал верно. Машина в UTC+4 (bias -240): логи бота/TV в
UTC+4, fire_id в UTC (при разборе времени вычитать 4 ч).

**Вебхук-токен NAS100 был БИТЫЙ (в хвост URL приписаны 4 лишние буквы `node`, 47 vs
43 симв) → TV отдавал 404, облачный буфер по потоку NAS100 не наполнялся вообще.**
Исправлено 23.07 вручную в UI (End→Backspace×4→Применить→Сохранить), сверено
программно: токен теперь = рабочему (`…sTBs`), 1 алерт 5198837287 active, дублей нет,
watchdog не задет (сверяет инпуты, не вебхук; alert_id тот же → эталон валиден).
Сделку 22.07 это НЕ стоило — её поймал CDP (−760). Как сверять токены: list_alerts →
`web_hook`, сравнить с рабочим GBPJPY (alert_id 5198806498). У всех 6 боевых один
токен `227o…sTBs`, воркер = единственный `WEBHOOK_TOKEN`, чужой токен = 404.

**ГРАБЛЯ (Claude Code): классификатор безопасности ЖЁСТКО блокирует запись ИИ в живые
TV-алерты — и MCP `ui_evaluate` с `create_alert`, и `Write` .cjs-скрипта с create_alert
(«Blocked by classifier»).** Значит: live-правки боевых алертов = ТОЛЬКО руками
пользователя в UI, либо им же через `! <cmd>`, либо с явным разрешением/Bash-правилом.
ЧТЕНИЕ через `ui_evaluate` (list_alerts, сверка токенов) — проходит нормально.
