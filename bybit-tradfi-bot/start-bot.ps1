$Host.UI.RawUI.WindowTitle = 'Торговый бот — демо (Just2Trade-MT5 245169)'
Set-Location 'C:\Users\lexas\bybit-tradfi-bot'

Write-Host "[1/2] Проверяю TradingView (порт 9222)..."
$tvRunning = $true
try {
    Invoke-WebRequest -Uri 'http://127.0.0.1:9222/json/version' -TimeoutSec 2 -UseBasicParsing | Out-Null
} catch {
    $tvRunning = $false
}

if ($tvRunning) {
    Write-Host "[1/2] TradingView уже запущен с портом 9222 — ок."
} else {
    Write-Host "[1/2] Запускаю TradingView, подожди несколько секунд..."
    & 'C:\Users\lexas\.claude\tools\tradingview-mcp\scripts\launch_tv_debug.bat'
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "ОШИБКА: TradingView не запустился с портом отладки 9222."
        Write-Host "Попробуй ещё раз или запусти TradingView вручную и открой этот файл снова."
        Read-Host "Нажми Enter для выхода"
        exit 1
    }
}

Write-Host ""
Write-Host "[2/2] Запускаю бота. Терминал MT5 откроется сам, если ещё не открыт."
Write-Host "     Остановить бота: Ctrl+C или закрыть это окно."
Write-Host "----------------------------------------------------------------------"
& '.\.venv\Scripts\python.exe' run.py trade

Write-Host ""
Write-Host "Бот остановлен."
Read-Host "Нажми Enter для выхода"
