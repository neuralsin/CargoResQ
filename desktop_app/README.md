# CargoResQ native operations console

This is the production desktop client. It uses CustomTkinter and connects to the deployed CargoResQ API; it does not serve the operations UI from a browser or bind the UI to a localhost page.

Set `CARGOESQ_API_URL` to the API origin, or enter it on the sign-in screen. Install and run it with:

```powershell
python -m pip install -r desktop_app/requirements.txt
python -m desktop_app.app
```

For a Windows release build:

```powershell
./desktop_app/build_windows.ps1
```

The resulting executable is placed in `dist/CargoResQ/CargoResQ.exe`.
