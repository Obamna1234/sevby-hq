#!/usr/bin/env python3
"""
SEVBY HQ command-line version (no window) - for Termux on Android, Linux servers, or any terminal.

It reuses all the download code from sevby_app.py (keep both files in the same folder),
so Bandcamp-first / YouTube-fallback, tagging, cover art and Spotify login work the same way.

Examples
  python sevby_cli.py songs.txt -o ~/storage/music
  python sevby_cli.py "https://open.spotify.com/playlist/XXXX" --client-id YOUR_ID -o ~/storage/music
  python sevby_cli.py "https://music.apple.com/us/playlist/NAME/pl.XXXX" -o ~/storage/music   (shared Apple Music playlist)
  python sevby_cli.py list1.txt list2.txt "https://open.spotify.com/playlist/XXXX" -o ~/music
      (several inputs = a queue: one after another, each in its own sub-folder)
  python sevby_cli.py --retry -o ~/music          (retry the songs that failed last time)
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


# \u2500\u2500 Load the shared code without needing a screen / tkinter \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
def _stub(name: str):
    from unittest.mock import MagicMock

    m = MagicMock()
    sys.modules[name] = m
    return m


try:
    import customtkinter  # noqa: F401
    import tkinter  # noqa: F401
except Exception:
    ctk = _stub("customtkinter")
    ctk.CTk = type("CTk", (), {})
    tk = _stub("tkinter")
    sys.modules["tkinter.filedialog"] = tk.filedialog
    sys.modules["tkinter.messagebox"] = tk.messagebox

sys.modules.setdefault("tkinterdnd2", None)  # drag-and-drop is a desktop-only extra
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sevby_app as sa  # noqa: E402

FAILED_PATH = Path.home() / ".sevby_hq_failed.txt"


def say(msg: str = "") -> None:
    print(msg, flush=True)


def _open_url(url: str, *a, **k) -> bool:
    """Spotify login: try to open a browser; always print the link so it can be copied."""
    say("\nOpen this link in your browser to log in to Spotify (one time):\n")
    say(url + "\n")
    for cmd in (["termux-open-url", url], ["xdg-open", url]):
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            break
        except Exception:
            continue
    return True


sa.webbrowser.open = _open_url


def is_spotify(s: str) -> bool:
    return "spotify.com/playlist" in s or s.startswith("spotify:playlist")


def run_job(songs: list[str], out_dir: str, source: str) -> tuple[int, list[str]]:
    """Process songs one by one with the same engine as the window: free sources, Bandcamp, YouTube."""
    os.makedirs(out_dir, exist_ok=True)
    sa.HQ_SONGS.clear()
    opts = {"use_bc": source != "youtube", "use_yt": source != "bandcamp", "free": bool(sa.QUALITY["free"])}
    state: dict = {}
    ok = 0
    skipped = 0
    failed: list[str] = []
    n = len(songs)

    for i, song in enumerate(songs, 1):
        if sa.STOP_EVENT.is_set():
            break
        say(f"[{i}/{n}] {song}")
        res = sa.process_song(song, out_dir, opts, state, say)
        if sa.STOP_EVENT.is_set():
            break
        st = res.get("status")
        if st == "ok":
            ok += 1
            say(f"  OK: {res.get('note', '')}")
        elif st in ("skip", "dup"):
            skipped += 1
            say(f"  skipped: {res.get('note', '')}")
        else:
            failed.append(song)
            say(f"  FAILED: {res.get('note', '')}")
    if skipped:
        say(f"{skipped} song(s) were already in the folder.")
    if sa.QUALITY["best"]:
        sa.write_hq_links(out_dir, say)
    return ok, failed


def get_songs(item: str, args) -> tuple[list[str], str] | None:
    """Return (songs, folder name) for one input: a Spotify link or a text file."""
    if is_spotify(item):
        cid = args.client_id or sa.load_config().get("client_id") or ""
        if not cid:
            say("A Spotify link needs your free Client ID (see the README). Use --client-id YOUR_ID.")
            return None
        cfg = sa.load_config()
        cfg["client_id"] = cid
        sa.save_config(cfg)
        say("Connecting to Spotify...")
        token = sa.get_spotify_access_token(cid, args.client_secret or "", say)
        if not token:
            say("Spotify login failed. Check the Client ID and that the Redirect URI is exactly "
                "http://127.0.0.1:8888")
            return None
        try:
            songs = sa.fetch_spotify_tracks(item, token)
            try:
                name = sa.spotify_playlist_name(item, token)
            except Exception:
                name = ""
        except Exception as e:
            say(f"Spotify error: {e}")
            return None
        return songs, sa.sanitize_filename(name).strip(". ") or "Spotify playlist"
    if "music.apple.com" in item:
        try:
            name, songs = sa.fetch_apple_playlist(item)
        except ValueError as e:
            say(str(e))
            return None
        say(f"Found {len(songs)} songs on Apple Music: {name}")
        return songs, sa.sanitize_filename(name).strip(". ") or "Apple Music playlist"
    p = Path(item).expanduser()
    if not p.is_file():
        say(f"Not a Spotify link or a file I can find: {item}")
        return None
    return sa.load_songs_from_file(str(p)), sa.sanitize_filename(p.stem) or "Song list"


def main() -> int:
    ap = argparse.ArgumentParser(description="SEVBY HQ: playlist to MP3 (command line)")
    ap.add_argument("inputs", nargs="*", help="song-list .txt files and/or Spotify playlist links")
    ap.add_argument("-o", "--out", default="", help="folder to save into")
    ap.add_argument("--source", choices=["both", "bandcamp", "youtube"], default="both")
    ap.add_argument("--quality", choices=["best", "standard"], default="best",
                    help="best = keep YouTube's original audio as M4A (default); standard = convert to MP3")
    ap.add_argument("--no-free", action="store_true",
                    help="do not check Jamendo / Internet Archive first")
    ap.add_argument("--jamendo-id", default="", help="your free Jamendo Client ID (remembered after first use)")
    ap.add_argument("--client-id", default="", help="your free Spotify Client ID (remembered after first use)")
    ap.add_argument("--client-secret", default="", help="optional, not needed")
    ap.add_argument("--retry", action="store_true", help="retry the songs that failed last time")
    args = ap.parse_args()
    sa.QUALITY["best"] = args.quality == "best"
    sa.QUALITY["free"] = not args.no_free
    _cfg = sa.load_config()
    if args.jamendo_id:
        _cfg["jamendo_id"] = args.jamendo_id.strip()
        sa.save_config(_cfg)
    sa.QUALITY["jamendo_id"] = _cfg.get("jamendo_id", "") or ""

    out = args.out or sa.load_config().get("cli_folder") or ""
    if not out:
        default = str(Path.home() / "storage" / "music") if (Path.home() / "storage").exists() \
            else str(Path.home() / "SEVBY")
        out = input(f"Save folder [{default}]: ").strip() or default
    out = os.path.expanduser(out)
    cfg = sa.load_config()
    cfg["cli_folder"] = out
    sa.save_config(cfg)

    if not sa.have_ytdlp():
        say("yt-dlp is not installed. Run:  pip install -U yt-dlp")
        return 1
    if sa.ffmpeg_path() == "ffmpeg" and not __import__("shutil").which("ffmpeg"):
        say("ffmpeg is not installed. In Termux run:  pkg install ffmpeg")
        return 1
    age = sa.ytdlp_age_days()
    if age is not None and age > 90:
        say(f"Heads-up: yt-dlp is {age} days old. If downloads fail, run:  pip install -U yt-dlp")

    jobs: list[tuple[list[str], str]] = []  # (songs, out dir)
    if args.retry:
        if not FAILED_PATH.is_file():
            say("Nothing to retry.")
            return 0
        songs = sa.parse_song_lines(FAILED_PATH.read_text(encoding="utf-8"))
        jobs.append((songs, out))
    else:
        if not args.inputs:
            ap.print_help()
            return 1
        multi = len(args.inputs) > 1
        for item in args.inputs:
            got = get_songs(item, args)
            if not got or not got[0]:
                say(f"Skipping: {item}")
                continue
            songs, name = got
            jobs.append((songs, os.path.join(out, name) if multi else out))

    if not jobs:
        return 1

    total_ok = 0
    all_failed: list[str] = []
    try:
        for k, (songs, folder) in enumerate(jobs, 1):
            if len(jobs) > 1:
                say("=" * 40)
                say(f"Queue {k}/{len(jobs)}: {folder} ({len(songs)} songs)")
            ok, failed = run_job(songs, folder, args.source)
            total_ok += ok
            all_failed += failed
            if sa.STOP_EVENT.is_set():
                break
    except KeyboardInterrupt:
        sa.STOP_EVENT.set()
        say("\nStopped. Finished songs are kept; run the same command again to continue.")

    say("-" * 40)
    say(f"Done. {total_ok} ok, {len(all_failed)} failed.")
    if all_failed:
        FAILED_PATH.write_text("\n".join(all_failed) + "\n", encoding="utf-8")
        say("Failed songs:")
        for s in all_failed:
            say(f"  x {s}")
        say("Run again with --retry to try just these.")
    elif FAILED_PATH.exists() and not sa.STOP_EVENT.is_set():
        FAILED_PATH.unlink()
    return 0


if __name__ == "__main__":
    sys.exit(main())
