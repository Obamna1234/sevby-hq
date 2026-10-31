# Security notes

## Changes from the October 2026 security review

A read-only review of SEVBY, SEVBY HQ, UniFetcher and Sevbify was done from the Sevbify project; these fixes
were then made here (SEVBY got the same ones). If you build on SEVBY later, keep them:

1. **yt-dlp updates are checksum-checked.** `download_ytdlp_update()` downloads the release's `SHA2-256SUMS`
   and only installs `~/.sevby_ytdlp/yt-dlp` if its SHA-256 matches. (That file is put first on Python's import
   path at the next start, so an unchecked file would run as code. Sevbify reads the same folder.)
2. **Chrome's sign-in is opt-in.** When YouTube says "sign in / confirm you're not a bot", SEVBY used to retry
   with Chrome's cookies (your Google account) automatically. Now only when "retry with Chrome's sign-in" is
   switched on under Advanced (`use_browser_cookies` in `~/.sevby_config.json`, default off).
3. **Spotify secrets are in the system's password store.** The login refresh token and the optional Client
   Secret are kept with `keyring` (Windows Credential Manager / macOS Keychain / Linux Secret Service) and moved
   out of `~/.sevby_config.json` the first time. Without a password store they're kept only until SEVBY closes.
4. **Deno is bundled** (checksum-checked in the build workflow) and yt-dlp is installed with `[default]`
   (yt-dlp-ejs), because YouTube now needs a JavaScript engine for its audio.

Not changed: the release setup. No secrets are committed in this repository.

## Follow-up hardening (31 October 2026)

A second pass added small, behaviour-preserving fixes (shared with SEVBY):

1. **yt-dlp option-injection guard.** `_run_ytdlp_cli()` now places a `--` end-of-options marker before the
   target URL, and `fix_bc_url()` rejects any value that isn't a normal `http(s)://` URL (CWE-88). Normal
   downloads are unchanged.
2. **Config file is user-private.** `save_config()` now sets `~/.sevby_hq_config.json` to `0600` after
   writing (CWE-276). No effect on Windows.
3. **Unpredictable temp file names.** Cover/thumbnail temp files use a random token instead of the process
   id (CWE-377).

**HQ-specific note (not a code change yet):** the "Free HQ" sources (Jamendo + Internet Archive) fetch files
from URLs supplied in remote metadata. This is a blind-SSRF *surface* (the fetched bytes are only checked as
audio, never returned), and local filenames are sanitised so there is no arbitrary write. Adding a host
allow-list + blocking redirects to private IP ranges is recommended as a later hardening step; it needs the
list of CDN/host names the app legitimately uses, so it is deferred rather than guessed.

These are low/medium-severity hardening items; no critical or high issues were found in the review.

## Reporting a problem

Open an issue on GitHub (without personal data), or contact the repository owner.
