# PyInstaller build spec for the native Windows operations console.
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

root = Path(SPECPATH)
datas = collect_data_files("customtkinter")

a = Analysis(
    [str(root / "app.py")],
    pathex=[str(root.parent), str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=["customtkinter", "api_client"],
    hooksconfig={},
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="CargoResQ",
    debug=False,
    strip=False,
    upx=True,
    console=False,
)
