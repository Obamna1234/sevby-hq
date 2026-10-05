# Third-party software and services

SEVBY HQ itself is MIT licensed (see `LICENSE`). It would not exist without the projects below, which belong to their respective authors. Each keeps its own licence.

| Component | Author / project | Licence | How SEVBY HQ uses it |
|---|---|---|---|
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | yt-dlp contributors | Unlicense (public domain) | Imported as a library; does all searching/downloading from YouTube and Bandcamp |
| [FFmpeg](https://ffmpeg.org) | FFmpeg developers | LGPL 2.1+ or GPL 2+ (GPL v3 in the Windows build) | Bundled as a **separate executable**; MP3 conversion, tags, cover art |
| [customtkinter](https://github.com/TomSchimansky/CustomTkinter) | Tom Schimansky | MIT | User interface |
| [tkinterdnd2](https://github.com/Eliav2/tkinterdnd2) | Eliav2 and contributors (tkdnd by Petasis) | MIT | Drag-and-drop |
| [PyInstaller](https://pyinstaller.org) | PyInstaller team | GPL-2.0 with a bootloader exception that allows distributing built apps under any licence | Build tool only |
| Python / Tk | Python Software Foundation / Tcl-Tk | PSF / Tcl licence | Runtime |

## FFmpeg

FFmpeg is a trademark of Fabrice Bellard. SEVBY HQ includes an unmodified FFmpeg build as a separate program that SEVBY HQ runs as a command-line tool.

- FFmpeg licence information: https://ffmpeg.org/legal.html
- FFmpeg source code: https://ffmpeg.org/download.html#get-sources
- **Windows build:** FFmpeg git build 2026-10-01 (essentials) from gyan.dev, https://www.gyan.dev/ffmpeg/builds/ . This build is licensed under the GNU GPL v3 (it was configured with --enable-gpl --enable-version3). Source code: https://ffmpeg.org/download.html#get-sources and the source link on the gyan.dev builds page.
- **Linux and macOS builds:** static FFmpeg binaries fetched by the build workflow (FedericoCarboni/setup-ffmpeg). Their licences and sources are listed on the download pages linked from that project.

## Services (not bundled, not affiliated)

SEVBY HQ talks to these services but is not affiliated with, endorsed by, or sponsored by any of them. Their names and trademarks belong to their owners.

- **Spotify**: Web API, used only to read playlist details. Use of the API is governed by the [Spotify Developer Terms](https://developer.spotify.com/terms). Each user supplies their own Client ID.
- **Jamendo**: public API, used to find Creative Commons tracks that allow download (an optional free Client ID from the user). Not affiliated.
- **Internet Archive**: public search and download of Creative Commons-licensed audio. Not affiliated.
- **Bandcamp**: public track pages.
- **YouTube / Google**: public videos.
