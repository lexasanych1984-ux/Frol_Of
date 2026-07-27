---
name: tradingview-desktop-workflow
description: Как заливать Pine в TradingView Desktop пользователя и что при этом ломается в его лэйауте
metadata: 
  node_type: memory
  type: reference
  originSessionId: c0d7da28-5be5-4d47-a5ea-911bd3b1acaf
  modified: 2026-07-26T17:31:23.172Z
---

Заливка Pine-кода в редактор: `node C:\Users\lexas\.claude\tools\tradingview-mcp\scripts\inject-pine.cjs <targetId> <файл.pine>`
— читает файл с диска, поэтому 20-30 КБ кода не проходят через контекст (в отличие от
`pine_set_source`). `targetId` берётся из `Invoke-RestMethod http://127.0.0.1:9222/json`
(страница с title про графики) и **меняется при каждой перезагрузке страницы** — если скрипт
отвечает `webSocketDebuggerUrl undefined` или `no monaco`, перезапроси id и убедись, что панель
Pine открыта.

**Побочный эффект, за которым надо следить:** `data_get_strategy_results` раскрывает скрытые
стратегии на графике, чтобы посчитать отчёт, и обратно их не прячет. У пользователя в рабочем
лэйауте (OANDA:USDCAD) две стратегии держатся **скрытыми намеренно** — «CRT Day 1H Strategy v1»
и «SMC 2-е колено 4H→M15». После каждого вызова возвращай им `visible: false` через
`indicator_toggle_visibility`. Автосохранение лэйаута у него включено, так что незамеченное
изменение сохранится молча.

`ui_open_panel pine-editor` у него **не работает** (возвращает success, панель не открывается) —
Pine Editor вынесен в правый widget-bar, открывается кликом по кнопке «Pine»; координаты кнопок
внутри панели надёжнее получать через `ui_evaluate` по `[data-name="pine-dialog-button"]`.

Проект, ради которого это делается: [[strategy-lab-project]].
