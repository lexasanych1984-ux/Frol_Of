# Облачный буфер сигналов — Cloudflare Worker + D1

Store-and-forward приёмник, убирающий главный узел отказа: локальный бот видит
алерт TradingView, только пока он запущен и TV жив. Этот приёмник принимает webhook
от TradingView **серверно** (даже когда домашний ПК выключен), хранит сигналы **7
дней**, а бот их **опрашивает** (pull) — никаких входящих портов на ПК.

- **Стоимость: $0** в free tier (Worker 100k req/день; D1 5M чтений / 100k записей /
  5 ГБ). Наш объём — единицы сигналов/день + ~7200 pull-GET/день — с огромным запасом.
- **Нечего патчить и держать живым**: нет ОС, cron, TLS-сертификатов. TLS + DDoS —
  из коробки. Надёжность edge Cloudflare ≫ домашнего ПК или $4-VPS.

Файлы: `worker.js` (логика), `schema.sql` (таблица D1), `wrangler.toml` (конфиг).

## Эндпоинты

| Метод | Путь | Назначение |
|-------|------|-----------|
| POST | `/hook/<token>` | принять тело алерта, ответить `200` мгновенно |
| GET | `/pull/<token>?after=<id>&limit=<n>` | забрать сигналы с `id > after`, по возрастанию |
| GET | `/head/<token>` | `{"max_id": N}` — инициализация курсора бота |

Токен — в пути URL (webhook TradingView умеет только фиксированный URL + тело, без
кастомных заголовков). Неверный токен/роут → `404`. Дедуп ретраев TV — по хэшу
`(тело + минута)`. Ретенция 7 дней — ежедневный cron + оппортунистически при вставке.

## Развёртывание (один раз, ~10 минут)

Нужен аккаунт Cloudflare (бесплатный) и Node.js. `wrangler` ставится через `npx`.

```bash
cd cloud

# 1. Логин в Cloudflare
npx wrangler login

# 2. Создать базу D1 и вписать её database_id в wrangler.toml
npx wrangler d1 create bybit-signals
#    → скопируй database_id из вывода в wrangler.toml (поле database_id)

# 3. Создать таблицу (в боевой, --remote)
npx wrangler d1 execute bybit-signals --file=schema.sql --remote

# 4. Сгенерировать ДЛИННЫЙ случайный токен и записать как секрет Worker
python -c "import secrets;print(secrets.token_urlsafe(32))"
npx wrangler secret put HOOK_TOKEN
#    → вставь тот же токен, что сгенерировал выше

# 5. Задеплоить
npx wrangler deploy
#    → получишь URL вида https://bybit-signals.<аккаунт>.workers.dev
```

Затем в `.env` бота:
```
WEBHOOK_PULL_URL=https://bybit-signals.<аккаунт>.workers.dev
WEBHOOK_TOKEN=<тот же токен, что в HOOK_TOKEN>
```
URL для поля webhook в алертах TradingView: `<WEBHOOK_PULL_URL>/hook/<WEBHOOK_TOKEN>`.

## Проверка (smoke)

Со стороны бота (проверяет всю цепочку, MT5 не трогает):
```
python run.py webhook-selftest
```

Вручную через curl (`$U` = URL, `$T` = токен):
```bash
# пусто на старте
curl "$U/head/$T"                                   # {"max_id":0}
# отправить тестовое тело
curl -X POST "$U/hook/$T" --data 'SELFTEST — не сигнал'
# забрать
curl "$U/pull/$T?after=0"                            # [{"id":1,"ts":...,"body":"SELFTEST — не сигнал",...}]
# дедуп: тот же POST в пределах 2 мин НЕ создаёт вторую запись
curl -X POST "$U/hook/$T" --data 'SELFTEST — не сигнал'   # {"ok":true,"stored":false,"reason":"duplicate"}
curl "$U/pull/$T?after=0"                            # по-прежнему одна запись
```

## Наблюдение и обслуживание

```bash
npx wrangler tail                                    # живой лог Worker
npx wrangler d1 execute bybit-signals --command "SELECT COUNT(*) FROM signals" --remote
```

## Безопасность

- Токен ≥32 байта, хранится как секрет Worker (не в git, не в `wrangler.toml`); в
  боте — в `.env` (в `.gitignore`). Сверка в постоянном времени.
- Тело ограничено 8 КБ. Это буфер сигналов, не публичный API — при желании включи
  Cloudflare WAF/Rate Limiting на маршрут `/hook/*`.
- Компрометация токена = злоумышленник может слать поддельные тела в буфер. Защита
  на стороне бота: гейт свежести (протухшее не исполняется) и риск-лимиты; при утечке —
  сгенерируй новый токен (шаг 4–5) и обнови `.env` + webhook-URL в алертах.

## Альтернатива (если Cloudflare не подходит)

VPS + FastAPI + SQLite (~$4-6/мес или Oracle Always Free). Те же 3 роута, тот же
контракт (`/hook`, `/pull?after`, `/head`) — бот-сторона не меняется, только
`WEBHOOK_PULL_URL`. Минус: сам патчишь ОС/TLS и следишь за аптаймом (риск нового узла
отказа). Поэтому по умолчанию — Worker.

## Откат / удаление

```bash
npx wrangler delete                                  # удалить Worker
npx wrangler d1 delete bybit-signals                 # удалить базу
```
После этого в `.env` очисти `WEBHOOK_PULL_URL`/`WEBHOOK_TOKEN` — бот вернётся к
работе только по CDP (с прежним узлом отказа).
