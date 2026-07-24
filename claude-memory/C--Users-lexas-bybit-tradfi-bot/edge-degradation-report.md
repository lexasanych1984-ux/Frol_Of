---
name: edge-degradation-report
description: "Система раннего предупреждения о деградации эджа: expectations.yaml + лог проскальзывания + run.py report + 2 мгновенные тревоги."
metadata: 
  node_type: memory
  type: project
  originSessionId: 3a1b3e31-a80a-48b4-8f49-d1294c293c51
  modified: 2026-07-21T14:40:45.882Z
---

Подсистема наблюдения за КАЧЕСТВОМ эджа (добавлена 2026-07-21), отдельно от
мониторинга живости ([[monitoring-health]]). Цель — заранее увидеть деградацию
демо-форвард-теста ([[project-direction]]) и дать объективную базу для решения о
реале: факт демо ↔ коридоры бэктеста, а не «на глаз».

**Коридоры** — `expectations.yaml` (в корне репо, коммитится). По стратегии
smc/crt/asweep: диапазоны WR, PF, сделок/мес, доли БУ, макс. серии стопов,
худший месяц %. Источники — SMC-портфель-финал.md + memory tradingview-mcp
([[portfolio-live-operations]], [[daily-sweep-strategy]]). Числа с пометкой
«⚠ ОЦЕНКА» (max_stop_streak у crt=7/asweep=10, worst_month у smc=−8%) — прикидка,
не из доков; юзер сверял/утверждал.

**Лог проскальзывания** — `logs/executions.csv` (append-only, пишет движок на
каждый вход): ПЛАН (цена алерта, риск %, RR) ↔ ФАКТ (цена фила, риск %, RR) +
дельта. Рыночный ордер — факт сразу из res.fill_price; limit/stop — факт None,
досчитывается отчётом из истории MT5 по position_id. Сводный `logs/trades.csv`
(его строит `run.py stats/report`) обогащается теми же колонками план↔факт.

**Отчёт** — `python run.py report [месяц]` (месяц: YYYY-MM | MM | last | пусто;
+`notify` — Telegram). Накопительный факт демо vs коридор, пометки
OK/ВНИМАНИЕ/ПРОБЛЕМА, сводка слиппеджа (индексы отдельно от FX). **Честность
выборки: при n<20 по стратегии — «мало данных», без тревог** (FX даёт ~1.5–2.5
сделки/мес, смысл только на накопленной истории). Markdown → `logs/reports/`.
Классификация исхода по 1R: win ≥+0.5R, stop ≤−0.5R, между — be (у CRT ~2/3 be —
норма). Автозапуск 1-го числа — `tools/install-report-task.ps1` (schtasks
MONTHLY /D 1, зовёт `report last notify`).

**Две мгновенные тревоги** (не месячные, Telegram):
1. Риск сделки выше заданного на >RISK_OVERSHOOT_PP (0.3 п.п., дефолт из
   expectations meta) — движок по цене фила рыночного ордера (ловит инцидент
   2026-07-20: 1%→1.39%).
2. Серия стопов подряд по стратегии > max_stop_streak — `bot/edgewatch.py`,
   опрос истории MT5 раз в EDGE_CHECK_INTERVAL_SEC (15 мин), в главном цикле
   рядом с health-монитором. Анти-спам событийный (по длине серии).

Код: `bot/{expectations,execlog,slippage,report,edgewatch}.py`. Тесты:
`tests/test_{report,slippage,execlog,expectations}.py` (76 всего зелёных).
Доки/приёмка: `docs/REPORTING.md`. `dryrun-samples` пишет в отдельный
`logs/dryrun-executions.csv`, боевой лог не засоряет.
