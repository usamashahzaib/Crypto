@echo off
cd /d "%~dp0"
python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
copy .env.example .env
echo.
echo Setup done.
echo Edit .env with your real keys, then run run_bot.bat.
pause
