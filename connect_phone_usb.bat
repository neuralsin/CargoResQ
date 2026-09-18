@echo off
setlocal
echo ===================================================
echo CargoResQ - Phone USB Tunnel (ADB Reverse)
echo ===================================================
echo.
echo Connecting port 8000 from phone to your PC...

set ADB_PATH="%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"
if exist %ADB_PATH% (
    set ADB_CMD=%ADB_PATH%
) else (
    where adb >nul 2>&1
    if %ERRORLEVEL% equ 0 (
        set ADB_CMD=adb
    ) else (
        echo [ERROR] adb.exe not found in Android SDK or PATH.
        pause
        exit /b 1
    )
)

echo Using ADB: %ADB_CMD%
%ADB_CMD% devices
echo.
%ADB_CMD% reverse tcp:8000 tcp:8000
if %ERRORLEVEL% equ 0 (
    echo.
    echo ===================================================
    echo [SUCCESS] USB tunnel established!
    echo On your Android phone, set Server Address to:
    echo   http://127.0.0.1:8000
    echo ===================================================
) else (
    echo.
    echo [NOTICE] Please ensure your phone is connected via USB
    echo and 'USB Debugging' is enabled in Developer Options.
)
echo.
pause
