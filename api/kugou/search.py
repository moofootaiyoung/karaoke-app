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


def _has_latin(text):
    """Return True if text contains mostly Latin-script characters."""
    if not text:
        return True
    latin = sum(1 for c in text if '\u0000' <= c <= '\u024f')
    return latin / max(len(text), 1) > 0.5


def kugou_search(query):
    parts  = re.split(r'\s[-–]\s', query, maxsplit=1)
    artist = parts[0].strip() if len(parts) == 2 else ""
    track  = parts[-1].strip()

    # Search with full artist+track so Kugou narrows to the right song
    full_query = f"{artist} {track}".strip() if artist else track
    enc  = urllib.parse.quote_plus(full_query)
    url  = (f"https://mobilecdn.kugou.com/api/v3/search/song"
            f"?keyword={enc}&pagesize=15&pagenum=1&bitrate=128&isfuzzy=0&format=json")
    data  = json.loads(_fetch(url))
    songs = data.get("data", {}).get("info", [])
    if not songs:
        return None

    noise = re.compile(
        r'(remix|ringtone|cover|tribute|karaoke|instrumental|piano|acoustic|version)', re.I)

    # Whether the query itself is Latin-script (English/Spanish/etc.)
    query_is_latin = _has_latin(full_query)

    def score(s):
        song_name   = s.get("songname",  "")
        singer_name = s.get("singername", "")
        # Title similarity — how well does the song name match our track?
        title_sc = _similarity(track, song_name)
        # Artist similarity
        art_sc   = _similarity(artist, singer_name) if artist else 0.5
        # Penalise if query is Latin but Kugou result is non-Latin (wrong language)
        lang_pen = 0.0
        if query_is_latin and not _has_latin(song_name):
            lang_pen = 0.5
        # Penalise noise words (cover, karaoke, etc.) unless user asked for them
        noisy = bool(noise.search(song_name)) and not noise.search(query)
        return (title_sc * 0.5 + art_sc * 0.5) - lang_pen - (0.3 if noisy else 0.0)

    ranked = sorted(songs, key=score, reverse=True)

    # Hard minimum: if the best candidate scores very low, don't return anything.
    # This avoids returning a completely unrelated song just because it's the
    # "least bad" result. Also return the score so JS can do its own sanity check.
    MIN_SCORE = 0.12
    if not ranked or score(ranked[0]) < MIN_SCORE:
        return None

    for song in ranked[:5]:
        song_score = score(song)
        if song_score < MIN_SCORE:
            break   # list is sorted; lower entries won't pass either
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
            "score":     round(song_score, 3),   # for JS-side sanity check
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
