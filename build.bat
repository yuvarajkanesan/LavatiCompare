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
  --exclude-module PySide6.QtQuick --exclude-module PySide6.QtPdf --exclude-module PySide6.QtOpenGL --exclude-module PySide6.QtNetwork ^
  --exclude-module PySide6.QtDBus --exclude-module PySide6.QtSql --exclude-module PySide6.QtTest --exclude-module lxml --exclude-module tkinter ^
  main.py || goto :err
rem --- drop components the app never loads (saves ~45 MB) ---
set I=dist\LavatiCompare\_internal
del /q "%I%\PySide6\opengl32sw.dll" "%I%\PySide6\Qt6Quick*.dll" "%I%\PySide6\Qt6Qml*.dll" "%I%\PySide6\Qt6Pdf*.dll" "%I%\PySide6\Qt6OpenGL*.dll" "%I%\PySide6\Qt6VirtualKeyboard*.dll" 2>nul
del /q "%I%\PySide6\plugins\imageformats\qpdf.dll" "%I%\PySide6\plugins\platforminputcontexts\qtvirtualkeyboardplugin.dll" "%I%\PySide6\plugins\generic\qtuiotouchplugin.dll" 2>nul
rmdir /s /q "%I%\PySide6\translations" 2>nul
del /q "%I%\PIL\_avif*.pyd" "%I%\PIL\_imagingtk*.pyd" 2>nul
dist\LavatiCompare\LavatiCompare.exe --selftest || goto :err
echo.
echo Built: dist\LavatiCompare\LavatiCompare.exe

set ISCC=
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe
python installer\make_version.py || goto :err
rem --- code signing: removes the "Unknown publisher" warning ---
rem Set SIGN_CERT_SHA1 to the thumbprint of a code-signing certificate that is in your Windows certificate store
rem (optional SIGN_TIMESTAMP_URL), or set LAVATI_SIGNTOOL to a full command using $f for the file.
if defined SIGN_CERT_SHA1 call :findsigntool
if defined SIGN_CERT_SHA1 if defined SIGNTOOL_EXE call :makesigncmd
if defined LAVATI_SIGNTOOL call :signapp || goto :err
if defined LAVATI_SIGNTOOL call set ISIGN=%%LAVATI_SIGNTOOL:"=$q%%
if defined LAVATI_SIGNTOOL (echo Signing enabled) else (echo Signing skipped: SIGN_CERT_SHA1 is not set - the installer will show Unknown publisher)
if defined ISCC (
  if defined LAVATI_SIGNTOOL (
    "%ISCC%" /DSign=1 "/Slavatisign=%ISIGN%" installer.iss || goto :err
  ) else (
    "%ISCC%" installer.iss || goto :err
  )
  echo Installer: dist\LavatiCompare-Setup.exe
) else (
  echo Inno Setup not found - skipping installer. Install it from https://jrsoftware.org/isdl.php and re-run.
)
exit /b 0
:err
echo Build failed.
exit /b 1

:findsigntool
set SIGNTOOL_EXE=
for /f "delims=" %%S in ('dir /b /s "%ProgramFiles(x86)%\Windows Kits\10\bin\signtool.exe" 2^>nul ^| findstr /i "\\x64\\"') do set SIGNTOOL_EXE=%%S
exit /b 0

:makesigncmd
if not defined SIGN_TIMESTAMP_URL set SIGN_TIMESTAMP_URL=http://timestamp.digicert.com
set LAVATI_SIGNTOOL="%SIGNTOOL_EXE%" sign /sha1 %SIGN_CERT_SHA1% /fd sha256 /tr %SIGN_TIMESTAMP_URL% /td sha256 /d "Lavati Compare" $f
exit /b 0

:signapp
call set SIGNCMD=%%LAVATI_SIGNTOOL:$f=dist\LavatiCompare\LavatiCompare.exe%%
%SIGNCMD%
exit /b %errorlevel%
exit /b 0
