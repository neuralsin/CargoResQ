Write-Host "===================================================" -ForegroundColor Yellow
Write-Host "Running CargoResQ Simulation Scenario..." -ForegroundColor Yellow
Write-Host "===================================================" -ForegroundColor Yellow
Set-Location -Path "$PSScriptRoot\.."
python export\run_simulation.py
