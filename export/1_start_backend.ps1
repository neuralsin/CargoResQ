Write-Host "===================================================" -ForegroundColor Cyan
Write-Host "Starting CargoResQ FastAPI Backend on port 8000..." -ForegroundColor Cyan
Write-Host "===================================================" -ForegroundColor Cyan
Set-Location -Path "$PSScriptRoot\.."
python main.py
