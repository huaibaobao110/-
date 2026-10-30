#!/usr/bin/env bash
# Run on a Mac with Python 3.10+
set -euo pipefail
cd "$(dirname "$0")"
echo "[1/3] Install deps..."
python3 -m pip install -r requirements.txt
echo "[2/3] PyInstaller..."
python3 -m PyInstaller --noconfirm chat_reply.spec
echo "[3/3] Done."
echo "App: dist/ChatReplyHelper.app (or dist/ChatReplyHelper/)"
echo "Note: WeChat/QQ window automation uses Windows APIs;"
echo "      on Mac, use screenshot / Ctrl+V image paste + copy/send manually."
