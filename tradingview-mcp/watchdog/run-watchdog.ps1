# Обёртка для Планировщика заданий (и ручного запуска).
# Находит node.exe и запускает watchdog-проверку. Лог пишет сам node
# (watchdog\logs\watchdog.log, UTF-8 c BOM). Здесь ловим только «node не найден».
# Пробрасывает аргументы: .\run-watchdog.ps1 --dry  /  --baseline  и т.п.
$ErrorActionPreference = 'Continue'
$here = $PSScriptRoot

$node = (Get-Command node -ErrorAction SilentlyContinue).Source
if (-not $node) {
    foreach ($p in @("$env:ProgramFiles\nodejs\node.exe",
                     "$env:ProgramFiles(x86)\nodejs\node.exe",
                     "$env:LOCALAPPDATA\Programs\nodejs\node.exe")) {
        if ($p -and (Test-Path $p)) { $node = $p; break }
    }
}

$logDir = Join-Path $here 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }

if (-not $node) {
    $line = ('{0} run-watchdog: node.exe не найден (PATH и стандартные пути) — проверка не запущена.' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
    Add-Content -Path (Join-Path $logDir 'watchdog.log') -Value $line -Encoding UTF8
    exit 2
}

& $node (Join-Path $here 'watchdog.mjs') @args
exit $LASTEXITCODE
