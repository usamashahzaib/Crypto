@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  .venv\Scripts\python.exe crypto_analysis_bot.py
) else (
  python crypto_analysis_bot.py
)
pause
