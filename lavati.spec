# PyInstaller spec for macOS / Linux (Windows uses build.bat). Run through build.sh.
import os
import sys

block_cipher = None
arch = os.environ.get("LAVATI_ARCH") or None          # None = the host architecture (arm64 on Apple Silicon)

a = Analysis(
    ["main.py"],
    pathex=["."],
    datas=[("fonts", "fonts"), ("assets", "assets")],
    hiddenimports=["openpyxl", "PIL", "paramiko", "pypdfium2"],
    excludes=["PySide6.QtWebEngineCore", "PySide6.QtQml", "PySide6.Qt3DCore"],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="LavatiCompare", console=False, target_arch=arch)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, name="LavatiCompare")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Lavati Compare.app",
        icon=None,
        bundle_identifier="com.lavati.compare",
        info_plist={
            "CFBundleName": "Lavati Compare",
            "CFBundleShortVersionString": "2.0",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            # lets Finder offer "Open With > Lavati Compare" for any file or folder, and accept drops on the Dock icon
            "CFBundleDocumentTypes": [
                {"CFBundleTypeName": "Any file", "CFBundleTypeRole": "Viewer", "LSItemContentTypes": ["public.item"]},
                {"CFBundleTypeName": "Folder", "CFBundleTypeRole": "Viewer", "LSItemContentTypes": ["public.folder"]},
            ],
        },
    )
