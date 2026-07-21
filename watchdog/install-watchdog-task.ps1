# Регистрирует watchdog конфигов TV в Планировщике заданий Windows: 2 раза в
# день сверяет живые боевые алерты с эталоном, при расхождении шлёт Telegram.
# СТРОГО READ-ONLY: ничего не пишет в TradingView.
#
# Установка (обычный PowerShell, без админа):
#   powershell -ExecutionPolicy Bypass -File watchdog\install-watchdog-task.ps1
# Своё время запусков:
#   ... install-watchdog-task.ps1 -Times 08:30,20:30
# Удалить задачу:
#   ... install-watchdog-task.ps1 -Uninstall

param(
    [switch]$Uninstall,
    [string[]]$Times = @('09:15', '21:15')
)

$ErrorActionPreference = 'Stop'
$TaskName = 'TV-Watchdog-Configs'
$here = $PSScriptRoot
$runner = Join-Path $here 'run-watchdog.ps1'

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Задача '$TaskName' удалена."
    } else {
        Write-Host "Задача '$TaskName' не найдена — удалять нечего."
    }
    return
}

if (-not (Test-Path $runner)) { throw "Не найден $runner" }

# Задача запускает PowerShell-обёртку (она находит node и пишет лог сама).
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$runner`"" `
    -WorkingDirectory $here

# Несколько ежедневных триггеров = «N раз в день».
$triggers = @()
foreach ($t in $Times) {
    $triggers += New-ScheduledTaskTrigger -Daily -At ([datetime]::Parse($t))
}

# От текущего пользователя, только когда он в системе (TV Desktop работает
# именно в его сессии; пароль не нужен).
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited

# StartWhenAvailable — если ПК спал в момент триггера, проверка догонит позже.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -MultipleInstances IgnoreNew

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
    -Principal $principal -Settings $settings `
    -Description 'Сторож конфигов TradingView: 2×/день сверяет 6 боевых strategy-алертов с эталоном, Telegram [WATCHDOG] при расхождении. READ-ONLY.' | Out-Null

Write-Host "Задача '$TaskName' зарегистрирована. Запуски: $($Times -join ', ') (ежедневно)."
Write-Host "  запускает: $runner"
Write-Host "  лог:       $(Join-Path $here 'logs\watchdog.log')"
Write-Host "Проверить сейчас:  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Удалить:           powershell -ExecutionPolicy Bypass -File watchdog\install-watchdog-task.ps1 -Uninstall"
