# Memory Index

- [bybit-tradfi-bot](bybit-tradfi-bot.md) — бот для Bybit TradFi (MT5) на TV-сигналах; проект в C:\Users\lexas\bybit-tradfi-bot
- [frol-trade-assistant](frol-trade-assistant.md) — самописная MQL5-панель ручной торговли в ручном MT5 (замена Trade Assistant Demo)
- [tv-config-watchdog](tv-config-watchdog.md) — read-only сторож 6 боевых TV-алертов (watchdog/); эталон = живое при известном нефикснутом расхождении (CRT риск, SMC сессии)
- [bybit-bot-signal-delivery](bybit-bot-signal-delivery.md) — доставка сигналов боту (CDP + Cloudflare webhook-буфер), диагностика, разбор инцидента «2 дня нет сигналов» + нюанс Telegram (нет пуша на сделку)

## Трейдинг: стратегии и сопровождение (перенесено из домашнего проекта 23.07.2026 — канон теперь здесь)

- [tv-strategies-repo](tv-strategies-repo.md) — код стратегий вынесен в отдельный репо C:\Users\lexas\tv-strategies (crypto/ + fx-indices/); в tradingview-mcp остался инструментарий. Читать, если ищешь .pine или выгрузки сделок
- [portfolio-live-operations](portfolio-live-operations.md) — ГЛАВНАЯ памятка по боевому портфелю (6 потоков: 3 SMC FX 2% + 2 Asia Sweep индексы 1% + CRT GER40 1%): состояние, 6 алертов, эталоны, регламент. Читать ПЕРВОЙ перед любым касанием боевой торговли
- [daily-sweep-strategy](daily-sweep-strategy.md) — CRT Day 1H GER40 (в бою с 15.07, алерт 5146158866): боевой конфиг, правила, ожидания, отбраковки, бэклог. Читать при работе с CRT-потоком
- [smc-second-leg-strategy](smc-second-leg-strategy.md) — история FX-ядра SMC «2-е колено» и УРОКИ (эдж 4H→M15, стоп-коридор = главный фильтр, что из надстроек вредит). Читать при работе с FX-потоками или новой SMC-идеей
- [ger40-asia-sweep-strategy](ger40-asia-sweep-strategy.md) — история исследования Asia Sweep (индексы GER40/NAS100): методика 18 окон, 10 отвергнутых фильтров, бэктесты, портфельный эффект. Читать при доработке индексных потоков
- [tradingview-mcp-pine-editor-quirks](tradingview-mcp-pine-editor-quirks.md) — 31 грабля инструментов pine_*/TV MCP и обходы (зомби-Monaco, невидимый инстанс, русская кнопка, кэш таблиц/тестера, REST-алерты). Читать ПЕРЕД любой правкой Pine/графика через MCP
- [user-timezone-eet](user-timezone-eet.md) — часовой пояс юзера EET/EEST (Europe/Kiev) + сессионная разметка (Азия/Франкфурт/Лондон/NY, ETH/RTH/IB), путь к sessions-indicator.pine. Читать при задании времени/сессий в Pine (канон живёт в домашнем проекте — здесь рабочая копия)
