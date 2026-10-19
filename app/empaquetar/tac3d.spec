# PyInstaller: genera la carpeta TAC3D\ con TAC3D.exe. Se usa desde compilar.ps1.
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
APP = os.path.join(ROOT, "app")

a = Analysis(
    [os.path.join(APP, "main.py")],
    pathex=[APP],
    datas=[
        (os.path.join(APP, "motor_requisitos.txt"), "."),
        (os.path.join(APP, "tac3d.ico"), "."),
        (os.path.join(APP, "herramientas", "uv.exe"), "."),
    ],
    hiddenimports=["pydicom.encoders", "pydicom.pixels"],
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "PIL"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="TAC3D", icon=os.path.join(APP, "tac3d.ico"),
          console=False, upx=False)
coll = COLLECT(
    exe, a.binaries, a.datas,
    Tree(os.path.join(ROOT, "pipeline"), prefix="pipeline", excludes=["__pycache__", "*.pyc"]),
    Tree(os.path.join(ROOT, "visor"), prefix="visor"),
    name="TAC3D", upx=False,
)
