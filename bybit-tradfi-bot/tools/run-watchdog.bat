@echo off
REM Scheduler wrapper for the external liveness checker.
REM watchdog.py writes logs\watchdog.log itself (UTF-8 + BOM, readable by
REM Get-Content without -Encoding). Here we only capture fatal stderr
REM (e.g. an import error) into a separate file, so nothing is double-logged.
REM %~dp0 = this .bat folder (tools\), so %~dp0.. = project root.
"%~dp0..\.venv\Scripts\python.exe" "%~dp0watchdog.py" 2>> "%~dp0..\logs\watchdog-error.log"
