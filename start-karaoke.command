#!/bin/bash
# KaraokeMode launcher — double-click this file to start the app

cd "$(dirname "$0")"

# Check if port 8081 is already in use
if lsof -i:8081 -sTCP:LISTEN -t &>/dev/null; then
  echo "Port 8081 already in use — opening browser anyway..."
  open -a "Google Chrome" "http://localhost:8081/karaoke-app-revise.html" 2>/dev/null \
    || open "http://localhost:8081/karaoke-app-revise.html"
  exit 0
fi

echo "Starting KaraokeMode server on http://localhost:8081 ..."
python3 server.py 8081 &
SERVER_PID=$!

# Give the server a moment to start
sleep 0.8

# Open in Chrome (falls back to default browser)
open -a "Google Chrome" "http://localhost:8081/karaoke-app-revise.html" 2>/dev/null \
  || open "http://localhost:8081/karaoke-app-revise.html"

echo ""
echo "  KaraokeMode is running at http://localhost:8081/karaoke-app-revise.html"
echo "  Keep this window open while using the app."
echo "  Press Ctrl+C or close this window to stop."
echo ""

# Keep running until user kills it
wait $SERVER_PID
