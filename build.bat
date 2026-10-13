@echo off
rem Builds dist\LavatiCompare\LavatiCompare.exe (and the installer if Inno Setup is installed).
setlocal
cd /d "%~dp0"
python -m pip install --quiet pyinstaller PySide6 openpyxl pillow paramiko pypdfium2 || goto :err
python -c "from PySide6.QtWidgets import QApplication; a=QApplication([]); from lavati import theme; theme.save_ico('assets/lavati.ico')" || goto :err
python -m PyInstaller --noconfirm --clean --windowed --name LavatiCompare ^
  --icon assets\lavati.ico ^
  --add-data "fonts;fonts" --add-data "assets;assets" ^
  --hidden-import openpyxl --hidden-import PIL --hidden-import paramiko --collect-all pypdfium2 ^
  --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtQml --exclude-module PySide6.Qt3DCore ^
  main.py || goto :err
echo.
echo Built: dist\LavatiCompare\LavatiCompare.exe

set ISCC=
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe
if defined ISCC (
  "%ISCC%" installer.iss || goto :err
  echo Installer: dist\LavatiCompare-Setup.exe
) else (
  echo Inno Setup not found - skipping installer. Install it from https://jrsoftware.org/isdl.php and re-run.
)
exit /b 0
:err
echo Build failed.
exit /b 1
