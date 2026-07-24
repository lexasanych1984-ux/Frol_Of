---
name: runtime-facts
description: "Боевые runtime-факты — демо-счёт, терминал, режим, источник сигналов."
metadata: 
  node_type: memory
  type: reference
  originSessionId: 4227871c-d4f3-4f60-ad39-be9015a6311d
  modified: 2026-07-21T09:37:13.534Z
---

Боевые параметры форвард-теста (из `.env`, на 2026-07-21):
- **MT5-счёт:** `245169` @ `Just2Trade-MT5` (демо, MT5 Standard, создан 2026-07-18).
- **Терминал:** `C:\Users\lexas\AppData\Roaming\MetaTrader 5\terminal64.exe` — отдельный,
  только для бота (старый `C:\Program Files\MetaTrader 5` — ручной, не трогать).
- **Режим:** `BYBIT_ENV=demo`, `DRY_RUN=false` (реальное исполнение виртуальными деньгами).
- **Источник сигналов:** `SIGNAL_SOURCE=cdp`, порт `9222` (TradingView Desktop с отладкой).
- **Символы:** EURUSD→EURUSD, GBPJPY→GBPJPY, GER40→GER30m, NAS100→USTECH100m (config.yaml).
- **Запуск:** `start-bot.ps1`/`start-bot.bat` (поднимает TradingView :9222, затем `run.py trade`).

Связано: [[project-direction]], [[monitoring-health]].
