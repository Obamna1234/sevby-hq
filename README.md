***Beta: only lightly tested. Windows is the best-tested build. Linux was lightly tested on Ubuntu (WSL2); the macOS build and the Android (Termux) version are untested or lightly tested.***

# SEVBY HQ: Playlist to M4A / MP3

*Screenshots show **SEVBY HQ** (gold theme) on Windows. Linux was tested on Ubuntu (via WSL2) and macOS is untested, so they may look slightly different. The green version is the [original SEVBY](https://github.com/Obamna1234/sevby).*

<p align="center">
<img width="754" height="928" alt="image" src="https://github.com/user-attachments/assets/af7d0cfd-2b37-4e0c-8470-493c81cdbc8f" />
<br><em>SEVBY HQ v0.10.0-beta importing a .txt file playlist</em>
</p>

<p align="center">
<img width="757" height="929" alt="image" src="https://github.com/user-attachments/assets/e33f1ecd-2af9-40b2-bf48-d1cdd8a5fe11" />
<br><em>SEVBY HQ v0.10.0-beta downloading from an imported .txt file playlist</em>
</p>

<p align="center">
<img width="758" height="929" alt="image" src="https://github.com/user-attachments/assets/9c9a522d-7340-40b2-860f-e91c613d6a5e" />
<br><em>SEVBY HQ v0.10.0-beta UI for pasting a Spotify URL playlist and the Client ID "What is this" pop up window</em>
</p>

<p align="center">
<img width="754" height="925" alt="image" src="https://github.com/user-attachments/assets/31177dcb-9b75-4157-9be8-17b02090295e" />
<br><em>SEVBY HQ v0.10.0-beta downloading from an imported .txt and a more detailed log</em>
</p>

<p align="center">
<img width="756" height="928" alt="image" src="https://github.com/user-attachments/assets/b42550a6-4714-449f-b789-bb31766a864a" />
<br><em>SEVBY HQ v0.10.0-beta exporting an Apple Music playlist URL to .txt &amp; automatically pasting into the text box</em>
</p>

<p align="center">
<img width="756" height="927" alt="image" src="https://github.com/user-attachments/assets/d7fb460a-e4c0-46b1-a079-10d78d691373" />
<br><em>SEVBY HQ v0.10.0-beta About &amp; Updates window</em>
</p>

The golden child of [SEVBY](https://github.com/Obamna1234/sevby). Same idea, with options for better audio quality if available.

Turn a plain text list of songs (or a Spotify playlist) into tagged audio files on your own computer.
SEVBY HQ looks for each song on **Bandcamp** first, and falls back to **YouTube** for anything it can't find.

## SEVBY or SEVBY HQ?

| | SEVBY | SEVBY HQ |
|---|---|---|
| Free lossless sources | - | **Optional, off by default.** When you turn it on, it checks Jamendo and the Internet Archive first for copies labelled Creative Commons, in FLAC (or 192 kbps+ MP3). Only a small share of songs are there, so most will still come from Bandcamp / YouTube |
| YouTube songs | Converted to MP3 | **Original audio kept as M4A**, no extra re-encoding (falls back to MP3 if no M4A exists) |
| Bandcamp songs | 128 kbps MP3 (Bandcamp's stream) | Same |
| Honest log | `OK` | Each song says what you actually got, e.g. `[YouTube original audio (M4A/AAC ~129 kbps)]` |
| Find better copies | - | A text file of search links for stores that sell lossless / 320 kbps versions |
| Works on | Every player | Almost every phone and computer. Some old MP3 players and car stereos can't play M4A |

If you want files that play everywhere, use the original SEVBY (or pick *Standard (MP3)* in HQ).

## How SEVBY HQ picks a source

For each song, in this order:

1. **Jamendo** and the **Internet Archive**, but **only if you have turned on "Also check Jamendo + Internet Archive first"** (it is off by default; see the warning below). Only copies labelled Creative Commons are used. If the same song is there as FLAC or a high-bitrate MP3, that file is used, and the log says so, e.g. `[Internet Archive, FLAC, Creative Commons]`, with a link to the item so you can check it. Album, year and cover art for these files come from Apple's iTunes search when available, not from the uploader's text.
2. **Bandcamp** (the free 128 kbps stream), as in the original SEVBY.
3. **YouTube**, keeping the original audio as M4A.

**Important:** Jamendo and the Internet Archive let anyone upload music and choose its licence label, and nobody checks it. A file labelled Creative Commons may be a mislabelled copy of a commercial song (during testing, a commercial rock song turned up on the Archive tagged this way). SEVBY HQ cannot verify the licence; it only matches the title, artist and length. That is why this option is off by default and shows a notice when you turn it on. **You are responsible for only keeping music you have the right to download.** If you are unsure, leave it off. Most songs will not be there anyway and will fall through to Bandcamp / YouTube. To avoid saving the wrong song, a file is only used when the title, artist and length all match closely. Jamendo is **optional and needs your own free Client ID**. Register at <https://devportal.jamendo.com>, create an app, and paste your ID into the box once. There is no shared key, because Jamendo's terms say keys are personal. By using your own key you agree to [Jamendo's API terms](https://devportal.jamendo.com/api_terms_of_use) yourself, which include crediting Jamendo and the artists (the log prints each track's Jamendo link) and not building an offline-access service. If you are unsure, leave the box empty and only the Internet Archive is checked. In Termux, add `--free` to turn these sources on. Leave the box unticked (or leave out `--free`) to skip them.

## Be realistic about quality

- **FLAC is the exception, not the rule.** SEVBY HQ only gets FLAC if you have turned on the free sources *and* a song is on **Jamendo** or the **Internet Archive** with a Creative Commons label *and* that copy is lossless. That is a small slice of music, mostly independent artists. For most songs (anything commercial or mainstream) expect Bandcamp's 128 kbps stream or YouTube's M4A instead. In a typical playlist, few or none of the songs will come as FLAC.
- Jamendo needs your own free Client ID (see below), and some artists switch downloads off. SEVBY HQ asks Jamendo for FLAC first and falls back to a high-bitrate MP3 if FLAC isn't offered. I have not been able to test every case, and Jamendo may require a logged-in account to serve FLAC files, in which case you would get the MP3 instead. The log always says which one you got.
- Free lossless files exist only for music released under Creative Commons. Everything else is limited by what Bandcamp's free stream and YouTube offer.
- Bandcamp's free stream and YouTube audio are only about **128-160 kbps**. SEVBY HQ keeps them as they are rather than re-encoding, which avoids losing a little more quality. It **cannot make them better** than they started.
- Real high quality (FLAC or 320 kbps) means buying the music. After each run, HQ writes `SEVBY HQ - find better quality.txt` into your music folder with search links (Bandcamp, Qobuz, 7digital) for the songs that came from YouTube. Buying from the artist on Bandcamp is the best way to support them.
- Spotify is not used for audio, only for reading a playlist's song names.

## Features

- Paste a list / `.txt` file of `Artist - Title` lines, **or** a shared **Apple Music playlist link** (no sign-in), **or** a Spotify playlist link (needs a Spotify Client ID, which currently requires Spotify Premium to create; see below)
- Bandcamp first, YouTube for the rest (or choose *Bandcamp only* / *YouTube only*)
- **Optional (off by default):** checks **Jamendo and the Internet Archive** first for copies labelled Creative Commons, lossless when available. Licence labels there are set by uploaders and are not verified, so a notice explains this when you turn it on
- Choose **Best available (M4A)** or **Standard (MP3)** for YouTube audio
- Title, artist, album, year, track number and square cover art
- YouTube matching picks the version with the right length and avoids live / remix / sped-up uploads
- Pause, Resume, Stop, *Retry failed (N)* (re-runs only the failed songs, into the right folders), *Open folder*
- A **queue** of several lists, each saved into its own sub-folder; the queue is remembered if you close the app
- A tidy, collapsible **song log** (one entry per song with where it came from), plus a *Details* view and *Copy log* for bug reports
- Songs already in the folder are skipped (even after an interrupted run); a song listed twice is only downloaded once
- Smarter matching: prefers the artist's own release over compilations, live albums and "Expanded Edition" reissues; album info from iTunes when Bandcamp has none
- Retries automatically when the internet drops for a moment
- **About & updates**: shows the yt-dlp version, checks once a day and updates it with one click (restart afterwards), tells you when a newer SEVBY release is out (once a day, with a Download button), plus diagnostics and a network test
- Runs locally. No account, nothing is uploaded anywhere

## Quick start

Download the zip for your system from the **Releases** page, unzip it and run it. On Linux or macOS you may need to make the file executable first (`chmod +x SEVBY-HQ`). Or run from source:

```bash
pip install -r requirements.txt
python sevby_app.py
```

From source you also need **ffmpeg** (put `ffmpeg.exe` / `ffmpeg` next to `sevby_app.py`, or install it so it's on your PATH).

**Android:** a command-line version for Termux (no window) is described in [TERMUX.md](TERMUX.md). An Android app is also available on the [SEVBY releases page](https://github.com/Obamna1234/sevby/releases) (this HQ edition itself is desktop and Termux only).

## Where does my song list come from?

SEVBY HQ needs a list of songs. The simplest is a text file with one `Artist - Title` per line. It also reads **CSV files** (with Artist and Title / Track Name columns), **M3U / M3U8 playlists**, and the **text file Apple Music exports**, so you can usually load an export as it is.

| Where your music lives | How to get a list |
|---|---|
| **Spotify** | Export with [Chosic](https://www.chosic.com/spotify-playlist-exporter/) (works with any account), or paste the playlist link if you have your own Spotify Client ID (see below) |
| **Apple Music** (iPhone, Mac or Windows) | In Apple Music open the playlist, tap **Share > Copy Link**, then in SEVBY HQ press **Apple Music** and paste the link. No sign-in needed. It only works for playlists anyone can open (not private ones), and it reads Apple's public web page, so it could stop working if Apple changes that page. Alternatives: on a Mac or in the Apple Music / iTunes app on Windows choose **File > Library > Export Playlist** and save as **Text**, or use a converter such as [TuneMyMusic](https://www.tunemymusic.com/) (needs you to sign in to Apple Music) |
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

## Where the music comes from (and what SEVBY HQ does not do)

SEVBY HQ only downloads from Bandcamp, YouTube and, if you turn it on, Jamendo and the Internet Archive. It does not use torrents, file-sharing networks or ripping groups, and it will not be extended to. There are two reasons:

- **Legal:** SEVBY HQ is meant for music you own, that is free to download, or that the artist allows. Pirated copies are none of those.
- **Safety:** files from unknown sources are a common way to spread malware, such as programs disguised as audio ("Song.flac.exe"), fake "codec" installers and tampered archives. SEVBY HQ only saves audio files and tags them. It never runs a downloaded file as a program.

This lowers the risk but doesn't remove it. Keep your antivirus and system up to date, and be careful with any music from sources you don't know.

## Disclaimer

SEVBY HQ is a tool for personal use. Only download music you have the right to download, such as music you own, that is free to download, or that the artist allows. Downloading copyrighted material without permission may be illegal where you live, and may break the terms of service of YouTube, Bandcamp or Spotify. You are responsible for how you use it. SEVBY HQ is not affiliated with or endorsed by Spotify, Bandcamp, YouTube or Google. SEVBY HQ is an independent open-source project and is not affiliated with Sevby LLC (sevby.com). The software is provided "as is", without warranty (see LICENSE).
