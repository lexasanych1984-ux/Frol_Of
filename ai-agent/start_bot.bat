@echo off
rem Запуск Telegram-бота (новостной модуль: /week, /news + автопуш).
rem Окно должно оставаться открытым — закрыл окно = остановил бота.
cd /d "%~dp0"
.venv\Scripts\python.exe -m src.telegram_bot.bot
pause
