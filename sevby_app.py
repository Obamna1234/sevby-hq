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

# An updated yt-dlp (downloaded from About & updates) wins over the copy bundled in the app.
YTDLP_UPDATE_DIR = Path.home() / ".sevby_hq_ytdlp"
YTDLP_ACTIVE = ""
try:
    _upd = YTDLP_UPDATE_DIR / "yt-dlp"
    if _upd.is_file():
        import importlib.machinery

        sys.path.insert(0, str(_upd))
        if getattr(sys, "frozen", False):
            # packaged apps look inside their own bundle first; make the path search go first for this
            if importlib.machinery.PathFinder in sys.meta_path:
                sys.meta_path.remove(importlib.machinery.PathFinder)
            sys.meta_path.insert(0, importlib.machinery.PathFinder)
        YTDLP_ACTIVE = str(_upd)
except Exception:
    YTDLP_ACTIVE = ""

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
            "album_name": r.get("album_name") or "",
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
                "album_name": r.get("album_name") or "",
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
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower().replace("&", " and "))


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
            if version_set(raw_title) != version_set(title):
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

    best = bc_choose(tracks, query)
    if not best:
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


_SMALL_WORDS = {"a", "an", "the", "of", "and", "or", "but", "in", "on", "at", "to", "for", "by", "vs", "with", "from"}


def _smart_case(part: str) -> str:
    """'the black sheep' -> 'The Black Sheep' (small words stay lower-case except the first one)."""
    words = part.split(" ")
    out = []
    for i, w in enumerate(words):
        if not w:
            out.append(w)
            continue
        if i > 0 and w in _SMALL_WORDS:
            out.append(w)
            continue
        out.append("/".join(seg[:1].upper() + seg[1:] for seg in w.split("/")))
    return " ".join(out)


def fix_case(line: str) -> str:
    """Only for lines typed entirely in lower case: give 'artist - title' normal capital letters.
    Lines that already contain a capital letter are left exactly as written."""
    if not line or line != line.lower() or not any(c.isalpha() for c in line):
        return line
    for sep in (" - ", " \u2013 ", " \u2014 "):
        if sep in line:
            a, t = line.split(sep, 1)
            return _smart_case(a.strip()) + sep + _smart_case(t.strip())
    return _smart_case(line)


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
        songs.append(fix_case(line))
    return songs


def load_songs_from_file(path: str) -> list[str]:
    data = Path(path).read_bytes()
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):  # Apple Music on Windows can save UTF-16
        raw = data.decode("utf-16", errors="replace")
    else:
        raw = data.decode("utf-8-sig", errors="replace")
    return parse_song_lines(raw)


# -- Apple Music: read a PUBLIC shared playlist / album link (no sign-in) ----------------------------

def _apple_artist(obj) -> str:
    if isinstance(obj, dict):
        return str(obj.get("name") or "").strip()
    if isinstance(obj, list):
        return ", ".join(x for x in (_apple_artist(o) for o in obj) if x)
    return str(obj or "").strip()


def _apple_from_jsonld(html: str) -> tuple[str, list[str]]:
    """Songs from the page's built-in schema.org data (MusicPlaylist / MusicAlbum)."""
    name, songs = "", []
    for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        for node in (data if isinstance(data, list) else [data]):
            if not isinstance(node, dict):
                continue
            tracks = node.get("track") or node.get("tracks")
            if not isinstance(tracks, list) or not tracks:
                continue
            owner = _apple_artist(node.get("byArtist"))
            found = []
            for t in tracks:
                if not isinstance(t, dict):
                    continue
                title = str(t.get("name") or "").strip()
                artist = _apple_artist(t.get("byArtist")) or owner
                if title and artist:
                    found.append(f"{artist} - {title}")
            if len(found) > len(songs):
                songs, name = found, str(node.get("name") or "").strip()
    return name, songs


def _apple_from_server_data(html: str) -> list[str]:
    """Fallback: pull (title, artist) pairs out of the page's embedded app data."""
    m = re.search(r'<script[^>]*id="serialized-server-data"[^>]*>(.*?)</script>', html, re.S | re.I)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return []
    out: list[str] = []

    def walk(o):
        if isinstance(o, dict):
            title, artist = o.get("title"), o.get("artistName")
            if isinstance(title, str) and isinstance(artist, str) and title and artist and "contentDescriptor" in o:
                kind = ((o.get("contentDescriptor") or {}).get("kind") or "")
                if kind in ("song", ""):
                    line = f"{artist} - {title}"
                    if line not in out:
                        out.append(line)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    return out


def fetch_apple_playlist(url: str) -> tuple[str, list[str]]:
    """(name, ['Artist - Title', ...]) for a shared Apple Music playlist or album link.
    Only works for links anyone can open (Share > Copy Link). Raises ValueError with a plain message."""
    url = (url or "").strip().split()[0] if (url or "").strip() else ""
    if "music.apple.com" not in url.lower():
        raise ValueError("That doesn't look like an Apple Music link (it should contain music.apple.com).")
    url = re.sub(r"//embed\.music\.apple\.com", "//music.apple.com", url, flags=re.I)
    req = urllib.request.Request(url, headers={
        "User-Agent": BROWSER_UA, "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml",
    })
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raise ValueError(f"Apple Music said no (HTTP {e.code}). Is the playlist public (Share > Copy Link)?")
    except Exception as e:
        raise ValueError(f"Couldn't reach Apple Music ({e}). Check your internet connection.")
    name, songs = _apple_from_jsonld(html)
    if not songs:
        songs = _apple_from_server_data(html)
    if not name:
        mt = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html, re.I)
        if mt:
            import html as _h

            name = re.split(r"\s+(?:on|by)\s+Apple Music", _h.unescape(mt.group(1)))[0].strip(" \u200e\u200f-")
    if not songs:
        raise ValueError("I couldn't find any songs on that page. The playlist may be private, or Apple changed "
                         "its page. Try another export method (see the README).")
    return name, songs


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
QUALITY = {"best": True, "free": False, "jamendo_id": ""}
LAST_FREE: dict = {}
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
    if not ok and not STOP_EVENT.is_set() and _NET_ERR_RE.search(err or ""):
        for delay in (5, 15):
            if log:
                log(f"  Network problem \u2013 retrying in {delay} s\u2026")
            for _ in range(delay * 5):
                if STOP_EVENT.is_set():
                    break
                time.sleep(0.2)
            if STOP_EVENT.is_set():
                break
            ok, err = runner(target, out_dir, safe, False, embed)
            if ok or not _NET_ERR_RE.search(err or ""):
                break
        if not ok and _NET_ERR_RE.search(err or "") and log:
            log("  Tip: the internet connection dropped. Run the list again later.")
    if STOP_EVENT.is_set() and not ok:
        _cleanup_partial(out_dir, safe)
        err = "stopped"
    elif ok and embed and not original:
        _ensure_cover(out_dir, safe, _LAST_INFO.get("info"), log)
    return ok, err


def _already_exists(out_dir: str, safe: str) -> bool:
    return existing_song(out_dir, safe) is not None


# \u2500\u2500 Tags & cover art \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

def meta_for(song: str) -> dict:
    """Tag info for a song: full Spotify details if we have them, else parsed from 'Artist - Title'."""
    if song in SONG_META:
        return SONG_META[song]
    artist, title = _split_query(song)
    meta = {"title": title}
    if artist:
        meta["artist"] = re.sub(r"\s*,\s*", ", ", artist)
    return meta


def _crop_box(src: str) -> tuple[int, int, int, int, int, int] | None:
    """Find black bars with ffmpeg's cropdetect. Returns (W, H, x, y, iw, ih) or None."""
    r = _run_ff(["-loop", "1", "-framerate", "10", "-t", "0.8", "-i", src,
                 "-vf", "cropdetect=limit=24:round=2:reset=0", "-f", "null", "-"])
    if r is None:
        return None
    text = (r.stderr or b"").decode("utf-8", errors="replace")
    crops = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", text)
    size = re.search(r"Stream #\d+:\d+.*?Video:.*?, (\d{2,5})x(\d{2,5})", text)
    if not crops or not size:
        return None
    w, h, x, y = (int(v) for v in crops[-1])
    return w, h, x, y, int(size.group(1)), int(size.group(2))


def _square_jpg(src: str) -> str | None:
    """Make a square cover from a video thumbnail: trim black bars that are the same on both sides,
    then crop the centre square if the picture is nearly square, else show it whole on a black square."""
    dst = os.path.join(tempfile.gettempdir(), f"sevby_square_{os.getpid()}.jpg")
    vf = []
    box = _crop_box(src)
    cw = ch = None
    if box:
        w, h, x, y, iw, ih = box
        left, right, top, bottom = x, iw - (x + w), y, ih - (y + h)
        tx = x if (abs(left - right) <= 3 and left >= iw * 0.02) else 0
        ty = y if (abs(top - bottom) <= 3 and top >= ih * 0.02) else 0
        cw, ch = iw - 2 * tx, ih - 2 * ty
        if tx or ty:
            vf.append(f"crop={cw}:{ch}:{tx}:{ty}")
    if cw is None:
        r0 = _run_ff(["-i", src])
        m = re.search(r"Video:.*?, (\d{2,5})x(\d{2,5})", (r0.stderr or b"").decode("utf-8", "replace")) if r0 else None
        if m:
            cw, ch = int(m.group(1)), int(m.group(2))
    if cw and ch:
        if cw / ch <= 1.25 and ch / cw <= 1.25:
            side = min(cw, ch)
            vf.append(f"crop={side}:{side}")
        else:
            side = max(cw, ch)
            vf.append(f"scale={side}:{side}:force_original_aspect_ratio=decrease,pad={side}:{side}:(ow-iw)/2:(oh-ih)/2:black")
    else:
        vf.append("crop=min(iw\\,ih):min(iw\\,ih)")
    r = _run_ff(["-y", "-i", src, "-vf", ",".join(vf), "-frames:v", "1", "-q:v", "2", dst])
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
    return re.findall(r"\w+", (s or "").lower().replace("&", " and "))


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


def search_youtube_candidates(query: str, n: int = 8) -> list[dict]:
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
                info = ydl.extract_info(f"ytsearch{n}:{query}", download=False)
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
        log(f"  OK (Bandcamp) \u2192 {safe}.mp3" + ("  [Bandcamp stream, 128 kbps MP3]" if QUALITY["best"] else ""))
    elif err == "stopped":
        log("  Stopped.")
    else:
        log(f"  Bandcamp download failed: {err}")
    return ok


def _alt_youtube_queries(artist: str, title: str) -> list[str]:
    """Other ways to word the YouTube search when 'Artist - Title' finds nothing usable."""
    a, t = (artist or "").strip(), clean_title(title or "") or (title or "").strip()
    if not t:
        return []
    out = []
    if a:
        out += [f"{a} {t} official audio", f"{t} {a}", f"{a} - Topic {t}"]
    else:
        out += [f"{t} official audio"]
    return out


def download_youtube_mp3(query: str, out_dir: str, log, use_cookies: bool = True) -> bool:
    safe = sanitize_filename(query)
    if _already_exists(out_dir, safe):
        log(f"  SKIP (exists): {safe}")
        return True
    meta = dict(meta_for(query))
    if query not in SONG_META and meta.get("artist"):
        it = itunes_lookup(meta["artist"], meta.get("title") or "", query)
        if it:
            for k, v in it.items():
                if v and not meta.get(k) and not k.endswith("_canon"):
                    meta[k] = v
            apply_canon(meta, it)

    # Pick the best-matching video (right length, not a live/remix version) instead of the first hit.
    targets: list[str] = []
    ranked = rank_youtube_results(search_youtube_candidates(query), query, meta)
    # Drop videos whose title shares (almost) nothing with the wanted song title.
    _tw = set(_words(meta.get("title") or _split_query(query)[1]))
    if _tw:
        ranked = [(sc, e) for sc, e in ranked
                  if len(_tw & set(_words((e.get("title") or "")))) / len(_tw) >= 0.5]
    if not ranked:
        # Short or common titles ("Duel") can be crowded out of the first results: try other wordings, and
        # only accept a video whose title or channel also names the artist.
        _art = meta.get("artist") or _split_query(query)[0]
        _aw = set(_words(_art))
        _ti = meta.get("title") or _split_query(query)[1]
        for q2 in _alt_youtube_queries(_art, _ti):
            if STOP_EVENT.is_set():
                break
            r2 = rank_youtube_results(search_youtube_candidates(q2, 15), query, meta)
            r2 = [(sc, e) for sc, e in r2
                  if _tw and len(_tw & set(_words(e.get("title") or ""))) / len(_tw) >= 0.5
                  and _aw and len(_aw & set(_words((e.get("title") or "") + " " + (e.get("channel") or e.get("uploader") or "")))) / len(_aw) >= 0.5]
            if r2:
                log("  (found with a different search wording)")
                ranked = r2
                break
    if not ranked:
        log("  FAIL: no matching video found on YouTube")
        return False
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


# -- Matching rules shared with the Android app: Bandcamp choice, iTunes album info, skip rules ------

VERSION_WORDS = ("remix", "live", "instrumental", "acoustic", "cover", "edit", "mix", "version", "karaoke")
_COMPILATION_RE = re.compile(
    r"compilation|\bvol\b|volume|various|sampler|presents|best of|collection|anthology|\b\d{2,3}\b", re.I)
_NET_ERR_RE = re.compile(
    r"getaddrinfo|name or service not known|temporary failure|timed out|timeout|connection reset|"
    r"connection aborted|network is unreachable|remote end closed|urlopen error|connection refused|"
    r"no route to host|server disconnected", re.I)


def _alnum(s: str) -> str:
    """Letters and digits only, lower-case (so 'M.A.D.E.S' == 'mades')."""
    return re.sub(r"[\W_]+", "", (s or "").lower())


def clean_title(t: str) -> str:
    """Drop (feat. ...), (Remastered), (Original Mix), (Explicit) ... and a trailing '- 2011 Remaster'."""
    t = re.sub(
        r"[\(\[][^\)\]]*\b(feat|ft|featuring|remaster|remastered|original mix|original version|explicit|clean)\b[^\)\]]*[\)\]]",
        " ", t or "", flags=re.I)
    t = re.sub(r"\s+-\s+(\d{4}\s+)?remaster(ed)?(\s+\d{4})?\s*$", "", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip()


def artists_of(artist: str) -> list[str]:
    parts = re.split(r"\s*[,&]\s*|\s+(?:feat\.?|ft\.?|featuring|x|and)\s+", artist or "", flags=re.I)
    out = [a for a in (_alnum(x) for x in parts) if a]
    return out or ([_alnum(artist)] if _alnum(artist) else [])


def version_set(text: str) -> set[str]:
    low = re.sub(r"\boriginal\s+(mix|version)\b", " ", (text or "").lower())
    return {w for w in VERSION_WORDS if re.search(r"\b" + w + r"\b", low)}


_LEAD_ARTICLE_RE = re.compile(r"^\s*(the|an|a)\s+", re.I)


def _title_key(t: str) -> str:
    """Letters and digits of a title, ignoring (feat. ...) / (Remastered) parts and a leading The / A / An."""
    return _alnum(_LEAD_ARTICLE_RE.sub("", clean_title(t)))


def _title_eq(a: str, b: str) -> bool:
    return bool(_title_key(a)) and _title_key(a) == _title_key(b)


def _strip_artist_prefix(name: str, artists: list[str]) -> str:
    m = re.match(r"^(.*?)\s+[-\u2013\u2014]\s+(.+)$", name or "")
    if m and any(a and (a in _alnum(m.group(1)) or _alnum(m.group(1)) in a) for a in artists if _alnum(m.group(1))):
        return m.group(2)
    return name or ""


def _host_label(url: str) -> str:
    m = re.match(r"https?://([^./]+)\.", url or "")
    return m.group(1).lower() if m else ""


_BC_BADVER_RE = re.compile(
    r"remake|re-?record|tribute|\bcovers?\b|bootleg|karaoke|\bdubs?\b|made famous|originally (performed|by)|"
    r"in the style of|\bversions?\b|rerecord|lullaby|8-?bit|piano (version|tribute)", re.I)


def _plain(s: str) -> str:
    """Lower-case letters and digits only, accents removed ('Sigur R\u00f3s' -> 'sigurros')."""
    import unicodedata
    s = unicodedata.normalize("NFD", (s or "").replace("&", " and "))
    return re.sub(r"[\W_]+", "", "".join(c for c in s if unicodedata.category(c) != "Mn").lower())


def _host_is_artist(url: str, cands: list[str]) -> bool:
    """True if the page's web address belongs to the artist: artist.bandcamp.com, or the artist's own
    domain such as store.sigurros.com. Pages run by DJs, fans or compilation accounts that merely credit
    the artist (a remix, a cover, a re-upload) are not accepted."""
    m = re.match(r"https?://([^/]+)", url or "")
    if not m:
        return False
    labels = m.group(1).lower().split(".")
    names = [labels[0]]
    if len(labels) >= 3 and not m.group(1).lower().endswith("bandcamp.com"):
        names.append(labels[-2])
    names = [_plain(n) for n in names if n]
    variants = []
    for a in cands:
        for v in (_plain(a), _plain(a.replace("&", " "))):
            if v and v not in variants:
                variants.append(v)
    for a in variants:
        for h in names:
            if h and (a == h or ((a in h or h in a) and min(len(a), len(h)) / max(len(a), len(h)) >= 0.75)):
                return True
    return False


def bc_choose(results: list[dict], query: str) -> dict | None:
    """Pick the right Bandcamp TRACK: same title, same artist, same kind of version."""
    artist, title = _split_query(query)
    if not artist or not title:
        return None  # a line without an artist is never matched (it goes to YouTube)
    want_artists = artists_of(artist)
    want_ver = version_set(title)
    pool: list[tuple[float, dict, bool]] = []
    for i, r in enumerate(results):
        url = r.get("url") or ""
        if "/track/" not in url:
            continue
        name = _strip_artist_prefix(r.get("name") or "", want_artists)
        if not _title_eq(name, title):
            continue
        band = _alnum(r.get("band_name") or "")
        # The page must belong to the artist itself (not "Queen Mary band", a tribute or a bootleg page):
        # the band name must equal the artist, or at least be mostly made of it.
        cands = want_artists + [_alnum(artist)]
        if not band or not any(
                a and (a == band or ((a in band or band in a) and min(len(a), len(band)) / max(len(a), len(band)) >= 0.75))
                for a in cands):
            continue
        if version_set(name) != want_ver:
            continue
        own = _host_is_artist(url, cands + [artist])
        album_txt = r.get("album_name") or ""
        if _BC_BADVER_RE.search(name + " " + album_txt) and not _BC_BADVER_RE.search(query):
            continue  # remake / tribute / cover / bootleg / "dubs" release
        if re.search(r"\blive\b|unplugged|in concert", album_txt, re.I) and "live" not in want_ver:
            continue
        score = -0.001 * i
        host = _host_label(url)
        if host and any(host == a or host in a or a in host for a in want_artists if len(a) > 2):
            score += 10
        album = r.get("album_name") or ""
        if album:
            score += -5 if _COMPILATION_RE.search(album) else 2
        pool.append((score, r, own))
    # 1. The artist's own page (artist.bandcamp.com or the artist's domain) wins.
    for score, r, own in sorted(pool, key=lambda x: x[0], reverse=True):
        if own:
            return r
    # 2. Another account (a label, a DJ, a fan, a compilation) is only trusted when its album is the
    #    same album Apple lists for this song. This stops re-uploads, covers and "various" pages that
    #    merely credit the artist.
    others = sorted((x for x in pool if not x[2]), key=lambda x: x[0], reverse=True)
    if others:
        try:
            it = itunes_lookup(artist, title, query)
        except Exception:
            it = None
        real_album = (it or {}).get("album") or ""
        if real_album:
            for score, r, own in others:
                if _title_eq(r.get("album_name") or "", real_album):
                    return r
    return None


_ITUNES_CACHE: dict = {}


def _device_country() -> str:
    try:
        import locale

        loc = (locale.getlocale()[0] or locale.getdefaultlocale()[0] or "")
        m = re.search(r"[_-]([A-Za-z]{2})$", loc)
        if m:
            return m.group(1).upper()
        names = {"australia": "AU", "united states": "US", "united kingdom": "GB", "canada": "CA",
                 "germany": "DE", "france": "FR", "new zealand": "NZ", "ireland": "IE"}
        for k, v in names.items():
            if k in loc.lower():
                return v
    except Exception:
        pass
    return "US"


def itunes_choose(results: list[dict], artist: str, title: str, line: str) -> dict | None:
    want_artists = artists_of(artist)
    want_ver = version_set(title)
    wants_live = "live" in want_ver
    best, best_score = None, -999.0
    for i, r in enumerate(results):
        if not _title_eq(r.get("trackName", ""), title):
            continue
        ra = _alnum(r.get("artistName", ""))
        if not ra or not any(a in ra or ra in a for a in want_artists):
            continue
        if version_set(r.get("trackName", "")) != want_ver:
            continue
        album = r.get("collectionName") or ""
        if re.search(r"\blive\b|unplugged|in concert", album, re.I) and not wants_live:
            continue
        score = 0.0
        cart = (r.get("collectionArtistName") or "")
        if cart and (cart.lower().startswith("various") or not any(
                a in _alnum(cart) or _alnum(cart) in a for a in want_artists)):
            score -= 10
        if _COMPILATION_RE.search(album):
            score -= 5
        if re.search(r"expanded|deluxe|anniversary|remaster|reissue|special edition|bonus", album, re.I):
            score -= 1
        if album and not re.search(r"\s-\s(single|ep)\s*$", album, re.I):
            score += 2
        score -= 0.0001 * i
        rd = (r.get("releaseDate") or "")[:10]
        key = (score, "".join(reversed(rd)) if False else 0)
        # ties go to the earliest release
        if score > best_score or (abs(score - best_score) < 0.001 and rd and best and rd < (best.get("releaseDate") or "9")[:10]):
            best, best_score = r, score
    return best


def apply_canon(meta: dict, it: dict | None) -> None:
    """Use Apple's spelling and capitals for the title / artist tags when they only differ from the typed
    line in capitals, punctuation or a leading 'The' ('kataklysm - black sheep' -> Kataklysm / The Black Sheep)."""
    if not it:
        return
    t, a = it.get("title_canon"), it.get("artist_canon")
    if t and meta.get("title") and _title_eq(t, meta["title"]):
        meta["title"] = t
    if a and meta.get("artist") and _alnum(a) == _alnum(meta["artist"]):
        meta["artist"] = a


def canonical_line(song: str) -> str:
    """'kataklysm - black sheep' / 'KATAKLYSM - The BLACK sheep' -> 'Kataklysm - The Black Sheep' when Apple Music
    knows the song and the only differences are capitals, punctuation or a leading 'The'. Otherwise unchanged."""
    if song in SONG_META or STOP_EVENT.is_set():
        return song
    artist, title = _split_query(song)
    if not artist or not title:
        return song
    try:
        it = itunes_lookup(artist, title, song)
    except Exception:
        return song
    meta = {"artist": artist, "title": title}
    apply_canon(meta, it)
    if meta["artist"] == artist and meta["title"] == title:
        return song
    return f"{meta['artist']} - {meta['title']}"


def itunes_lookup(artist: str, title: str, line: str = "") -> dict | None:
    """Album / year / track / cover / length for a song from the iTunes Search API (no key needed)."""
    key = (_alnum(artist), _alnum(title))
    if key in _ITUNES_CACHE:
        return _ITUNES_CACHE[key]
    found = None
    countries = [_device_country()] + (["US"] if _device_country() != "US" else [])
    for cc in countries:
        if STOP_EVENT.is_set():
            break
        q = urllib.parse.urlencode({"term": f"{artist} {clean_title(title)}", "media": "music",
                                    "entity": "song", "limit": 25, "country": cc})
        data = None
        for delay in (0, 5, 15):
            if delay:
                time.sleep(delay)
            data = _free_json("https://itunes.apple.com/search?" + q)
            if data is not None:
                break
        r = itunes_choose((data or {}).get("results") or [], artist, title, line)
        if r:
            album = re.sub(r"\s-\s(Single|EP)\s*$", "", r.get("collectionName") or "", flags=re.I).strip()
            art = r.get("artworkUrl100") or ""
            found = {
                "album": album or None,
                "year": (r.get("releaseDate") or "")[:4] or None,
                "track": r.get("trackNumber") or None,
                "track_total": r.get("trackCount") or None,
                "duration": (r.get("trackTimeMillis") or 0) / 1000.0 or None,
                "cover_url": re.sub(r"/\d+x\d+bb", "/1000x1000bb", art) if art else None,
                "title_canon": clean_title(r.get("trackName") or "") or None,
                "artist_canon": (r.get("artistName") or "").strip() or None,
            }
            break
    _ITUNES_CACHE[key] = found
    return found


def read_tags(path: str) -> dict:
    """Read album / date / track from a file with the bundled ffmpeg (it prints them when probing)."""
    r = _run_ff(["-i", path])
    out = {}
    if r is None:
        return out
    text = (r.stderr or b"").decode("utf-8", errors="replace")
    for key in ("album", "date", "track", "title", "artist"):
        m = re.search(r"^\s+" + key + r"\s*:\s*(.+)$", text, re.M | re.I)
        if m:
            out[key] = m.group(1).strip()
    return out


MIN_BYTES = 100 * 1024


def existing_song(out_dir: str, safe: str) -> str | None:
    """A finished copy of this song already in the folder (>= 100 KB). Smaller files are leftovers of an
    interrupted run: they are deleted so the song gets downloaded again."""
    found = None
    for ext in ("mp3", "m4a", "flac"):
        p = os.path.join(out_dir, f"{safe}.{ext}")
        if os.path.isfile(p):
            try:
                if os.path.getsize(p) >= MIN_BYTES:
                    found = found or p
                else:
                    os.remove(p)
            except OSError:
                pass
    return found


def _file_seconds(path: str) -> float | None:
    """Length of an audio file in seconds (read with the bundled ffmpeg), or None."""
    r = _run_ff(["-i", path])
    if r is None:
        return None
    text = (r.stderr or b"").decode("utf-8", errors="replace")
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if not m:
        return None
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def _expected_seconds(song: str) -> float | None:
    """The real length of the song, from Spotify / Apple details or an iTunes lookup (None if unknown)."""
    meta = meta_for(song)
    dur = meta.get("duration")
    if not dur and meta.get("artist"):
        try:
            it = itunes_lookup(meta["artist"], meta.get("title") or "", song)
        except Exception:
            it = None
        dur = (it or {}).get("duration")
    try:
        return float(dur) if dur else None
    except (TypeError, ValueError):
        return None


def _bandcamp_wrong_length(out_dir: str, safe: str, song: str, log) -> bool:
    """True (and the file is deleted) when the downloaded Bandcamp track is clearly a different version
    of the song (remix, edit, slowed, live ...) judging by its length. Unknown lengths are accepted."""
    path = os.path.join(out_dir, f"{safe}.mp3")
    if not os.path.isfile(path):
        return False
    want = _expected_seconds(song)
    got = _file_seconds(path)
    if not want or not got:
        return False
    if abs(got - want) <= max(12.0, 0.05 * want):
        return False
    log(f"  Bandcamp copy is {_fmt_len(got)} but the real song is {_fmt_len(want)} - "
        "probably a remix or other version, skipping it")
    try:
        os.remove(path)
    except OSError:
        pass
    _remove_loose_images(out_dir, safe)
    return True


def retag_bandcamp(out_dir: str, safe: str, query: str) -> tuple[str, str]:
    """Title and artist always come from the user's line. Album / year / track: Bandcamp, then iTunes.
    Returns (album, year) for the log."""
    mp3 = os.path.join(out_dir, f"{safe}.mp3")
    if not os.path.isfile(mp3):
        return "", ""
    meta = dict(meta_for(query))
    tags = read_tags(mp3)
    album = tags.get("album") or meta.get("album") or ""
    year = (tags.get("date") or "")[:4] or meta.get("year") or ""
    track = meta.get("track")
    total = meta.get("track_total")
    if not track and tags.get("track"):
        m = re.match(r"(\d+)(?:\s*/\s*(\d+))?", tags["track"])
        if m:
            track, total = int(m.group(1)), (int(m.group(2)) if m.group(2) else None)
    it = None
    if meta.get("artist") and (not album or not year or query not in SONG_META):
        it = itunes_lookup(meta.get("artist") or "", meta.get("title") or "", query)
        if query not in SONG_META:
            apply_canon(meta, it)
    if not album or not year:
        if it:
            album = album or it.get("album") or ""
            year = year or it.get("year") or ""
            track = track or it.get("track")
            total = total or it.get("track_total")
    meta.update(album=album or meta.get("title"), year=year or None, track=track, track_total=total)
    _apply_tags(mp3, meta, None, wipe=True)
    return (album or meta.get("title") or ""), year


def _fmt_album(album: str, year: str) -> str:
    if album and year:
        return f"{album} ({year})"
    return album or year or ""


def _src_result(res: dict) -> str:
    return res.get("note") or ""


def process_song(song: str, out_dir: str, opts: dict, state: dict, say, step=lambda _t: None) -> dict:
    """One song, start to finish. Used by the window and the command-line version.
    opts: use_bc, use_yt, free.  state: seen (set), bc_errors (int), bandcamp_down (bool).
    Returns {'status': ok|skip|dup|fail, 'source': ..., 'note': ..., 'path': ...}."""
    typed_safe = sanitize_filename(song)
    if typed_safe.lower() in state.setdefault("seen", set()):
        return {"status": "dup", "source": None, "note": "listed twice"}
    step("Checking the folder\u2026")
    if existing_song(out_dir, typed_safe):
        state.setdefault("seen", set()).add(typed_safe.lower())
        return {"status": "skip", "source": None, "note": "already downloaded"}
    # file name = the official spelling (Apple Music), however the line was typed
    song = canonical_line(song)
    safe = sanitize_filename(song)
    key = safe.lower()
    if key in state.setdefault("seen", set()) or typed_safe.lower() in state["seen"]:
        return {"status": "dup", "source": None, "note": "listed twice"}
    state["seen"].add(key)
    state["seen"].add(typed_safe.lower())

    if existing_song(out_dir, safe):
        return {"status": "skip", "source": None, "note": "already downloaded"}

    # 1. free, legal lossless sources
    if opts.get("free"):
        step("Checking Jamendo / Internet Archive\u2026")
        if download_free_hq(song, out_dir, say):
            p = existing_song(out_dir, safe) or ""
            kind = p.rsplit(".", 1)[-1].upper() if p else ""
            src = LAST_FREE.get("source") or "Internet Archive"
            return {"status": "ok", "source": src, "path": p, "note": f"{src} \u00b7 {kind}, Creative Commons"}
        if STOP_EVENT.is_set():
            return {"status": "fail", "source": None, "note": "stopped"}

    # 2. Bandcamp
    bc_note = ""
    if opts.get("use_bc"):
        step("Searching Bandcamp\u2026")
        if DEBUG.get("bc_blocked"):
            res = {"found": False, "error": "pretend-blocked (test switch)"}
        else:
            res = search_bandcamp(song, use_api=not state.get("bandcamp_down"))
        if res.get("found") and res.get("url"):
            state["bc_errors"] = 0
            say(f"  -> Bandcamp: {res['url']}")
            step("Downloading from Bandcamp\u2026")
            if download_bandcamp_mp3(res["url"], song, out_dir, say):
                if _bandcamp_wrong_length(out_dir, safe, song, say):
                    bc_note = " (Bandcamp copy was a different version)"
                else:
                    step("Looking up album info\u2026")
                    album, year = retag_bandcamp(out_dir, safe, song)
                    return {"status": "ok", "source": "Bandcamp", "path": existing_song(out_dir, safe) or "",
                            "note": "Bandcamp" + (f" \u00b7 {_fmt_album(album, year)}" if album or year else "")}
            else:
                if STOP_EVENT.is_set():
                    return {"status": "fail", "source": None, "note": "stopped"}
                bc_note = " (Bandcamp download failed)"
        elif res.get("error"):
            state["bc_errors"] = state.get("bc_errors", 0) + 1
            say(f"  -> Bandcamp search failed: {res['error']}")
            bc_note = " (Bandcamp blocked)"
            if state["bc_errors"] >= 3 and not state.get("bandcamp_down"):
                state["bandcamp_down"] = True
                say("  (Bandcamp search keeps refusing - trying artist pages only from now on)")
        else:
            state["bc_errors"] = 0
            say("  -> not on Bandcamp")
            bc_note = " (not on Bandcamp)"

    # 3. YouTube
    if opts.get("use_yt") and not STOP_EVENT.is_set():
        step("Searching YouTube\u2026")
        if download_youtube_mp3(song, out_dir, say):
            p = existing_song(out_dir, safe) or ""
            tags = read_tags(p) if p else {}
            album = tags.get("album") or ""
            year = (tags.get("date") or "")[:4]
            extra = _fmt_album(album, year)
            return {"status": "ok", "source": "YouTube", "path": p,
                    "note": "YouTube" + (f" \u00b7 {extra}" if extra else "") + bc_note}
        if STOP_EVENT.is_set():
            return {"status": "fail", "source": None, "note": "stopped"}
        return {"status": "fail", "source": None,
                "note": "not found on YouTube" if "not on" in bc_note or not opts.get("use_bc") else "couldn't be downloaded"}
    if opts.get("use_bc"):
        return {"status": "fail", "source": None, "note": bc_note.strip(" ()") or "not on Bandcamp"}
    return {"status": "fail", "source": None, "note": "no source selected"}



# -- Free & legal lossless sources: Jamendo and Internet Archive (Creative Commons only) ----------

_FREE_UA = "SEVBY-HQ/" + VERSION + " (https://github.com/Obamna1234/sevby-hq)"


def _norm(t: str) -> str:
    """Lower-case a title and drop brackets, 'feat.' parts and punctuation, for comparing."""
    t = (t or "").lower().replace("&", " and ")
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


def _free_json(url: str):
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
    data = _free_json("https://api.jamendo.com/v3.0/tracks/?" + q)
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
    data = _free_json("https://archive.org/advancedsearch.php?" + params)
    docs = ((data or {}).get("response") or {}).get("docs") or []
    for d in docs[:4]:
        ident = d.get("identifier")
        meta = _free_json(f"https://archive.org/metadata/{urllib.parse.quote(ident or '')}") if ident else None
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
                    "year": None, "cover_url": cover_url, "title": title,
                    "item_page": f"https://archive.org/details/{urllib.parse.quote(ident)}"}
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
                if artist:
                    # The Archive's own album / cover text is whatever the uploader typed (it can be wrong).
                    # Prefer the official details from iTunes; use the Archive's only if iTunes has none.
                    try:
                        it = itunes_lookup(artist, title, query)
                    except Exception:
                        it = None
                    if query not in SONG_META:
                        apply_canon(meta, it)
                    if cand.get("source") == "Internet Archive":
                        for k in ("album", "year", "track", "track_total", "cover_url"):
                            if it and it.get(k) and not meta.get(k):
                                meta[k] = it[k]
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
                LAST_FREE["source"] = cand["source"]
                log(f"  OK ({cand['source']}) -> {safe}.{kind}  [{cand['source']}, {real}, Creative Commons]")
                if cand.get("page"):
                    log(f"  Credit: this track is on Jamendo - {cand['page']}")
                if cand.get("item_page"):
                    log(f"  Check the licence yourself: {cand['item_page']} "
                        "(the Archive's licence label is set by the uploader and is not verified)")
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


# -- Colours (same layout as the Android app, in HQ gold) --------------------------------------------
C_BG = "#141416"
C_SURFACE = "#1E1E22"
C_TEXT = "#F2F2F2"
C_MUTED = "#9A9AA2"
C_ACCENT = "#E5B93C"
C_ACCENT_H = "#C99B22"
C_ON_ACCENT = "#141416"
C_ERR = "#FF6B6B"
C_ERR_BG = "#C94A4A"

# Test switches (About -> Advanced)
DEBUG = {"bc_blocked": False}


# -- yt-dlp version / updates ---------------------------------------------------------------------
def ytdlp_version() -> str:
    try:
        return str(yt_dlp.version.__version__)
    except Exception:
        return "not installed"


def ver_tuple(v: str) -> tuple:
    """'2026.08.19.1' -> (2026, 8, 19, 1), compared number by number."""
    out = []
    for part in re.split(r"[.\-]", str(v or "")):
        m = re.match(r"\d+", part)
        out.append(int(m.group()) if m else 0)
    return tuple(out)


def ytdlp_latest_tag() -> str | None:
    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest",
            headers={"User-Agent": "SEVBY-HQ", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8")).get("tag_name") or None
    except Exception:
        return None


APP_REPO_URL = "https://github.com/Obamna1234/sevby-hq"
UPDATE_PRERELEASES = True  # HQ is a beta; the plain SEVBY build ignores pre-releases


def app_ver_cmp(a: str, b: str) -> int:
    """Compare two app versions. 1.0.1 > 1.0.0; a finished 1.0.0 beats 1.0.0-beta; 0.10.0 > 0.9.2."""
    def split(v):
        v = str(v or "").strip().lower().lstrip("v").split(" ")[0]
        core, _, pre = v.partition("-")
        nums = [int(x) if x.isdigit() else 0 for x in core.split(".")]
        parts = [x for x in re.split(r"[.\-]", pre) if x]
        return nums, parts
    ca, pa = split(a)
    cb, pb = split(b)
    n = max(len(ca), len(cb))
    ca += [0] * (n - len(ca))
    cb += [0] * (n - len(cb))
    if ca != cb:
        return 1 if ca > cb else -1
    if not pa and not pb:
        return 0
    if not pa:
        return 1
    if not pb:
        return -1
    rank = {"dev": 0, "alpha": 1, "beta": 2, "rc": 3}
    for i in range(max(len(pa), len(pb))):
        if i >= len(pa):
            return -1
        if i >= len(pb):
            return 1
        x, y = pa[i], pb[i]
        if x.isdigit() and y.isdigit():
            if int(x) != int(y):
                return 1 if int(x) > int(y) else -1
        elif x.isdigit() != y.isdigit():
            return 1 if x.isdigit() else -1
        elif x != y:
            rx, ry = rank.get(x, 2), rank.get(y, 2)
            return 1 if (rx, x) > (ry, y) else -1
    return 0


def app_pick_release(releases, include_pre: bool = False):
    """Newest desktop release from GitHub's /releases list: skips drafts, android-v... releases,
    releases with no files yet (still building) and (unless asked) pre-releases."""
    best = None
    plat = "windows" if sys.platform.startswith("win") else "macos" if sys.platform == "darwin" else "linux"
    for r in releases if isinstance(releases, list) else []:
        try:
            tag = str(r.get("tag_name") or "")
            if not tag or r.get("draft") or tag.lower().startswith("android"):
                continue
            if r.get("prerelease") and not include_pre:
                continue
            assets = [a for a in (r.get("assets") or []) if isinstance(a, dict)]
            if not assets:
                continue
            ver = tag.lstrip("vV")
            if not re.match(r"\d", ver):
                continue
            url = ""
            for a in assets:
                name = str(a.get("name") or "").lower()
                if plat in name or (plat == "windows" and name.endswith(".exe")):
                    url = a.get("browser_download_url") or ""
                    break
            rel = {"version": ver, "page": r.get("html_url") or APP_REPO_URL + "/releases", "url": url}
            if best is None or app_ver_cmp(ver, best["version"]) > 0:
                best = rel
        except Exception:
            continue
    return best


def app_latest_release():
    """(checked, release). checked is False when GitHub couldn't be reached."""
    try:
        path = APP_REPO_URL.split("github.com/")[1]
        req = urllib.request.Request(
            f"https://api.github.com/repos/{path}/releases?per_page=30",
            headers={"User-Agent": "SEVBY", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
        return True, app_pick_release(data, UPDATE_PRERELEASES)
    except Exception:
        return False, None


def download_ytdlp_update(tag: str) -> tuple[bool, str]:
    """Download the official yt-dlp release file into the user's folder. It is picked up on next start."""
    try:
        YTDLP_UPDATE_DIR.mkdir(parents=True, exist_ok=True)
        dest = YTDLP_UPDATE_DIR / "yt-dlp"
        tmp = YTDLP_UPDATE_DIR / "yt-dlp.part"
        url = f"https://github.com/yt-dlp/yt-dlp/releases/download/{tag}/yt-dlp"
        req = urllib.request.Request(url, headers={"User-Agent": "SEVBY-HQ"})
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
        import zipfile

        if not zipfile.is_zipfile(tmp):
            tmp.unlink(missing_ok=True)
            return False, "The download wasn't a valid yt-dlp file. Try again later."
        os.replace(tmp, dest)
        return True, "ok"
    except Exception as e:
        return False, f"Couldn't download the update ({e})."


def diagnostics_text() -> str:
    lines = [
        f"{APP_NAME} {VERSION}",
        f"Python {sys.version.split()[0]} on {sys.platform}" + (" (packaged app)" if getattr(sys, "frozen", False) else ""),
        f"yt-dlp {ytdlp_version()}" + (f"  [updated copy: {YTDLP_ACTIVE}]" if YTDLP_ACTIVE else "  [bundled copy]"),
        f"ffmpeg: {ffmpeg_path()}",
    ]
    try:
        out = subprocess.run([ffmpeg_path(), "-version"], capture_output=True, text=True, timeout=10,
                             creationflags=0x08000000 if os.name == "nt" else 0).stdout
        lines.append("  " + (out.splitlines()[0] if out else "no output"))
    except Exception as e:
        lines.append(f"  ffmpeg failed to run: {e}")
    lines.append(f"Jamendo key: {'set' if load_config().get('jamendo_id') else 'not set'}")
    lines.append(f"Drag and drop: {'yes' if _HAS_DND else 'no'}")
    lines.append(f"Log file: {LOG_PATH}")
    return "\n".join(lines)


def network_test_text() -> str:
    tests = [
        ("Bandcamp", "https://bandcamp.com/"),
        ("YouTube", "https://www.youtube.com/"),
        ("Jamendo", "https://api.jamendo.com/v3.0/"),
        ("Internet Archive", "https://archive.org/"),
        ("iTunes", "https://itunes.apple.com/search?term=test&limit=1"),
        ("GitHub", "https://api.github.com/"),
    ]
    out = []
    for name, url in tests:
        t0 = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
            with urllib.request.urlopen(req, timeout=10) as r:
                out.append(f"{name}: OK ({int((time.time() - t0) * 1000)} ms)")
        except urllib.error.HTTPError as e:
            out.append(f"{name}: reachable (HTTP {e.code})")
        except Exception as e:
            out.append(f"{name}: FAILED ({str(e)[:50]})")
    return "\n".join(out)


def _icon_png_b64(kind: str, color: str, size: int = 18) -> str:
    """A smooth (anti-aliased) line icon as a transparent PNG, drawn in pure Python (no extra libraries)."""
    import base64, math, struct, zlib
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    u = size / 24.0
    w = 2.1 * u

    def seg(px, py, x1, y1, x2, y2):
        dx, dy = x2 - x1, y2 - y1
        t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
        return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))

    def poly(px, py, pts):
        return min(seg(px, py, *pts[i], *pts[i + 1]) for i in range(len(pts) - 1))

    def dist(px, py):
        x, y = px / u, py / u
        if kind == "file":
            d = min(poly(x, y, [(5, 3), (14, 3), (19, 8), (19, 21), (5, 21), (5, 3)]),
                    poly(x, y, [(14, 3), (14, 8), (19, 8)]))
        elif kind == "globe":
            d = min(abs(math.hypot(x - 12, y - 12) - 9),
                    abs(math.hypot((x - 12) / 4.2, (y - 12) / 9) - 1) * 6.0,
                    seg(x, y, 3, 12, 21, 12))
        else:  # link: two interlocking capsules
            d = min(abs(seg(x, y, 4.6, 12, 10.2, 12) - 3.7), abs(seg(x, y, 13.8, 12, 19.4, 12) - 3.7))
        return d * u

    ss = 6
    rows = []
    for j in range(size):
        row = bytearray([0])
        for i in range(size):
            hit = 0
            for sj in range(ss):
                for si in range(ss):
                    if dist(i + (si + 0.5) / ss, j + (sj + 0.5) / ss) <= w / 2:
                        hit += 1
            row += bytes((r, g, b, round(255 * hit / (ss * ss))))
        rows.append(bytes(row))

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))
    return base64.b64encode(png).decode("ascii")


class IconButton(ctk.CTkFrame):
    """Outlined button with an icon + name on the first line and a small grey line under it."""

    def __init__(self, master, icon: str, title: str, sub: str, command, accent: str, height: int = 54):
        super().__init__(master, height=height, width=10, fg_color=C_BG, border_width=1,
                         border_color="#33333a", corner_radius=6)
        self.grid_propagate(False)
        self.pack_propagate(False)
        self._command, self._enabled = command, True
        inner = tk.Frame(self, bg=C_BG, bd=0, highlightthickness=0)
        inner.place(relx=0.5, rely=0.5, anchor="center")
        top = tk.Frame(inner, bg=C_BG, bd=0, highlightthickness=0)
        top.pack()
        self._img = tk.PhotoImage(data=_icon_png_b64(icon, accent))
        self._ic = tk.Label(top, image=self._img, bg=C_BG, bd=0, highlightthickness=0)
        self._ic.pack(side="left", padx=(0, 6))
        self._t = ctk.CTkLabel(top, text=title, text_color=C_TEXT, fg_color=C_BG, height=20, corner_radius=0,
                               font=ctk.CTkFont(size=13, weight="bold"))
        self._t.pack(side="left")
        self._s = ctk.CTkLabel(inner, text=sub, text_color=C_MUTED, fg_color=C_BG, height=16, corner_radius=0,
                               font=ctk.CTkFont(size=11))
        self._s.pack()
        self._tk_bg = (inner, top, self._ic)
        self._parts = (self, inner, top, self._ic, self._t, self._s)
        self.bind("<Enter>", lambda _e: self._hover(True))
        self.bind("<Leave>", lambda _e: self._hover(False))
        self.bind("<ButtonRelease-1>", self._click)

    def bind(self, seq, func, add=None):  # hover tips / clicks reach every part of the button
        for w in self._parts:
            if w is self:
                ctk.CTkFrame.bind(self, seq, func, add="+")
            else:
                w.bind(seq, func, add="+")

    def _paint(self, bg: str):
        ctk.CTkFrame.configure(self, fg_color=bg)
        for w in self._tk_bg:
            w.configure(bg=bg)
        self._t.configure(fg_color=bg)
        self._s.configure(fg_color=bg)

    def _hover(self, on: bool):
        if self._enabled:
            self._paint("#2a2a30" if on else C_BG)

    def _click(self, e):
        inside = (self.winfo_rootx() <= e.x_root <= self.winfo_rootx() + self.winfo_width()
                  and self.winfo_rooty() <= e.y_root <= self.winfo_rooty() + self.winfo_height())
        if self._enabled and inside and self._command:
            self._command()

    def configure(self, **kw):
        if "state" in kw:
            self._enabled = kw.pop("state") != "disabled"
            self._t.configure(text_color=C_TEXT if self._enabled else "#5a5a62")
            self._s.configure(text_color=C_MUTED if self._enabled else "#4a4a52")
            self._paint(C_BG)
        if kw:
            super().configure(**kw)


class Tooltip:
    """Small hover message for any widget (shows after a short pause, hides when the mouse leaves)."""

    _all: list = []

    def __init__(self, widget, text: str, delay: int = 450, wrap: int = 300):
        Tooltip._all.append(self)
        self.widget, self.text, self.delay, self.wrap = widget, text, delay, wrap
        self._job = None
        self._win = None
        for ev, fn in (("<Enter>", self._schedule), ("<Leave>", self._hide), ("<ButtonPress>", self._hide)):
            try:
                widget.bind(ev, fn, add="+")
            except Exception:
                pass

    def _schedule(self, _e=None):
        self._cancel()
        self._job = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._job:
            try:
                self.widget.after_cancel(self._job)
            except Exception:
                pass
            self._job = None

    def _show(self):
        self._job = None
        if self._win is not None:
            return
        try:
            x = self.widget.winfo_rootx() + 12
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
            win = tk.Toplevel(self.widget)
            win.wm_overrideredirect(True)
            win.attributes("-topmost", True)
            tk.Label(win, text=self.text, justify="left", wraplength=self.wrap, bg="#2A2A30", fg="#F2F2F2",
                     relief="solid", borderwidth=1, padx=9, pady=6, font=("Segoe UI", 9)).pack()
            win.update_idletasks()
            sw = win.winfo_screenwidth()
            x = max(0, min(x, sw - win.winfo_reqwidth() - 8))
            win.wm_geometry(f"+{x}+{y}")
            self._win = win
        except Exception:
            self._win = None

    def _hide(self, _e=None):
        self._cancel()
        if self._win is not None:
            try:
                self._win.destroy()
            except Exception:
                pass
            self._win = None


class SevbyApp(_SevbyBase):
    SOURCE_CHOICES = (
        ("Both", "Bandcamp + YouTube", "Bandcamp first, YouTube for anything Bandcamp doesn't have"),
        ("Bandcamp only", "Bandcamp only", "Songs that aren't on Bandcamp are skipped"),
        ("YouTube only", "YouTube only", "Everything comes from YouTube"),
    )
    QUALITIES = ("Best available (M4A)", "Standard (MP3)")

    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} v{VERSION}: Playlist to MP3")
        self._apply_icon(self)
        self.configure(fg_color=C_BG)
        self.minsize(720, 700)
        self._center_window(760, 900)

        cfg = load_config()
        self.run_mode: str | None = None  # which mode the running job belongs to
        self.running = False
        self._job: dict | None = None
        self.failed_items: list[tuple[str, str, dict]] = []  # (song, folder, job) of the last finished run
        self.queue: list[dict] = list(cfg.get("queue") or [])
        for j in self.queue:
            j.pop("_running", None)
        self._loaded_name = ""  # name of the .txt file last loaded (used to name its queue folder)
        self._cur_start = None  # index in the song list where the current song's entry begins
        self._list_i, self._list_n = 1, 1
        self._counts = {"ok": 0, "skip": 0, "fail": 0}
        self._ytdlp_latest: str | None = None
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        page = ctk.CTkScrollableFrame(self, fg_color=C_BG, corner_radius=0)
        page.pack(fill="both", expand=True)
        self.page = page

        # -- Header ---------------------------------------------------------------------------
        head = ctk.CTkFrame(page, fg_color="transparent")
        head.pack(fill="x", padx=22, pady=(14, 0))
        ctk.CTkLabel(head, text="SEVBY HQ", text_color=C_ACCENT,
                     font=ctk.CTkFont(size=28, weight="bold")).pack(side="left")
        ctk.CTkButton(head, text="About & updates", width=130, height=30, fg_color="transparent",
                      border_width=1, border_color=C_MUTED, text_color=C_TEXT, hover_color=C_SURFACE,
                      command=self.show_about).pack(side="right")
        self.status_label = ctk.CTkLabel(page, text="", anchor="w", text_color=C_MUTED,
                                         font=ctk.CTkFont(size=11))
        self.status_label.pack(fill="x", padx=24, pady=(0, 4))
        self.banner = ctk.CTkFrame(page, fg_color=C_SURFACE, corner_radius=8)  # shown when yt-dlp is outdated
        self.banner_label = ctk.CTkLabel(self.banner, text="", anchor="w", text_color=C_ACCENT)
        self.banner_label.pack(side="left", padx=12, pady=8)
        ctk.CTkButton(self.banner, text="Update", width=80, height=28, fg_color=C_ACCENT,
                      hover_color=C_ACCENT_H, text_color=C_ON_ACCENT,
                      command=self.show_about).pack(side="right", padx=10, pady=6)

        self.app_banner = ctk.CTkFrame(page, fg_color=C_SURFACE, corner_radius=8)  # shown when a newer SEVBY exists
        self.app_banner_label = ctk.CTkLabel(self.app_banner, text="", anchor="w", text_color=C_ACCENT)
        self.app_banner_label.pack(side="left", padx=12, pady=8)
        ctk.CTkButton(self.app_banner, text="Later", width=60, height=28, fg_color="transparent",
                      hover_color="#2a2a30", text_color=C_MUTED,
                      command=self.app_banner.pack_forget).pack(side="right", padx=(0, 10), pady=6)
        ctk.CTkButton(self.app_banner, text="Download", width=90, height=28, fg_color=C_ACCENT,
                      hover_color=C_ACCENT_H, text_color=C_ON_ACCENT,
                      command=self.open_app_update).pack(side="right", padx=6, pady=6)
        self._app_release = None

        # -- Mode switch ----------------------------------------------------------------------
        self.mode = ctk.StringVar(value=cfg.get("last_mode") if cfg.get("last_mode") in ("txt", "spotify") else "txt")
        mode_frame = ctk.CTkFrame(page, fg_color="transparent")
        mode_frame.pack(fill="x", padx=22, pady=(6, 0))
        self.mode_radios = [
            ctk.CTkRadioButton(mode_frame, text="Song list / .txt file", variable=self.mode, value="txt",
                               command=self._switch_mode, fg_color=C_ACCENT, hover_color=C_ACCENT_H),
            ctk.CTkRadioButton(mode_frame, text="Spotify playlist link", variable=self.mode, value="spotify",
                               command=self._switch_mode, fg_color=C_ACCENT, hover_color=C_ACCENT_H),
        ]
        self.mode_radios[0].pack(side="left", padx=(0, 20))
        self.mode_radios[1].pack(side="left")

        # -- Song list card -------------------------------------------------------------------
        self.txt_frame = ctk.CTkFrame(page, fg_color=C_SURFACE, corner_radius=10)
        self.songs_box = ctk.CTkTextbox(self.txt_frame, height=130, fg_color=C_BG, border_width=1,
                                        border_color="#33333a")
        self.songs_box.pack(fill="x", padx=12, pady=(12, 6))
        self.hint = ctk.CTkLabel(self.songs_box, text="Paste your songs here, one per line:  Artist - Title",
                                 text_color=C_MUTED, font=ctk.CTkFont(size=12))
        self.hint.place(x=10, y=7)
        self.hint.bind("<Button-1>", lambda _e: self.songs_box.focus_set())
        btn_row = ctk.CTkFrame(self.txt_frame, fg_color="transparent")
        btn_row.pack(fill="x", padx=12, pady=(0, 6))
        for _c, _w in enumerate((10, 11, 11, 0)):
            btn_row.grid_columnconfigure(_c, weight=_w, uniform="sb" if _w else "")
        self.load_btn = IconButton(btn_row, "file", "Import", ".txt or .csv", self.load_txt, C_ACCENT)
        self.load_btn.grid(row=0, column=0, sticky="ew")
        self.chosic_btn = IconButton(btn_row, "globe", "Chosic", "Spotify playlists", self.open_chosic, C_ACCENT)
        self.chosic_btn.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        self.apple_btn = IconButton(btn_row, "link", "Apple Music", "paste a link", self.open_apple_dialog, C_ACCENT)
        self.apple_btn.grid(row=0, column=2, sticky="ew", padx=(6, 0))
        Tooltip(self.load_btn, "Import songs from a file: .txt (one 'Artist - Title' per line), .csv (e.g. from "
                               "TuneMyMusic or Soundiiz), .tsv or .m3u playlists.")
        Tooltip(self.chosic_btn, "Opens the Chosic website in your browser. Paste a PUBLIC Spotify playlist link "
                                 "there, then copy or download the song list it gives you. Back here, paste it "
                                 "into the box above or click Import (.txt or .csv). No Spotify account or keys "
                                 "needed.")
        Tooltip(self.apple_btn, "Opens a small window. Paste a PUBLIC Apple Music playlist or album link "
                                "(Share > Copy Link) and click Get songs. If you already copied the link, it is "
                                "filled in for you. The songs then appear in the box above.")
        self.clear_songs_btn = ctk.CTkButton(btn_row, text="Clear list", width=84, height=32,
                                             fg_color="transparent", border_width=1, border_color=C_ERR,
                                             text_color=C_ERR, hover_color="#2a2a30", command=self.clear_songs)
        self.clear_songs_btn.grid(row=0, column=3, sticky="ne", padx=(14, 0))
        self.count_label = ctk.CTkLabel(self.txt_frame, text="0 songs", text_color=C_MUTED, anchor="w")
        self.count_label.pack(fill="x", padx=14, pady=(0, 6))
        self.add_btn = ctk.CTkButton(self.txt_frame, text="Add to queue", height=34, fg_color="transparent",
                                     border_width=1, border_color=C_ACCENT, text_color=C_ACCENT,
                                     hover_color="#2a2a30", command=self.add_to_queue)
        self.add_btn.pack(fill="x", padx=12, pady=(0, 12))

        # -- Spotify card ---------------------------------------------------------------------
        self.sp_frame = ctk.CTkFrame(page, fg_color=C_SURFACE, corner_radius=10)
        ctk.CTkLabel(self.sp_frame, text="Public & private playlists \u00b7 one-time login saved on this PC",
                     text_color=C_MUTED, font=ctk.CTkFont(size=11)).pack(anchor="w", padx=12, pady=(10, 2))
        ctk.CTkLabel(self.sp_frame, text="Playlist URL  (paste or drop a Spotify link)").pack(
            anchor="w", padx=12, pady=(6, 2))
        self.sp_url = ctk.CTkEntry(self.sp_frame, placeholder_text="https://open.spotify.com/playlist/...")
        self.sp_url.pack(fill="x", padx=12, pady=2)
        key_row = ctk.CTkFrame(self.sp_frame, fg_color="transparent")
        key_row.pack(fill="x", padx=12, pady=(8, 2))
        ctk.CTkLabel(key_row, text="Client ID").pack(side="left")
        ctk.CTkButton(key_row, text="What is this?", width=100, height=24, fg_color="transparent",
                      border_width=1, command=self.show_client_id_help).pack(side="left", padx=(10, 0))
        ctk.CTkButton(key_row, text="Open Spotify Dashboard", width=160, height=24, fg_color=C_ACCENT,
                      hover_color=C_ACCENT_H, text_color=C_ON_ACCENT,
                      command=self.open_spotify_dashboard).pack(side="left", padx=(8, 0))
        self.client_id = ctk.CTkEntry(self.sp_frame, placeholder_text="Client ID")
        self.client_id.pack(fill="x", padx=12, pady=2)
        self.client_secret = ctk.CTkEntry(self.sp_frame, placeholder_text="Client Secret (optional, not needed)",
                                          show="\u2022")
        self.client_secret.pack(fill="x", padx=12, pady=2)
        chosic_row = ctk.CTkFrame(self.sp_frame, fg_color="transparent")
        chosic_row.pack(fill="x", padx=12, pady=(8, 10))
        ctk.CTkLabel(chosic_row, text="No keys? Use the Chosic website with a PUBLIC playlist link:",
                     text_color=C_MUTED, font=ctk.CTkFont(size=11)).pack(side="left")
        _cb = ctk.CTkButton(chosic_row, text="Open Chosic website", width=150, height=28, fg_color=C_ACCENT,
                            hover_color=C_ACCENT_H, text_color=C_ON_ACCENT, command=self.open_chosic)
        _cb.pack(side="right")
        Tooltip(_cb, "Opens the Chosic website in your browser. Paste a PUBLIC Spotify playlist link there, "
                     "then copy or download the song list it gives you. Back here, switch to the Song list tab "
                     "and paste it, or click Import (.txt or .csv). No Spotify account or keys needed.")
        if cfg.get("client_id"):
            self.client_id.insert(0, cfg["client_id"])
        if cfg.get("client_secret"):
            self.client_secret.insert(0, cfg["client_secret"])

        # -- Queue card (only when it has lists) ----------------------------------------------
        self.queue_card = ctk.CTkFrame(page, fg_color=C_SURFACE, corner_radius=10)
        qh = ctk.CTkFrame(self.queue_card, fg_color="transparent")
        qh.pack(fill="x", padx=12, pady=(10, 0))
        self.queue_title = ctk.CTkLabel(qh, text="Queue", font=ctk.CTkFont(size=14, weight="bold"))
        self.queue_title.pack(side="left")
        self.clear_btn = ctk.CTkButton(qh, text="Clear queue", width=90, height=26, fg_color="transparent",
                                       border_width=1, border_color=C_MUTED, text_color=C_TEXT,
                                       hover_color="#2a2a30", command=self.clear_queue)
        self.clear_btn.pack(side="right")
        ctk.CTkLabel(self.queue_card, text="Each list goes into its own sub-folder, one after another.",
                     text_color=C_MUTED, font=ctk.CTkFont(size=11), anchor="w").pack(
            fill="x", padx=12, pady=(0, 4))
        self.queue_rows = ctk.CTkFrame(self.queue_card, fg_color="transparent")
        self.queue_rows.pack(fill="x", padx=12, pady=(0, 10))

        # -- Save folder ----------------------------------------------------------------------
        self.folder_label = ctk.CTkLabel(page, text="Save to folder", anchor="w",
                                         font=ctk.CTkFont(size=13, weight="bold"))
        self.folder_label.pack(fill="x", padx=24, pady=(14, 2))
        folder_row = ctk.CTkFrame(page, fg_color="transparent")
        folder_row.pack(fill="x", padx=22)
        self.folder_entry = ctk.CTkEntry(folder_row, placeholder_text="Choose where your music will be saved")
        self.folder_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.browse_btn = ctk.CTkButton(folder_row, text="Choose", width=90, fg_color=C_ACCENT,
                                        hover_color=C_ACCENT_H, text_color=C_ON_ACCENT,
                                        command=self.browse_folder)
        self.browse_btn.pack(side="right")
        ctk.CTkLabel(page, text="Queued lists each get their own sub-folder here.",
                     anchor="w", text_color=C_MUTED, font=ctk.CTkFont(size=11), wraplength=650,
                     justify="left").pack(fill="x", padx=24)
        if cfg.get("last_folder") and os.path.isdir(cfg["last_folder"]):
            self.folder_entry.insert(0, cfg["last_folder"])

        # -- Source (three radio choices with a one-line explanation) -------------------------
        ctk.CTkLabel(page, text="Where to look", anchor="w", font=ctk.CTkFont(size=13, weight="bold")).pack(
            fill="x", padx=24, pady=(14, 2))
        saved_src = cfg.get("source") if cfg.get("source") in [c[0] for c in self.SOURCE_CHOICES] else "Both"
        self.source = ctk.StringVar(value=saved_src)
        self.source_radios = []
        for value, label, expl in self.SOURCE_CHOICES:
            rb = ctk.CTkRadioButton(page, text=label, variable=self.source, value=value,
                                    command=self._save_prefs, fg_color=C_ACCENT, hover_color=C_ACCENT_H)
            rb.pack(anchor="w", padx=28, pady=(4, 0))
            ctk.CTkLabel(page, text=expl, text_color=C_MUTED, font=ctk.CTkFont(size=11), anchor="w").pack(
                fill="x", padx=52)
            self.source_radios.append(rb)

        # -- HQ options -----------------------------------------------------------------------
        hq = ctk.CTkFrame(page, fg_color=C_SURFACE, corner_radius=10)
        hq.pack(fill="x", padx=22, pady=(14, 0))
        ctk.CTkLabel(hq, text="Audio quality", anchor="w", font=ctk.CTkFont(size=13, weight="bold")).pack(
            fill="x", padx=14, pady=(10, 0))
        self.quality = ctk.StringVar(
            value=cfg.get("quality") if cfg.get("quality") in self.QUALITIES else self.QUALITIES[0])
        self.quality_radios = []
        for q, expl in (("Best available (M4A)", "Keeps YouTube's original audio (no re-encoding) as M4A"),
                        ("Standard (MP3)", "Converts everything to MP3 (plays on any device)")):
            rb = ctk.CTkRadioButton(hq, text=q, variable=self.quality, value=q, command=self._save_prefs,
                                    fg_color=C_ACCENT, hover_color=C_ACCENT_H)
            rb.pack(anchor="w", padx=18, pady=(6, 0))
            ctk.CTkLabel(hq, text=expl, text_color=C_MUTED, font=ctk.CTkFont(size=11), anchor="w").pack(
                fill="x", padx=42)
            self.quality_radios.append(rb)
        self.free_hq = ctk.BooleanVar(value=bool(cfg.get("free_sources", False)))
        self.free_chk = ctk.CTkCheckBox(hq, text="Also check Jamendo + Internet Archive first (off by default)",
                                        variable=self.free_hq, command=self._on_free_toggle,
                                        fg_color=C_ACCENT, hover_color=C_ACCENT_H, checkmark_color=C_ON_ACCENT)
        self.free_chk.pack(anchor="w", padx=18, pady=(10, 0))
        ctk.CTkLabel(hq, text="Creative Commons copies, lossless (FLAC) when found. Tick to read what this means",
                     text_color=C_MUTED, font=ctk.CTkFont(size=11), anchor="w").pack(fill="x", padx=44)
        j_row = ctk.CTkFrame(hq, fg_color="transparent")
        j_row.pack(fill="x", padx=18, pady=(6, 12))
        ctk.CTkLabel(j_row, text="Your own Jamendo Client ID (optional)", text_color=C_MUTED,
                     font=ctk.CTkFont(size=11)).pack(side="left", padx=(0, 8))
        self.jamendo_id = ctk.CTkEntry(j_row, placeholder_text="from devportal.jamendo.com", width=220, height=26)
        self.jamendo_id.pack(side="left")
        if cfg.get("jamendo_id"):
            self.jamendo_id.insert(0, cfg["jamendo_id"])
        self.jamendo_id.bind("<FocusOut>", lambda _e: self._save_prefs())

        # -- Start / Pause --------------------------------------------------------------------
        action_row = ctk.CTkFrame(page, fg_color="transparent")
        action_row.pack(fill="x", padx=22, pady=(16, 6))
        self.start_btn = ctk.CTkButton(action_row, text="Start Download", height=46, fg_color=C_ACCENT,
                                       hover_color=C_ACCENT_H, text_color=C_ON_ACCENT,
                                       font=ctk.CTkFont(size=16, weight="bold"), command=self.start)
        self.start_btn.pack(side="left", fill="x", expand=True)
        self.pause_btn = ctk.CTkButton(action_row, text="Pause", width=96, height=46, fg_color=C_SURFACE,
                                       hover_color="#2a2a30", text_color=C_TEXT, state="disabled",
                                       command=self.toggle_pause)
        self.pause_btn.pack(side="right", padx=(8, 0))
        util_row = ctk.CTkFrame(page, fg_color="transparent")
        util_row.pack(fill="x", padx=22, pady=(0, 6))
        ctk.CTkButton(util_row, text="Open folder", width=110, height=28, fg_color="transparent",
                      border_width=1, border_color=C_MUTED, text_color=C_TEXT, hover_color=C_SURFACE,
                      command=self.open_folder).pack(side="left")
        self.retry_btn = ctk.CTkButton(util_row, text="Retry failed", width=140, height=28, fg_color=C_ACCENT,
                                       hover_color=C_ACCENT_H, text_color=C_ON_ACCENT, command=self.retry_failed)
        # (the Retry button only appears after a run with failures)

        # -- Progress area (only while running) ------------------------------------------------
        self.prog_frame = ctk.CTkFrame(page, fg_color=C_SURFACE, corner_radius=10)
        self.list_heading = ctk.CTkLabel(self.prog_frame, text="", anchor="w", text_color=C_ACCENT)
        p1 = ctk.CTkFrame(self.prog_frame, fg_color="transparent")
        p1.pack(fill="x", padx=12, pady=(10, 0))
        self.prog_song = ctk.CTkLabel(p1, text="", anchor="w", justify="left")
        self.prog_song.pack(side="left", fill="x", expand=True)
        self.prog_pct = ctk.CTkLabel(p1, text="0%", text_color=C_ACCENT, font=ctk.CTkFont(weight="bold"))
        self.prog_pct.pack(side="right")
        self.prog_step = ctk.CTkLabel(self.prog_frame, text="", anchor="w", text_color=C_MUTED,
                                      font=ctk.CTkFont(size=12))
        self.prog_step.pack(fill="x", padx=12)
        self.progress = ctk.CTkProgressBar(self.prog_frame, height=10, progress_color=C_ACCENT,
                                           fg_color="#33333a")
        self.progress.pack(fill="x", padx=12, pady=(6, 12))
        self.progress.set(0)

        # -- Log (collapsible) ----------------------------------------------------------------
        self.log_card = ctk.CTkFrame(page, fg_color=C_SURFACE, corner_radius=10)
        self.log_card.pack(fill="x", padx=22, pady=(10, 18))
        self.log_open = bool(cfg.get("log_open", True))
        self.log_head = ctk.CTkButton(self.log_card, text="", anchor="w", height=34, fg_color="transparent",
                                      hover_color="#2a2a30", text_color=C_TEXT, command=self._toggle_log)
        self.log_head.pack(fill="x", padx=4, pady=2)
        self.log_body = ctk.CTkFrame(self.log_card, fg_color="transparent")
        self.songs_view = ctk.CTkTextbox(self.log_body, height=320, state="disabled", fg_color=C_BG,
                                         wrap="word")
        self.details_box = ctk.CTkTextbox(self.log_body, height=320, state="disabled", fg_color=C_BG,
                                          font=ctk.CTkFont(family="Consolas", size=11), wrap="word")
        tb = self.songs_view._textbox
        tb.tag_config("head", foreground=C_ACCENT, font=("Segoe UI", 10, "bold"))
        tb.tag_config("ok", foreground=C_ACCENT)
        tb.tag_config("skip", foreground=C_MUTED)
        tb.tag_config("fail", foreground=C_ERR)
        tb.tag_config("now", foreground=C_ACCENT)
        tb.tag_config("note", foreground=C_MUTED, lmargin1=18, lmargin2=18)
        tb.tag_config("noterr", foreground=C_ERR, lmargin1=18, lmargin2=18)
        tb.tag_config("sum", foreground=C_TEXT, font=("Segoe UI", 10, "bold"))
        self.show_details = False
        self.songs_view.pack(fill="x", padx=8, pady=(0, 4))
        btns = ctk.CTkFrame(self.log_body, fg_color="transparent")
        btns.pack(fill="x", padx=8, pady=(0, 10))
        self.details_btn = ctk.CTkButton(btns, text="Details", width=90, height=28, fg_color=C_BG,
                                         hover_color="#2a2a30", text_color=C_TEXT, command=self._toggle_details)
        self.details_btn.pack(side="left")
        ctk.CTkButton(btns, text="Copy log", width=90, height=28, fg_color=C_BG, hover_color="#2a2a30",
                      text_color=C_TEXT, command=self.copy_log).pack(side="left", padx=(8, 0))
        self._log_header = "Log"
        self._apply_log_open()

        self._switch_mode()
        self._refresh_queue_ui()
        self._setup_drag_drop()
        self._poll_box()
        self._init_status()

    # ---------------------------------------------------------------------------------------
    # small UI helpers
    def _poll_box(self):
        """Keep the song count and the hint text in step with what's in the box."""
        try:
            text = self.songs_box.get("1.0", "end")
            n = len(parse_song_lines(text))
            self.count_label.configure(text=f"{n} song" + ("" if n == 1 else "s") + "")
            if text.strip():
                self.hint.place_forget()
            else:
                self.hint.place(x=10, y=7)
        except Exception:
            pass
        self.after(400, self._poll_box)

    def clear_songs(self):
        self.songs_box.delete("1.0", "end")
        self._loaded_name = ""
        if getattr(self, "_resume_jobs", None):
            self._resume_jobs = None
            self._refresh_queue_ui()

    def _switch_mode(self):
        # Always insert the input panel just above "Save folder" so the layout never shuffles.
        self.sp_frame.pack_forget()
        self.txt_frame.pack_forget()
        mode = self.mode.get()
        frame = self.txt_frame if mode == "txt" else self.sp_frame
        frame.pack(fill="x", padx=22, pady=(12, 0), before=self.queue_card if self.queue_card.winfo_ismapped()
                   else self.folder_label)
        self._save_prefs()

    def _save_prefs(self):
        try:
            cfg = load_config()
            cfg["last_mode"] = self.mode.get()
            cfg["source"] = self.source.get()
            cfg["quality"] = self.quality.get()
            cfg["free_sources"] = bool(self.free_hq.get())
            cfg["jamendo_id"] = self.jamendo_id.get().strip()
            cfg["log_open"] = bool(self.log_open)
            folder = self.folder_entry.get().strip()
            if folder and os.path.isdir(folder):
                cfg["last_folder"] = folder
            save_config(cfg)
        except Exception:
            pass

    def _save_queue(self):
        try:
            cfg = load_config()
            cfg["queue"] = [{k: v for k, v in j.items() if not k.startswith("_") and k != "quiet"}
                            for j in self.queue]
            save_config(cfg)
        except Exception:
            pass

    def _on_close(self):
        self._save_prefs()
        self.destroy()

    def _set_inputs(self, enabled: bool):
        st = "normal" if enabled else "disabled"
        for w in (self.songs_box, self.folder_entry):
            w.configure(state=st)
        for w in (self.load_btn, self.clear_songs_btn, self.add_btn, self.browse_btn, self.chosic_btn, self.apple_btn,
                  self.free_chk, *self.source_radios, *self.quality_radios, *self.mode_radios):
            w.configure(state=st)
        self.jamendo_id.configure(state=st)

    # ---------------------------------------------------------------------------------------
    # status line, update banner, About
    def _init_status(self):
        self._set_status("Ready")
        threading.Thread(target=self._check_ytdlp_daily, daemon=True).start()
        threading.Thread(target=self._check_app_daily, daemon=True).start()

    def _set_status(self, text: str):
        self.status_label.configure(text=f"{text} \u00b7 yt-dlp {ytdlp_version()}")

    def _check_ytdlp_daily(self):
        try:
            cfg = load_config()
            now = time.time()
            latest = cfg.get("yt_latest")
            if not latest or now - float(cfg.get("yt_check_time") or 0) > 86400:
                latest = ytdlp_latest_tag()
                if latest:
                    cfg = load_config()
                    cfg["yt_latest"], cfg["yt_check_time"] = latest, now
                    save_config(cfg)
            self._ytdlp_latest = latest
            if latest and ver_tuple(latest) > ver_tuple(ytdlp_version()):
                self.after(0, lambda: self._show_banner(latest))
        except Exception:
            pass

    def _check_app_daily(self, force: bool = False):
        """Once a day, look for a newer SEVBY release on GitHub (remembers the answer)."""
        try:
            cfg = load_config()
            now = time.time()
            if force or now - float(cfg.get("app_check_time") or 0) > 86400:
                checked, rel = app_latest_release()
                if checked:
                    cfg = load_config()
                    cfg["app_latest"] = rel or {}
                    cfg["app_check_time"] = now
                    save_config(cfg)
            self._app_release = self._app_available(load_config().get("app_latest"))
            if self._app_release:
                self.after(0, self._show_app_banner)
        except Exception:
            pass

    @staticmethod
    def _app_available(rel):
        if isinstance(rel, dict) and rel.get("version") and app_ver_cmp(rel["version"], VERSION) > 0:
            return rel
        return None

    def _show_app_banner(self):
        rel = self._app_release
        if not rel:
            return
        self.app_banner_label.configure(text=f"{APP_NAME} {rel['version']} is available")
        if not self.app_banner.winfo_ismapped():
            self.app_banner.pack(fill="x", padx=22, pady=(0, 6), after=self.status_label)

    def open_app_update(self):
        rel = self._app_release
        if not rel:
            return
        if self.running and not messagebox.askokcancel(
                APP_NAME, "Your browser will download the new version. Updating means closing SEVBY, which "
                          "stops a download in progress. Resume carries on afterwards. Continue?"):
            return
        webbrowser.open(rel.get("url") or rel.get("page") or APP_REPO_URL + "/releases")

    def _show_banner(self, latest: str):
        self.banner_label.configure(text=f"A newer yt-dlp ({latest}) is available")
        if not self.banner.winfo_ismapped():
            self.banner.pack(fill="x", padx=22, pady=(0, 6), after=self.status_label)

    def show_about(self):
        dlg = ctk.CTkToplevel(self)
        dlg.title("About & updates")
        self._apply_icon(dlg)
        dlg.configure(fg_color=C_BG)
        dlg.transient(self)
        w, h = 520, 720
        self.update_idletasks()
        x = self.winfo_rootx() + max(0, (self.winfo_width() - w) // 2)
        y = self.winfo_rooty() + 20
        dlg.geometry(f"{w}x{h}+{x}+{y}")
        body = ctk.CTkScrollableFrame(dlg, fg_color=C_BG)
        body.pack(fill="both", expand=True)

        acard = ctk.CTkFrame(body, fg_color=C_SURFACE, corner_radius=10)
        acard.pack(fill="x", padx=14, pady=(12, 0))
        ctk.CTkLabel(acard, text=APP_NAME, font=ctk.CTkFont(size=15, weight="bold"),
                     anchor="w").pack(fill="x", padx=14, pady=(12, 0))
        ctk.CTkLabel(acard, text=f"Version {VERSION}", anchor="w").pack(fill="x", padx=14)
        astatus = ctk.CTkLabel(acard, text="", anchor="w", text_color=C_MUTED)
        astatus.pack(fill="x", padx=14)
        abtn = ctk.CTkButton(acard, text="Check for app updates", height=40, fg_color=C_ACCENT,
                             hover_color=C_ACCENT_H, text_color=C_ON_ACCENT)
        abtn.pack(fill="x", padx=14, pady=(8, 6))
        ctk.CTkLabel(acard, justify="left", anchor="w", wraplength=440, text_color=C_MUTED,
                     font=ctk.CTkFont(size=11),
                     text="New versions of SEVBY (fixes and features) come from the project's GitHub releases. "
                          "Your browser downloads the new file; close SEVBY and run it instead. Your folder, "
                          "settings and queue are kept.").pack(fill="x", padx=14, pady=(0, 12))

        def refresh_app():
            rel = self._app_release
            if rel:
                astatus.configure(text=f"Update available: {rel['version']}", text_color=C_ACCENT)
                abtn.configure(text=f"Download {rel['version']}", command=self.open_app_update, state="normal")
            else:
                done_once = bool(load_config().get("app_check_time"))
                astatus.configure(text="Up to date \u2713" if done_once else "", text_color=C_MUTED)
                abtn.configure(text="Check for app updates", command=do_app_check, state="normal")

        def do_app_check():
            abtn.configure(state="disabled", text="Checking\u2026")

            def work():
                checked, rel = app_latest_release()
                def done():
                    if checked:
                        cfg = load_config()
                        cfg["app_latest"] = rel or {}
                        cfg["app_check_time"] = time.time()
                        save_config(cfg)
                        self._app_release = self._app_available(rel)
                        if self._app_release:
                            self._show_app_banner()
                        refresh_app()
                    else:
                        astatus.configure(text="Couldn't reach GitHub. Check your internet connection.",
                                          text_color=C_ERR)
                        abtn.configure(state="normal", text="Check for app updates")
                self.after(0, done)
            threading.Thread(target=work, daemon=True).start()

        refresh_app()

        card = ctk.CTkFrame(body, fg_color=C_SURFACE, corner_radius=10)
        card.pack(fill="x", padx=14, pady=(8, 8))
        ctk.CTkLabel(card, text="Downloader (yt-dlp)", font=ctk.CTkFont(size=15, weight="bold"),
                     anchor="w").pack(fill="x", padx=14, pady=(12, 0))
        ver = ctk.CTkLabel(card, text=f"Version {ytdlp_version()}", anchor="w")
        ver.pack(fill="x", padx=14)
        status = ctk.CTkLabel(card, text="", anchor="w", text_color=C_MUTED)
        status.pack(fill="x", padx=14)
        btn = ctk.CTkButton(card, text="Check for updates", height=40, fg_color=C_ACCENT,
                            hover_color=C_ACCENT_H, text_color=C_ON_ACCENT)
        btn.pack(fill="x", padx=14, pady=(8, 6))
        ctk.CTkLabel(card, justify="left", anchor="w", wraplength=440, text_color=C_MUTED,
                     font=ctk.CTkFont(size=11),
                     text="YouTube changes often. If downloads suddenly start failing, update here first. "
                          "SEVBY HQ also checks once a day and shows a message on the main screen when an "
                          "update is out.").pack(fill="x", padx=14, pady=(0, 12))

        state = {"latest": self._ytdlp_latest}

        def refresh_status():
            cur = ytdlp_version()
            latest = state["latest"]
            if latest and ver_tuple(latest) > ver_tuple(cur):
                status.configure(text=f"Update available: {latest}", text_color=C_ACCENT)
                btn.configure(text=f"Update to {latest}", command=do_update)
            else:
                status.configure(text="Up to date \u2713" if latest else "", text_color=C_MUTED)
                btn.configure(text="Check for updates", command=do_check)

        def do_check():
            btn.configure(state="disabled", text="Checking\u2026")

            def work():
                latest = ytdlp_latest_tag()
                def done():
                    btn.configure(state="normal")
                    if latest:
                        state["latest"] = self._ytdlp_latest = latest
                        cfg = load_config()
                        cfg["yt_latest"], cfg["yt_check_time"] = latest, time.time()
                        save_config(cfg)
                    else:
                        status.configure(text="Couldn't reach GitHub. Check your internet connection.",
                                         text_color=C_ERR)
                    refresh_status() if latest else btn.configure(text="Check for updates")
                self.after(0, done)
            threading.Thread(target=work, daemon=True).start()

        def do_update():
            latest = state["latest"]
            btn.configure(state="disabled", text="Updating\u2026")
            status.configure(text="Downloading the newest yt-dlp\u2026", text_color=C_MUTED)

            def work():
                ok, msg = download_ytdlp_update(latest)
                def done():
                    if ok:
                        status.configure(text=f"Updated to {latest}. Restart SEVBY HQ to use it.",
                                         text_color=C_ACCENT)
                        btn.configure(state="normal", text="Restart now", command=self.restart_app)
                        self.banner.pack_forget()
                    else:
                        status.configure(text=msg, text_color=C_ERR)
                        btn.configure(state="normal", text=f"Update to {latest}")
                self.after(0, done)
            threading.Thread(target=work, daemon=True).start()

        refresh_status()

        about = ctk.CTkFrame(body, fg_color=C_SURFACE, corner_radius=10)
        about.pack(fill="x", padx=14, pady=8)
        ctk.CTkLabel(about, text=f"About {APP_NAME} v{VERSION}", font=ctk.CTkFont(size=15, weight="bold"),
                     anchor="w").pack(fill="x", padx=14, pady=(12, 4))
        ctk.CTkLabel(
            about, justify="left", anchor="w", wraplength=440, font=ctk.CTkFont(size=12),
            text=("Turns a list of songs into tagged audio files on your own computer. It checks free "
                  "Creative Commons sources for lossless files first, then Bandcamp, then YouTube.\n\n"
                  "Built on yt-dlp, FFmpeg and customtkinter, which do the heavy lifting. Not affiliated "
                  "with Bandcamp, YouTube, Spotify, Jamendo, the Internet Archive or Apple.\n\n"
                  "Please support artists: if you like a song, buy it on Bandcamp.\n"
                  "Only download music you have the right to download."),
        ).pack(fill="x", padx=14)
        row = ctk.CTkFrame(about, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(8, 12))
        ctk.CTkButton(row, text="Project page", width=110, fg_color=C_BG, hover_color="#2a2a30",
                      text_color=C_TEXT,
                      command=lambda: webbrowser.open("https://github.com/Obamna1234/sevby-hq")).pack(side="left")
        ctk.CTkButton(row, text="Copy diagnostics", width=130, fg_color=C_BG, hover_color="#2a2a30",
                      text_color=C_TEXT, command=lambda: self._copy_text(diagnostics_text())).pack(
            side="left", padx=(8, 0))

        adv_open = {"v": False}
        adv = ctk.CTkFrame(body, fg_color=C_SURFACE, corner_radius=10)
        adv.pack(fill="x", padx=14, pady=(8, 14))
        adv_body = ctk.CTkFrame(adv, fg_color="transparent")
        adv_btn = ctk.CTkButton(adv, text="\u25b8 Advanced", anchor="w", fg_color="transparent",
                                hover_color="#2a2a30", text_color=C_TEXT, height=34)
        adv_btn.pack(fill="x", padx=4, pady=2)

        def toggle_adv():
            adv_open["v"] = not adv_open["v"]
            if adv_open["v"]:
                adv_btn.configure(text="\u25be Advanced")
                adv_body.pack(fill="x", padx=12, pady=(0, 10))
            else:
                adv_btn.configure(text="\u25b8 Advanced")
                adv_body.pack_forget()
        adv_btn.configure(command=toggle_adv)

        engine = ctk.CTkTextbox(adv_body, height=130, fg_color=C_BG, font=ctk.CTkFont(family="Consolas", size=11))
        engine.pack(fill="x")
        engine.insert("1.0", diagnostics_text())
        engine.configure(state="disabled")
        net_out = ctk.CTkLabel(adv_body, text="", anchor="w", justify="left", text_color=C_MUTED,
                               font=ctk.CTkFont(size=11), wraplength=440)
        net_btn = ctk.CTkButton(adv_body, text="Network test", width=120, fg_color=C_BG, hover_color="#2a2a30",
                                text_color=C_TEXT)
        net_btn.pack(anchor="w", pady=(8, 0))
        net_out.pack(fill="x")

        def run_net():
            net_btn.configure(state="disabled")
            net_out.configure(text="Testing\u2026")

            def work():
                text = network_test_text()
                self.after(0, lambda: (net_out.configure(text=text), net_btn.configure(state="normal")))
            threading.Thread(target=work, daemon=True).start()
        net_btn.configure(command=run_net)
        blocked = ctk.BooleanVar(value=DEBUG["bc_blocked"])
        ctk.CTkSwitch(adv_body, text="Pretend Bandcamp is blocked (to test the YouTube fallback)",
                      variable=blocked, progress_color=C_ACCENT,
                      command=lambda: DEBUG.__setitem__("bc_blocked", bool(blocked.get()))).pack(
            anchor="w", pady=(10, 0))

    def _copy_text(self, text: str):
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
        except Exception:
            pass

    def restart_app(self):
        try:
            if getattr(sys, "frozen", False):
                subprocess.Popen([sys.executable])
            else:
                subprocess.Popen([sys.executable] + sys.argv)
        except Exception:
            return
        self._on_close()

    # ---------------------------------------------------------------------------------------
    # log: header bar, song list, details
    def _apply_log_open(self):
        self.log_head.configure(text=("\u25be " if self.log_open else "\u25b8 ") + self._log_header)
        if self.log_open:
            self.log_body.pack(fill="x", padx=4, pady=(0, 2))
            self._show_view()
        else:
            self.log_body.pack_forget()

    def _toggle_log(self):
        self.log_open = not self.log_open
        self._apply_log_open()
        self._save_prefs()

    def _set_log_header(self, text: str):
        def _():
            self._log_header = text
            self.log_head.configure(text=("\u25be " if self.log_open else "\u25b8 ") + text)
        self.after(0, _)

    def _show_view(self):
        self.songs_view.pack_forget()
        self.details_box.pack_forget()
        (self.details_box if self.show_details else self.songs_view).pack(fill="x", padx=8, pady=(0, 4),
                                                                         before=self.log_body.winfo_children()[-1])
        self.details_btn.configure(text="Song list" if self.show_details else "Details")

    def _toggle_details(self):
        self.show_details = not self.show_details
        self._show_view()

    def copy_log(self):
        try:
            songs = self.songs_view.get("1.0", "end").strip()
            details = self.details_box.get("1.0", "end").strip()
            self._copy_text(f"{self._log_header}\n\n{songs}\n\n--- Details ---\n{details}\n")
        except Exception:
            pass

    @staticmethod
    def _at_bottom(box) -> bool:
        try:
            return box.yview()[1] >= 0.995
        except Exception:
            return True

    def log(self, msg: str):
        """Technical log: file + the 'Details' view."""
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except OSError:
            pass

        def _():
            box = self.details_box
            was = self._at_bottom(box)
            box.configure(state="normal")
            box.insert("end", msg + "\n")
            try:
                if int(box.index("end-1c").split(".")[0]) > 3000:
                    box.delete("1.0", "1000.0")
            except Exception:
                pass
            if was:
                box.see("end")
            box.configure(state="disabled")
        self.after(0, _)

    def _sv(self, fn):
        """Edit the song list (state juggling + auto-scroll only if the reader was at the bottom)."""
        box = self.songs_view
        was = self._at_bottom(box)
        box.configure(state="normal")
        try:
            fn(box._textbox)
        finally:
            if was:
                box.see("end")
            box.configure(state="disabled")

    def sv_clear(self):
        self._cur_start = None
        self._sv(lambda t: t.delete("1.0", "end"))

    def sv_heading(self, text: str):
        self.after(0, lambda: self._sv(lambda t: t.insert("end", text + "\n", "head")))

    def sv_summary(self, text: str):
        self.after(0, lambda: self._sv(lambda t: t.insert("end", text + "\n", "sum")))

    def sv_now(self, i: int, n: int, song: str):
        def _():
            self._cur_song = song
            self._cur_step = "Starting\u2026"
            self._render_now()
            self.prog_song.configure(text=f"{i} / {n} \u00b7 {song}")
            self.prog_pct.configure(text=f"{int(100 * (i - 1) / max(1, n))}%")
            self.progress.set((i - 1) / max(1, n))
            self.prog_step.configure(text="Starting\u2026")
            self._set_log_header(f"Log \u00b7 {i} / {n}" + (f" (list {self._list_i}/{self._list_n})" if self._list_n > 1 else ""))
        self.after(0, _)

    def _render_now(self):
        def f(t):
            if self._cur_start:
                t.delete(self._cur_start, "end-1c")
            self._cur_start = t.index("end-1c")
            t.insert("end", "NOW  ", "now")
            t.insert("end", self._cur_song + "\n")
            t.insert("end", "    " + self._cur_step + "\n", "note")
        self._sv(f)

    def sv_step(self, text: str):
        def _():
            self._cur_step = text
            self.prog_step.configure(text=text)
            if self._cur_start:
                self._render_now()
        self.after(0, _)

    def sv_result(self, i: int, n: int, song: str, res: dict):
        status = res.get("status")
        mark, tag, ntag = {"ok": ("\u2713", "ok", "note"), "skip": ("\u2713", "skip", "note"),
                           "dup": ("\u2713", "skip", "note"), "fail": ("\u2717", "fail", "noterr")}[status]

        def f(t):
            if self._cur_start:
                t.delete(self._cur_start, "end-1c")
            t.insert("end", mark + " ", tag)
            t.insert("end", song + "\n", "skip" if status in ("skip", "dup") else None)
            t.insert("end", "    " + (res.get("note") or "") + "\n", ntag)
            try:
                if int(t.index("end-1c").split(".")[0]) > 6000:
                    t.delete("1.0", "2000.0")
            except Exception:
                pass
            self._cur_start = None

        def _():
            self._sv(f)
            self.progress.set(i / max(1, n))
            self.prog_pct.configure(text=f"{int(100 * i / max(1, n))}%")
        self.after(0, _)

    # ---------------------------------------------------------------------------------------
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
            "Opened the Chosic website in your browser (it only works with PUBLIC Spotify playlists).\n"
            "1) Paste your public Spotify playlist link there\n"
            "2) Download / copy the song list as text\n"
            "3) Switch to \u201cSong list / .txt file\u201d in SEVBY and paste it, or click Import (.txt or .csv)"
        )

    def open_apple_dialog(self):
        """Paste a shared Apple Music playlist link; the songs are read from the public page (no sign-in)."""
        old = getattr(self, "_apple_dlg", None)
        try:
            if old is not None and old.winfo_exists():
                old.deiconify()
                old.lift()
                old.focus_force()
                return
        except Exception:
            pass
        dlg = ctk.CTkToplevel(self)
        self._apple_dlg = dlg
        dlg.title("Apple Music playlist link")
        self._apply_icon(dlg)
        dlg.configure(fg_color=C_BG)
        dlg.resizable(False, False)
        dlg.transient(self)
        w, h = 520, 330
        self.update_idletasks()
        x = self.winfo_rootx() + max(0, (self.winfo_width() - w) // 2)
        y = self.winfo_rooty() + 80
        dlg.geometry(f"{w}x{h}+{x}+{y}")
        box = ctk.CTkFrame(dlg, fg_color=C_SURFACE, corner_radius=10)
        box.pack(fill="both", expand=True, padx=14, pady=14)
        ctk.CTkLabel(box, text="Apple Music playlist link", font=ctk.CTkFont(size=15, weight="bold"),
                     anchor="w").pack(fill="x", padx=14, pady=(12, 2))
        ctk.CTkLabel(box, justify="left", anchor="w", wraplength=470, text_color=C_MUTED,
                     font=ctk.CTkFont(size=12),
                     text="In Apple Music open the playlist, tap Share, then Copy Link, and paste it here. "
                          "No sign-in needed. It only works for playlists anyone can open (not private ones). "
                          "If it doesn't work, use File > Library > Export Playlist on a Mac, or a converter "
                          "such as TuneMyMusic (see the README).").pack(fill="x", padx=14, pady=(0, 8))
        entry = ctk.CTkEntry(box, placeholder_text="https://music.apple.com/.../playlist/...")
        entry.pack(fill="x", padx=14, pady=4)
        try:
            clip = self.clipboard_get().strip()
            if "music.apple.com" in clip:
                entry.insert(0, clip)
        except Exception:
            pass
        status = ctk.CTkLabel(box, text="", anchor="w", justify="left", wraplength=470, text_color=C_MUTED)
        status.pack(fill="x", padx=14, pady=(4, 0))
        go = ctk.CTkButton(box, text="Get songs", height=38, fg_color=C_ACCENT, hover_color=C_ACCENT_H,
                           text_color=C_ON_ACCENT)
        go.pack(fill="x", padx=14, pady=(8, 12))

        def done(name, songs, err):
            if err:
                status.configure(text=err, text_color=C_ERR)
                go.configure(state="normal", text="Get songs")
                return
            self.songs_box.configure(state="normal")
            self.songs_box.delete("1.0", "end")
            self.songs_box.insert("1.0", "\n".join(songs))
            self._loaded_name = name or "Apple Music playlist"
            self.mode.set("txt")
            self._switch_mode()
            self.log(f"Loaded {len(songs)} songs from Apple Music: {name}")
            dlg.destroy()

        def work(url):
            try:
                name, songs = fetch_apple_playlist(url)
                err = None
            except ValueError as e:
                name, songs, err = "", [], str(e)
            except Exception as e:
                name, songs, err = "", [], f"Something went wrong: {e}"
            self.after(0, lambda: done(name, songs, err))

        def click():
            url = entry.get().strip()
            if not url:
                status.configure(text="Paste the link first.", text_color=C_ERR)
                return
            go.configure(state="disabled", text="Getting songs\u2026")
            status.configure(text="", text_color=C_MUTED)
            threading.Thread(target=work, args=(url,), daemon=True).start()
        go.configure(command=click)
        entry.bind("<Return>", lambda _e: click())
        dlg.after(200, entry.focus_set)

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
        # Then hand Windows the exact icon sizes it wants (taskbar / title bar), so nothing is scaled and blurry.
        for ms in (450, 1200):
            win.after(ms, lambda: self._win_exact_icons(win))

    def _win_exact_icons(self, win):
        try:
            import ctypes

            u = ctypes.windll.user32
            u.LoadImageW.restype = ctypes.c_void_p
            u.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_uint]
            u.SendMessageW.restype = ctypes.c_void_p
            u.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
            hwnd = int(win.wm_frame(), 16) or win.winfo_id()
            try:
                u.GetDpiForWindow.restype = ctypes.c_uint
                u.GetDpiForWindow.argtypes = [ctypes.c_void_p]
                dpi = int(u.GetDpiForWindow(hwnd)) or 96
            except Exception:
                dpi = 96
            scale = dpi / 96.0
            frames = (16, 20, 24, 32, 40, 48, 64)  # sizes stored in sevbyicon.ico

            def nearest(v):
                return min(frames, key=lambda f: abs(f - v))

            small = nearest(16 * scale)
            big = nearest(24 * scale)  # the taskbar button icon
            keep = getattr(self, "_hicons", None)
            if keep is None:
                keep = self._hicons = []
            for kind, size in ((0, small), (1, big)):  # ICON_SMALL, ICON_BIG
                h = u.LoadImageW(None, ICON_PATH, 1, size, size, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
                if h:
                    keep.append(h)
                    u.SendMessageW(hwnd, 0x80, kind, h)  # WM_SETICON
        except Exception:
            pass

    def _center_window(self, width: int, height: int):
        """Place the main window in the centre of the screen."""
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = max(0, (sw - width) // 2)
        y = max(0, (sh - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    FREE_NOTICE = (
        "What this does\n"
        "SEVBY HQ looks for each song on Jamendo and the Internet Archive first. If it finds a matching copy "
        "that is labelled Creative Commons, it downloads that file (often FLAC) instead of using Bandcamp or "
        "YouTube. Album, year and cover art come from Apple's iTunes search when available.\n\n"
        "Please read before turning it on\n"
        "\u2022 Both sites let anyone upload music and choose its licence label, and nobody checks it. A song "
        "labelled \u201cCreative Commons\u201d may not really be free to download, and a file can be a mislabelled "
        "copy of a commercial song.\n"
        "\u2022 SEVBY HQ cannot verify this for you. It only matches the title, artist and length. The log shows "
        "where every file came from, with a link, so you can check it yourself.\n"
        "\u2022 You are responsible for keeping only music you have the right to download. If you are unsure, "
        "leave this off.\n"
        "\u2022 Jamendo needs your own free Client ID. By using it you agree to Jamendo's API terms (which include "
        "crediting Jamendo and the artists).\n"
        "\u2022 Most songs will not be found there at all. They fall back to Bandcamp or YouTube as usual."
    )

    def show_free_notice(self) -> bool:
        """Modal explanation shown when the free sources are switched on. True = the user accepts."""
        dlg = ctk.CTkToplevel(self)
        dlg.title("Free sources: what you are turning on")
        self._apply_icon(dlg)
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()
        w, h = 500, 560
        self.update_idletasks()
        x = self.winfo_rootx() + max(0, (self.winfo_width() - w) // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - h) // 2)
        dlg.geometry(f"{w}x{h}+{x}+{y}")
        result = {"ok": False}
        frame = ctk.CTkFrame(dlg, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=18, pady=14)
        ctk.CTkLabel(frame, text="Jamendo + Internet Archive", font=ctk.CTkFont(size=16, weight="bold")).pack(
            anchor="w", pady=(0, 8))
        ctk.CTkLabel(frame, text=self.FREE_NOTICE, justify="left", anchor="w", wraplength=450,
                     font=ctk.CTkFont(size=12)).pack(anchor="w", fill="x")
        row = ctk.CTkFrame(frame, fg_color="transparent")
        row.pack(pady=(16, 0))

        def accept():
            result["ok"] = True
            dlg.destroy()

        ctk.CTkButton(row, text="I understand, turn on", width=170, fg_color=C_ACCENT, hover_color=C_ACCENT_H,
                      text_color=C_ON_ACCENT, command=accept).pack(side="left", padx=(0, 8))
        ctk.CTkButton(row, text="Keep it off", width=110, fg_color=C_SURFACE, hover_color="#2a2a30",
                      text_color=C_TEXT, command=dlg.destroy).pack(side="left")
        dlg.focus_set()
        self.wait_window(dlg)
        return result["ok"]

    def _on_free_toggle(self):
        """Ticking the box shows the notice first; if it is not accepted the box is cleared again."""
        if self.free_hq.get() and not self.show_free_notice():
            self.free_hq.set(False)
        self._save_prefs()

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

    def _checkpoint(self) -> bool:
        """Hold while paused; return True if the user pressed Stop."""
        while PAUSE_EVENT.is_set() and not STOP_EVENT.is_set():
            time.sleep(0.2)
        return STOP_EVENT.is_set()


    def _info_box(self, title: str, msg: str):
        self.after(0, lambda: messagebox.showinfo(title, msg))

    def set_progress(self, fraction: float, label: str = ""):
        """Kept for the engine: the bar is driven per song now, so only the step text matters."""
        if label:
            self.after(0, lambda: self.prog_step.configure(text=label))

    # ---------------------------------------------------------------------------------------
    # running
    def _job_from_screen(self, out_dir: str) -> dict | None:
        mode = self.mode.get()
        job = {
            "mode": mode, "out_dir": out_dir, "source": self.source.get(), "quality": self.quality.get(),
            "free_hq": bool(self.free_hq.get()), "jamendo_id": self.jamendo_id.get().strip(),
            "raw": self.songs_box.get("1.0", "end") if mode == "txt" else "",
            "url": self.sp_url.get().strip(), "cid": self.client_id.get().strip(),
            "secret": self.client_secret.get().strip(), "songs": None,
        }
        return job

    def start(self):
        if self.running:
            self.stop()
            return
        rj = getattr(self, "_resume_jobs", None)
        if rj and not self.queue:
            same = all(j["mode"] != "txt" or j.get("raw", "").strip() == self.songs_box.get("1.0", "end").strip()
                       for j in rj) if self.mode.get() == "txt" else True
            if same and all(os.path.isdir(j["out_dir"]) for j in rj):
                self._begin([dict(j) for j in rj])  # finished songs are skipped, so this carries on
                return
        self._resume_jobs = None
        # songs still in the box while a queue is waiting: they go to the end of the queue first
        if self.queue and self.mode.get() == "txt" and parse_song_lines(self.songs_box.get("1.0", "end")):
            self.add_to_queue()
        if self.queue:
            bad = [j for j in self.queue if not os.path.isdir(j["out_dir"])]
            if bad:
                messagebox.showerror("Error", "A save folder in the queue no longer exists. Clear the queue "
                                              "or choose the folder again.")
                return
            self._save_prefs()
            self._begin(list(self.queue))
            return
        out_dir = self.folder_entry.get().strip()
        if not out_dir or not os.path.isdir(out_dir):
            messagebox.showerror("Error", "Please choose a valid save folder.")
            return
        if self.mode.get() == "txt" and not parse_song_lines(self.songs_box.get("1.0", "end")):
            messagebox.showinfo(APP_NAME, "Paste or load some songs first.")
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
        job = self._job_from_screen(out_dir)
        self._save_prefs()
        self._begin([job])

    def _begin(self, jobs: list):
        try:
            LOG_PATH.write_text("", encoding="utf-8")
        except OSError:
            pass
        self._job = jobs[0] if len(jobs) == 1 else None
        self._resume_jobs = None
        self._last_jobs = list(jobs)
        self.run_mode = jobs[0]["mode"]
        self.running = True
        self._list_n = len(jobs)
        self._list_i = 1
        self._counts = {"ok": 0, "skip": 0, "fail": 0}
        self._new_failed: list[tuple[str, str, dict]] = []
        STOP_EVENT.clear()
        PAUSE_EVENT.clear()
        self.retry_btn.pack_forget()
        self.sv_clear()
        self.details_box.configure(state="normal")
        self.details_box.delete("1.0", "end")
        self.details_box.configure(state="disabled")
        self._set_inputs(False)
        self.start_btn.configure(text="Stop", fg_color=C_ERR_BG, hover_color="#a33a3a", text_color="white")
        self.pause_btn.configure(state="normal", text="Pause")
        self.prog_frame.pack(fill="x", padx=22, pady=(4, 6), before=self.log_card)
        if self._list_n > 1:
            self.list_heading.pack(fill="x", padx=12, pady=(10, 0), before=self.prog_frame.winfo_children()[1])
        self.prog_song.configure(text="Starting\u2026")
        self.prog_step.configure(text="")
        self.progress.set(0)
        self.prog_pct.configure(text="0%")
        self._set_log_header("Log \u00b7 starting")
        self._set_status("Working")
        self._refresh_queue_ui()
        threading.Thread(target=self._worker, args=(jobs,), daemon=True).start()

    # -- Queue ---------------------------------------------------------------------------------
    def add_to_queue(self):
        """Snapshot what is on screen as one queue item, then clear the inputs for the next one."""
        if self.running:
            return
        out_dir = self.folder_entry.get().strip()
        if not out_dir or not os.path.isdir(out_dir):
            messagebox.showerror("Error", "Choose a save folder first \u2014 each queue item is saved "
                                          "in its own folder inside it.")
            return
        mode = self.mode.get()
        job = self._job_from_screen(out_dir)
        job.update(raw="", url="", cid="", secret="")
        if mode == "txt":
            raw = self.songs_box.get("1.0", "end")
            songs = parse_song_lines(raw)
            if not songs:
                messagebox.showinfo(APP_NAME, "Paste or load some songs first.")
                return
            base = sanitize_filename(self._loaded_name).strip(". ") or f"Song list {len(self.queue) + 1}"
            name, k = base, 2
            while any(j.get("subfolder") == name for j in self.queue):
                name, k = f"{base} ({k})", k + 1
            job.update(raw=raw, name=name, count=len(songs), subfolder=name)
            self.songs_box.delete("1.0", "end")
            self._loaded_name = ""
        else:
            url = self.sp_url.get().strip()
            cid = self.client_id.get().strip()
            m = re.search(r"playlist[/:]([a-zA-Z0-9]+)", url)
            if not m:
                messagebox.showinfo(APP_NAME, "Paste a Spotify playlist link first.")
                return
            if not cid:
                messagebox.showinfo(APP_NAME, "Enter your Spotify Client ID first.")
                return
            job.update(url=url, cid=cid, secret=self.client_secret.get().strip(),
                       name=f"Spotify playlist \u2026{m.group(1)[:6]}", count=0, subfolder=True)
            self.sp_url.delete(0, "end")
        self.queue.append(job)
        self._refresh_queue_ui()
        self._save_queue()
        self._save_prefs()

    def clear_queue(self):
        if self.running:
            return
        self.queue.clear()
        self._refresh_queue_ui()
        self._save_queue()

    def _queue_remove(self, job: dict):
        if job.get("_running"):
            return
        self.queue = [j for j in self.queue if j is not job]
        self._refresh_queue_ui()
        self._save_queue()

    def _refresh_queue_ui(self):
        n = len(self.queue)
        for w in self.queue_rows.winfo_children():
            w.destroy()
        if n:
            self.queue_title.configure(text=f"Queue ({n})")
            for i, j in enumerate(self.queue, 1):
                row = ctk.CTkFrame(self.queue_rows, fg_color="transparent")
                row.pack(fill="x", pady=(0, 4))
                cnt = f" \u00b7 {j['count']} song" + ("" if j["count"] == 1 else "s") if j.get("count") else ""
                left = ctk.CTkFrame(row, fg_color="transparent")
                left.pack(side="left", fill="x", expand=True)
                ctk.CTkLabel(left, text=f"{i}. {j.get('name', 'List')}{cnt}", anchor="w").pack(fill="x")
                sub = j.get("subfolder")
                dest = os.path.join(j.get("out_dir", ""), sub) if isinstance(sub, str) and sub else j.get("out_dir", "")
                if sub is True:
                    dest = os.path.join(j.get("out_dir", ""), "(playlist name)")
                ctk.CTkLabel(left, text="Saves to: " + dest.replace("\\", "/"), anchor="w", text_color=C_MUTED,
                             font=ctk.CTkFont(size=11), wraplength=560, justify="left").pack(fill="x")
                if not j.get("_running"):
                    ctk.CTkButton(row, text="\u2715", width=26, height=22, fg_color="transparent",
                                  hover_color="#2a2a30", text_color=C_MUTED,
                                  command=lambda jj=j: self._queue_remove(jj)).pack(side="right")
            if not self.queue_card.winfo_ismapped():
                self.queue_card.pack(fill="x", padx=22, pady=(12, 0), before=self.folder_label)
        else:
            self.queue_card.pack_forget()
        self.clear_btn.configure(state="disabled" if self.running else "normal")
        if not self.running:
            self.start_btn.configure(text=f"Start Download ({n} list{'' if n == 1 else 's'})" if n else
                                     ("Resume" if getattr(self, "_resume_jobs", None) else "Start Download"))

    # -- Retry ---------------------------------------------------------------------------------
    def _show_retry(self):
        n = len(self.failed_items)
        if n:
            self.retry_btn.configure(text=f"Retry failed ({n})")
            self.retry_btn.pack(side="left", padx=(8, 0))
        else:
            self.retry_btn.pack_forget()

    def retry_failed(self):
        if not self.failed_items or self.running:
            return
        groups: dict[str, list[str]] = {}
        base: dict[str, dict] = {}
        for song, folder, job in self.failed_items:
            groups.setdefault(folder, []).append(song)
            base.setdefault(folder, job)
        jobs = []
        for folder, songs in groups.items():
            if not os.path.isdir(folder):
                os.makedirs(folder, exist_ok=True)
            j = dict(base[folder])
            j.update(songs=songs, out_dir=folder, exact_dir=True, subfolder=None,
                     name=os.path.basename(folder) or folder, count=len(songs))
            jobs.append(j)
        self._begin(jobs)

    def _done_dialog(self, summary: str):
        self._beep()
        if messagebox.askyesno(APP_NAME, summary + "\n\nOpen the folder?"):
            self.open_folder()

    def stop(self):
        """Abort the current download and end the run."""
        STOP_EVENT.set()
        PAUSE_EVENT.clear()
        self.start_btn.configure(state="disabled", text="Stopping\u2026")
        self.pause_btn.configure(state="disabled")
        self.log("Stopping\u2026 (cancelling the current download)")

    def toggle_pause(self):
        """Pause before the next song / resume."""
        if PAUSE_EVENT.is_set():
            PAUSE_EVENT.clear()
            self.pause_btn.configure(text="Pause")
            self._set_status("Working")
            self.log("Resumed.")
        else:
            PAUSE_EVENT.set()
            self.pause_btn.configure(text="Resume")
            self._set_status("Paused")
            self.prog_step.configure(text="Paused \u00b7 tap Resume to carry on")
            self._set_log_header("Log \u00b7 paused")
            self.log("Pausing after the current song finishes\u2026")

    def sv_stopped(self, song: str):
        """The run was stopped while this song was in progress: say clearly that it was not saved."""
        def f(t):
            if self._cur_start:
                t.delete(self._cur_start, "end-1c")
            t.insert("end", "\u25a0 ", "skip")
            t.insert("end", song + "\n", "skip")
            t.insert("end", "    stopped before it finished (not saved)\n", "note")
            self._cur_start = None
        self.after(0, lambda: self._sv(f))

    def sv_pause_mark(self, on: bool):
        """A visible line while paused, so it's clear everything above is already finished and saved."""
        def f(t):
            if on:
                self._pause_start = t.index("end-1c")
                t.insert("end", "PAUSED \u00b7 the songs above are finished and saved. Press Resume to carry on.\n", "sum")
            elif getattr(self, "_pause_start", None):
                t.delete(self._pause_start, "end-1c")
                self._pause_start = None
        self.after(0, lambda: self._sv(f))

    def sv_drop_now(self):
        """Remove the unfinished 'current song' entry (used when the run is stopped mid-song)."""
        def f(t):
            if self._cur_start:
                t.delete(self._cur_start, "end-1c")
            self._cur_start = None
        self.after(0, lambda: self._sv(f))

    def _job_error(self, job: dict, msg: str):
        self.log(msg)
        first = msg.strip().splitlines()[0] if msg.strip() else "Couldn't start"
        self.sv_summary("\u2717 " + first)

    def _run_job(self, job: dict):
        out_dir = job["out_dir"]
        source = job["source"]
        opts = {"use_bc": source != "YouTube only", "use_yt": source != "Bandcamp only",
                "free": bool(job.get("free_hq", False))}
        QUALITY["best"] = str(job.get("quality", "")).startswith("Best")
        QUALITY["free"] = opts["free"]
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
                    self._job_error(job, "No songs found. Paste a list or load a .txt file.")
                    return
                self.log(f"Using {len(songs)} songs from list/file.")
            else:
                url, cid, secret = job["url"], job["cid"], job["secret"]
                if not url or "spotify" not in url.lower():
                    self._job_error(job, "Enter a valid Spotify playlist URL.")
                    return
                if not cid:
                    self._job_error(job, "Client ID required for Spotify links. Use Song list mode if you have none.")
                    return
                cfg = load_config()
                cfg["client_id"] = cid
                if secret:
                    cfg["client_secret"] = secret
                save_config(cfg)
                self.log("Connecting to Spotify\u2026")
                token = get_spotify_access_token(cid, secret, self.log)
                if not token:
                    self._job_error(job, "Spotify login failed. Check the Client ID and that the Redirect URI in "
                                         "your Spotify app is exactly http://127.0.0.1:8888 - or export with Chosic "
                                         "and use Song list mode.")
                    return
                if job.get("subfolder") is True:  # queued playlist: folder named after it
                    try:
                        nm = spotify_playlist_name(url, token)
                    except Exception:
                        nm = ""
                    job["subfolder"] = sanitize_filename(nm).strip(". ") or "Spotify playlist"
                    job["name"] = job["subfolder"]
                self.log("Fetching Spotify playlist\u2026")
                try:
                    songs = fetch_spotify_tracks(url, token)
                except Exception as e:
                    self._job_error(job, f"Spotify error: {e}")
                    return
                if not songs:
                    self._job_error(job, "No tracks returned (empty playlist?).")
                    return
                self.log(f"Found {len(songs)} tracks on Spotify.")

            sub = job.get("subfolder")
            if isinstance(sub, str) and sub and not job.get("exact_dir"):
                out_dir = os.path.join(out_dir, sub)
                os.makedirs(out_dir, exist_ok=True)
            self.log(f"Saving into: {out_dir}")

            if not have_ytdlp():
                self.log("yt-dlp is not installed, so only free sources can be used.")
            else:
                age = ytdlp_age_days()
                if age is not None and age > 90:
                    self.log(f"Heads-up: this copy of yt-dlp is {age} days old. If YouTube downloads fail, "
                             "open About & updates and update it.")
            self.log("YouTube audio: " + ("Best available (original M4A kept, no re-encoding)."
                                           if QUALITY["best"] else "Standard (converted to MP3)."))

            n = len(songs)
            name = job.get("name") or os.path.basename(out_dir) or "Songs"
            where = os.path.basename(out_dir) or out_dir
            self.sv_heading(f"\u25b8 {name} \u00b7 {n} songs \u2192 {where}")
            state: dict = {}
            c = {"ok": 0, "skip": 0, "fail": 0}
            stopped = False
            for i, song in enumerate(songs, 1):
                if PAUSE_EVENT.is_set() and not STOP_EVENT.is_set():
                    self.sv_pause_mark(True)
                    held = True
                else:
                    held = False
                if self._checkpoint():
                    if held:
                        self.sv_pause_mark(False)
                    stopped = True
                    break
                if held:
                    self.sv_pause_mark(False)
                self.sv_now(i, n, song)
                self.log("-" * 40)
                self.log(f"[{i}/{n}] {song}")
                try:
                    res = process_song(song, out_dir, opts, state, self.log, self.sv_step)
                except _Stopped:
                    res = {"status": "fail", "note": "stopped"}
                except Exception as e:
                    self.log(f"  Unexpected error: {e}")
                    res = {"status": "fail", "note": "unexpected error (see Details)"}
                if STOP_EVENT.is_set() and res.get("status") == "fail" and res.get("note") == "stopped":
                    self.sv_stopped(song)
                    stopped = True
                    break
                st = res.get("status")
                if st == "ok":
                    c["ok"] += 1
                elif st in ("skip", "dup"):
                    c["skip"] += 1
                else:
                    c["fail"] += 1
                    self._new_failed.append((song, out_dir, job))
                self._counts[{"ok": "ok", "skip": "skip", "dup": "skip"}.get(st, "fail")] += 1
                self.sv_result(i, n, song, res)
                if res.get("note"):
                    self.log(f"  => {res['note']}")

            summary = f"{c['ok']} downloaded"
            if c["skip"]:
                summary += f" \u00b7 {c['skip']} already there"
            if c["fail"]:
                summary += f" \u00b7 {c['fail']} failed"
            self.log("=" * 40)
            self.log(f"{'Stopped' if stopped else 'Done'}. {summary}.")
            self.log(f"Folder: {out_dir}")
            if QUALITY["best"] and not stopped:
                write_hq_links(out_dir, self.log)
            if not stopped:
                job["_summary"] = summary
                self.sv_summary("Done \u00b7 " + summary.replace(" \u00b7 ", " \u00b7 "))
        except Exception as e:
            self.log(f"Unexpected error: {e}")


    def _worker(self, jobs: list):
        """Run one job, or a whole queue - strictly one after the other."""
        lines = []
        stopped = False
        try:
            total = len(jobs)
            for k, job in enumerate(jobs, 1):
                if STOP_EVENT.is_set():
                    break
                self._list_i = k
                job["_running"] = True
                self.after(0, self._refresh_queue_ui)
                if total > 1:
                    self.log("=" * 40)
                    self.log(f"List {k}/{total}: {job.get('name', '')}")
                self._run_job(job)
                job["_running"] = False
                if "_summary" in job:
                    lines.append(f"{job.get('name', 'List')}: {job['_summary']}")
                    if job in self.queue:  # finished lists leave the queue
                        self.queue = [j for j in self.queue if j is not job]
                        self._save_queue()
            stopped = STOP_EVENT.is_set()
        except Exception as e:
            self.log(f"Unexpected error: {e}")
        finally:
            PAUSE_EVENT.clear()
            self.after(0, lambda: self._finish(stopped, lines))

    def _finish(self, stopped: bool, lines: list):
        self.running = False
        self.run_mode = None
        c = self._counts
        summary = f"{c['ok']} downloaded"
        if c["skip"]:
            summary += f" \u00b7 {c['skip']} already there"
        if c["fail"]:
            summary += f" \u00b7 {c['fail']} failed"
        if stopped:
            self._set_log_header(f"Log \u00b7 Stopped \u00b7 {summary}")
            self.prog_step.configure(text="Stopped")
            self._set_status("Stopped")
            self.sv_summary("Stopped \u00b7 " + summary)
            # a stopped run keeps the previous failed list
            if not self.queue:
                self._resume_jobs = [{k: v for k, v in j.items() if not k.startswith("_")}
                                     for j in getattr(self, "_last_jobs", [])]
        else:
            self._set_log_header(f"Log \u00b7 Done \u00b7 {summary}")
            self.prog_step.configure(text="Done")
            self.progress.set(1.0)
            self.prog_pct.configure(text="100%")
            self._set_status("Ready")
            self.failed_items = list(self._new_failed)
        self._show_retry()
        self.start_btn.configure(state="normal", fg_color=C_ACCENT, hover_color=C_ACCENT_H, text_color=C_ON_ACCENT)
        self.pause_btn.configure(state="disabled", text="Pause")
        self._set_inputs(True)
        self._refresh_queue_ui()
        if stopped:
            self.start_btn.configure(text="Resume" if not self.queue else
                                     f"Resume ({len(self.queue)} list{'' if len(self.queue) == 1 else 's'})")
        self.list_heading.pack_forget()
        if not stopped and (c["ok"] or c["skip"] or c["fail"]):
            body = "Finished.\n" + summary.replace(" \u00b7 ", "\n")
            if len(lines) > 1:
                body = "Queue finished.\n\n" + "\n".join(lines)
            self._done_dialog(body)


if __name__ == "__main__":
    app = SevbyApp()
    app.mainloop()
