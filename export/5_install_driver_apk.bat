@echo off
title CargoResQ APK Sideload
cd /d "%~dp0"
echo ===================================================
echo Installing CargoResQ-Driver.apk via ADB...
echo ===================================================
adb devices
adb install -r CargoResQ-Driver.apk
pause
