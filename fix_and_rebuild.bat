@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [1/4] Copy config into dist...
if exist config.json copy /Y config.json dist\ChatReplyHelper\config.json >nul
copy /Y config.example.json dist\ChatReplyHelper\config.example.json >nul
echo [2/4] PyInstaller rebuild...
"C:\Users\huaib\agent-cad\.venv\Scripts\python.exe" -m PyInstaller --noconfirm chat_reply.spec
if errorlevel 1 goto fail
if exist config.json copy /Y config.json dist\ChatReplyHelper\config.json >nul
copy /Y config.example.json dist\ChatReplyHelper\config.example.json >nul
echo [3/4] Build installer...
call build_installer.bat
echo [4/4] Patch installed copy if present...
set "INST=%LocalAppData%\Programs\ChatReplyHelper"
if exist "%INST%\ChatReplyHelper.exe" (
  xcopy /E /Y /I "dist\ChatReplyHelper\*" "%INST%\" >nul
  if exist config.json copy /Y config.json "%INST%\config.json" >nul
  echo Patched: %INST%
)
echo.
echo Done. Desktop installer: dist\installer\ChatReplyHelper-Setup-1.0.0.exe
pause
exit /b 0
:fail
echo Build failed.
pause
exit /b 1
