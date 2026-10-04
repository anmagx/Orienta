@echo off
REM Orienta Launcher
cd /d "%~dp0"
"%~dp0\.venv\Scripts\python.exe" "%~dp0\..\..\orienta.py"
if errorlevel 1 pause
