# SEVBY HQ on Android (Termux, command line)

This is the no-window version of SEVBY HQ. It runs in Termux, a free terminal app for Android.
It is a beta: it has not been tested on many phones yet, so please report problems under Issues.

## 1. Install Termux
Get it from **F-Droid** (https://f-droid.org/packages/com.termux/) or the Termux GitHub releases page.
The Google Play version is outdated and will not work.

## 2. One-time setup (copy these into Termux, one line at a time)
```
pkg upgrade
pkg install python ffmpeg git
pip install -U yt-dlp curl_cffi
termux-setup-storage
git clone https://github.com/Obamna1234/sevby-hq
cd sevby-hq
```
`termux-setup-storage` asks for permission to save files; allow it. Music then goes into `~/storage/music` by default.

## 3. Use it
From a text file of `Artist - Title` lines:
```
python sevby_cli.py songs.txt -o ~/storage/music
```
From a Spotify playlist (needs a Spotify Client ID, which requires Spotify Premium to create; see the main README. Without Premium, use a .txt list instead):
```
python sevby_cli.py "https://open.spotify.com/playlist/XXXX" --client-id YOUR_CLIENT_ID -o ~/storage/music
```
The first time, SEVBY prints a link. Open it in your phone browser, log in to Spotify and approve. Keep Termux running while you do that (use split screen or switch back quickly). After that the login is remembered.

Several playlists / lists in one go (a queue, one after another, each in its own folder):
```
python sevby_cli.py list1.txt list2.txt "https://open.spotify.com/playlist/XXXX" -o ~/storage/music
```
Only Bandcamp or only YouTube: add `--source bandcamp` or `--source youtube`.

Free lossless sources: SEVBY HQ first checks Jamendo and the Internet Archive for Creative Commons copies (FLAC where available). For Jamendo, get a free Client ID at https://devportal.jamendo.com and run once with `--jamendo-id YOUR_ID` (it is remembered). Skip these sources with `--no-free`.

Audio quality: by default SEVBY HQ keeps YouTube's original audio as **M4A** (no re-encoding). Add `--quality standard` to convert everything to MP3 like the original SEVBY.

Retry the songs that failed last time:
```
python sevby_cli.py --retry -o ~/storage/music
```
Stop at any time with **Ctrl+C** (on the Termux keyboard row: CTRL then C). Finished songs are kept and skipped next time.

## Tips
- Keep the phone awake during long runs: run `termux-wake-lock` first, and in Android settings set Termux battery usage to "Unrestricted".
- `curl_cffi` (installed in the setup step above) lets Bandcamp downloads work on Android. Without it, SEVBY still works but uses YouTube for every song.
- Update yt-dlp now and then: `pip install -U yt-dlp`. If downloads fail with errors like 403, this is the first thing to try.
- Update SEVBY: `cd ~/sevby-hq && git pull`.
- Your files are in the `Music` folder on the phone, in your music app after a minute or two.
- Commands are case-sensitive. If your keyboard capitalizes the first letter, turn off auto-capitalization, or paste the commands instead of typing them.
- If a song is skipped as "exists", it was already downloaded earlier. Use a new folder with `-o` to download it again.
