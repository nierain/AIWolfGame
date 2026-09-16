@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" panel_game.py --port 0 --open-browser --provider codex-cli
) else (
  python panel_game.py --port 0 --open-browser --provider codex-cli
)
if errorlevel 1 pause
