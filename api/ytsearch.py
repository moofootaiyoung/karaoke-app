"""
Vercel serverless function — /api/ytsearch?q=QUERY&skip=VIDEO_ID

Returns up to 8 YouTube video candidates as [{id, title}, …].
Used by the karaoke app's auto-find-alternative logic when a video
blocks embedding.

Uses public Invidious instances (open-source YT front-end with a
search API — no API key required).  Falls back across several
instances so one outage doesn't break the feature.
"""

import json
import ssl
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler

_CTX      = ssl._create_unverified_context()
_TIMEOUT  = 6   # seconds per request

# Public Invidious instances — tried in order until one responds.
# List sourced from https://api.invidious.io/instances.json (well-maintained set).
_INSTANCES = [
    "https://inv.nadeko.net",
    "https://invidious.nerdvpn.de",
    "https://invidious.privacyredirect.com",
    "https://vid.puffyan.us",
    "https://yt.cdaut.de",
]


def _fetch_json(url: str) -> dict | list | None:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT, context=_CTX) as r:
            return json.loads(r.read())
    except Exception:
        return None


def youtube_search(query: str, skip_id: str = "", n: int = 8) -> list:
    """
    Search YouTube via Invidious and return up to n [{id, title}] results.
    Skips skip_id (the currently-blocked video) so the app never retries it.
    """
    encoded = urllib.parse.quote_plus(query)

    for instance in _INSTANCES:
        url = (
            f"{instance}/api/v1/search"
            f"?q={encoded}&type=video&fields=videoId,title&page=1"
        )
        data = _fetch_json(url)
        if not isinstance(data, list) or len(data) == 0:
            continue   # this instance failed or returned nothing — try next

        results = []
        for item in data:
            vid_id = item.get("videoId", "")
            title  = item.get("title",   "")
            if not vid_id or vid_id == skip_id:
                continue
            results.append({"id": vid_id, "title": title})
            if len(results) >= n:
                break

        if results:
            return results   # got good data — no need to try other instances

    return []   # all instances failed


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs      = urllib.parse.urlparse(self.path).query
        params  = urllib.parse.parse_qs(qs)
        query   = " ".join(params.get("q",    [""])).strip()
        skip_id = " ".join(params.get("skip", [""])).strip()

        try:
            results = youtube_search(query, skip_id=skip_id) if query else []
            body    = json.dumps(results, ensure_ascii=False).encode("utf-8")
        except Exception as exc:
            body = json.dumps({"error": str(exc)}).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type",   "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.end_headers()

    def log_message(self, *args):
        pass
