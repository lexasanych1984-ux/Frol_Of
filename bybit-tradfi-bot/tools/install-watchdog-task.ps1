# Регистрирует внешний watchdog в Планировщике заданий Windows: раз в час
# проверяет, что процесс бота жив, иначе шлёт Telegram-тревогу.
# Запуск (обычный PowerShell, без админа):
#   powershell -ExecutionPolicy Bypass -File tools\install-watchdog-task.ps1
# Удалить задачу:
#   powershell -ExecutionPolicy Bypass -File tools\install-watchdog-task.ps1 -Uninstall

param([switch]$Uninstall)

$ErrorActionPreference = 'Stop'
$TaskName = 'bybit-tradfi-bot watchdog'
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
$runner = Join-Path $Root 'tools\run-watchdog.bat'

if (-not (Test-Path $py)) {
    throw "Не найден $py — сначала создай venv (см. README) и поставь зависимости."
}
if (-not (Test-Path (Join-Path $Root 'logs'))) {
    New-Item -ItemType Directory -Path (Join-Path $Root 'logs') | Out-Null
}

# Задача запускает батник-обёртку: он сам перенаправляет вывод в
# logs\watchdog.log (никакого хрупкого экранирования кавычек в аргументах).
$action = New-ScheduledTaskAction -Execute $runner -WorkingDirectory $Root

# Раз в час, бессрочно, первый запуск — через минуту после регистрации.
$trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1)) `
    -RepetitionInterval (New-TimeSpan -Hours 1) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

# От текущего пользователя, только когда он в системе (пароль не нужен).
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -MultipleInstances IgnoreNew

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings `
    -Description 'Внешний чекер живости bybit-tradfi-bot: раз в час, Telegram-тревога если процесс упал.' | Out-Null

Write-Host "Задача '$TaskName' зарегистрирована: раз в час."
Write-Host "  запускает: $runner"
Write-Host "  лог:       $(Join-Path $Root 'logs\watchdog.log')"
Write-Host "Проверить сейчас:  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Удалить:           powershell -ExecutionPolicy Bypass -File tools\install-watchdog-task.ps1 -Uninstall"
