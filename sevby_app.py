#!/usr/bin/env python3
"""
SEVBY: Playlist to MP3  (Spotify Export via Bandcamp & YouTube)
=============================================
Standalone desktop app (customtkinter).

Paths:
  1) Load a .txt / paste song list  (no Spotify account needed)
  2) Spotify playlist URL (public OR private) - needs only a Client ID (Spotify requires Premium to create one).
     First use opens your browser once to log in; after that it is remembered.

Then:
  - Search Bandcamp for each song
  - Download the Bandcamp tracks (as MP3)
  - Download everything not on Bandcamp from YouTube (as MP3, bundled ffmpeg)

Build example:
  pyinstaller --clean --noconsole --onefile ^
    --collect-all yt_dlp --collect-all customtkinter ^
    --add-binary "ffmpeg.exe;." sevby_app.py
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox

# yt-dlp as a Python module (works inside the packaged .exe, no PATH needed)
try:
    import yt_dlp
except ImportError:
    yt_dlp = None

# Optional: drag-and-drop (pip install tkinterdnd2)
try:
    from tkinterdnd2 import DND_FILES, DND_TEXT, TkinterDnD

    _HAS_DND = True
except ImportError:
    _HAS_DND = False

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("green")


def resource_path(name: str) -> str:
    """Find a bundled file both when run as a script and inside the PyInstaller .exe."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


ICON_PATH = resource_path("sevbyicon.ico")

# Give the app its own identity so Windows shows OUR icon in the taskbar
# (otherwise it groups the window under python.exe / the default icon).
if os.name == "nt":
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("sevby.hq.playlist.downloader")
    except Exception:
        pass

VERSION = "0.10.0-beta"
APP_NAME = "SEVBY HQ"
CONFIG_PATH = Path.home() / ".sevby_hq_config.json"
LOG_PATH = Path.home() / ".sevby_hq_log.txt"  # copy of the last run's log, handy for bug reports
BANDCAMP_API = "https://bandcamp.com/api/fuzzysearch/2/app_autocomplete"

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Must match the Redirect URI typed into the Spotify developer app
SPOTIFY_REDIRECT = "http://127.0.0.1:8888"
SPOTIFY_PORT = 8888
SPOTIFY_SCOPES = "playlist-read-private playlist-read-collaborative"

# Pause / Stop flags shared between the buttons and the worker thread
STOP_EVENT = threading.Event()
PAUSE_EVENT = threading.Event()


class _Stopped(Exception):
    """Raised inside a yt-dlp progress hook to abort the current download."""


# \u2500\u2500 Config helpers \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_config(data: dict) -> None:
    try:
        CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


# \u2500\u2500 Bandcamp search \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

def fix_bc_url(url: str) -> str:
    if not url:
        return ""
    if url.count("https://") > 1:
        url = "https://" + url.split("https://")[-1]
    return url


def _split_query(query: str) -> tuple[str, str]:
    """Return (artist, title). Artist is '' if no separator found."""
    for sep in (" - ", " \u2013 ", " \u2014 "):
        if sep in query:
            a, t = query.split(sep, 1)
            return a.strip(), t.strip()
    return "", query.strip()


def bc_score(result: dict, query: str) -> float:
    q = query.lower().strip()
    name = (result.get("name") or "").lower()
    band = (result.get("band_name") or "").lower()
    q_clean = q.replace("\u2013", " ").replace("\u2014", " ").replace("-", " ").replace(",", " ")
    tokens = [t for t in q_clean.split() if len(t) > 1]
    score = 0.0
    for tok in tokens:
        if tok in band:
            score += 35
        if tok in name:
            score += 20
    _, title_guess = _split_query(q)
    title_guess = title_guess.lower()
    if title_guess and title_guess in name:
        score += 40
    if result.get("type") == "t":
        score += 5
    if any(w in name for w in ("remix", "cover", "bootleg", "tribute")):
        if not any(w in q for w in ("remix", "cover", "bootleg")):
            score -= 25
    return score


def _artist_matches(result: dict, query: str) -> bool:
    """Guard against wrong-artist hits: at least one artist word must be in the band name."""
    artist, _ = _split_query(query)
    if not artist:
        return True
    band = (result.get("band_name") or "").lower()
    words = [w for w in re.split(r"[\s,&]+", artist.lower()) if len(w) > 1]
    return any(w in band for w in words) if words else True


SIMPLE_UA = "Mozilla/5.0 (compatible; BandcampSongLinker/1.0)"
BCSEARCH_API = "https://bandcamp.com/api/bcsearch_public_api/1/autocomplete_elastic"

# Which request style worked last time (tried first on the next search)
_bc_pref = [0]


def _describe_block(status: int | None, body: str) -> str:
    """Short human-readable reason for a non-JSON reply."""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", body, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()[:140]
    low = body.lower()
    hint = ""
    if "javascript" in low and ("challenge" in low or "enable" in low):
        hint = " (Bandcamp is showing a JavaScript bot check)"
    elif "avg" in low:
        hint = " (looks like an AVG block page)"
    elif "cloudflare" in low or "just a moment" in low or "captcha" in low:
        hint = " (looks like a bot check)"
    elif "<html" in low:
        hint = " (got a web page instead of JSON)"
    st = f"HTTP {status}: " if status else ""
    return f"{st}{text or 'empty reply'}{hint}"


def _fetch(url: str, headers: dict, data: bytes | None = None) -> tuple[str | None, str | None]:
    """Return (json_text, None) or (None, reason)."""
    req = urllib.request.Request(url, headers=headers, data=data)
    status = None
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.status
            raw = resp.read().decode("utf-8", errors="replace").strip()
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return None, _describe_block(e.code, body)
    except Exception as e:
        return None, f"network error: {e}"
    if not raw or raw[0] not in "{[":
        return None, _describe_block(status, raw)
    return raw, None


def _fetch_curl(url: str, headers: dict, data: bytes | None = None) -> tuple[str | None, str | None]:
    """Same request through the system curl (a different, standard HTTP client)."""
    exe = shutil.which("curl.exe") or shutil.which("curl")
    if not exe:
        return None, "curl not available"
    cmd = [exe, "-sS", "-L", "--max-time", "20", url]
    for k, v in headers.items():
        cmd += ["-H", f"{k}: {v}"]
    if data is not None:
        cmd += ["--data-binary", data.decode("utf-8")]
    flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=30, creationflags=flags)
    except Exception as e:
        return None, f"curl failed: {e}"
    raw = r.stdout.decode("utf-8", errors="replace").strip()
    if not raw or raw[0] not in "{[":
        return None, _describe_block(None, raw or r.stderr.decode("utf-8", errors="replace"))
    return raw, None


def _norm_fuzzy(data: dict) -> list[dict]:
    return [
        {
            "type": r.get("type") or "",
            "name": r.get("name") or "",
            "band_name": r.get("band_name") or "",
            "url": r.get("url") or "",
        }
        for r in (data.get("results") or [])
    ]


def _norm_bcsearch(data) -> list[dict]:
    block = data.get("auto") if isinstance(data, dict) else None
    items = (block or {}).get("results") if isinstance(block, dict) else None
    if items is None and isinstance(data, dict):
        items = data.get("results")
    out = []
    for r in items or []:
        url = r.get("item_url_path") or r.get("item_url_root") or r.get("url") or ""
        out.append(
            {
                "type": r.get("type") or "",
                "name": r.get("name") or "",
                "band_name": r.get("band_name") or "",
                "url": url,
            }
        )
    return out


def _bandcamp_request(q: str) -> tuple[list, str | None]:
    """Try several request styles; return (results, None) or ([], reason)."""
    qs = urllib.parse.urlencode({"q": q, "param_with_locations": "true"})
    fuzzy_url = f"{BANDCAMP_API}?{qs}"
    post_body = json.dumps(
        {"search_text": q, "search_filter": "t", "full_page": False, "fan_id": None}
    ).encode()

    strategies = [
        # 0: exactly what the working bandcamp_search.py does
        lambda: (
            _fetch(fuzzy_url, {"User-Agent": SIMPLE_UA, "Accept": "application/json"}),
            _norm_fuzzy,
        ),
        # 1: look like a normal browser
        lambda: (
            _fetch(
                fuzzy_url,
                {
                    "User-Agent": BROWSER_UA,
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Referer": "https://bandcamp.com/",
                    "Origin": "https://bandcamp.com",
                },
            ),
            _norm_fuzzy,
        ),
        # 2: the website's own search API
        lambda: (
            _fetch(
                BCSEARCH_API,
                {
                    "User-Agent": BROWSER_UA,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Referer": "https://bandcamp.com/",
                    "Origin": "https://bandcamp.com",
                },
                data=post_body,
            ),
            _norm_bcsearch,
        ),
        # 3: same plain request through the system curl
        lambda: (
            _fetch_curl(fuzzy_url, {"User-Agent": SIMPLE_UA, "Accept": "application/json"}),
            _norm_fuzzy,
        ),
    ]

    order = [_bc_pref[0]] + [i for i in range(len(strategies)) if i != _bc_pref[0]]
    first_reason = None
    for idx in order:
        (raw, reason), norm = strategies[idx]()
        if raw is not None:
            try:
                results = norm(json.loads(raw))
            except Exception as e:
                reason = f"could not read reply: {e}"
            else:
                _bc_pref[0] = idx
                return results, None
        if first_reason is None:
            first_reason = reason
        time.sleep(0.4)
    return [], first_reason or "unknown error"


def _norm_text(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _bc_subdomains(artist: str) -> list[str]:
    """Likely Bandcamp subdomains for an artist name."""
    first = re.split(r"\s*(?:,|&|\bfeat\.?|\bft\.?|\bx\b)\s*", artist, flags=re.I)[0]
    low = re.sub(r"['\u2019`]", "", first.lower()).strip()
    out: list[str] = []
    for s in (re.sub(r"[^a-z0-9]", "", low), re.sub(r"[^a-z0-9]+", "-", low).strip("-")):
        if s and s not in out:
            out.append(s)
    return out


def _bc_title_slugs(title: str) -> list[str]:
    """Likely URL slugs for a track title."""
    no_paren = re.sub(r"\s*[\(\[][^)\]]*[\)\]]", "", title).strip()
    variants = [title, no_paren, re.split(r"\s+[-\u2013\u2014]\s+", no_paren)[0].strip()]
    out: list[str] = []
    for v in variants:
        low = re.sub(r"['\u2019`]", "", v.lower())
        slug = re.sub(r"[^a-z0-9]+", "-", low).strip("-")
        if slug and slug not in out:
            out.append(slug)
    return out


def _probe_bc_track(url: str) -> dict | None:
    """Read a Bandcamp track page through yt-dlp (no download). None if it doesn't exist."""
    if yt_dlp is None:
        return None
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "logger": _QuietLogger(),
        "socket_timeout": 15,
        "retries": 1,
        "no_color": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception:
        return None


def _guess_bandcamp(query: str) -> dict | None:
    """When Bandcamp's search is blocked: try the artist's own track page and verify it."""
    artist, title = _split_query(query)
    if not artist or not title or yt_dlp is None:
        return None
    want = _norm_text(re.sub(r"\s*[\(\[][^)\]]*[\)\]]", "", title))
    artist_words = [w for w in re.split(r"[\s,&]+", artist.lower()) if len(w) > 1]
    wants_remix = "remix" in title.lower()
    for sub in _bc_subdomains(artist):
        for slug in _bc_title_slugs(title):
            if STOP_EVENT.is_set():
                return None
            url = f"https://{sub}.bandcamp.com/track/{slug}"
            info = _probe_bc_track(url)
            if not info:
                continue
            raw_title = info.get("track") or info.get("title") or ""
            got_title = _norm_text(raw_title)
            got_artist = (info.get("artist") or info.get("uploader") or "").lower()
            if not got_title or not want:
                continue
            title_ok = want in got_title or got_title in want
            artist_ok = any(w in got_artist for w in artist_words) or sub in _norm_text(got_artist)
            if wants_remix and "remix" not in raw_title.lower():
                continue
            if title_ok and artist_ok:
                return {
                    "found": True,
                    "name": raw_title,
                    "band_name": info.get("artist") or "",
                    "url": info.get("webpage_url") or url,
                    "guessed": True,
                }
    return None


def search_bandcamp(query: str, use_api: bool = True) -> dict:
    """Find the best TRACK on Bandcamp for 'Artist - Title'."""
    if not use_api:
        return _guess_bandcamp(query) or {"found": False}

    results, err = _bandcamp_request(query)
    if err and not results:
        guess = _guess_bandcamp(query)
        if guess:
            return guess
        return {"found": False, "error": err}

    tracks = [r for r in results if r.get("type") == "t"]

    if not tracks:
        _, title = _split_query(query)
        if title and title != query:
            more, err2 = _bandcamp_request(title)
            tracks = [r for r in more if r.get("type") == "t"]
            if err2 and not tracks:
                return {"found": False, "error": err2}

    tracks = [r for r in tracks if _artist_matches(r, query)]
    if not tracks:
        return {"found": False}

    best = max(tracks, key=lambda r: bc_score(r, query))
    if bc_score(best, query) < 20:
        return {"found": False}
    url = fix_bc_url(best.get("url") or "")
    if not url:
        return {"found": False}
    return {
        "found": True,
        "name": best.get("name") or "",
        "band_name": best.get("band_name") or "",
        "url": url,
    }


# \u2500\u2500 Spotify (PKCE login = Client ID only, works for private playlists) \u2500\u2500\u2500\u2500\u2500\u2500

def _http_json(req: urllib.request.Request, timeout: int = 20) -> dict:
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 5:
                # Spotify rate limit (can happen on very large playlists): wait as told, retry
                try:
                    wait = int(e.headers.get("Retry-After", "2"))
                except (TypeError, ValueError):
                    wait = 2
                time.sleep(min(max(wait, 1), 60))
                continue
            try:
                body = e.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                body = ""
            raise RuntimeError(f"HTTP {e.code} {body}".strip()) from e
    raise RuntimeError("HTTP 429 too many requests")


def _token_post(fields: dict) -> dict:
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(
        "https://accounts.spotify.com/api/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    return _http_json(req, timeout=20)


def spotify_login_pkce(client_id: str, log, timeout: int = 180) -> dict | None:
    """Open the browser once for Spotify login. Returns token dict or None."""
    verifier = base64.urlsafe_b64encode(os.urandom(64)).decode().rstrip("=")
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    state = secrets.token_urlsafe(16)
    got: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a, **k):
            pass

        def do_GET(self):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" in qs or "error" in qs:
                got["code"] = (qs.get("code") or [""])[0]
                got["error"] = (qs.get("error") or [""])[0]
                got["state"] = (qs.get("state") or [""])[0]
                body = (
                    "<html><body style='font-family:sans-serif;background:#1a1a1a;"
                    "color:#eee;text-align:center;padding-top:80px'>"
                    "<h2>SEVBY connected to Spotify</h2>"
                    "<p>You can close this tab and go back to the app.</p>"
                    "</body></html>"
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

    try:
        server = HTTPServer(("127.0.0.1", SPOTIFY_PORT), Handler)
    except OSError as e:
        log(f"Could not open local login port {SPOTIFY_PORT}: {e}")
        return None
    server.timeout = 1

    params = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": SPOTIFY_REDIRECT,
            "scope": SPOTIFY_SCOPES,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
            "state": state,
        }
    )
    log("Opening your browser to log in to Spotify (one-time)\u2026")
    webbrowser.open(f"https://accounts.spotify.com/authorize?{params}")

    deadline = time.time() + timeout
    try:
        while time.time() < deadline and not got:
            server.handle_request()
    finally:
        server.server_close()

    if not got:
        log("Spotify login timed out.")
        return None
    if got.get("error") or not got.get("code"):
        log(f"Spotify login was cancelled or failed: {got.get('error') or 'no code'}")
        return None
    if got.get("state") != state:
        log("Spotify login state mismatch \u2014 aborted.")
        return None

    try:
        return _token_post(
            {
                "grant_type": "authorization_code",
                "code": got["code"],
                "redirect_uri": SPOTIFY_REDIRECT,
                "client_id": client_id,
                "code_verifier": verifier,
            }
        )
    except Exception as e:
        log(f"Spotify token exchange failed: {e}")
        return None


def spotify_client_credentials(client_id: str, client_secret: str) -> str | None:
    auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    data = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
    req = urllib.request.Request(
        "https://accounts.spotify.com/api/token",
        data=data,
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        return _http_json(req).get("access_token")
    except Exception:
        return None


def get_spotify_access_token(client_id: str, client_secret: str, log) -> str | None:
    """Refresh token -> browser login -> (fallback) client credentials."""
    cfg = load_config()

    # 1) Saved refresh token (no browser needed)
    rt = cfg.get("refresh_token")
    if rt and cfg.get("refresh_client_id") == client_id:
        try:
            tok = _token_post(
                {"grant_type": "refresh_token", "refresh_token": rt, "client_id": client_id}
            )
            if tok.get("access_token"):
                if tok.get("refresh_token"):
                    cfg["refresh_token"] = tok["refresh_token"]
                    save_config(cfg)
                return tok["access_token"]
        except Exception:
            log("Saved Spotify login expired \u2014 logging in again.")

    # 2) Browser login (works for private playlists)
    tok = spotify_login_pkce(client_id, log)
    if tok and tok.get("access_token"):
        cfg = load_config()
        cfg["client_id"] = client_id
        cfg["refresh_client_id"] = client_id
        if tok.get("refresh_token"):
            cfg["refresh_token"] = tok["refresh_token"]
        save_config(cfg)
        return tok["access_token"]

    # 3) Optional fallback: old client-credentials (public playlists only)
    if client_secret:
        log("Trying public-playlist access with Client Secret\u2026")
        return spotify_client_credentials(client_id, client_secret)
    return None


# Extra info about each song ("Artist - Title" string -> tags, length, cover URL).
# Filled from Spotify playlists; used for tagging and for picking the right YouTube video.
SONG_META: dict[str, dict] = {}


def _spotify_meta(track: dict) -> dict:
    album = track.get("album") or {}
    images = album.get("images") or []
    release = album.get("release_date") or ""
    return {
        "artist": ", ".join(a.get("name", "") for a in (track.get("artists") or []) if a.get("name")),
        "album_artist": ", ".join(
            a.get("name", "") for a in (album.get("artists") or []) if a.get("name")
        ),
        "title": track.get("name") or "",
        "album": album.get("name") or "",
        "year": release[:4] if release[:4].isdigit() else "",
        "track": track.get("track_number") or 0,
        "track_total": album.get("total_tracks") or 0,
        "duration": (track.get("duration_ms") or 0) / 1000.0,
        "cover_url": images[0].get("url", "") if images else "",
    }


def fetch_spotify_tracks(playlist_url: str, token: str) -> list[str]:
    m = re.search(r"playlist[/:]([a-zA-Z0-9]+)", playlist_url)
    if not m:
        raise ValueError("Could not find a playlist ID in that URL.")
    pid = m.group(1)

    last_err: Exception | None = None
    for endpoint in ("items", "tracks"):  # Spotify renamed this endpoint; try both
        tracks: list[str] = []
        url = f"https://api.spotify.com/v1/playlists/{pid}/{endpoint}?limit=100"
        try:
            while url:
                req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
                data = _http_json(req)
                for item in data.get("items") or []:
                    track = item.get("track") or item.get("item") or {}
                    name = track.get("name") or ""
                    artists = ", ".join(
                        a.get("name", "") for a in (track.get("artists") or []) if a.get("name")
                    )
                    if name and artists:
                        label = f"{artists} - {name}"
                        tracks.append(label)
                        SONG_META[label] = _spotify_meta(track)
                url = data.get("next")
            return tracks
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(
        f"Could not read that playlist ({last_err}). "
        "Make sure you logged in with the account that can see it."
    )


def spotify_playlist_name(playlist_url: str, token: str) -> str:
    m = re.search(r"playlist[/:]([a-zA-Z0-9]+)", playlist_url)
    if not m:
        return ""
    req = urllib.request.Request(
        f"https://api.spotify.com/v1/playlists/{m.group(1)}?fields=name",
        headers={"Authorization": f"Bearer {token}"},
    )
    return (_http_json(req).get("name") or "").strip()


# \u2500\u2500 Song list parsing \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

_TITLE_COLS = ("track name", "track", "title", "name", "song", "song name", "track title")
_ARTIST_COLS = ("artist name(s)", "artist name", "artist", "artists", "artist(s)", "album artist")


def _structured_to_lines(text: str) -> list[str] | None:
    """Turn an M3U playlist, or a CSV / tab-separated export (Apple Music, TuneMyMusic, Soundiiz,
    Exportify...) into 'Artist - Title' lines. Returns None if the text isn't one of those."""
    import csv
    import io

    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None
    # M3U / M3U8: "#EXTINF:123,Artist - Title"
    if lines[0].strip().upper().startswith("#EXTM3U") or any(ln.startswith("#EXTINF:") for ln in lines[:5]):
        out = []
        for ln in lines:
            if ln.startswith("#EXTINF:") and "," in ln:
                t = ln.split(",", 1)[1].strip()
                if t:
                    out.append(t)
        return out or None
    # CSV / TSV with a header row naming a title column and an artist column
    head = lines[0]
    delim = "\t" if head.count("\t") >= 1 else ("," if head.count(",") >= 1 else None)
    if not delim:
        return None
    try:
        rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=delim))
    except csv.Error:
        return None
    cols = [c.strip().lower() for c in rows[0]]
    ti = next((cols.index(c) for c in _TITLE_COLS if c in cols), None)
    ai = next((cols.index(c) for c in _ARTIST_COLS if c in cols), None)
    if ti is None or ai is None or ti == ai:
        return None
    out = []
    for r in rows[1:]:
        if len(r) <= max(ti, ai):
            continue
        title = r[ti].strip()
        artist = re.split(r"\s*[;/]\s*", r[ai].strip())[0].strip()
        if title and artist:
            out.append(f"{artist} - {title}")
        elif title:
            out.append(title)
    return out or None


def parse_song_lines(text: str) -> list[str]:
    structured = _structured_to_lines(text)
    if structured is not None:
        return structured
    songs = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("===") or line.startswith("http"):
            continue
        if "  \u2192  " in line:
            line = line.split("  \u2192  ")[0].strip()
        songs.append(line)
    return songs


def load_songs_from_file(path: str) -> list[str]:
    data = Path(path).read_bytes()
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):  # Apple Music on Windows can save UTF-16
        raw = data.decode("utf-16", errors="replace")
    else:
        raw = data.decode("utf-8-sig", errors="replace")
    return parse_song_lines(raw)


# \u2500\u2500 Downloading (Bandcamp + YouTube, both via yt-dlp) \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

def ffmpeg_path() -> str:
    """Bundled ffmpeg first (inside the app / next to it), then whatever is on PATH."""
    exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        p = os.path.join(sys._MEIPASS, exe)
        if os.path.isfile(p):
            return p
    base = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
    p = os.path.join(base, exe)
    if os.path.isfile(p):
        return p
    return shutil.which("ffmpeg") or "ffmpeg"


def ytdlp_age_days() -> int | None:
    """How old the installed yt-dlp is (YouTube breaks old versions), or None if unknown."""
    try:
        from datetime import datetime

        ver = yt_dlp.version.__version__  # e.g. 2025.09.26
        d = datetime.strptime(".".join(ver.split(".")[:3]), "%Y.%m.%d")
        return (datetime.now() - d).days
    except Exception:
        return None


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]', "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:180]


def have_ytdlp() -> bool:
    return yt_dlp is not None or shutil.which("yt-dlp") is not None


class _QuietLogger:
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def _ffmpeg_location() -> str | None:
    ff = ffmpeg_path()
    if ff and ff != "ffmpeg":
        return os.path.dirname(ff)
    return None


def _strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


def _stop_hook(_d) -> None:
    """yt-dlp calls this while downloading; raising aborts the download."""
    if STOP_EVENT.is_set():
        raise _Stopped()


def _cleanup_partial(out_dir: str, safe: str) -> None:
    """Remove half-finished files for one song after a Stop."""
    try:
        has_mp3 = os.path.exists(os.path.join(out_dir, f"{safe}.mp3"))
        for name in os.listdir(out_dir):
            if not name.startswith(safe + "."):
                continue
            low = name.lower()
            if low.endswith((".part", ".ytdl", ".temp")) or (
                not has_mp3 and low.endswith((".jpg", ".jpeg", ".webp", ".png", ".webm", ".m4a", ".opus"))
            ):
                try:
                    os.remove(os.path.join(out_dir, name))
                except OSError:
                    pass
    except OSError:
        pass


_LAST_INFO: dict = {}  # metadata of the most recent yt-dlp download (used for cover repair)

# "Best available" keeps YouTube's original audio (M4A/AAC) instead of re-encoding to MP3.
QUALITY = {"best": True, "free": True, "jamendo_id": ""}
HQ_SONGS: list[str] = []  # songs that came from YouTube in this run (for the "find better quality" file)


def hq_search_links(song: str) -> list[tuple[str, str]]:
    """Search pages where a better-quality copy can be bought (we only build links, nothing is fetched)."""
    q = urllib.parse.quote_plus(song)
    return [
        ("Bandcamp", f"https://bandcamp.com/search?q={q}"),
        ("Qobuz", f"https://www.qobuz.com/us-en/search?q={q}"),
        ("7digital", f"https://us.7digital.com/search?q={q}"),
    ]


def write_hq_links(out_dir: str, log) -> None:
    """Write a small text file with buy/search links for the songs that only came from YouTube."""
    if not HQ_SONGS:
        return
    path = os.path.join(out_dir, "SEVBY HQ - find better quality.txt")
    lines = [
        "SEVBY HQ - songs that came from YouTube (about 128-160 kbps at best).",
        "If you love one, these links search stores that sell lossless / 320 kbps copies.",
        "Buying from the artist on Bandcamp is the best way to support them.",
        "",
    ]
    for song in HQ_SONGS:
        lines.append(song)
        for name, url in hq_search_links(song):
            lines.append(f"  {name}: {url}")
        lines.append("")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        log(f"{len(HQ_SONGS)} song(s) came from YouTube. Links to find better-quality copies: {path}")
    except OSError:
        pass


def _run_ff(args: list[str]):
    """Run the bundled ffmpeg quietly. Returns CompletedProcess or None if ffmpeg is missing."""
    flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    try:
        return subprocess.run(
            [ffmpeg_path(), "-hide_banner", *args],
            capture_output=True,
            timeout=90,
            creationflags=flags,
        )
    except Exception:
        return None


def _has_cover(mp3: str) -> bool:
    """True if the MP3 contains embedded art (ffmpeg lists it as a Video stream)."""
    r = _run_ff(["-i", mp3])
    if r is None:
        return True  # can't check \u2192 don't touch the file
    return "Video:" in (r.stderr or b"").decode("utf-8", errors="replace")


def _thumb_urls(info: dict | None) -> list[str]:
    if not info:
        return []
    urls: list[str] = []
    if info.get("thumbnail"):
        urls.append(info["thumbnail"])
    thumbs = [t for t in (info.get("thumbnails") or []) if t.get("url")]
    thumbs.sort(key=lambda t: ((t.get("preference") or 0), (t.get("width") or 0)), reverse=True)
    for t in thumbs:
        if t["url"] not in urls:
            urls.append(t["url"])
    return urls[:5]


def _to_jpg(src: str) -> str | None:
    if src.lower().endswith((".jpg", ".jpeg")):
        return src
    dst = os.path.join(tempfile.gettempdir(), f"sevby_cover_{os.getpid()}.jpg")
    r = _run_ff(["-y", "-i", src, "-q:v", "2", dst])
    return dst if r is not None and r.returncode == 0 and os.path.isfile(dst) else None


def _find_or_fetch_cover(out_dir: str, safe: str, info: dict | None) -> str | None:
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        p = os.path.join(out_dir, safe + ext)
        if os.path.isfile(p):
            jpg = _to_jpg(p)
            if jpg:
                return jpg
    for url in _thumb_urls(info):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": SIMPLE_UA})
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = resp.read()
        except Exception:
            continue
        if len(data) < 1000:
            continue
        ext = ".webp" if data[:4] == b"RIFF" else (".png" if data[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg")
        tmp = os.path.join(tempfile.gettempdir(), f"sevby_thumb_{os.getpid()}{ext}")
        try:
            with open(tmp, "wb") as f:
                f.write(data)
        except OSError:
            continue
        jpg = _to_jpg(tmp)
        if jpg:
            return jpg
    return None


def _embed_cover(mp3: str, img: str) -> bool:
    tmp = mp3[:-4] + ".sevby-tmp.mp3"
    r = _run_ff([
        "-y", "-i", mp3, "-i", img,
        "-map", "0:a", "-map", "1:v", "-c", "copy",
        "-id3v2_version", "3",
        "-metadata:s:v", "title=Album cover",
        "-metadata:s:v", "comment=Cover (front)",
        tmp,
    ])
    ok = r is not None and r.returncode == 0 and os.path.isfile(tmp)
    if ok:
        try:
            os.replace(tmp, mp3)
            return True
        except OSError:
            pass
    try:
        if os.path.exists(tmp):
            os.remove(tmp)
    except OSError:
        pass
    return False


def _ensure_cover(out_dir: str, safe: str, info: dict | None, log) -> None:
    """After a download: make sure the MP3 really has its cover art; repair it if not."""
    mp3 = os.path.join(out_dir, f"{safe}.mp3")
    if not os.path.isfile(mp3):
        return
    try:
        if not _has_cover(mp3):
            img = _find_or_fetch_cover(out_dir, safe, info)
            if img and _embed_cover(mp3, img):
                if log:
                    log("  Cover art was missing \u2014 added it.")
            elif log:
                log("  Note: no cover art could be found for this track.")
    finally:
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            p = os.path.join(out_dir, safe + ext)
            if os.path.isfile(p):
                try:
                    os.remove(p)
                except OSError:
                    pass


def _run_ytdlp_module(
    target: str, out_dir: str, safe: str, cookies: bool, embed: bool = True, original: bool = False
) -> tuple[bool, str]:
    opts = {
        "format": "bestaudio[ext=m4a]/bestaudio/best" if original else "bestaudio/best",
        "noplaylist": True,
        "outtmpl": os.path.join(out_dir, safe.replace("%", "%%") + ".%(ext)s"),
        "writethumbnail": embed,
        "postprocessors": [] if original else (
            [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "0"},
                {"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"},
                {"key": "FFmpegMetadata", "add_metadata": True},
                {"key": "EmbedThumbnail"},
            ]
            if embed
            else [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "0"}]
        ),
        "quiet": True,
        "no_warnings": True,
        "logger": _QuietLogger(),
        "socket_timeout": 30,
        "retries": 3,
        "progress_hooks": [_stop_hook],
        "no_color": True,
    }
    loc = _ffmpeg_location()
    if loc:
        opts["ffmpeg_location"] = loc
    if cookies:
        opts["cookiesfrombrowser"] = ("chrome",)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(target, download=True)
        if isinstance(info, dict) and info.get("entries"):
            entries = [e for e in info["entries"] if e]
            info = entries[0] if entries else info
        _LAST_INFO["info"] = info if isinstance(info, dict) else None
        return True, ""
    except Exception as e:
        if STOP_EVENT.is_set():
            return False, "stopped"
        msg = _strip_ansi(str(e)).strip().splitlines()
        return False, (msg[-1] if msg else "unknown error")


def _run_ytdlp_cli(
    target: str, out_dir: str, safe: str, cookies: bool, embed: bool = True, original: bool = False
) -> tuple[bool, str]:
    out_template = os.path.join(out_dir, f"{safe}.%(ext)s")
    cmd = [
        "yt-dlp", "--no-playlist", "--no-colors", "-x",
        "--audio-format", "mp3", "--audio-quality", "0",
    ]
    if embed:
        cmd += ["--embed-thumbnail", "--embed-metadata", "--convert-thumbnails", "jpg"]
    cmd += [
        "-o", out_template, target,
    ]
    loc = _ffmpeg_location()
    if loc:
        cmd.extend(["--ffmpeg-location", loc])
    if cookies:
        cmd.extend(["--cookies-from-browser", "chrome"])
    flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=240, creationflags=flags
        )
        if r.returncode == 0:
            return True, ""
        err = (r.stderr or r.stdout or "").strip().splitlines()
        return False, (err[-1] if err else "unknown error")
    except subprocess.TimeoutExpired:
        return False, "timed out"
    except FileNotFoundError:
        return False, "yt-dlp not found \u2014 run: pip install -U yt-dlp"
    except Exception as e:
        return False, str(e)


def _download(
    target: str, out_dir: str, safe: str, allow_cookies: bool, log=None, embed: bool = True,
    original: bool = False,
) -> tuple[bool, str]:
    _LAST_INFO.clear()
    if yt_dlp is None:
        original = False  # keeping the original audio needs the yt-dlp module
    base = _run_ytdlp_module if yt_dlp is not None else _run_ytdlp_cli
    runner = lambda t, o, sf, ck, em: base(t, o, sf, ck, em, original)
    ok, err = runner(target, out_dir, safe, False, embed)
    if not ok and not STOP_EVENT.is_set() and any(k in err.lower() for k in ("403", "forbidden")):
        time.sleep(3)  # YouTube sometimes refuses one request; try once more
        ok, err = runner(target, out_dir, safe, False, embed)
    if not ok and not STOP_EVENT.is_set() and allow_cookies and any(
        k in err.lower() for k in ("sign in", "bot", "cookie", "age", "confirm", "403", "forbidden")
    ):
        ok, err2 = runner(target, out_dir, safe, True, embed)
        err = err2 or err
    if STOP_EVENT.is_set() and not ok:
        _cleanup_partial(out_dir, safe)
        err = "stopped"
    elif ok and embed and not original:
        _ensure_cover(out_dir, safe, _LAST_INFO.get("info"), log)
    return ok, err


def _already_exists(out_dir: str, safe: str) -> bool:
    return any(
        os.path.exists(os.path.join(out_dir, f"{safe}.{ext}"))
        for ext in ("mp3", "m4a", "flac", "webm", "opus")
    )


# \u2500\u2500 Tags & cover art \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

def meta_for(song: str) -> dict:
    """Tag info for a song: full Spotify details if we have them, else parsed from 'Artist - Title'."""
    if song in SONG_META:
        return SONG_META[song]
    artist, title = _split_query(song)
    meta = {"title": title}
    if artist:
        meta["artist"] = artist
    return meta


def _square_jpg(src: str) -> str | None:
    """Centre-crop an image to a square (YouTube thumbnails are 16:9 with black bars)."""
    dst = os.path.join(tempfile.gettempdir(), f"sevby_square_{os.getpid()}.jpg")
    r = _run_ff(["-y", "-i", src, "-vf", "crop=min(iw\\,ih):min(iw\\,ih)", "-q:v", "2", dst])
    return dst if r is not None and r.returncode == 0 and os.path.isfile(dst) else None


def _fetch_image(url: str) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": SIMPLE_UA})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read()
    except Exception:
        return None
    if len(data) < 1000:
        return None
    ext = ".png" if data[:8] == b"\x89PNG\r\n\x1a\n" else (".webp" if data[:4] == b"RIFF" else ".jpg")
    tmp = os.path.join(tempfile.gettempdir(), f"sevby_cover_dl_{os.getpid()}{ext}")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
    except OSError:
        return None
    return _to_jpg(tmp)


def _apply_tags(mp3: str, meta: dict, cover: str | None = None, wipe: bool = False) -> bool:
    """Write ID3 tags (and optionally a cover) into an MP3 without re-encoding."""
    tmp = mp3[:-4] + ".sevby-tmp.mp3"
    args = ["-y", "-i", mp3]
    if cover:
        args += ["-i", cover, "-map", "0:a", "-map", "1:v"]
    else:
        args += ["-map", "0"]  # keep the audio and any cover already inside
    args += ["-c", "copy", "-id3v2_version", "3", "-write_id3v1", "1"]
    if wipe:
        args += ["-map_metadata", "-1"]  # drop yt-dlp / YouTube junk tags
    track = meta.get("track") or 0
    tags = {
        "title": meta.get("title"),
        "artist": meta.get("artist"),
        "album_artist": meta.get("album_artist") or meta.get("artist"),
        "album": meta.get("album"),
        "date": meta.get("year"),
        "track": (f"{track}/{meta['track_total']}" if track and meta.get("track_total")
                  else (str(track) if track else None)),
    }
    for k, v in tags.items():
        if v:
            args += ["-metadata", f"{k}={v}"]
    if cover:
        args += ["-metadata:s:v", "title=Album cover", "-metadata:s:v", "comment=Cover (front)"]
    args.append(tmp)
    r = _run_ff(args)
    if r is not None and r.returncode == 0 and os.path.isfile(tmp):
        try:
            os.replace(tmp, mp3)
            return True
        except OSError:
            pass
    try:
        if os.path.exists(tmp):
            os.remove(tmp)
    except OSError:
        pass
    return False


def _apply_tags_m4a(path: str, meta: dict, cover: str | None = None) -> bool:
    """Write MP4/M4A tags (and optionally a cover) without re-encoding the audio."""
    tmp = path[:-4] + ".sevby-tmp.m4a"
    args = ["-y", "-i", path]
    if cover:
        args += ["-i", cover, "-map", "0:a", "-map", "1:v", "-disposition:v:0", "attached_pic"]
    else:
        args += ["-map", "0:a"]
    args += ["-c", "copy", "-map_metadata", "-1"]  # drop YouTube junk tags
    track = meta.get("track") or 0
    tags = {
        "title": meta.get("title"),
        "artist": meta.get("artist"),
        "album_artist": meta.get("album_artist") or meta.get("artist"),
        "album": meta.get("album"),
        "date": meta.get("year"),
        "track": (f"{track}/{meta['track_total']}" if track and meta.get("track_total")
                  else (str(track) if track else None)),
    }
    for k, v in tags.items():
        if v:
            args += ["-metadata", f"{k}={v}"]
    args.append(tmp)
    r = _run_ff(args)
    if r is not None and r.returncode == 0 and os.path.isfile(tmp):
        try:
            os.replace(tmp, path)
            return True
        except OSError:
            pass
    try:
        if os.path.exists(tmp):
            os.remove(tmp)
    except OSError:
        pass
    return False


def _finalize_original(out_dir: str, safe: str, info: dict | None, meta: dict, log) -> str:
    """Tag a YouTube download that kept its original audio. Returns a short quality label.
    M4A is tagged as-is. Anything else (Opus/WebM) is converted to MP3 like the standard mode."""
    src = None
    for ext in ("m4a", "mp4", "webm", "opus", "ogg", "mp3"):
        cand = os.path.join(out_dir, f"{safe}.{ext}")
        if os.path.isfile(cand):
            src = cand
            break
    if not src:
        return "?"
    abr = (info or {}).get("abr")
    kbps = f" ~{int(round(abr))} kbps" if abr else ""
    ext = src.rsplit(".", 1)[-1].lower()
    if ext in ("m4a", "mp4"):
        if ext == "mp4":
            new = os.path.join(out_dir, f"{safe}.m4a")
            try:
                os.replace(src, new)
                src = new
            except OSError:
                pass
        try:
            cover = _fetch_image(meta["cover_url"]) if meta.get("cover_url") else None
            if not cover:
                raw = _find_or_fetch_cover(out_dir, safe, info)
                cover = _square_jpg(raw) if raw else None
            if not _apply_tags_m4a(src, meta, cover):
                log("  Note: couldn't write tags for this track.")
            elif not cover:
                log("  Note: no cover art could be found for this track.")
        finally:
            _remove_loose_images(out_dir, safe)
        return f"YouTube original audio (M4A/AAC{kbps})"
    # Not M4A: convert to MP3 so tags and cover work everywhere
    mp3 = os.path.join(out_dir, f"{safe}.mp3")
    r = _run_ff(["-y", "-i", src, "-vn", "-c:a", "libmp3lame", "-q:a", "0", mp3])
    if r is not None and r.returncode == 0 and os.path.isfile(mp3):
        try:
            os.remove(src)
        except OSError:
            pass
        _finalize_youtube(out_dir, safe, info, meta, log)
        return "YouTube, converted to MP3 (no M4A version was available)"
    return "YouTube (kept as " + ext + ", could not convert)"


def _remove_loose_images(out_dir: str, safe: str) -> None:
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        p = os.path.join(out_dir, safe + ext)
        if os.path.isfile(p):
            try:
                os.remove(p)
            except OSError:
                pass


def _finalize_youtube(out_dir: str, safe: str, info: dict | None, meta: dict, log) -> None:
    """Give a YouTube download proper tags and a square cover (Spotify's if we have it)."""
    mp3 = os.path.join(out_dir, f"{safe}.mp3")
    if not os.path.isfile(mp3):
        return
    try:
        cover = _fetch_image(meta["cover_url"]) if meta.get("cover_url") else None
        if not cover:
            raw = _find_or_fetch_cover(out_dir, safe, info)
            cover = _square_jpg(raw) if raw else None
        if not _apply_tags(mp3, meta, cover, wipe=True):
            log("  Note: couldn't write tags for this track.")
        elif not cover:
            log("  Note: no cover art could be found for this track.")
    finally:
        _remove_loose_images(out_dir, safe)


# \u2500\u2500 YouTube matching \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

_UNWANTED = (
    "live", "remix", "cover", "karaoke", "instrumental", "sped up", "slowed", "reverb",
    "nightcore", "8d", "bass boosted", "mashup", "reaction", "acoustic", "tribute",
    "extended", "1 hour", "10 hours", "full album", "loop",
)


def _words(s: str) -> list[str]:
    return re.findall(r"\w+", (s or "").lower())


def score_youtube_result(entry: dict, query: str, meta: dict, index: int = 0) -> float:
    """Higher = more likely the right recording. Uses length, title, channel, and 'bad version' words."""
    title = (entry.get("title") or "")
    chan = (entry.get("channel") or entry.get("uploader") or "")
    t_low = title.lower()
    wanted = " ".join([query, meta.get("title") or "", meta.get("artist") or ""]).lower()
    score = -0.5 * index

    # Length vs. the real track length (Spotify) - the strongest signal
    target = meta.get("duration") or 0
    dur = entry.get("duration")
    if not dur:
        score -= 20  # live streams / unknown length
    elif target:
        diff = abs(dur - target)
        score += 40 if diff <= 3 else 28 if diff <= 8 else 12 if diff <= 15 else -15 if diff <= 40 else -45
    else:
        score += -25 if dur > 600 else (-10 if dur < 60 else 0)

    # Title / artist words
    song_title = meta.get("title") or _split_query(query)[1]
    tw = set(_words(song_title))
    if tw:
        score += 25 * len(tw & set(_words(title))) / len(tw)
    aw = set(_words(meta.get("artist") or _split_query(query)[0]))
    if aw:
        score += 15 * len(aw & set(_words(title + " " + chan))) / len(aw)

    # Official-ish sources
    if chan.lower().endswith("- topic"):
        score += 20
    if "official audio" in t_low:
        score += 6
    elif "official" in t_low:
        score += 2

    # Wrong versions (unless the user actually asked for them)
    penalty = 0
    for w in _UNWANTED:
        if re.search(r"\b" + re.escape(w) + r"\b", t_low) and not re.search(
            r"\b" + re.escape(w) + r"\b", wanted
        ):
            penalty += 30
    score -= min(penalty, 60)
    return score


def rank_youtube_results(entries: list[dict], query: str, meta: dict) -> list[tuple[float, dict]]:
    scored = [(score_youtube_result(e, query, meta, i), e) for i, e in enumerate(entries)]
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def search_youtube_candidates(query: str) -> list[dict]:
    """Top YouTube search results (title, length, channel) without downloading anything."""
    if yt_dlp is None:
        return []
    opts = {
        "quiet": True, "no_warnings": True, "logger": _QuietLogger(), "no_color": True,
        "extract_flat": True, "skip_download": True, "socket_timeout": 30,
    }
    for attempt in range(2):
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(f"ytsearch8:{query}", download=False)
            return [e for e in (info or {}).get("entries") or [] if e and (e.get("id") or e.get("url"))]
        except Exception:
            if STOP_EVENT.is_set():
                return []
            time.sleep(2)
    return []


def _fmt_len(sec) -> str:
    try:
        sec = int(sec)
        return f"{sec // 60}:{sec % 60:02d}"
    except Exception:
        return "?:??"


def download_bandcamp_mp3(bc_url: str, query: str, out_dir: str, log) -> bool:
    """Download a Bandcamp track page as MP3. Returns True on success."""
    safe = sanitize_filename(query)
    if _already_exists(out_dir, safe):
        log(f"  SKIP (exists): {safe}")
        return True
    ok, err = _download(bc_url, out_dir, safe, allow_cookies=False, log=log)
    if ok:
        meta = SONG_META.get(query)
        if meta and meta.get("album"):  # Spotify knows this track: make the tags consistent
            _apply_tags(os.path.join(out_dir, f"{safe}.mp3"), meta, None, wipe=False)
        log(f"  OK (Bandcamp) \u2192 {safe}.mp3" + ("  [Bandcamp stream, 128 kbps MP3]" if QUALITY["best"] else ""))
    elif err == "stopped":
        log("  Stopped.")
    else:
        log(f"  Bandcamp download failed: {err}")
    return ok


def download_youtube_mp3(query: str, out_dir: str, log, use_cookies: bool = True) -> bool:
    safe = sanitize_filename(query)
    if _already_exists(out_dir, safe):
        log(f"  SKIP (exists): {safe}")
        return True
    meta = meta_for(query)

    # Pick the best-matching video (right length, not a live/remix version) instead of the first hit.
    targets: list[str] = []
    ranked = rank_youtube_results(search_youtube_candidates(query), query, meta)
    for score, entry in ranked[:2]:
        url = entry.get("url") or ""
        if not url.startswith("http"):
            url = f"https://www.youtube.com/watch?v={entry.get('id')}"
        if not targets:
            log(
                f"  Match: {entry.get('title')} [{_fmt_len(entry.get('duration'))}]"
                + (f" \u2014 {entry.get('channel') or entry.get('uploader')}" if (entry.get('channel') or entry.get('uploader')) else "")
                + ("" if score >= 20 else "  (best guess)")
            )
        targets.append(url)
    targets.append(f"ytsearch1:{query}")  # last resort

    original = bool(QUALITY["best"]) and yt_dlp is not None
    ok, err = False, "no result"
    for target in targets:
        ok, err = _download(target, out_dir, safe, allow_cookies=use_cookies, log=log, embed=False,
                            original=original)
        if ok or STOP_EVENT.is_set():
            break
    if ok and original:
        label = _finalize_original(out_dir, safe, _LAST_INFO.get("info"), meta, log)
        final = next((f"{safe}.{e}" for e in ("m4a", "mp3") if os.path.isfile(os.path.join(out_dir, f"{safe}.{e}"))), safe)
        log(f"  OK \u2192 {final}  [{label}]")
        HQ_SONGS.append(query)
    elif ok:
        _finalize_youtube(out_dir, safe, _LAST_INFO.get("info"), meta, log)
        log(f"  OK \u2192 {safe}.mp3")
        HQ_SONGS.append(query)
    elif err == "stopped":
        log("  Stopped.")
    else:
        log(f"  FAIL: {err}")
        if "403" in err or "forbidden" in err.lower():
            log("  Tip: YouTube changes often \u2014 update yt-dlp (pip install -U yt-dlp) and rebuild SEVBY.")
    return ok


# -- Free & legal lossless sources: Jamendo and Internet Archive (Creative Commons only) ----------

_FREE_UA = "SEVBY-HQ/" + VERSION + " (https://github.com/Obamna1234/sevby-hq)"


def _norm(t: str) -> str:
    """Lower-case a title and drop brackets, 'feat.' parts and punctuation, for comparing."""
    t = (t or "").lower()
    t = re.sub(r"[\(\[\{].*?[\)\]\}]", " ", t)
    t = re.sub(r"\b(feat|ft|featuring)\b.*$", " ", t)
    return " ".join(re.findall(r"\w+", t))


def _same_song(c_title: str, c_artist: str, c_dur, title: str, artist: str, dur) -> bool:
    """Strict check so a wrong song is never taken: same title, same artist, similar length."""
    if not _norm(c_title) or _norm(c_title) != _norm(title):
        return False
    if artist:
        aw = set(_words(artist))
        cw = set(_words(c_artist))
        if not aw or len(aw & cw) < max(1, int(len(aw) * 0.6 + 0.5)):
            return False
    try:
        if dur and c_dur and abs(float(c_dur) - float(dur)) > 6:
            return False
    except (TypeError, ValueError):
        pass
    return True


def _http_get(url: str, timeout: int = 20) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _FREE_UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception:
        return None


def _http_json(url: str):
    data = _http_get(url)
    if not data:
        return None
    try:
        return json.loads(data.decode("utf-8", errors="replace"))
    except ValueError:
        return None


def _http_save(url: str, dest: str, timeout: int = 60) -> bool:
    """Stream a file to disk (stoppable). Returns True if something was written."""
    tmp = dest + ".part"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _FREE_UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(tmp, "wb") as f:
            while True:
                if STOP_EVENT.is_set():
                    raise _Stopped()
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
        os.replace(tmp, dest)
        return True
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


def _audio_kind(path: str) -> str | None:
    """'flac' or 'mp3' if the file really is one (guards against HTML error pages / zips)."""
    try:
        with open(path, "rb") as f:
            head = f.read(4)
        size = os.path.getsize(path)
    except OSError:
        return None
    if size < 50_000:
        return None
    if head == b"fLaC":
        return "flac"
    if head[:3] == b"ID3" or (head[:1] == b"\xff" and (head[1] & 0xE0) == 0xE0):
        return "mp3"
    return None


def search_jamendo(title: str, artist: str, dur, client_id: str) -> dict | None:
    if not client_id:
        return None
    q = urllib.parse.urlencode({
        "client_id": client_id, "format": "json", "limit": 8, "namesearch": title,
        "audiodownload_allowed": "true", "imagesize": 600,
    })
    data = _http_json("https://api.jamendo.com/v3.0/tracks/?" + q)
    for t in (data or {}).get("results") or []:
        if not t.get("audiodownload_allowed", True):
            continue
        if not _same_song(t.get("name", ""), t.get("artist_name", ""), t.get("duration"), title, artist, dur):
            continue
        tid = t.get("id")
        if not tid or t.get("audiodownload_allowed") is False:
            continue
        # Documented endpoint: redirects to the file; 404 if the artist disabled downloads.
        base = "https://api.jamendo.com/v3.0/tracks/file/?" + urllib.parse.urlencode(
            {"client_id": client_id, "id": tid, "action": "download"})
        urls = [("FLAC", base + "&audioformat=flac"), ("MP3 (V0)", base + "&audioformat=mp32")]
        year = (t.get("releasedate") or "")[:4]
        return {"source": "Jamendo", "page": f"https://www.jamendo.com/track/{tid}", "urls": urls, "album": t.get("album_name"), "year": year or None,
                "cover_url": t.get("album_image") or t.get("image"), "title": t.get("name")}
    return None


def search_archive(title: str, artist: str, dur) -> dict | None:
    """Creative-Commons-licensed audio on the Internet Archive with a real FLAC/320k+ file."""
    if not artist:
        return None
    q = (f'creator:("{artist}") AND mediatype:audio AND licenseurl:(*creativecommons*)')
    params = urllib.parse.urlencode({"q": q, "fl[]": ["identifier", "title", "creator"],
                                     "rows": 6, "output": "json"}, doseq=True)
    data = _http_json("https://archive.org/advancedsearch.php?" + params)
    docs = ((data or {}).get("response") or {}).get("docs") or []
    for d in docs[:4]:
        ident = d.get("identifier")
        meta = _http_json(f"https://archive.org/metadata/{urllib.parse.quote(ident or '')}") if ident else None
        if not meta:
            continue
        best = None
        for f in meta.get("files") or []:
            fmt = (f.get("format") or "").lower()
            name = f.get("name") or ""
            if fmt in ("flac", "24bit flac"):
                rank = 3
            elif fmt in ("vbr mp3", "mp3") and str(f.get("bitrate") or "0").isdigit() and int(str(f.get("bitrate") or "0")) >= 192:
                rank = 2
            else:
                continue
            ftitle = f.get("title") or os.path.splitext(os.path.basename(name))[0]
            if not _same_song(ftitle, f.get("creator") or d.get("creator") or artist, f.get("length"), title, artist, dur):
                continue
            if best is None or rank > best[0]:
                best = (rank, name, fmt)
        if best:
            # Cover: a front-cover style image in the item, else the item's own thumbnail.
            imgs = [(f.get("name") or "") for f in meta.get("files") or []
                    if (f.get("format") or "").lower() in ("jpeg", "png")
                    and "thumb" not in (f.get("name") or "").lower() and "/" not in (f.get("name") or "")]
            pick = next((n for n in imgs if re.search(r"cover|front|folder|artwork|album", n, re.I)), None) \
                or (imgs[0] if imgs else None)
            cover_url = (f"https://archive.org/download/{urllib.parse.quote(ident)}/{urllib.parse.quote(pick)}"
                         if pick else f"https://archive.org/services/img/{urllib.parse.quote(ident)}")
            url = f"https://archive.org/download/{urllib.parse.quote(ident)}/{urllib.parse.quote(best[1])}"
            label = "FLAC" if best[0] == 3 else "MP3 (192k+)"
            return {"source": "Internet Archive", "urls": [(label, url)], "album": meta.get("metadata", {}).get("title"),
                    "year": None, "cover_url": cover_url, "title": title}
    return None


def _apply_tags_flac(path: str, meta: dict, cover: str | None) -> bool:
    """Tag a FLAC without re-encoding (Vorbis comments + optional front cover)."""
    tmp = path[:-5] + ".sevby-tmp.flac"
    args = ["-y", "-i", path]
    if cover:
        args += ["-i", cover, "-map", "0:a", "-map", "1:v", "-disposition:v:0", "attached_pic"]
    else:
        args += ["-map", "0:a"]
    args += ["-c", "copy", "-map_metadata", "-1"]
    track = meta.get("track") or 0
    tags = {"title": meta.get("title"), "artist": meta.get("artist"),
            "album_artist": meta.get("album_artist") or meta.get("artist"),
            "album": meta.get("album"), "date": meta.get("year"), "track": str(track) if track else None}
    for k, v in tags.items():
        if v:
            args += ["-metadata", f"{k}={v}"]
    args.append(tmp)
    r = _run_ff(args)
    if r is not None and r.returncode == 0 and os.path.isfile(tmp):
        try:
            os.replace(tmp, path)
            return True
        except OSError:
            pass
    try:
        if os.path.exists(tmp):
            os.remove(tmp)
    except OSError:
        pass
    return False


def download_free_hq(query: str, out_dir: str, log) -> bool:
    """Try Jamendo, then the Internet Archive, for a lossless (or 192k+) copy under a Creative Commons
    licence. Returns True if a file was saved. Never raises; on any doubt it returns False and the
    normal Bandcamp / YouTube path takes over."""
    try:
        safe = sanitize_filename(query)
        if _already_exists(out_dir, safe):
            log(f"  SKIP (exists): {safe}")
            return True
        meta = dict(meta_for(query))
        title = meta.get("title") or ""
        artist = meta.get("artist") or ""
        if not title:
            return False
        dur = meta.get("duration")
        cands = []
        c = search_jamendo(title, artist, dur, QUALITY.get("jamendo_id") or "")
        if c:
            cands.append(c)
        if not STOP_EVENT.is_set():
            c = search_archive(title, artist, dur)
            if c:
                cands.append(c)
        for cand in cands:
            for label, url in cand["urls"]:
                if STOP_EVENT.is_set():
                    return False
                dest = os.path.join(out_dir, safe + ".dl")
                if not _http_save(url, dest):
                    continue
                kind = _audio_kind(dest)
                if not kind:
                    try:
                        os.remove(dest)
                    except OSError:
                        pass
                    continue
                final = os.path.join(out_dir, f"{safe}.{kind}")
                os.replace(dest, final)
                meta.setdefault("album", cand.get("album"))
                if not meta.get("album") and cand.get("album"):
                    meta["album"] = cand["album"]
                if cand.get("year") and not meta.get("year"):
                    meta["year"] = cand["year"]
                cover = None
                try:
                    url_c = meta.get("cover_url") or cand.get("cover_url")
                    raw = _fetch_image(url_c) if url_c else None
                    cover = _square_jpg(raw) if raw else None
                except Exception:
                    cover = None
                tagged = _apply_tags_flac(final, meta, cover) if kind == "flac" else _apply_tags(final, meta, cover, wipe=True)
                if not tagged:
                    log("  Note: couldn't write tags for this track.")
                elif not cover:
                    log("  Note: no cover art could be found for this track.")
                real = "FLAC" if kind == "flac" else "MP3 (V0 / high bitrate)"
                log(f"  OK ({cand['source']}) -> {safe}.{kind}  [{cand['source']}, {real}, Creative Commons]")
                if cand.get("page"):
                    log(f"  Credit: this track is on Jamendo - {cand['page']}")
                return True
        return False
    except _Stopped:
        return False
    except Exception as e:
        log(f"  (free-source check skipped: {e})")
        return False



# \u2500\u2500 GUI base (with optional drag-and-drop) \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

if _HAS_DND:

    class _SevbyBase(ctk.CTk, TkinterDnD.DnDWrapper):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.TkdndVersion = TkinterDnD._require(self)

else:

    class _SevbyBase(ctk.CTk):
        pass


class SevbyApp(_SevbyBase):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} v{VERSION}: Playlist to MP3")
        self._apply_icon(self)
        self.minsize(580, 720)
        self._center_window(680, 800)

        cfg = load_config()
        self._total_steps = 0
        self._done_steps = 0
        self.run_mode: str | None = None  # which mode the running job belongs to (picks its log box)
        self._job: dict | None = None
        self.failed: list[str] = []
        self.queue: list[dict] = []
        self._loaded_name = ""  # name of the .txt file last loaded (used to name its queue folder)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Header
        ctk.CTkLabel(self, text="SEVBY HQ", font=ctk.CTkFont(size=28, weight="bold")).pack(
            pady=(16, 2)
        )
        ctk.CTkLabel(
            self,
            text="Bandcamp first \u00b7 YouTube for the rest",
            font=ctk.CTkFont(size=12),
            text_color="gray70",
        ).pack(pady=(0, 12))

        # Mode switch
        self.mode = ctk.StringVar(value=cfg.get("last_mode") if cfg.get("last_mode") in ("txt", "spotify") else "txt")
        mode_frame = ctk.CTkFrame(self, fg_color="transparent")
        mode_frame.pack(fill="x", padx=28)
        ctk.CTkRadioButton(
            mode_frame, text="Song list / .txt file", variable=self.mode, value="txt",
            command=self._switch_mode
        ).pack(side="left", padx=(0, 20))
        ctk.CTkRadioButton(
            mode_frame, text="Spotify playlist link", variable=self.mode, value="spotify",
            command=self._switch_mode
        ).pack(side="left")

        # --- TXT mode ---
        self.txt_frame = ctk.CTkFrame(self)
        self.txt_frame.pack(fill="x", padx=28, pady=(12, 0))

        ctk.CTkLabel(
            self.txt_frame,
            text="Paste songs (Artist - Title), load a file (.txt, .csv, .m3u), or drop it here:",
        ).pack(anchor="w", padx=12, pady=(10, 4))
        self.songs_box = ctk.CTkTextbox(self.txt_frame, height=110)
        self.songs_box.pack(fill="x", padx=12, pady=4)
        btn_row = ctk.CTkFrame(self.txt_frame, fg_color="transparent")
        btn_row.pack(fill="x", padx=12, pady=(4, 10))
        ctk.CTkButton(btn_row, text="Load .txt file", width=140, command=self.load_txt).pack(
            side="left"
        )
        ctk.CTkButton(
            btn_row,
            text="Open Chosic (export playlist)",
            width=180,
            command=self.open_chosic,
        ).pack(side="left", padx=(10, 0))
        if _HAS_DND:
            ctk.CTkLabel(
                btn_row,
                text="Drop .txt anywhere on this window",
                text_color="gray60",
                font=ctk.CTkFont(size=11),
            ).pack(side="right")

        # --- Spotify mode ---
        self.sp_frame = ctk.CTkFrame(self)

        ctk.CTkLabel(
            self.sp_frame,
            text="Public & private playlists \u00b7 one-time login saved on this PC",
            text_color="gray70",
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=12, pady=(10, 2))

        ctk.CTkLabel(
            self.sp_frame,
            text="Playlist URL  (paste or drop a Spotify link)",
        ).pack(anchor="w", padx=12, pady=(6, 2))
        self.sp_url = ctk.CTkEntry(
            self.sp_frame, placeholder_text="https://open.spotify.com/playlist/..."
        )
        self.sp_url.pack(fill="x", padx=12, pady=2)

        # Client ID row + info button
        key_row = ctk.CTkFrame(self.sp_frame, fg_color="transparent")
        key_row.pack(fill="x", padx=12, pady=(8, 2))
        ctk.CTkLabel(key_row, text="Client ID").pack(side="left")
        ctk.CTkButton(
            key_row,
            text="What is this?",
            width=100,
            height=24,
            fg_color="transparent",
            border_width=1,
            command=self.show_client_id_help,
        ).pack(side="left", padx=(10, 0))
        ctk.CTkButton(
            key_row,
            text="Open Spotify Dashboard",
            width=160,
            height=24,
            command=self.open_spotify_dashboard,
        ).pack(side="left", padx=(8, 0))

        self.client_id = ctk.CTkEntry(self.sp_frame, placeholder_text="Client ID")
        self.client_id.pack(fill="x", padx=12, pady=2)
        self.client_secret = ctk.CTkEntry(
            self.sp_frame, placeholder_text="Client Secret (optional, not needed)", show="\u2022"
        )
        self.client_secret.pack(fill="x", padx=12, pady=2)

        # Chosic helper
        chosic_row = ctk.CTkFrame(self.sp_frame, fg_color="transparent")
        chosic_row.pack(fill="x", padx=12, pady=(8, 10))
        ctk.CTkLabel(
            chosic_row,
            text="No keys? Export a .txt with Chosic instead:",
            text_color="gray70",
            font=ctk.CTkFont(size=11),
        ).pack(side="left")
        ctk.CTkButton(
            chosic_row,
            text="Open Chosic",
            width=110,
            height=28,
            command=self.open_chosic,
        ).pack(side="right")

        if cfg.get("client_id"):
            self.client_id.insert(0, cfg["client_id"])
        if cfg.get("client_secret"):
            self.client_secret.insert(0, cfg["client_secret"])

        # Output folder
        self.folder_label = ctk.CTkLabel(self, text="Save folder")
        self.folder_label.pack(anchor="w", padx=28, pady=(14, 2))
        folder_row = ctk.CTkFrame(self, fg_color="transparent")
        folder_row.pack(fill="x", padx=28)
        self.folder_entry = ctk.CTkEntry(folder_row, placeholder_text="Choose where your MP3s will be saved")
        self.folder_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ctk.CTkButton(folder_row, text="Browse", width=100, command=self.browse_folder).pack(
            side="right"
        )

        if cfg.get("last_folder") and os.path.isdir(cfg["last_folder"]):
            self.folder_entry.insert(0, cfg["last_folder"])

        # Where to download from
        src_row = ctk.CTkFrame(self, fg_color="transparent")
        src_row.pack(fill="x", padx=28, pady=(12, 0))
        ctk.CTkLabel(src_row, text="Download from").pack(side="left", padx=(0, 10))
        self.SOURCES = ("Both", "Bandcamp only", "YouTube only")
        self.source = ctk.StringVar(
            value=cfg.get("source") if cfg.get("source") in self.SOURCES else "Both"
        )
        ctk.CTkSegmentedButton(
            src_row, values=list(self.SOURCES), variable=self.source,
            command=lambda _v: self._save_prefs(),
        ).pack(side="left")

        # Audio quality
        q_row = ctk.CTkFrame(self, fg_color="transparent")
        q_row.pack(fill="x", padx=28, pady=(10, 0))
        ctk.CTkLabel(q_row, text="YouTube audio").pack(side="left", padx=(0, 10))
        self.QUALITIES = ("Best available (M4A)", "Standard (MP3)")
        self.quality = ctk.StringVar(
            value=cfg.get("quality") if cfg.get("quality") in self.QUALITIES else self.QUALITIES[0]
        )
        ctk.CTkSegmentedButton(
            q_row, values=list(self.QUALITIES), variable=self.quality,
            command=lambda _v: self._save_prefs(),
        ).pack(side="left")

        # Free lossless sources
        f_row = ctk.CTkFrame(self, fg_color="transparent")
        f_row.pack(fill="x", padx=28, pady=(10, 0))
        self.free_hq = ctk.BooleanVar(value=bool(cfg.get("free_hq", True)))
        ctk.CTkCheckBox(
            f_row, text="Check Jamendo + Internet Archive first (free, Creative Commons, lossless when available)",
            variable=self.free_hq, command=self._save_prefs,
        ).pack(side="left")
        j_row = ctk.CTkFrame(self, fg_color="transparent")
        j_row.pack(fill="x", padx=28, pady=(6, 0))
        ctk.CTkLabel(j_row, text="Your own Jamendo Client ID (optional)").pack(side="left", padx=(0, 10))
        self.jamendo_id = ctk.CTkEntry(j_row, placeholder_text="from devportal.jamendo.com", width=240)
        self.jamendo_id.pack(side="left")
        if cfg.get("jamendo_id"):
            self.jamendo_id.insert(0, cfg["jamendo_id"])
        self.jamendo_id.bind("<FocusOut>", lambda _e: self._save_prefs())

        # Start
        action_row = ctk.CTkFrame(self, fg_color="transparent")
        action_row.pack(fill="x", padx=28, pady=(16, 8))
        self.start_btn = ctk.CTkButton(
            action_row,
            text="Start Download",
            height=42,
            font=ctk.CTkFont(size=15, weight="bold"),
            command=self.start,
        )
        self.start_btn.pack(side="left", fill="x", expand=True)
        self.stop_btn = ctk.CTkButton(
            action_row,
            text="Stop",
            width=80,
            height=42,
            fg_color="#8b2e2e",
            hover_color="#a33a3a",
            state="disabled",
            command=self.stop,
        )
        self.stop_btn.pack(side="right", padx=(8, 0))
        self.pause_btn = ctk.CTkButton(
            action_row,
            text="Pause",
            width=80,
            height=42,
            state="disabled",
            command=self.toggle_pause,
        )
        self.pause_btn.pack(side="right", padx=(8, 0))

        # Progress
        prog_row = ctk.CTkFrame(self, fg_color="transparent")
        prog_row.pack(fill="x", padx=28, pady=(0, 6))
        self.progress = ctk.CTkProgressBar(prog_row, height=10)
        self.progress.pack(side="left", fill="x", expand=True, padx=(0, 10))
        # CustomTkinter always draws a small rounded "dot" of fill at 0%; hide it by
        # painting the fill the same colour as the track until there is real progress.
        self._bar_fill = self.progress.cget("progress_color")
        self._bar_track = self.progress.cget("fg_color")
        self.progress.configure(progress_color=self._bar_track)
        self.progress.set(0)
        self.progress_label = ctk.CTkLabel(
            prog_row, text="Ready", width=90, anchor="e", text_color="gray70"
        )
        self.progress_label.pack(side="right")

        # Open folder / retry
        util_row = ctk.CTkFrame(self, fg_color="transparent")
        util_row.pack(fill="x", padx=28, pady=(0, 8))
        ctk.CTkButton(
            util_row, text="Open folder", width=110, height=28,
            fg_color="transparent", border_width=1, command=self.open_folder,
        ).pack(side="left")
        self.retry_btn = ctk.CTkButton(
            util_row, text="Retry failed", width=140, height=28,
            state="disabled", command=self.retry_failed,
        )
        self.retry_btn.pack(side="left", padx=(8, 0))
        self.clear_btn = ctk.CTkButton(
            util_row, text="Clear queue", width=100, height=28,
            fg_color="transparent", border_width=1, state="disabled", command=self.clear_queue,
        )
        self.clear_btn.pack(side="right")
        self.add_btn = ctk.CTkButton(
            util_row, text="Add to queue", width=110, height=28, command=self.add_to_queue,
        )
        self.add_btn.pack(side="right", padx=(0, 8))
        self.queue_label = ctk.CTkLabel(
            self, text="", anchor="w", justify="left", text_color="gray70", wraplength=600,
        )  # shown only while the queue has items

        # Logs: one per mode so .txt runs and Spotify runs don't get mixed together
        self.log_holder = ctk.CTkFrame(self, fg_color="transparent")
        self.log_holder.pack(fill="both", expand=True, padx=28, pady=(0, 16))
        self.log_boxes = {
            "txt": ctk.CTkTextbox(self.log_holder, height=150, state="disabled"),
            "spotify": ctk.CTkTextbox(self.log_holder, height=150, state="disabled"),
        }

        self._switch_mode()
        self._setup_drag_drop()

    def _switch_mode(self):
        # Always insert the input panel just above "Save folder" so the layout never shuffles.
        self.sp_frame.pack_forget()
        self.txt_frame.pack_forget()
        mode = self.mode.get()
        frame = self.txt_frame if mode == "txt" else self.sp_frame
        frame.pack(fill="x", padx=28, pady=(12, 0), before=self.folder_label)
        for box in self.log_boxes.values():
            box.pack_forget()
        self.log_boxes[mode].pack(fill="both", expand=True)
        self._save_prefs()

    def _save_prefs(self):
        try:
            cfg = load_config()
            cfg["last_mode"] = self.mode.get()
            cfg["source"] = self.source.get()
            cfg["quality"] = self.quality.get()
            cfg["free_hq"] = bool(self.free_hq.get())
            cfg["jamendo_id"] = self.jamendo_id.get().strip()
            folder = self.folder_entry.get().strip()
            if folder and os.path.isdir(folder):
                cfg["last_folder"] = folder
            save_config(cfg)
        except Exception:
            pass

    def _on_close(self):
        self._save_prefs()
        self.destroy()

    def log(self, msg: str):
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except OSError:
            pass

        box = self.log_boxes.get(self.run_mode or self.mode.get(), self.log_boxes["txt"])

        def _():
            box.configure(state="normal")
            box.insert("end", msg + "\n")
            try:
                # keep the on-screen log light for huge playlists (full log stays in the file)
                if int(box.index("end-1c").split(".")[0]) > 3000:
                    box.delete("1.0", "1000.0")
            except Exception:
                pass
            box.see("end")
            box.configure(state="disabled")
        self.after(0, _)

    def _info_box(self, title: str, msg: str):
        # tkinter dialogs must be opened from the main thread
        self.after(0, lambda: messagebox.showinfo(title, msg))

    def browse_folder(self):
        path = filedialog.askdirectory()
        if path:
            self.folder_entry.delete(0, "end")
            self.folder_entry.insert(0, path)
            self._save_prefs()

    def open_chosic(self):
        """Open Chosic playlist exporter in the default browser."""
        webbrowser.open("https://www.chosic.com/spotify-playlist-exporter/")
        self.log(
            "Opened Chosic in your browser.\n"
            "1) Paste your Spotify playlist link there\n"
            "2) Download / copy the song list as text\n"
            "3) Switch to \u201cSong list / .txt file\u201d in SEVBY and paste or load it"
        )

    def _apply_icon(self, win):
        """Replace CustomTkinter's default blue icon with ours (window + taskbar)."""
        if os.name != "nt":
            png = resource_path("sevby_logo_512.png")
            if os.path.isfile(png):
                try:
                    self._icon_img = tk.PhotoImage(file=png)
                    win.iconphoto(True, self._icon_img)
                except Exception:
                    pass
            return
        if not os.path.isfile(ICON_PATH):
            return

        def _set():
            try:
                win.iconbitmap(ICON_PATH)
            except Exception:
                pass

        _set()
        # CustomTkinter installs its own blue icon ~200 ms after the window opens; override it.
        win.after(300, _set)

    def _center_window(self, width: int, height: int):
        """Place the main window in the centre of the screen."""
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = max(0, (sw - width) // 2)
        y = max(0, (sh - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def show_client_id_help(self):
        """Centered help dialog over the main window."""
        dlg = ctk.CTkToplevel(self)
        dlg.title("What is the Client ID?")
        self._apply_icon(dlg)
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()

        w, h = 470, 480
        self.update_idletasks()
        px = self.winfo_rootx()
        py = self.winfo_rooty()
        pw = self.winfo_width()
        ph = self.winfo_height()
        x = px + max(0, (pw - w) // 2)
        y = py + max(0, (ph - h) // 2)
        dlg.geometry(f"{w}x{h}+{x}+{y}")

        frame = ctk.CTkFrame(dlg, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=18, pady=14)

        ctk.CTkLabel(
            frame,
            text="What is the Client ID?",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", pady=(0, 8))

        help_text = (
            "It's a code from Spotify that lets SEVBY read your\n"
            "playlists (public AND private). It is NOT your password.\n"
            "NOTE: Spotify now requires a Premium account to create one\n"
            "(the Web API option stays greyed out on free accounts).\n"
            "No Premium? Skip this: use Chosic below and Song list mode.\n\n"
            "How to get it (about 2 minutes, one time only):\n\n"
            "1. Click \u201cOpen Spotify Dashboard\u201d in SEVBY\n"
            "   (or go to developer.spotify.com/dashboard)\n"
            "2. Log in with your normal Spotify account\n"
            "3. Click \u201cCreate app\u201d\n"
            "   \u2022 App name: anything (e.g. SEVBY)\n"
            "   \u2022 Redirect URI: type exactly  http://127.0.0.1:8888\n"
            "   \u2022 Tick Web API \u2192 agree to the terms \u2192 Save\n"
            "4. Open the app \u2192 Settings \u2192 copy the Client ID\n"
            "5. Come back and press Start \u2014 SEVBY pastes it from your\n"
            "   clipboard (or paste it into the Client ID box yourself).\n"
            "   No Secret needed.\n\n"
            "The first time you press Start, your browser opens so you\n"
            "can click \u201cAgree\u201d. After that SEVBY remembers you.\n"
            "Saved only on this PC.\n\n"
            "Prefer not to? Click \u201cOpen Chosic\u201d, export a .txt,\n"
            "and use Song list mode \u2014 no keys needed."
        )
        ctk.CTkLabel(
            frame,
            text=help_text,
            justify="left",
            anchor="w",
            wraplength=420,
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", fill="x")

        ctk.CTkButton(frame, text="OK", width=100, command=dlg.destroy).pack(
            pady=(14, 0)
        )
        dlg.focus_set()

    def load_txt(self):
        # Windows needs separate patterns so .txt files actually appear
        path = filedialog.askopenfilename(
            title="Select song list",
            filetypes=[
                ("Song lists", "*.txt *.csv *.m3u *.m3u8"),
                ("Text files", "*.txt"),
                ("CSV files", "*.csv"),
                ("Playlists", "*.m3u *.m3u8"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        self._load_song_file(path)

    def _load_song_file(self, path: str):
        try:
            # tkdnd sometimes wraps paths in {braces}
            path = path.strip().strip("{}")
            songs = load_songs_from_file(path)
            self.songs_box.delete("1.0", "end")
            self.songs_box.insert("1.0", "\n".join(songs))
            self._loaded_name = Path(path).stem
            self.mode.set("txt")
            self._switch_mode()
            self.log(f"Loaded {len(songs)} songs from:\n  {path}")
        except Exception as e:
            messagebox.showerror("Error", f"Could not read file:\n{e}")

    def _setup_drag_drop(self):
        if not _HAS_DND:
            return

        def on_file_drop(event):
            data = event.data.strip()
            # May be one or more paths
            if data.startswith("{") and data.endswith("}"):
                data = data[1:-1]
            path = data.split("} {")[0].strip().strip("{}")
            lower = path.lower()
            if lower.endswith((".txt", ".csv", ".text")):
                self._load_song_file(path)
            elif "spotify.com" in lower or "spotify:" in lower:
                self.mode.set("spotify")
                self._switch_mode()
                self.sp_url.delete(0, "end")
                self.sp_url.insert(0, path)
                self.log(f"Spotify link dropped:\n  {path}")
            else:
                self.log(f"Dropped item not recognized (need .txt or Spotify link):\n  {path}")

        def on_text_drop(event):
            text = (event.data or "").strip()
            if "spotify.com" in text.lower() or text.lower().startswith("spotify:"):
                self.mode.set("spotify")
                self._switch_mode()
                self.sp_url.delete(0, "end")
                self.sp_url.insert(0, text.split()[0])
                self.log("Spotify link dropped into SEVBY.")
            elif text:
                # Treat as pasted song lines
                self.mode.set("txt")
                self._switch_mode()
                self.songs_box.delete("1.0", "end")
                self.songs_box.insert("1.0", text)
                n = len(parse_song_lines(text))
                self.log(f"Dropped text with {n} song line(s).")

        try:
            self.drop_target_register(DND_FILES, DND_TEXT)
            self.dnd_bind("<<Drop>>", on_file_drop)
            # Also bind text drops where supported
            self.dnd_bind("<<Drop:DND_Text>>", on_text_drop)
        except Exception:
            pass

    def set_progress(self, fraction: float, label: str = ""):
        fraction = max(0.0, min(1.0, fraction))

        def _():
            self.progress.configure(
                progress_color=self._bar_fill if fraction > 0.005 else self._bar_track
            )
            self.progress.set(fraction)
            if label:
                self.progress_label.configure(text=label)

        self.after(0, _)

    def start(self):
        if self.queue:  # a queue is waiting: run it (each item has its own settings)
            self._save_prefs()
            self._begin(list(self.queue))
            return
        out_dir = self.folder_entry.get().strip()
        if not out_dir or not os.path.isdir(out_dir):
            messagebox.showerror("Error", "Please choose a valid save folder.")
            return

        if self.mode.get() == "spotify" and not self.client_id.get().strip():
            try:
                clip = self.clipboard_get().strip()
            except Exception:
                clip = ""
            if re.fullmatch(r"[0-9a-fA-F]{32}", clip):
                self.client_id.insert(0, clip)
                self.log("Pasted your Client ID from the clipboard.")
            else:
                self._need_client_id()
                return

        mode = self.mode.get()
        job = {
            "mode": mode,
            "out_dir": out_dir,
            "source": self.source.get(),
            "quality": self.quality.get(),
            "free_hq": bool(self.free_hq.get()), "jamendo_id": self.jamendo_id.get().strip(),
            "raw": self.songs_box.get("1.0", "end") if mode == "txt" else "",
            "url": self.sp_url.get().strip(),
            "cid": self.client_id.get().strip(),
            "secret": self.client_secret.get().strip(),
            "songs": None,  # set when retrying failed songs
        }
        self._save_prefs()
        self._begin([job])

    def _begin(self, jobs: list):
        try:
            LOG_PATH.write_text("", encoding="utf-8")
        except OSError:
            pass
        self._job = jobs[0] if len(jobs) == 1 else None
        self.run_mode = jobs[0]["mode"]
        self._set_failed([])
        STOP_EVENT.clear()
        PAUSE_EVENT.clear()
        self.start_btn.configure(state="disabled", text="Working...")
        self.pause_btn.configure(state="normal", text="Pause")
        self.stop_btn.configure(state="normal")
        self.add_btn.configure(state="disabled")
        self.clear_btn.configure(state="disabled")
        self.set_progress(0, "Starting\u2026")
        threading.Thread(target=self._worker, args=(jobs,), daemon=True).start()

    # \u2500\u2500 Queue \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    def add_to_queue(self):
        """Snapshot what is on screen as one queue item, then clear the inputs for the next one."""
        out_dir = self.folder_entry.get().strip()
        if not out_dir or not os.path.isdir(out_dir):
            messagebox.showerror("Error", "Choose a save folder first \u2014 each queue item is saved "
                                          "in its own folder inside it.")
            return
        mode = self.mode.get()
        job = {
            "mode": mode, "out_dir": out_dir, "source": self.source.get(),
            "quality": self.quality.get(),
            "free_hq": bool(self.free_hq.get()), "jamendo_id": self.jamendo_id.get().strip(),
            "raw": "", "url": "", "cid": "", "secret": "", "songs": None,
        }
        if mode == "txt":
            raw = self.songs_box.get("1.0", "end")
            songs = parse_song_lines(raw)
            if not songs:
                messagebox.showinfo("SEVBY", "Paste or load some songs first.")
                return
            name = sanitize_filename(self._loaded_name).strip(". ") or f"Song list {len(self.queue) + 1}"
            job.update(raw=raw, name=f"{name} ({len(songs)} songs)", subfolder=name)
            self.songs_box.delete("1.0", "end")
            self._loaded_name = ""
        else:
            url = self.sp_url.get().strip()
            cid = self.client_id.get().strip()
            m = re.search(r"playlist[/:]([a-zA-Z0-9]+)", url)
            if not m:
                messagebox.showinfo("SEVBY", "Paste a Spotify playlist link first.")
                return
            if not cid:
                messagebox.showinfo("SEVBY", "Enter your Spotify Client ID first.")
                return
            job.update(
                url=url, cid=cid, secret=self.client_secret.get().strip(),
                name=f"Spotify playlist \u2026{m.group(1)[:6]}", subfolder=True,
            )
            self.sp_url.delete(0, "end")
        self.queue.append(job)
        self._refresh_queue_ui()
        self._save_prefs()

    def clear_queue(self):
        self.queue.clear()
        self._refresh_queue_ui()

    def _queue_remove(self, job: dict):
        self.queue = [j for j in self.queue if j is not job]
        self._refresh_queue_ui()

    def _refresh_queue_ui(self):
        n = len(self.queue)
        running = self.run_mode is not None
        if n:
            lines = [f"{i}. {j['name']}  \u2192  {j['out_dir']}" for i, j in enumerate(self.queue[:6], 1)]
            if n > 6:
                lines.append(f"\u2026and {n - 6} more")
            self.queue_label.configure(text="Queue (runs one after another):\n" + "\n".join(lines))
            if not self.queue_label.winfo_ismapped():
                self.queue_label.pack(fill="x", padx=28, pady=(0, 6), before=self.log_holder)
        else:
            self.queue_label.pack_forget()
        if not running:
            self.start_btn.configure(text=f"Start queue ({n})" if n else "Start Download")
            self.clear_btn.configure(state="normal" if n else "disabled")

    def _set_failed(self, songs: list[str]):
        self.failed = list(songs)
        if songs:
            self.retry_btn.configure(state="normal", text=f"Retry failed ({len(songs)})")
        else:
            self.retry_btn.configure(state="disabled", text="Retry failed")

    def retry_failed(self):
        if not self.failed or not self._job or self.start_btn.cget("state") == "disabled":
            return
        job = dict(self._job)
        if not os.path.isdir(job["out_dir"]):
            messagebox.showerror("Error", "The save folder no longer exists.")
            return
        job["songs"] = list(self.failed)
        self._begin([job])

    def open_folder(self):
        d = self.folder_entry.get().strip()
        if not d or not os.path.isdir(d):
            messagebox.showinfo("SEVBY", "Choose a save folder first.")
            return
        try:
            if os.name == "nt":
                os.startfile(d)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", d])
            else:
                subprocess.Popen(["xdg-open", d])
        except Exception as e:
            messagebox.showerror("Error", f"Could not open the folder:\n{e}")

    def _beep(self):
        try:
            if os.name == "nt":
                import winsound

                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            else:
                self.bell()
        except Exception:
            pass

    def _done_dialog(self, summary: str):
        self._beep()
        if messagebox.askyesno("SEVBY", summary + "\n\nOpen the folder?"):
            self.open_folder()

    def open_spotify_dashboard(self):
        webbrowser.open("https://developer.spotify.com/dashboard")

    def _need_client_id(self):
        """No Client ID yet: send the user to Spotify and explain the next step."""
        self.log(
            "Spotify needs a Client ID (one-time setup; Spotify requires Premium to create one).\n"
            "No Premium? Use Chosic to export a .txt and choose Song list mode instead.\n"
            "Opening the Spotify Developer dashboard\u2026\n"
            "Log in \u2192 Create app \u2192 Redirect URI  http://127.0.0.1:8888  \u2192 Settings \u2192 copy the Client ID.\n"
            "Then come back and press Start \u2014 SEVBY will paste it from your clipboard."
        )
        self.open_spotify_dashboard()
        self.client_id.focus_set()
        self.show_client_id_help()

    def stop(self):
        """Abort the current download and end the run."""
        STOP_EVENT.set()
        PAUSE_EVENT.clear()
        self.stop_btn.configure(state="disabled")
        self.pause_btn.configure(state="disabled")
        self.log("Stopping\u2026 (cancelling the current download)")

    def toggle_pause(self):
        """Pause before the next song / resume."""
        if PAUSE_EVENT.is_set():
            PAUSE_EVENT.clear()
            self.pause_btn.configure(text="Pause")
            self.set_progress(self.progress.get(), "Running")
            self.log("Resumed.")
        else:
            PAUSE_EVENT.set()
            self.pause_btn.configure(text="Resume")
            self.set_progress(self.progress.get(), "Paused")
            self.log("Pausing after the current song finishes\u2026")

    def _checkpoint(self) -> bool:
        """Hold while paused; return True if the user pressed Stop."""
        while PAUSE_EVENT.is_set() and not STOP_EVENT.is_set():
            time.sleep(0.2)
        return STOP_EVENT.is_set()

    def _run_job(self, job: dict):
        out_dir = job["out_dir"]
        source = job["source"]
        use_bc = source != "YouTube only"
        use_yt = source != "Bandcamp only"
        QUALITY["best"] = str(job.get("quality", "")).startswith("Best")
        QUALITY["free"] = bool(job.get("free_hq", True))
        QUALITY["jamendo_id"] = job.get("jamendo_id", "") or ""
        HQ_SONGS.clear()
        try:
            songs: list[str] = []

            if job.get("songs") is not None:
                songs = list(job["songs"])
                self.log(f"Retrying {len(songs)} song(s) that failed last time.")
            elif job["mode"] == "txt":
                songs = parse_song_lines(job["raw"])
                if not songs:
                    self.log("No songs found. Paste a list or load a .txt file.")
                    return
                self.log(f"Using {len(songs)} songs from list/file.")
            else:
                url, cid, secret = job["url"], job["cid"], job["secret"]
                if not url or "spotify" not in url.lower():
                    self.log("Enter a valid Spotify playlist URL.")
                    return
                if not cid:
                    self.log(
                        "Client ID required for Spotify links.\n"
                        "Click \u201cWhat is this?\u201d for the 2-minute one-time setup.\n"
                        "Or switch to Song list / .txt mode (no keys needed)."
                    )
                    return
                cfg = load_config()
                cfg["client_id"] = cid
                if secret:
                    cfg["client_secret"] = secret
                save_config(cfg)

                self.log("Connecting to Spotify\u2026")
                token = get_spotify_access_token(cid, secret, self.log)
                if not token:
                    self.log(
                        "Spotify login failed.\n"
                        "Check the Client ID and that the Redirect URI in your Spotify app is exactly "
                        "http://127.0.0.1:8888\n"
                        "Or export with Chosic and use Song list / .txt instead."
                    )
                    return
                if job.get("subfolder") is True:  # queued playlist: folder named after it
                    try:
                        nm = spotify_playlist_name(url, token)
                    except Exception:
                        nm = ""
                    job["subfolder"] = sanitize_filename(nm).strip(". ") or "Spotify playlist"
                self.log("Fetching Spotify playlist\u2026")
                try:
                    songs = fetch_spotify_tracks(url, token)
                except Exception as e:
                    self.log(f"Spotify error: {e}")
                    self.log("Export with Chosic and use Song list / .txt if this keeps happening.")
                    return
                if not songs:
                    self.log("No tracks returned (empty playlist?).")
                    return
                self.log(f"Found {len(songs)} tracks on Spotify.")

            sub = job.get("subfolder")
            if isinstance(sub, str) and sub:
                out_dir = os.path.join(out_dir, sub)
                os.makedirs(out_dir, exist_ok=True)
                self.log(f"Saving into: {out_dir}")

            can_download = have_ytdlp()
            if not can_download:
                self.log("yt-dlp not installed \u2014 will only collect Bandcamp links (pip install yt-dlp).")
            else:
                age = ytdlp_age_days()
                if age is not None and age > 90:
                    self.log(
                        f"Heads-up: this copy of yt-dlp is {age} days old. YouTube and Bandcamp change often \u2014 "
                        "if downloads fail (e.g. HTTP 403), update it with  pip install -U yt-dlp  "
                        "and rebuild SEVBY."
                    )
            if source != "Both":
                self.log(f"Source: {source}.")
            self.log("YouTube audio: " + ("Best available (original M4A kept, no re-encoding)."
                                           if QUALITY["best"] else "Standard (converted to MP3)."))

            # progress ranges depend on which sources are on
            if use_bc and use_yt:
                search_end, dl_end, yt_start = 0.3, 0.65, 0.65
            elif use_bc:
                search_end, dl_end, yt_start = 0.4, 1.0, 1.0
            else:
                search_end, dl_end, yt_start = 0.0, 0.0, 0.0

            not_found: list[str] = []
            bc_ok = 0
            free_ok = 0
            if QUALITY["free"] and songs:
                self.log("\u2500" * 40)
                self.log("Checking Jamendo" + (" and " if QUALITY["jamendo_id"] else " (no Client ID, skipped) and ")
                         + "Internet Archive for lossless Creative Commons copies\u2026")
                remaining: list[str] = []
                total0 = len(songs)
                for i, song in enumerate(songs, 1):
                    if self._checkpoint():
                        break
                    self.set_progress(0.05 * (i / total0), f"Free sources {i}/{total0}")
                    self.log(f"[{i}/{total0}] {song}")
                    if download_free_hq(song, out_dir, self.log):
                        free_ok += 1
                    else:
                        remaining.append(song)
                        self.log("  \u2192 not found there")
                if STOP_EVENT.is_set():
                    self._finish_stopped()
                    return
                self.log(f"Free sources: {free_ok} found, {len(remaining)} to look for elsewhere")
                songs = remaining
            n = len(songs)

            if use_bc:
                # \u2500\u2500 Pass 1: Bandcamp search \u2500\u2500
                self.log("\u2500" * 40)
                self.log("Searching Bandcamp\u2026")
                bc_hits: list[tuple[str, str]] = []  # (song, url)
                consecutive_errors = 0
                bandcamp_down = False

                for i, song in enumerate(songs, 1):
                    if self._checkpoint():
                        break
                    self.set_progress(search_end * (i / n), f"Search {i}/{n}")
                    self.log(f"[{i}/{n}] {song}")
                    result = search_bandcamp(song, use_api=not bandcamp_down)
                    if result.get("found") and result.get("url"):
                        consecutive_errors = 0
                        bc_hits.append((song, result["url"]))
                        self.log(
                            f"  \u2192 Bandcamp: {result['url']}"
                            + (" (found by trying the artist's page)" if result.get("guessed") else "")
                        )
                    else:
                        not_found.append(song)
                        err = result.get("error")
                        if err:
                            consecutive_errors += 1
                            self.log(f"  \u2192 Bandcamp search failed: {err}")
                            if consecutive_errors >= 3:
                                bandcamp_down = True
                                self.log(
                                    "Bandcamp's search keeps refusing SEVBY (it shows a JavaScript bot check).\n"
                                    "For the rest of this run SEVBY will try each artist's own Bandcamp "
                                    "page instead, and use YouTube for anything it can't find.\n"
                                    "To test your connection, run this in PowerShell and tell me what it prints:\n"
                                    '  curl.exe -s "https://bandcamp.com/api/fuzzysearch/2/app_autocomplete?q=Perturbator"'
                                )
                        else:
                            consecutive_errors = 0
                            self.log("  \u2192 not on Bandcamp")
                    time.sleep(0.25)

                self.log(f"Bandcamp: {len(bc_hits)} found, {len(not_found)} missing")

                if STOP_EVENT.is_set():
                    self._finish_stopped()
                    return

                # \u2500\u2500 Pass 2: download the Bandcamp tracks \u2500\u2500
                if bc_hits and can_download:
                    self.log("\u2500" * 40)
                    self.log(f"Downloading {len(bc_hits)} songs from Bandcamp\u2026")
                    b = len(bc_hits)
                    for i, (song, bc_url) in enumerate(bc_hits, 1):
                        if self._checkpoint():
                            break
                        self.set_progress(
                            search_end + (dl_end - search_end) * (i / b), f"Bandcamp {i}/{b}"
                        )
                        self.log(f"[{i}/{b}] {song}")
                        if download_bandcamp_mp3(bc_url, song, out_dir, self.log):
                            bc_ok += 1
                        elif not STOP_EVENT.is_set():
                            self.log("  \u2192 will try YouTube instead" if use_yt else "  \u2192 failed")
                            not_found.append(song)
                elif bc_hits:
                    not_found.extend(s for s, _ in bc_hits)

                if STOP_EVENT.is_set():
                    self._finish_stopped()
                    return
            else:
                not_found = list(songs)

            # \u2500\u2500 Pass 3: YouTube for the rest \u2500\u2500
            yt_ok = 0
            failed: list[str] = []
            if use_yt and not_found:
                if not can_download:
                    self.log("yt-dlp isn't installed, so songs not on Bandcamp can't be downloaded.")
                    failed = list(not_found)
                else:
                    self.log("\u2500" * 40)
                    self.log(f"Downloading {len(not_found)} songs from YouTube\u2026")
                    m = len(not_found)
                    for i, song in enumerate(not_found, 1):
                        if self._checkpoint():
                            break
                        self.set_progress(
                            yt_start + (1.0 - yt_start) * (i / m), f"YouTube {i}/{m}"
                        )
                        self.log(f"[{i}/{m}] {song}")
                        if download_youtube_mp3(song, out_dir, self.log):
                            yt_ok += 1
                        elif not STOP_EVENT.is_set():
                            failed.append(song)

                    if STOP_EVENT.is_set():
                        self.log(f"So far \u2014 Bandcamp: {bc_ok} ok \u00b7 YouTube: {yt_ok} ok, {len(failed)} failed.")
                        self._finish_stopped()
                        if not job.get("quiet"):
                            self.after(0, lambda f=list(failed): self._set_failed(f))
                        return
            elif not use_yt:
                failed = list(not_found)  # Bandcamp-only: whatever it couldn't get

            self.set_progress(1.0, "Done")
            self.log("\u2500" * 40)
            parts = []
            if QUALITY["free"]:
                parts.append(f"Jamendo/Archive: {free_ok} ok")
            if use_bc:
                parts.append(f"Bandcamp: {bc_ok} ok")
            if use_yt:
                parts.append(f"YouTube: {yt_ok} ok")
            parts.append(f"{len(failed)} failed")
            summary = " \u00b7 ".join(parts)
            self.log(f"Done. {summary}.")
            self.log(f"Folder: {out_dir}")
            if QUALITY["best"]:
                write_hq_links(out_dir, self.log)
            if failed:
                self.log("Songs that could not be downloaded (press \u201cRetry failed\u201d to try them again):")
                for song in failed:
                    self.log(f"  \u2717 {song}")
            job["_summary"] = summary
            if not job.get("quiet"):
                self.after(0, lambda f=list(failed): self._set_failed(f))
                self.after(0, lambda: self._done_dialog("Finished.\n" + summary.replace(" \u00b7 ", "\n")))
        except Exception as e:
            self.log(f"Unexpected error: {e}")

    def _worker(self, jobs: list):
        """Run one job, or a whole queue - strictly one after the other."""
        try:
            total = len(jobs)
            lines = []
            for k, job in enumerate(jobs, 1):
                if STOP_EVENT.is_set():
                    break
                if total > 1:
                    self.log("\u2550" * 40)
                    self.log(f"Queue {k}/{total}: {job['name']}")
                    job["quiet"] = True
                self._run_job(job)
                if total > 1 and not STOP_EVENT.is_set():
                    if "_summary" in job:
                        lines.append(f"{job['name']}: {job['_summary']}")
                        self.after(0, lambda j=job: self._queue_remove(j))  # done \u2192 leaves the queue
                    else:
                        lines.append(f"{job['name']}: did not run (kept in the queue)")
            if total > 1 and not STOP_EVENT.is_set():
                self.set_progress(1.0, "Done")
                self.log("\u2550" * 40)
                self.log(f"Queue finished ({total} items).")
                self.after(0, lambda: self._done_dialog("Queue finished.\n\n" + "\n".join(lines)))
        except Exception as e:
            self.log(f"Unexpected error: {e}")
        finally:
            PAUSE_EVENT.clear()
            self.after(0, self._reset_buttons)

    def _finish_stopped(self):
        self.set_progress(self.progress.get(), "Stopped")
        self.log("\u2500" * 40)
        self.log("Stopped. Songs already downloaded are kept; run again to continue "
                 "(finished songs are skipped).")

    def _reset_buttons(self):
        self.run_mode = None
        self.start_btn.configure(state="normal")
        self.add_btn.configure(state="normal")
        self._refresh_queue_ui()
        self.pause_btn.configure(state="disabled", text="Pause")
        self.stop_btn.configure(state="disabled")


if __name__ == "__main__":
    app = SevbyApp()
    app.mainloop()
