Write-Host "===================================================" -ForegroundColor Magenta
Write-Host "Launching CargoResQ Desktop Operations Console..." -ForegroundColor Magenta
Write-Host "===================================================" -ForegroundColor Magenta
Set-Location -Path "$PSScriptRoot\.."
python desktop_app\app.py
