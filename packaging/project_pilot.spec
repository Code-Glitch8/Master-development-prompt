# PyInstaller spec (one-folder build). Run from the repository root:
#   pyinstaller packaging/project_pilot.spec --noconfirm --distpath build/dist --workpath build/work
import os
from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.getcwd())
frontend_dist = os.path.join(ROOT, "frontend", "dist")
if not os.path.isfile(os.path.join(frontend_dist, "index.html")):
    raise SystemExit("frontend/dist is missing - run `npm run build` in frontend/ first")

hidden = (
    collect_submodules("uvicorn")
    + collect_submodules("keyring.backends")
    + collect_submodules("sqlalchemy.dialects.sqlite")
    + collect_submodules("websockets")
    + collect_submodules("app")
    + ["multipart", "python_multipart", "anyio._backends._asyncio"]
)

a = Analysis(
    [os.path.join(ROOT, "backend", "run_service.py")],
    pathex=[os.path.join(ROOT, "backend")],
    datas=[(frontend_dist, "frontend_dist")],
    hiddenimports=hidden,
    excludes=["tkinter", "pytest", "PyInstaller"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ProjectPilot", console=True)
coll = COLLECT(exe, a.binaries, a.datas, name="ProjectPilot")
