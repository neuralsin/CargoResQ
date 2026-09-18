@echo off
title Open Port 8000 in Windows Firewall
echo ===================================================
echo CargoResQ - Open Port 8000 in Windows Firewall
echo ===================================================
echo.
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [ERROR] Administrator permissions required.
    echo.
    echo Please right-click this file and select:
    echo    "Run as administrator"
    echo.
    pause
    exit /b 1
)

echo Adding inbound rule for port 8000...
netsh advfirewall firewall delete rule name="CargoResQ Backend (8000)" >nul 2>&1
netsh advfirewall firewall add rule name="CargoResQ Backend (8000)" dir=in action=allow protocol=TCP localport=8000 profile=any

echo.
echo ===================================================
echo [SUCCESS] Port 8000 is now OPEN in Windows Firewall!
echo Phones on your Wi-Fi or Hotspot can now connect.
echo ===================================================
echo.
pause
