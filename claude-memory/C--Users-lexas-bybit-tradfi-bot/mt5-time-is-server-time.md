---
name: mt5-time-is-server-time
description: "Метки времени из MetaTrader5-питона — это время СЕРВЕРА (UTC+3), а не UTC и не локальное; fromtimestamp врёт на +1 ч."
metadata: 
  node_type: memory
  type: reference
  originSessionId: 8127cc3c-7a71-4b67-bc2d-b4deb058a59e
  modified: 2026-07-30T09:41:28.155Z
---

Все epoch-метки из пакета `MetaTrader5` (дилы `deal.time`, бары `rates[0]`, тики `tick.time`)
— это **время торгового сервера Just2Trade, UTC+3**, упакованное так, как будто это UTC.
Проверка на живом тике 30.07.2026: истинный UTC 09:38:58, `datetime.fromtimestamp(tick.time,
UTC)` = 12:38:59 → смещение ровно +3 ч.

Отсюда правила пересчёта (локальное время ПК = UTC+4, [[trading-windows-uptime]]):
* `datetime.fromtimestamp(epoch, UTC)` = **время сервера**;
* истинный UTC = время сервера − 3 ч;
* `datetime.fromtimestamp(epoch)` (как обычно и пишут) = сервер + 1 ч — **врёт**, именно так
  закрытие в 18:52 UTC 29.07 превратилось в «01:52 30.07».

**Why:** на этой путанице я 30.07.2026 построил ложный вывод о выходах стратегии и посчитал
несуществующие −2.9k USD ([[exit-alerts-are-sl-tp-mirrors]]). Логи бота при этом пишутся в
ЛОКАЛЬНОМ времени, а алерты TradingView несут истинный UTC — в одном разборе сходятся сразу
три шкалы.

**How to apply:** сравнивая событие TV с историей MT5, приводить всё к истинному UTC и
подписывать шкалу в выводе («сервер» / «UTC» / «локально»). Для проверки шкалы — тик против
`datetime.now(UTC)`, это две строки.
