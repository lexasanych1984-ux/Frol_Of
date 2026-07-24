---
name: projects-backup-system
description: "Единый git-бэкап всех проектов юзера в github.com/lexasanych1984-ux/Frol_Of, ветка projects-backup. Скрипт backup.ps1, еженедельно по Планировщику."
metadata: 
  node_type: memory
  type: project
  originSessionId: 992c3fc4-6302-40a0-b9a0-67c0b2819031
  modified: 2026-07-24T20:04:38.494Z
---

Единый бэкап рабочих файлов всех проектов в один приватный репозиторий.

- **Локальный репо:** `C:\Users\lexas\projects-backup` (ветка `projects-backup`).
- **Remote:** `github.com/lexasanych1984-ux/Frol_Of`, ветка **`projects-backup`**
  (в `master` того же репо лежит проект бота — не путать; ветки независимы).
- **Скрипт:** `C:\Users\lexas\projects-backup\backup.ps1` (ASCII-only — PS 5.1 читает
  no-BOM .ps1 как ANSI и ломается на кириллице). Ручной прогон:
  `powershell -ExecutionPolicy Bypass -File C:\Users\lexas\projects-backup\backup.ps1`
  (`-DryRun` = синхронизация+скан+превью без коммита; `-InstallSchedule` = задача).
- **Планировщик:** задача `ProjectsBackup`, еженедельно ночь вс→пн 03:30.
- **Telegram:** итог `[BACKUP]` в тот же канал, что watchdog/бот (через `.env` бота → `TELEGRAM_ENV_FILE`).

**Что бэкапит (подпапка = проект):** tradingview-mcp, bybit-tradfi-bot,
ai-agent (`D:\MY\Crypto\AI agent`), mql5-src (+ `terminal-copy\FrolTradeAssistant.mq5`
из папки данных ручного MT5), sw-macros-futerovka (`C:\SW_Macros\Futerovka`),
claude-memory (memory-папки всех claude-проектов). Первый бэкап: 418 файлов ~71 МБ.

**Секреты НИКОГДА не коммитятся:** robocopy исключает `.env`/`*.key`/`credentials*`/
`token*`/`*.log` (bot.log содержал webhook-токен!); каждый `.env` → `.env.example`
с пустыми значениями; перед коммитом секрет-скан диффа (СТОП при находке); корневой
`.gitignore` — второй слой. Вложенные (исходные) `.gitignore` удаляются, иначе они
прятали бы ценные рабочие файлы (config.yaml, trades.csv, state.db, отчёты).

**Личность git** в репо задаётся скриптом (`lexas` / `lexasanych1984@gmail.com`),
т.к. global git-identity не настроена и свежий репо коммитить не мог.
