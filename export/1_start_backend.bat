@echo off
title CargoResQ Backend Server
cd /d "%~dp0\.."
echo ===================================================
echo Starting CargoResQ FastAPI Backend on port 8000...
echo ===================================================
python main.py
pause
