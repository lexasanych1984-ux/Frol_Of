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
# Время 10:00, а не 09:05: ПК поднимают ~09:30, и в 09:05 задача просто не запустилась бы.
$cmd = "`"$runner`""
schtasks /Create /F /TN "$TaskName" /TR "$cmd" /SC MONTHLY /D 1 /ST 10:00 `
    /RL LIMITED | Out-Null

if ($LASTEXITCODE -ne 0) {
    throw "schtasks вернул код $LASTEXITCODE — задача не создана."
}

# schtasks не умеет задать «догнать пропущенный запуск» и не снимает запрет на
# батарее — правим уже созданную задачу. Без этого отчёт молча пропадает, если
# 1-го числа ПК спал или ноут не был в сети (ровно так же терялся сторож конфигов).
# Правим через XML: Set-ScheduledTask на месячном триггере от schtasks падает
# с «Параметр задан неверно» (cmdlet не умеет round-trip такого триггера).
$tmp = Join-Path $env:TEMP 'report-task.xml'
schtasks /query /tn "$TaskName" /xml ONE | Out-File -FilePath $tmp -Encoding unicode
[xml]$x = Get-Content -Raw -Path $tmp
$ns = $x.Task.xmlns
$s = $x.Task.Settings
$s.DisallowStartIfOnBatteries = 'false'
$s.StopIfGoingOnBatteries = 'false'
if ($s.StartWhenAvailable) { $s.StartWhenAvailable = 'true' } else {
    $n = $x.CreateElement('StartWhenAvailable', $ns)
    $n.InnerText = 'true'
    $s.InsertBefore($n, $s.SelectSingleNode('*[local-name()="AllowStartOnDemand"]')) | Out-Null
}
$x.Save($tmp)
schtasks /Create /F /TN "$TaskName" /XML $tmp | Out-Null
if ($LASTEXITCODE -ne 0) { throw "schtasks /XML вернул код $LASTEXITCODE — настройки не применены." }
Remove-Item $tmp -Force

Write-Host "Задача '$TaskName' зарегистрирована: 1-го числа месяца в 10:00 (догоняет пропуск)."
Write-Host "  запускает: $runner  (python run.py report last notify)"
Write-Host "  отчёты:    $(Join-Path $Root 'logs\reports')"
Write-Host "Проверить сейчас:  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Удалить:           powershell -ExecutionPolicy Bypass -File tools\install-report-task.ps1 -Uninstall"
