#!/bin/bash
# KaraokeMode — Stems Server Launcher
# Double-click this file on macOS to start the stems server.

cd "$(dirname "$0")"

echo ""
echo "  KaraokeMode Stems Server"
echo "  ========================"
echo ""

# Check Python
if ! command -v python3 &>/dev/null; then
  echo "  ERROR: python3 not found. Install it from https://www.python.org/"
  read -p "  Press Enter to close…"
  exit 1
fi

# Check / install dependencies
MISSING=""
python3 -c "import demucs" 2>/dev/null || MISSING="$MISSING demucs"
python3 -c "import yt_dlp"  2>/dev/null || MISSING="$MISSING yt-dlp"

if [ -n "$MISSING" ]; then
  echo "  Installing missing packages:$MISSING"
  echo ""
  pip3 install $MISSING
  echo ""
fi

# Start server
python3 stems_server.py
