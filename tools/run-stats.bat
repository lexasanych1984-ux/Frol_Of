@echo off
REM Scheduler wrapper: rebuild logs\trades.csv from MT5 history.
REM
REM Why a scheduled job at all: trades.csv is NOT written by the trading loop.
REM It is a snapshot produced only by `run.py stats`, which regenerates the whole
REM file from MT5 deal history. Nothing called it automatically, so the journal
REM sat frozen on 2026-07-22 while three closures (GBPJPY -976.79, EURUSD -1336.35,
REM GER40 -1125.85) piled up unrecorded. Losing them is impossible - MT5 history is
REM the source of truth - but the journal must be current for the 01.11 checkpoint.
REM
REM Runs while the MT5 terminal is still up (bot lives ~09:30-23:20), otherwise
REM stats cannot reach the account. Fatal stderr goes to its own file.
REM %~dp0 = this .bat folder (tools\), so %~dp0.. = project root.
"%~dp0..\.venv\Scripts\python.exe" "%~dp0..\run.py" stats 365 2>> "%~dp0..\logs\stats-error.log"
