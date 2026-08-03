# Регистрирует watchdog конфигов TV в Планировщике заданий Windows: КАЖДЫЙ ЧАС
# в торговое окно сверяет живые боевые алерты с эталоном, при расхождении шлёт
# Telegram. СТРОГО READ-ONLY: ничего не пишет в TradingView.
#
# Почему каждый час, а не 2×/день: TradingView сам глушит strategy-алерт (напр.
# «Остановлено — Ошибка расчёта», 29.07.2026), и до следующей проверки поток
# сигналов мёртв молча — бот этого не видит, для него это просто тишина. При
# двух прогонах (09:15/21:15) слепое окно достигало 12 часов, т.е. целого
# торгового дня. Спама нет: одинаковое расхождение не повторяется чаще
# ANTISPAM_MIN (300 мин), «всё ок» уходит раз в сутки, а при закрытом TV прогон
# просто пишет в лог «TV недоступен».
#
# Установка (обычный PowerShell, без админа):
#   powershell -ExecutionPolicy Bypass -File watchdog\install-watchdog-task.ps1
# Своё окно проверок:
#   ... install-watchdog-task.ps1 -Start 08:30 -IntervalMinutes 30 -DurationHours 15
# Удалить задачу:
#   ... install-watchdog-task.ps1 -Uninstall

param(
    [switch]$Uninstall,
    # Начало окна проверок и шаг. По умолчанию 09:15 каждый час в течение 14 ч
    # (последний прогон 23:15) — окно накрывает аптайм бота: он поднимается
    # ~09:30 и работает до ночи, вне этого окна TradingView закрыт.
    [string]$Start = '09:15',
    [int]$IntervalMinutes = 60,
    [int]$DurationHours = 14
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

# Ежедневный триггер + повтор внутри дня. Repetition нельзя задать прямо на
# -Daily, поэтому берём её у вспомогательного -Once триггера (штатный приём).
$trigger = New-ScheduledTaskTrigger -Daily -At ([datetime]::Parse($Start))
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At ([datetime]::Parse($Start)) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration (New-TimeSpan -Hours $DurationHours)).Repetition
$triggers = @($trigger)

# От текущего пользователя, только когда он в системе (TV Desktop работает
# именно в его сессии; пароль не нужен).
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited

# StartWhenAvailable — если ПК спал в момент триггера, проверка догонит позже.
# AllowStartIfOnBatteries/DontStopIfGoingOnBatteries — иначе Планировщик по
# умолчанию НЕ запускает задачу на батарее и глушит её при отключении от сети:
# на ноутбуке сторож молча простаивал бы (прогон — пара секунд node, батарею
# это не съест).
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$lastRun = ([datetime]::Parse($Start)).AddHours($DurationHours).ToString('HH:mm')
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
    -Principal $principal -Settings $settings `
    -Description "Сторож конфигов TradingView: каждые $IntervalMinutes мин с $Start (окно $DurationHours ч) сверяет 6 боевых strategy-алертов с эталоном, Telegram [WATCHDOG] при расхождении. READ-ONLY." | Out-Null

Write-Host "Задача '$TaskName' зарегистрирована. Запуски: с $Start каждые $IntervalMinutes мин, окно $DurationHours ч (последний ~$lastRun), ежедневно."
Write-Host "  запускает: $runner"
Write-Host "  лог:       $(Join-Path $here 'logs\watchdog.log')"
Write-Host "Проверить сейчас:  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Удалить:           powershell -ExecutionPolicy Bypass -File watchdog\install-watchdog-task.ps1 -Uninstall"
