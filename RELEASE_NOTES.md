## What's new in v0.10.3-beta

**Android**
- Long song names in non-Latin scripts (e.g. Japanese) are saved properly instead of being reported "already downloaded" and skipped.
- One badly formatted Bandcamp or Apple Music page can no longer stop a whole run; importing a huge file shows a message instead of crashing.
- Updates are only ever downloaded from this project's GitHub releases.
- SEVBY HQ for Android can now tell you about new versions: a banner on the main screen and in **About & updates**, with one tap to download. (Before, it never found HQ's releases.)
- The update button always checks GitHub again first, so it offers the newest version.

**Windows / Linux / macOS**
- Security and reliability fixes from a full review:
  - Your Spotify Client Secret is no longer written into the settings file when you queue a playlist (it stays in your system's password store).
  - Settings are saved safely: they can't be wiped or half-written if two things save at the same time.
  - Song names with unusual characters, very long non-Latin names or names like "CON" / "NUL" now save correctly on every system.
  - Apple Music links are checked properly, cover pictures are only downloaded over https with a size limit, and the temporary cover images are cleaned up after each song.
  - The update button only ever opens this project's GitHub page.
  - Running from source with only the yt-dlp program (not the Python module): downloads no longer report a false "FAIL".
  - Command-line version: odd characters in a Windows console or one broken song no longer stop the whole run.
- Internet Archive / Jamendo (optional free sources): a live or remix version is no longer taken for the original, lengths like "4:05" are checked, odd Archive data can't stop the search, and very large files are refused instead of filling your disk.

---

## Install (Windows / Linux / macOS)
Under **Assets**, download **SEVBY-HQ-Windows.zip**, **SEVBY-HQ-Linux.zip**, or **SEVBY-HQ-macOS.zip**,
extract, and run the SEVBY HQ program inside. On Windows use More info → Run anyway; on macOS right-click → Open the first time.

## Install (Android)
Under **Assets**, download **`SEVBY-HQ-…-arm64-v8a.apk`** (most phones) or `…-universal.apk`, open it,
allow installing unknown apps if prompted, then **Install**.
