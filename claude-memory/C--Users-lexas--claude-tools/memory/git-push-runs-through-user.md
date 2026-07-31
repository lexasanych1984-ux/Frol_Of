---
name: git-push-runs-through-user
description: git push в репозиториях пользователя блокируется классификатором — просить выполнить через ! с POSIX-путём
metadata: 
  node_type: memory
  type: project
  originSessionId: 6380586c-6229-4b9a-8a80-0083dae57125
  modified: 2026-07-31T14:43:03.494Z
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

Связано: [[tradingview-desktop-workflow]].
