Write-Host "===================================================" -ForegroundColor Green
Write-Host "Seeding CargoResQ Enterprise Demo Dataset..." -ForegroundColor Green
Write-Host "===================================================" -ForegroundColor Green
Set-Location -Path "$PSScriptRoot\.."
python scripts\seed_demo.py --reset --password Password123!
