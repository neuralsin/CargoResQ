$ErrorActionPreference = "Stop"
python -m pip install -r desktop_app/requirements.txt
python -m PyInstaller --clean --noconfirm desktop_app/CargoResQ.spec
Write-Host "Built dist/CargoResQ/CargoResQ.exe"
