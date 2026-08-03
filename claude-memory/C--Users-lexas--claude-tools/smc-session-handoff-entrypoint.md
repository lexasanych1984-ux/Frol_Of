---
name: smc-session-handoff-entrypoint
description: Точка входа в работу по SMC — хэндофф 01.08 плюс отчёт A/B пола стопа; ждёт решения пользователя
metadata: 
  node_type: memory
  type: project
  originSessionId: 110c2a4a-2c15-47c3-93ea-bc641f6222cd
  modified: 2026-08-01T11:55:37.445Z
---

**Начинать работу по SMC с двух файлов, в этом порядке:**

1. `C:\Users\lexas\tv-strategies\fx-indices\docs\session-handoff-2026-08-01.md` — состояние
   деплоя, устройство алертов, почему v25 отдельный скрипт.
2. `C:\Users\lexas\tv-strategies\fx-indices\docs\smc-v25-stopfloor-ab-2026-08-02.md` — прогон
   A/B пола стопа от 02.08.2026.

Не восстанавливать контекст по отдельным старым отчётам, пока не прочитаны эти два.

**Статусы на 02.08.2026:**

1. **Боевая конфигурация EURUSD:**
   `poiMode=snapshot / maxBos=1 / stopMode=skip / keepSetup=off / maxHoldH=42 / minStopPip=30`.
   Алерт **`5274108372`**, скрипт «SMC 2-е колено v25 POI» (pine `USER;bfab6682…`, ver 2.0).
   Вотчдог: `--baseline` и `--dry` 6/6, 0 расхождений, в эталоне `minStopPip=30`.
2. **A/B пола стопа закрыт, A(30) принято и залито 02.08.** Итог и оговорки —
   [[smc-v25-stopfloor-ab-result]]. Отложенная проверка — [[smc-stopfloor-forward-checkpoint]].
3. **Портфельное расширение отклонено 02.08.** AUDUSD и USDJPY провалили критерии —
   [[smc-eurusd-specific]]. Новых потоков не появилось, боевых по-прежнему шесть.
4. **Свап алертов полностью закрыт.** Старый `5272544462` удалён пользователем 02.08,
   вотчдог `--dry` подтвердил 6/6. Незакрытых хвостов по деплою нет.
5. **Оба GBPJPY-потока сидят на v24, не на v25** — тайм-стопа у них нет и быть не может,
   пол стопа их не касается (у них ATR-коридор). Подробности — [[smc-gbpjpy-streams]].
   ⚠️ Бот не различает эти два потока: до закрытия тикета две GBPJPY-позиции одной
   стороны разбирать вручную.
6. **GBPJPY-протокол не стартовать** — очередь после тикета бота и учёта (решение 02.08).

Связано: [[smc-v25-poi-backtest-criteria]], [[smc-mfe-distribution]], [[smc-position-blocking]],
[[tradingview-desktop-workflow]], [[smc-stop-and-r-from-tester]], [[git-push-runs-through-user]].
