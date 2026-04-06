#!/usr/bin/env python3
"""
KaraokeMode — Option B Stems Server
====================================
Downloads YouTube audio via yt-dlp, separates vocals with Demucs,
caches the instrumental stem, and serves it to the karaoke app.

Install dependencies:
    pip install demucs yt-dlp

Run:
    python3 stems_server.py

Listens on http://localhost:8082
"""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PORT       = 8082
CACHE_DIR  = Path(__file__).parent / "stems_cache"
CACHE_DIR.mkdir(exist_ok=True)

# In-memory job tracker: video_id → "processing" | "done" | "error"
_jobs: dict = {}
_jobs_lock  = threading.Lock()


# ── Helpers ────────────────────────────────────────────────────────

def _status_file(video_id: str) -> Path:
    return CACHE_DIR / video_id / "status.json"

def _stem_file(video_id: str) -> Path:
    return CACHE_DIR / video_id / "no_vocals.mp3"

def _read_status(video_id: str) -> dict:
    sf = _status_file(video_id)
    if sf.exists():
        try:
            return json.loads(sf.read_text())
        except Exception:
            pass
    return {}

def _write_status(video_id: str, data: dict):
    _status_file(video_id).write_text(json.dumps(data))

def _fmt(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


# ── Core processing pipeline ───────────────────────────────────────

def _process(video_id: str):
    job_dir = CACHE_DIR / video_id
    job_dir.mkdir(exist_ok=True)
    wav_path  = job_dir / "audio.wav"
    stem_path = _stem_file(video_id)

    try:
        # ── Step 1: Download audio ─────────────────────────────────
        _write_status(video_id, {"status": "downloading", "progress": "Downloading audio…"})
        print(f"[{video_id}] Downloading audio with yt-dlp…")

        dl = subprocess.run(
            [
                "yt-dlp",
                "-f", "bestaudio",
                "-x", "--audio-format", "wav",
                "--audio-quality", "0",
                "-o", str(wav_path.with_suffix("")),   # yt-dlp appends .wav
                "--no-playlist",
                f"https://www.youtube.com/watch?v={video_id}",
            ],
            capture_output=True, timeout=180,
        )
        if dl.returncode != 0:
            raise RuntimeError(f"yt-dlp failed: {dl.stderr.decode()[:300]}")

        # yt-dlp may produce audio.wav or audio.wav.wav — find it
        candidates = list(job_dir.glob("audio*.wav"))
        if not candidates:
            raise RuntimeError("Downloaded audio file not found")
        actual_wav = candidates[0]
        if actual_wav != wav_path:
            actual_wav.rename(wav_path)

        # ── Step 2: Separate stems with Demucs ────────────────────
        _write_status(video_id, {"status": "separating",
                                  "progress": "Separating vocals (this takes a few minutes)…"})
        print(f"[{video_id}] Running Demucs…")

        dm = subprocess.run(
            [
                sys.executable, "-m", "demucs",
                "--two-stems=vocals",       # only split into vocals + no_vocals
                "--mp3",                     # output as MP3 to save space
                "--mp3-bitrate", "192",
                "-o", str(job_dir),
                str(wav_path),
            ],
            capture_output=True, timeout=900,   # 15 min max
        )
        if dm.returncode != 0:
            raise RuntimeError(f"Demucs failed: {dm.stderr.decode()[:400]}")

        # ── Step 3: Locate the instrumental stem ──────────────────
        # Demucs outputs to: job_dir/htdemucs/audio/no_vocals.mp3
        found = list(job_dir.glob("*/audio*/no_vocals.mp3"))
        if not found:
            found = list(job_dir.glob("**/no_vocals.mp3"))
        if not found:
            raise RuntimeError("Demucs output file not found")
        found[0].rename(stem_path)

        # Clean up raw audio and Demucs temp tree to save disk
        wav_path.unlink(missing_ok=True)
        for tmp in job_dir.glob("htdemucs"):
            import shutil; shutil.rmtree(tmp, ignore_errors=True)
        for tmp in job_dir.glob("mdx_extra*"):
            import shutil; shutil.rmtree(tmp, ignore_errors=True)

        _write_status(video_id, {"status": "done"})
        print(f"[{video_id}] Done — stem saved to {stem_path}")

    except Exception as exc:
        msg = str(exc)
        print(f"[{video_id}] ERROR: {msg}")
        _write_status(video_id, {"status": "error", "message": msg})

    finally:
        with _jobs_lock:
            _jobs.pop(video_id, None)


def start_job(video_id: str):
    with _jobs_lock:
        if video_id in _jobs:
            return   # already running
        _jobs[video_id] = True
    t = threading.Thread(target=_process, args=(video_id,), daemon=True)
    t.start()


# ── HTTP handler ───────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        qs     = parse_qs(parsed.query)
        path   = parsed.path.rstrip("/")

        # ── GET /stems?id=VIDEO_ID ─────────────────────────────────
        if path == "/stems":
            video_id = "".join(qs.get("id", [""])).strip()
            if not video_id:
                return self._json(400, {"error": "Missing id parameter"})

            stem = _stem_file(video_id)

            # Already cached?
            if stem.exists():
                return self._json(200, {
                    "status": "done",
                    "url":    f"http://localhost:{PORT}/stems/file/{video_id}",
                })

            # Check persisted status (e.g. error from previous run)
            saved = _read_status(video_id)
            if saved.get("status") == "error":
                # Allow retry by clearing the error
                _status_file(video_id).unlink(missing_ok=True)

            # Kick off processing if not already running
            start_job(video_id)

            # Return current progress message
            saved = _read_status(video_id)
            return self._json(200, {
                "status":   saved.get("status", "processing"),
                "progress": saved.get("progress", "Starting…"),
            })

        # ── GET /stems/file/VIDEO_ID — serve the stem MP3 ─────────
        if path.startswith("/stems/file/"):
            video_id = path.split("/stems/file/")[-1].split("/")[0]
            stem     = _stem_file(video_id)
            if not stem.exists():
                return self._json(404, {"error": "Stem not ready"})

            data = stem.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type",   "audio/mpeg")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Accept-Ranges",  "bytes")
            self._cors()
            self.end_headers()
            self.wfile.write(data)
            return

        # ── GET /stems/status?id=VIDEO_ID ─────────────────────────
        if path == "/stems/status":
            video_id = "".join(qs.get("id", [""])).strip()
            stem     = _stem_file(video_id)
            if stem.exists():
                return self._json(200, {
                    "status": "done",
                    "url": f"http://localhost:{PORT}/stems/file/{video_id}",
                })
            saved = _read_status(video_id)
            return self._json(200, saved or {"status": "unknown"})

        self._json(404, {"error": "Not found"})

    def _json(self, code: int, data: dict):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type",   "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"  {self.address_string()} {fmt % args}")


# ── Entry point ────────────────────────────────────────────────────

if __name__ == "__main__":
    # Quick dependency check
    missing = []
    for cmd in ("yt-dlp", "demucs"):
        try:
            subprocess.run([sys.executable, "-m", cmd, "--version"]
                           if cmd == "demucs" else [cmd, "--version"],
                           capture_output=True, timeout=5)
        except FileNotFoundError:
            missing.append(cmd)
    if missing:
        print(f"\n  Missing dependencies: {', '.join(missing)}")
        print("  Run:  pip install demucs yt-dlp\n")
        sys.exit(1)

    print(f"\n  KaraokeMode Stems Server")
    print(f"  Listening on http://localhost:{PORT}")
    print(f"  Cache directory: {CACHE_DIR.resolve()}")
    print(f"  Press Ctrl+C to stop\n")

    server = HTTPServer(("localhost", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
