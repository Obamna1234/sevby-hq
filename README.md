***Beta: only lightly tested. Windows is the best-tested build. Linux/Mac builds and the Android (Termux) version are untested or lightly tested.***

# SEVBY HQ: Playlist to M4A / MP3

A sibling of [SEVBY](https://github.com/Obamna1234/sevby). Same idea, with options for better audio quality.

Turn a plain text list of songs (or a Spotify playlist) into tagged audio files on your own computer.
SEVBY HQ looks for each song on **Bandcamp** first, and falls back to **YouTube** for anything it can't find.

## SEVBY or SEVBY HQ?

| | SEVBY | SEVBY HQ |
|---|---|---|
| Free lossless sources | - | **Checks Jamendo and the Internet Archive first** for Creative Commons copies in FLAC (or 192 kbps+ MP3) |
| YouTube songs | Converted to MP3 | **Original audio kept as M4A**, no extra re-encoding (falls back to MP3 if no M4A exists) |
| Bandcamp songs | 128 kbps MP3 (Bandcamp's stream) | Same |
| Honest log | `OK` | Each song says what you actually got, e.g. `[YouTube original audio (M4A/AAC ~129 kbps)]` |
| Find better copies | - | A text file of search links for stores that sell lossless / 320 kbps versions |
| Works on | Every player | Almost every phone and computer. Some old MP3 players and car stereos can't play M4A |

If you want files that play everywhere, use the original SEVBY (or pick *Standard (MP3)* in HQ).

## How SEVBY HQ picks a source

For each song, in this order:

1. **Jamendo** and the **Internet Archive**, but only music under a Creative Commons licence (free and legal to download). If the same song is there as FLAC or a high-bitrate MP3, that file is used, and the log says so, e.g. `[Internet Archive, FLAC, Creative Commons]`.
2. **Bandcamp** (the free 128 kbps stream), as in the original SEVBY.
3. **YouTube**, keeping the original audio as M4A.

The first two only know music its artists chose to release freely, so most mainstream songs will not be there and will fall through to Bandcamp / YouTube. To avoid saving the wrong song, a file is only used when the title, artist and length all match closely. Jamendo is **optional and needs your own free Client ID**. Register at <https://devportal.jamendo.com>, create an app, and paste your ID into the box once. There is no shared key, because Jamendo's terms say keys are personal. By using your own key you agree to [Jamendo's API terms](https://devportal.jamendo.com/api_terms_of_use) yourself, which include crediting Jamendo and the artists (the log prints each track's Jamendo link) and not building an offline-access service. If you are unsure, leave the box empty and only the Internet Archive is checked. Untick the box in the app (or use `--no-free` in Termux) to skip these sources.

## Be realistic about quality

- Free lossless files exist only for music released under Creative Commons. Everything else is limited by what Bandcamp's free stream and YouTube offer:
- Bandcamp's free stream and YouTube audio are only about **128-160 kbps**. SEVBY HQ keeps them as they are rather than re-encoding, which avoids losing a little more quality. It **cannot make them better** than they started.
- Real high quality (FLAC or 320 kbps) means buying the music. After each run, HQ writes `SEVBY HQ - find better quality.txt` into your music folder with search links (Bandcamp, Qobuz, 7digital) for the songs that came from YouTube. Buying from the artist on Bandcamp is the best way to support them.
- Spotify is not used for audio, only for reading a playlist's song names.

## Features

- Paste a list / `.txt` file of `Artist - Title` lines, **or** a Spotify playlist link (needs a Spotify Client ID, which currently requires Spotify Premium to create; see below)
- Bandcamp first, YouTube for the rest (or choose *Bandcamp only* / *YouTube only*)
- Checks **Jamendo and the Internet Archive** first for free, Creative Commons lossless copies
- Choose **Best available (M4A)** or **Standard (MP3)** for YouTube audio
- Title, artist, album, year, track number and square cover art
- YouTube matching picks the version with the right length and avoids live / remix / sped-up uploads
- Pause, Stop, *Retry failed (N)* (re-runs only the failed songs, into the right folders), *Open folder*
- A **queue** of several lists, each saved into its own sub-folder; the queue is remembered if you close the app
- A tidy, collapsible **song log** (one entry per song with where it came from), plus a *Details* view and *Copy log* for bug reports
- Songs already in the folder are skipped (even after an interrupted run); a song listed twice is only downloaded once
- Smarter matching: prefers the artist's own release over compilations, live albums and "Expanded Edition" reissues; album info from iTunes when Bandcamp has none
- Retries automatically when the internet drops for a moment
- **About & updates**: shows the yt-dlp version, checks once a day and updates it with one click (restart afterwards), plus diagnostics and a network test
- Runs locally. No account, nothing is uploaded anywhere

## Quick start

Download the zip for your system from the **Releases** page, unzip it and run it. Or run from source:

```bash
pip install -r requirements.txt
python sevby_app.py
```

From source you also need **ffmpeg** (put `ffmpeg.exe` / `ffmpeg` next to `sevby_app.py`, or install it so it's on your PATH).

**Android:** a command-line version for Termux (no window) is described in [TERMUX.md](TERMUX.md). A proper Android app is planned.

## Where does my song list come from?

SEVBY HQ needs a list of songs. The simplest is a text file with one `Artist - Title` per line. It also reads **CSV files** (with Artist and Title / Track Name columns), **M3U / M3U8 playlists**, and the **text file Apple Music exports**, so you can usually load an export as it is.

| Where your music lives | How to get a list |
|---|---|
| **Spotify** | Export with [Chosic](https://www.chosic.com/spotify-playlist-exporter/) (works with any account), or paste the playlist link if you have your own Spotify Client ID (see below) |
| **Apple Music** (iPhone or Mac) | On a Mac (or the Apple Music / iTunes app on Windows), select a playlist and choose **File > Library > Export Playlist**, then save as **Text**. On an iPhone, use a converter such as [TuneMyMusic](https://www.tunemymusic.com/) to export your playlist to a CSV or TXT file. These converters need you to sign in to your Apple Music account, and I have not tested them |
| **YouTube Music, Tidal, Qobuz, Deezer** and others | A converter like TuneMyMusic or [Soundiiz](https://soundiiz.com/) can export a playlist to a file. Check that the one you use supports your service |
| **A folder of music files, foobar2000, MusicBee, Plex** | Export a playlist as **M3U** from your player, or type the songs in |
| **A wish list** (Bandcamp, Discogs, Last.fm, notes app) | Copy the names into a text file, one `Artist - Title` per line |

SEVBY HQ does not log in to any of these services itself.

## Song list format

One song per line:

```
Perturbator - Future Club
Carpenter Brut - Turbo Killer
```

## Using a Spotify playlist (one-time setup)

Spotify requires every app to have its own Client ID. **Spotify currently requires a Premium account to use its Web API, so a free account may not be able to create one.** The author does not have Premium and could not test this mode. If you don't have Premium, export the playlist to text with [Chosic](https://www.chosic.com/spotify-playlist-exporter/) and use *Song list / .txt file*. That works with any Spotify account.

1. Go to <https://developer.spotify.com/dashboard> and log in.
2. Click **Create app**. Use any name and description.
3. Under **Redirect URIs** add exactly `http://127.0.0.1:8888` and click **Add**.
4. Tick **Web API**, agree to the terms, and save.
5. Open the app's **Settings** and copy the **Client ID**.
6. In SEVBY HQ choose *Spotify playlist link*, paste the link and the Client ID, pick a save folder and press **Start**.

## Building it yourself

```bash
pip install -r requirements.txt pyinstaller
python build.py
```

The `dist/` folder then contains `SEVBY-HQ` (or `SEVBY-HQ.exe`). The GitHub Actions workflow in `.github/workflows/build.yml` builds Windows, Linux and macOS versions with the latest yt-dlp, and re-publishes them to the newest release every month.

## Troubleshooting

- **Downloads suddenly stop working (especially from YouTube)** - the downloader inside (yt-dlp) needs updating. Open **About & updates** and press the update button, then restart. The files on the **Releases** page are also rebuilt automatically every month. Termux and source users can run `pip install -U yt-dlp`. This is a hobby project maintained when time allows, so fixes are not guaranteed.
- **A song plays on my phone but not in my car / old MP3 player** - choose *Standard (MP3)* (or use `--quality standard` in Termux) or use the original SEVBY.
- **"Bandcamp blocked / Client Challenge"** - SEVBY HQ then tries each artist's own Bandcamp page and uses YouTube for the rest.
- A copy of each run's log is saved to `~/.sevby_hq_log.txt` - attach it to bug reports.

## Credits - what SEVBY HQ is built on

A small interface that glues together some excellent open-source projects. The heavy lifting is theirs:

- **[yt-dlp](https://github.com/yt-dlp/yt-dlp)** - finds and downloads the audio (Unlicense)
- **[FFmpeg](https://ffmpeg.org)** - writes tags and cover art, converts to MP3 when needed (GPL v3 in the Windows build, bundled as a separate program)
- **[customtkinter](https://github.com/TomSchimansky/CustomTkinter)** by Tom Schimansky - the user interface (MIT)
- **[tkinterdnd2](https://github.com/Eliav2/tkinterdnd2)** - drag and drop (MIT)
- **[PyInstaller](https://pyinstaller.org)** - packages the app (GPL-2.0 with bootloader exception)
- **[Jamendo](https://www.jamendo.com)** and the **[Internet Archive](https://archive.org)** - free Creative Commons music, searched first for lossless copies (not affiliated)
- **[Spotify Web API](https://developer.spotify.com/documentation/web-api)** - only used to read playlist track names and details
- **[Bandcamp](https://bandcamp.com)** - please support artists by buying their music there when you can

Full licence details: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Antivirus note

SEVBY HQ is unsigned and built with PyInstaller, so some antivirus programs may flag or delay it as an unknown file. The full source code is in this repo and the release files are built automatically by the GitHub Actions workflow. You can also run it from source (see Quick start).

## Disclaimer

SEVBY HQ is a tool for personal use. Only download music you have the right to download, such as music you own, that is free to download, or that the artist allows. Downloading copyrighted material without permission may be illegal where you live, and may break the terms of service of YouTube, Bandcamp or Spotify. You are responsible for how you use it. SEVBY HQ is not affiliated with or endorsed by Spotify, Bandcamp, YouTube or Google. The software is provided "as is", without warranty (see [LICENSE](LICENSE)).
