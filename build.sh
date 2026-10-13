#!/bin/sh
# Builds Lavati Compare for the current OS (macOS app bundle / Linux folder) with PyInstaller.
#   ./build.sh                 native build (on an Apple Silicon Mac this produces an arm64 app)
#   LAVATI_ARCH=universal2 ./build.sh   macOS universal binary (needs universal2 Python and wheels)
set -e
cd "$(dirname "$0")"
python3 -m pip install --quiet pyinstaller PySide6 openpyxl pillow paramiko pypdfium2
python3 -m PyInstaller --noconfirm --clean lavati.spec
case "$(uname -s)" in
  Darwin) echo "Built: dist/Lavati Compare.app  ($(lipo -archs "dist/Lavati Compare.app/Contents/MacOS/LavatiCompare" 2>/dev/null))" ;;
  *)      echo "Built: dist/LavatiCompare/LavatiCompare" ;;
esac
