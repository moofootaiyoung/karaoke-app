"""
Vercel serverless function — /api/kugou/search?q=ARTIST+TRACK

Searches Kugou for the best-matching song and returns the KRC lyrics
candidate ID + accesskey needed by /api/kugou/lyrics.

Response: { id, accesskey, song, singer } or {} on failure.
"""

import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler

# On Vercel (Linux) Kugou's certs are valid; _create_unverified_context
# is kept as a safe fallback in case of CDN cert issues.
_CTX = ssl._create_unverified_context()


def _fetch(url, headers=None, timeout=8):
    h = {"User-Agent": "KuGou2012", "Accept": "*/*"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
        return r.read()


def _similarity(a, b):
    ta = set(re.split(r'\W+', a.lower()))
    tb = set(re.split(r'\W+', b.lower()))
    ta.discard(""); tb.discard("")
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(len(ta), len(tb))


def kugou_search(query):
    parts  = re.split(r'\s[-–]\s', query, maxsplit=1)
    artist = parts[0].strip() if len(parts) == 2 else ""
    track  = parts[-1].strip()

    enc  = urllib.parse.quote_plus(track)
    url  = (f"https://mobilecdn.kugou.com/api/v3/search/song"
            f"?keyword={enc}&pagesize=10&pagenum=1&bitrate=128&isfuzzy=0&format=json")
    data  = json.loads(_fetch(url))
    songs = data.get("data", {}).get("info", [])
    if not songs:
        return None

    noise = re.compile(
        r'(remix|ringtone|cover|tribute|karaoke|instrumental|piano|acoustic|version)', re.I)

    def score(s):
        art_sc = _similarity(artist, s.get("singername", "")) if artist else 0.5
        noisy  = bool(noise.search(s.get("songname", ""))) and not noise.search(query)
        return art_sc - (0.4 if noisy else 0.0)

    for song in sorted(songs, key=score, reverse=True)[:5]:
        lurl  = (f"https://krcs.kugou.com/search"
                 f"?ver=1&man=yes&client=pc&hash={song['hash']}&timelength=0")
        try:
            ldata = json.loads(_fetch(lurl))
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


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs     = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(qs)
        query  = " ".join(params.get("q", [""])).strip()

        try:
            result = kugou_search(query) if query else None
            body   = json.dumps(result or {}, ensure_ascii=False).encode("utf-8")
        except Exception as e:
            body = json.dumps({"error": str(e)}).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type",   "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass   # suppress Vercel function logs
