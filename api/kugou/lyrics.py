"""
Vercel serverless function — /api/kugou/lyrics?id=ID&ak=ACCESSKEY

Downloads a KRC file from Kugou, decrypts it (strip 4-byte magic,
XOR with key, zlib-decompress), and returns the plain text.

Response: { krc: "<decoded KRC text>" } or {} on failure.
"""

import base64
import json
import ssl
import urllib.parse
import urllib.request
import zlib
from http.server import BaseHTTPRequestHandler

_CTX = ssl._create_unverified_context()

# Kugou's static XOR obfuscation key (public, documented in multiple OSS projects)
_KRC_KEY = bytes([64, 71, 97, 119, 94, 50, 116, 71, 81, 54, 49, 45, 206, 210, 110, 105])


def _fetch(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": "KuGou2012", "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
        return r.read()


def kugou_lyrics(lyric_id, accesskey):
    url  = (f"https://lyrics.kugou.com/download"
            f"?ver=1&client=pc&id={lyric_id}&accesskey={accesskey}&fmt=krc&charset=utf8")
    data = json.loads(_fetch(url))
    if data.get("status") != 200:
        return None

    raw       = base64.b64decode(data["content"])
    decrypted = bytearray(raw[4:])           # skip 4-byte "krc1" magic
    for i in range(len(decrypted)):
        decrypted[i] ^= _KRC_KEY[i % len(_KRC_KEY)]
    return zlib.decompress(bytes(decrypted)).decode("utf-8", errors="replace")


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs        = urllib.parse.urlparse(self.path).query
        params    = urllib.parse.parse_qs(qs)
        lyric_id  = " ".join(params.get("id", [""])).strip()
        accesskey = " ".join(params.get("ak", [""])).strip()

        try:
            if lyric_id and accesskey:
                krc  = kugou_lyrics(lyric_id, accesskey)
                body = json.dumps({"krc": krc} if krc else {}, ensure_ascii=False).encode("utf-8")
            else:
                body = b"{}"
        except Exception as e:
            body = json.dumps({"error": str(e)}).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type",   "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass
