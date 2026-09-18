@echo off
title CargoResQ Simulation Mode
cd /d "%~dp0\.."
echo ===================================================
echo Running CargoResQ Simulation Scenario...
echo ===================================================
python export\run_simulation.py
pause
