@echo off
title CargoResQ Master Launcher
cd /d "%~dp0\.."
echo ===================================================
echo [1/3] Resetting and seeding enterprise demo data...
echo ===================================================
python scripts\seed_demo.py --reset --password Password123!

echo.
echo ===================================================
echo [2/3] Starting backend server in separate window...
echo ===================================================
start "CargoResQ Backend Server" cmd /k python main.py
timeout /t 3 /nobreak >nul

echo.
echo ===================================================
echo [3/3] Launching Desktop Operations Console...
echo ===================================================
python desktop_app\app.py
