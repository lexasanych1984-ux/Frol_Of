@echo off
REM Обёртка для Планировщика: месячный отчёт эджа за ПРОШЛЫЙ месяц + Telegram.
REM %~dp0 = папка tools\, значит %~dp0.. = корень проекта.
REM Фатальный stderr (напр. ошибка импорта) — в отдельный файл, не в общий лог.
"%~dp0..\.venv\Scripts\python.exe" "%~dp0..\run.py" report last notify 2>> "%~dp0..\logs\report-error.log"
