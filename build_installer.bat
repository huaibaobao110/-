@echo off
cd /d "%~dp0"
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
  echo Inno Setup not found. Install with:
  echo   winget install JRSoftware.InnoSetup
  exit /b 1
)
if not exist "dist\ChatReplyHelper\ChatReplyHelper.exe" (
  echo Missing dist\ChatReplyHelper — run build_windows.bat first.
  exit /b 1
)
if not exist "dist\ChatReplyHelper\config.example.json" (
  copy /Y config.example.json dist\ChatReplyHelper\config.example.json >nul
)
"%ISCC%" "installer\ChatReplyHelper.iss"
echo.
echo Installer: dist\installer\ChatReplyHelper-Setup-1.0.0.exe
pause
