# Регистрирует месячный отчёт эджа в Планировщике заданий Windows: 1-го числа
# каждого месяца строит отчёт за ПРОШЛЫЙ месяц (факт демо ↔ коридор бэктеста),
# кладёт Markdown в logs\reports\ и шлёт краткую сводку в Telegram.
# Запуск (обычный PowerShell, без админа):
#   powershell -ExecutionPolicy Bypass -File tools\install-report-task.ps1
# Удалить задачу:
#   powershell -ExecutionPolicy Bypass -File tools\install-report-task.ps1 -Uninstall
# Проверить сейчас (сформирует отчёт за прошлый месяц):
#   Start-ScheduledTask -TaskName 'bybit-tradfi-bot monthly report'

param([switch]$Uninstall)

$ErrorActionPreference = 'Stop'
$TaskName = 'bybit-tradfi-bot monthly report'
$Root = Split-Path -Parent $PSScriptRoot   # корень проекта (tools\..)

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Задача '$TaskName' удалена."
    } else {
        Write-Host "Задача '$TaskName' не найдена — удалять нечего."
    }
    return
}

$py = Join-Path $Root '.venv\Scripts\python.exe'
$runner = Join-Path $Root 'tools\run-report.bat'

if (-not (Test-Path $py)) {
    throw "Не найден $py — сначала создай venv (см. README) и поставь зависимости."
}
if (-not (Test-Path (Join-Path $Root 'logs'))) {
    New-Item -ItemType Directory -Path (Join-Path $Root 'logs') | Out-Null
}

# «1-го числа месяца» надёжнее всего задаётся через schtasks (/SC MONTHLY /D 1);
# New-ScheduledTaskTrigger в Windows PowerShell 5.1 месячный триггер не умеет.
$cmd = "`"$runner`""
schtasks /Create /F /TN "$TaskName" /TR "$cmd" /SC MONTHLY /D 1 /ST 09:05 `
    /RL LIMITED | Out-Null

if ($LASTEXITCODE -ne 0) {
    throw "schtasks вернул код $LASTEXITCODE — задача не создана."
}

Write-Host "Задача '$TaskName' зарегистрирована: 1-го числа месяца в 09:05."
Write-Host "  запускает: $runner  (python run.py report last notify)"
Write-Host "  отчёты:    $(Join-Path $Root 'logs\reports')"
Write-Host "Проверить сейчас:  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Удалить:           powershell -ExecutionPolicy Bypass -File tools\install-report-task.ps1 -Uninstall"
