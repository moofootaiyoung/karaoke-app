#!/usr/bin/env python3
"""
Karaoke server — serves the static app AND proxies Kugou API requests
so the browser can fetch word-level (KRC) lyrics without CORS issues.

Endpoints
─────────
GET /api/kugou/search?q=TRACK+ARTIST   → {id, accesskey, song, singer} or {}
GET /api/kugou/lyrics?id=ID&ak=KEY     → {krc: "<decoded KRC text>"} or {}

KRC format (word-level):
  [line_start_ms,line_dur_ms]<word_off_ms,word_dur_ms,0>word <…>word …

Usage:
    python3 server.py [PORT]          # default 8081
"""

import base64
import http.server
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
import zlib

PORT  = int(sys.argv[1]) if len(sys.argv) > 1 else 8081
CTX   = ssl._create_unverified_context()   # Kugou CDN has cert mismatches on macOS

# XOR key used by Kugou to obfuscate KRC data (after the 4-byte "krc1" magic)
KRC_KEY = bytes([64, 71, 97, 119, 94, 50, 116, 71, 81, 54, 49, 45, 206, 210, 110, 105])


def _fetch(url, headers=None, timeout=10):
    """GET a URL and return the response bytes (SSL errors ignored)."""
    h = {"User-Agent": "Mozilla/5.0 Chrome/124.0", "Accept": "*/*"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return r.read()


def _similarity(a, b):
    """Rough token-overlap score between two lowercase strings (0.0–1.0)."""
    ta = set(re.split(r'\W+', a.lower()))
    tb = set(re.split(r'\W+', b.lower()))
    ta.discard(""); tb.discard("")
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(len(ta), len(tb))


def kugou_search(query):
    """Search Kugou; prefer the official version that best matches the query.

    Strategy
    --------
    1. Search Kugou by *track name only* (adding artist name pollutes results
       with cover/tribute songs).
    2. Score each result by artist-name similarity to the query.
    3. Prefer songs whose title doesn't contain remix/ringtone/cover keywords
       unless the query itself mentions them.
    4. Pick the highest-scoring song that has KRC lyrics available.
    """
    # Split "artist - title" if present (we get this from YouTube titles)
    parts   = re.split(r'\s[-–]\s', query, maxsplit=1)
    artist  = parts[0].strip() if len(parts) == 2 else ""
    track   = parts[-1].strip()

    # Search Kugou by track name only
    enc  = urllib.parse.quote_plus(track)
    url  = (f"https://mobilecdn.kugou.com/api/v3/search/song"
            f"?keyword={enc}&pagesize=10&pagenum=1&bitrate=128&isfuzzy=0&format=json")
    data = json.loads(_fetch(url))
    songs = data.get("data", {}).get("info", [])
    if not songs:
        return None

    # Score each song; sort best first
    noise = re.compile(r'(remix|ringtone|cover|tribute|karaoke|instrumental|piano|acoustic|version)',
                       re.I)
    def score(s):
        name   = s.get("songname", "")
        singer = s.get("singername", "")
        art_sc = _similarity(artist, singer) if artist else 0.5
        # Penalise off-topic variants unless query mentions them
        noisy  = bool(noise.search(name)) and not noise.search(query)
        return art_sc - (0.4 if noisy else 0.0)

    ranked = sorted(songs, key=score, reverse=True)

    # Try ranked songs until we find one with KRC lyrics
    for song in ranked[:5]:
        hash_val = song["hash"]
        lurl  = (f"https://krcs.kugou.com/search"
                 f"?ver=1&man=yes&client=pc&hash={hash_val}&timelength=0")
        try:
            ldata = json.loads(_fetch(lurl, headers={"User-Agent": "KuGou2012"}))
        except Exception:
            continue
        cands = ldata.get("candidates", [])
        if not cands:
            continue
        best = cands[0]
        return {
            "id":        best["id"],
            "accesskey": best["accesskey"],
            "song":      best.get("song",   song.get("songname",  "")),
            "singer":    best.get("singer", song.get("singername", "")),
        }
    return None


def kugou_lyrics(lyric_id, accesskey):
    """Download and decrypt a KRC file; return the decoded UTF-8 text."""
    url  = (f"https://lyrics.kugou.com/download"
            f"?ver=1&client=pc&id={lyric_id}&accesskey={accesskey}&fmt=krc&charset=utf8")
    data = json.loads(_fetch(url, headers={"User-Agent": "KuGou2012"}))
    if data.get("status") != 200:
        return None

    raw       = base64.b64decode(data["content"])
    # Strip the 4-byte "krc1" magic header, XOR-decrypt, then zlib-decompress
    decrypted = bytearray(raw[4:])
    for i in range(len(decrypted)):
        decrypted[i] ^= KRC_KEY[i % len(KRC_KEY)]
    return zlib.decompress(bytes(decrypted)).decode("utf-8", errors="replace")


class KaraokeHandler(http.server.SimpleHTTPRequestHandler):
    """Static file server + Kugou lyrics proxy."""

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/kugou/search":
            self._handle_search(parsed.query)
        elif parsed.path == "/api/kugou/lyrics":
            self._handle_lyrics(parsed.query)
        else:
            super().do_GET()

    # ── /api/kugou/search ────────────────────────────────────────────

    def _handle_search(self, qs):
        params = urllib.parse.parse_qs(qs)
        query  = " ".join(params.get("q", [""])).strip()
        if not query:
            return self._json({})
        try:
            result = kugou_search(query)
            self._json(result or {})
        except Exception as e:
            self._json({"error": str(e)})

    # ── /api/kugou/lyrics ────────────────────────────────────────────

    def _handle_lyrics(self, qs):
        params    = urllib.parse.parse_qs(qs)
        lyric_id  = " ".join(params.get("id", [""])).strip()
        accesskey = " ".join(params.get("ak", [""])).strip()
        if not lyric_id or not accesskey:
            return self._json({})
        try:
            krc = kugou_lyrics(lyric_id, accesskey)
            self._json({"krc": krc} if krc else {})
        except Exception as e:
            self._json({"error": str(e)})

    # ── helpers ──────────────────────────────────────────────────────

    def _json(self, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type",  "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        # Log API calls and errors; suppress static file noise
        if args and isinstance(args[0], str):
            path = args[0].split()[1] if " " in args[0] else args[0]
            if path.startswith("/api/") or (len(args) > 1 and str(args[1])[:1] in "45"):
                super().log_message(fmt, *args)


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)) or ".")
    print(f"🎤  KaraokeMode  http://localhost:{PORT}/karaoke-app-revise.html")
    print(f"    Kugou proxy → /api/kugou/{{search,lyrics}}")
    print()
    with http.server.HTTPServer(("", PORT), KaraokeHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down.")
