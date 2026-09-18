@echo off
title CargoResQ Demo Data Seeder
cd /d "%~dp0\.."
echo ===================================================
echo Seeding CargoResQ Enterprise Demo Dataset...
echo ===================================================
python scripts\seed_demo.py --reset --password Password123!
pause
