Write-Host "===================================================" -ForegroundColor Cyan
Write-Host "Installing CargoResQ-Driver.apk via ADB..." -ForegroundColor Cyan
Write-Host "===================================================" -ForegroundColor Cyan
Set-Location -Path $PSScriptRoot
adb devices
adb install -r CargoResQ-Driver.apk
