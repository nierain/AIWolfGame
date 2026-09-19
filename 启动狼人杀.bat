@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" panel_game.py --port 0 --open-browser
) else (
  python panel_game.py --port 0 --open-browser
)
if errorlevel 1 pause
