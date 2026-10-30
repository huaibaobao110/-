@echo off
cd /d "%~dp0"
echo [1/3] Install build deps...
python -m pip install -r requirements.txt -q
echo [2/3] PyInstaller...
python -m PyInstaller --noconfirm chat_reply.spec
echo [3/3] Done.
echo Output: dist\ChatReplyHelper\
echo Copy your config.json into that folder before first run.
pause
