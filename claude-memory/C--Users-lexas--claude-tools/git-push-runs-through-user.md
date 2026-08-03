---
name: git-push-runs-through-user
description: git push в репозиториях пользователя блокируется классификатором — просить выполнить через ! с POSIX-путём
metadata: 
  node_type: memory
  type: project
  originSessionId: 6380586c-6229-4b9a-8a80-0083dae57125
  modified: 2026-08-01T10:07:56.948Z
---

`git push` из моих инструментов **блокируется классификатором разрешений** (проверено и в
форграунде, и в фоне, на `C:\Users\lexas\tv-strategies`). `git remote add`, `add`, `commit`,
`status`, `log` проходят нормально — упирается только push.

**Why:** обходить запрет нельзя, а молча копить локальные коммиты вредно — пользователь
теряет их из виду. `gh` в системе не установлен, создать репозиторий за него тоже нельзя.

**How to apply:** коммичу сам, затем прошу выполнить пуш через префикс `!` и **обязательно
даю POSIX-путь**:

```
! git -C /c/Users/lexas/tv-strategies push -u origin main
```

`!` выполняется в Git Bash, где обратный слэш — экранирование, поэтому
`C:\Users\lexas\tv-strategies` схлопывается в `C:Userslexastv-strategies` и команда падает
с `fatal: cannot change to ...`. Windows-путь работает только в кавычках.

После пуша сверяю `git status --short --branch` (должно быть без `ahead`) и
`git log --oneline -1 origin/main`. Если коммитов накопилось несколько — перечисляю их
в напоминании, чтобы было видно, что именно улетает.

⚠️ **Пользователь иногда присылает команду текстом, без `!`.** Тогда она приходит мне
в сообщение, а не в шелл, и мой запуск снова упрётся в классификатор. Не повторять попытку —
попросить скопировать строку вместе с восклицательным знаком.

## Куда пушится какой репозиторий

| Репозиторий | remote | Куда пушить |
|---|---|---|
| `C:\Users\lexas\tv-strategies` | `lexasanych1984-ux/tv-strategies` | `push` в `main`, работает |
| `C:\Users\lexas\projects-backup` | `lexasanych1984-ux/Frol_Of` | ветка `projects-backup` |
| `C:\Users\lexas\.claude\tools\tradingview-mcp` | ⚠️ `tradesdontlie/tradingview-mcp` — **чужой апстрим** | см. ниже |

**У `tradingview-mcp` `origin` — это апстрим самого MCP-сервера, прав на запись нет,
`push` отдаёт 403.** Это не сбой и не классификатор, ретрай не поможет. `origin` нужен для
`pull` (там выходят обновления вроде `tv_update`). Свои коммиты уезжают отдельной веткой
в личный репозиторий:

```
! git -C /c/Users/lexas/.claude/tools/tradingview-mcp push https://github.com/lexasanych1984-ux/Frol_Of.git main:tradingview-wip
```

Ветка `tradingview-wip` в `Frol_Of` заведена 02.08.2026 на коммите `63c99ad`. `gh` в системе
не установлен, поэтому завести нормальный форк за пользователя нельзя — только он через веб.

Связано: [[tradingview-desktop-workflow]], [[smc-session-handoff-entrypoint]].
