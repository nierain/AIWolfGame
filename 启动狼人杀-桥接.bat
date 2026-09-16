@echo off
cd /d "%~dp0"
if not exist "codex_bridge\tasks" mkdir "codex_bridge\tasks"
if not exist "codex_bridge\responses" mkdir "codex_bridge\responses"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" panel_game.py --port 18765 --open-browser --provider codex
) else (
  python panel_game.py --port 18765 --open-browser --provider codex
)
if errorlevel 1 pause
