# Возобновление бота после паузы: включает обратно задачи Планировщика,
# отключённые при постановке на паузу, и запускает бота (start-bot.ps1).
# Ничего не переустанавливает и не трогает .env/config.yaml — только
# включает то, что было выключено.

$tasks = @(
    "bybit-tradfi-bot daily journal",
    "bybit-tradfi-bot monthly report",
    "bybit-tradfi-bot watchdog"
)

Write-Host "Включаю задачи Планировщика..."
foreach ($t in $tasks) {
    try {
        Enable-ScheduledTask -TaskName $t -ErrorAction Stop | Out-Null
        Write-Host "  [OK] $t"
    } catch {
        Write-Host "  [!] Не удалось включить '$t': $($_.Exception.Message)"
    }
}

Write-Host ""
Write-Host "Задачи включены. Запускаю бота..."
Write-Host ""
& (Join-Path $PSScriptRoot 'start-bot.ps1')
