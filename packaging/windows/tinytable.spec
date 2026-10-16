# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build for the Windows installer payload. From the repo root:
#   pyinstaller --noconfirm --clean packaging/windows/tinytable.spec
# Produces dist/TinyTable/ with one shared runtime and an exe per program.
# Build frontend/dist first (npm run build); build.ps1 does both.

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

HERE = Path(SPECPATH)
ROOT = HERE.parents[1]
PATHEX = [str(ROOT / "backend"), str(ROOT)]

if not (ROOT / "frontend" / "dist" / "index.html").is_file():
    raise SystemExit("frontend/dist is missing; run npm run build in frontend first")

SERVER_DATAS = [
    (str(ROOT / "frontend" / "dist"), "frontend/dist"),
    (str(ROOT / "backend" / "app" / "standalone.html"), "app"),
    (str(ROOT / "backend" / "app" / "commons_protocol_schema.json"), "app"),
]
APP_MODULES = collect_submodules("app")

# (entry script, exe name, console, datas, hidden imports)
PROGRAMS = [
    # uvicorn picks its loop/protocol implementations by import string.
    ("entry_server.py", "tinytable-server", False, SERVER_DATAS, APP_MODULES + collect_submodules("uvicorn")),
    ("entry_cli.py", "tinytable-cli", True, [], APP_MODULES),
    ("entry_replika.py", "tinytable-replika", True, [], []),
    ("entry_kindroid.py", "tinytable-kindroid", True, [], []),
    ("entry_matrix.py", "tinytable-matrix", True, [], []),
]

collected = []
for script, name, console, datas, hidden in PROGRAMS:
    a = Analysis([str(HERE / script)], pathex=PATHEX, datas=datas, hiddenimports=hidden, excludes=["tkinter"])
    pyz = PYZ(a.pure)
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=name, console=console, upx=False)
    collected += [exe, a.binaries, a.datas]

coll = COLLECT(*collected, upx=False, name="TinyTable")
