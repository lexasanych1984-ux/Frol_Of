---
name: smc-stop-and-r-from-tester
description: Как достать пер-сделочный стоп в пипсах и R из отчёта TradingView без правки Pine
metadata: 
  node_type: memory
  type: reference
  originSessionId: 110c2a4a-2c15-47c3-93ea-bc641f6222cd
  modified: 2026-08-01T07:56:10.929Z
---

Стратегии, где `qty = strategy.equity * riskPct/100 / riskD` и вход разрешён только при
`position_size == 0` (вся линейка SMC), позволяют восстановить стоп и R **из отчёта тестера**,
не трогая Pine и не перезаписывая метки:

```
equity_до_сделки = initial_capital + Σ pnl предыдущих закрытых сделок
risk$            = equity_до_сделки × riskPct/100
стоп_в_пипсах    = (risk$ / qty) / pipSize        // pipSize = mintick × 10
R                = pnl / risk$
```

Данные берутся из `ds.reportData().trades[]`: `e.p/e.tm` — вход, `x.p/x.tm/x.c` — выход,
`q` — qty, `tp.v` — pnl, `cp.v` — накопленный. Проверено 02.08.2026: восстановленное
распределение стопа воспроизвело таблицу вскрытия по всем трём корзинам до третьего знака.

⚠️ **Две ловушки.**

1. `reportData().trades` содержит **открытую на конец окна позицию**, переоценённую по
   последнему бару, с пустым комментарием выхода (`x.c === ''`). `performance` её не считает.
   Фильтровать по `x.c`, иначе получишь на сделку больше, чем в отчёте.
2. Округлённые столбцы в ранее выгруженных CSV не годятся для проверки границ. В
   `data/smc-v25-autopsy-64-trades.csv` стоп округлён до целых пипсов, и две сделки,
   записанные как «30», фактически 29.5 и 29.7 — фильтр `<30` по CSV давал 22 сделки вместо 24.

Экспектанси окна считать **только** от gross-цифр тестера:
`expR = (Net / сделок) / (grossLoss / убыточных)`. Формула «winrate × 3R» неприменима, если
есть выходы по тайм-стопу — см. [[smc-v25-poi-backtest-criteria]].

Раннер, снимающий всё это по набору вариантов инпутов:
`C:\Users\lexas\.claude\tools\tradingview-mcp\scripts\smc-ab-runner.cjs`.
Исторические окна набираются реплеем: `replay_start(D)` встаёт на последний бар **D−1**,
и повторный `replay_start` без `replay_stop` позицию не меняет.

Связано: [[tradingview-desktop-workflow]], [[smc-v25-stopfloor-ab-result]].
