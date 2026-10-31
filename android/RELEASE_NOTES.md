**What's new in beta.5**

- Better matching: rejects tribute bands, re-recordings, live albums and other accounts' uploads; checks the Bandcamp file's length against Apple's
- File names and tags use the official spelling from Apple ("kataklysm - black sheep" → `Kataklysm - The Black Sheep.mp3`); songs already saved under the name you typed are still skipped
- A leading "The / A / An" no longer stops a song being found
- If YouTube's first search finds nothing, SEVBY tries up to three other wordings (the artist has to match too)
- Clear message when the wrong APK is installed for the device (e.g. the ARM one on an emulator)
- **Import** also reads CSV exports (Chosic, Exportify, TuneMyMusic, Soundiiz, Apple Music) and M3U playlists
- Clearer buttons with icons: **Import** (.txt or .csv), **Chosic** (Spotify playlists), **Apple Music** (paste a link)

---

**First beta of SEVBY for Android** – a proper app (no Termux needed).

Paste or import a `.txt` list of `Artist - Title` lines, pick a folder, press **Start Download**. SEVBY looks for each song on
**Bandcamp** first and uses **YouTube** for the rest, then saves a 128 kbps MP3 with title, artist, album, year,
track number and square cover art (album details from Bandcamp or Apple's iTunes Search).

- Queue several lists; each goes into its own sub-folder
- Pause / Resume / Stop, also from the notification; after Stop, **Resume** carries on where it left off
- Skips songs already in the folder, never makes "(1)" copies, *Retry failed* for the misses
- Update the downloader (yt-dlp) from **About & updates** when YouTube changes – no reinstall needed
- Tells you when a new SEVBY version is out (banner on the main screen and in **About & updates**); one tap downloads it and it installs over the old one, keeping your settings
- **Apple Music**: paste a public Apple Music playlist or album link and SEVBY fills in the song list (no sign-in)
- Got a Spotify playlist? The **Chosic** button opens the Chosic website, which turns it into a song list to paste
- If Bandcamp blocks your connection (some mobile networks and hotspots do), it falls back to YouTube

**Which file?**

| File | For |
|---|---|
| `SEVBY-…-arm64-v8a.apk` | almost every phone from the last ~7 years |
| `SEVBY-…-armeabi-v7a.apk` | older 32-bit phones |
| `SEVBY-…-universal.apk` | if unsure (bigger) |

Open the APK on your phone and allow "install unknown apps" for your browser or Files app when asked.
Android 7.0 or newer.

This is a beta and has been tested mainly on an emulator and one phone. Please open an Issue with the
copied log (**Copy log** button) if something goes wrong.

The APK includes youtubedl-android, Python, yt-dlp and FFmpeg; as a whole it is distributed under the GNU GPL v3.
See THIRD_PARTY_NOTICES.md. Only download music you have the right to download.
