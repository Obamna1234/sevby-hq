# SEVBY for Android

A native Android version of SEVBY, built on [youtubedl-android](https://github.com/yausername/youtubedl-android)
(which bundles yt-dlp, Python and FFmpeg). Beta.

## Run it from Android Studio

1. **File → Open** and choose this `android` folder (not the repo root).
2. Let Gradle sync (first time downloads a lot).
3. Plug in the phone with USB debugging on, pick it in the device list, press **Run**.

## Get an APK from GitHub

Every push that changes `android/` runs the **Build Android APK** workflow.
Open the run under **Actions** and download the `SEVBY-android-…` artifact (a zip with the APKs):

| File | For |
|---|---|
| `…-arm64-v8a.apk` | almost every phone from the last ~7 years (smallest) |
| `…-armeabi-v7a.apk` | older 32-bit phones |
| `…-universal.apk` | any device, if unsure (largest) |

## Making a release

Android releases have their own tags, **`android-v…`** (e.g. `android-v0.1.0-beta`), so they never clash with the
desktop releases (`v…`). The version shown in the app comes from the tag.

1. Make sure the signing-key secrets below are set (release builds stop with an error without them).
2. Edit `android/RELEASE_NOTES.md` if needed and commit it.
3. On GitHub: **Releases → Draft a new release → Choose a tag →** type `android-v0.1.0-beta` and pick
   **Create new tag on publish** (target `main`). Title: `SEVBY for Android 0.1.0-beta`. Paste
   `android/RELEASE_NOTES.md` into the description. Tick **Set as a pre-release**, untick **Set as the latest
   release**, then **Publish release**.
4. Publishing creates the tag, which starts the **Build Android APK** workflow. After ~10 minutes the signed APKs
   appear on the release.

Once the APKs are on the release, everyone's app shows **"SEVBY x.y.z is available"** within a day (it checks
GitHub's releases list for the newest `android-v…` release with APKs; drafts are ignored). Tapping *Download* opens
the APK for their phone in the browser, and it installs over the old version because it's signed with the same key.
Use a higher version in each new tag (e.g. `android-v0.1.1-beta`, `android-v0.2.0`).

A manual run with **publish** ticked re-uploads the APKs to the newest `android-v…` release.
The desktop workflow's monthly rebuild skips `android-v…` releases.

## Signing key (do this once, so updates install over the old app)

Without a key, the workflow signs with a throwaway key and every new build has to be
installed after uninstalling the previous one. To fix that:

**Windows (PowerShell)**, using the Java that comes with Android Studio:

```powershell
cd $HOME\Documents
& "C:\Program Files\Android\Android Studio\jbr\bin\keytool.exe" -genkeypair -v -keystore sevby.jks -alias sevby -keyalg RSA -keysize 4096 -validity 10000
[Convert]::ToBase64String([IO.File]::ReadAllBytes("$PWD\sevby.jks")) | Set-Content -NoNewline sevby.jks.b64
```

It asks for a password (type it twice; nothing shows while typing) and a few name questions (any answers, or
Enter to skip; type `yes` at the end).

**Linux / macOS:**

```bash
keytool -genkeypair -v -keystore sevby.jks -alias sevby -keyalg RSA -keysize 4096 -validity 10000
base64 -w0 sevby.jks > sevby.jks.b64      # macOS: base64 -i sevby.jks -o sevby.jks.b64
```

In the repo: **Settings → Secrets and variables → Actions → New repository secret**, add:

- `SEVBY_KEYSTORE_BASE64` – the contents of `sevby.jks.b64`
- `SEVBY_KEYSTORE_PASSWORD` – the keystore password you chose
- `SEVBY_KEY_ALIAS` – `sevby`
- `SEVBY_KEY_PASSWORD` – the same password again (newer keytool uses one password for both)

Keep `sevby.jks` and its password somewhere safe and **never commit it** (it's in `.gitignore`).
If you lose it, users have to uninstall before installing future versions.

Builds run from Android Studio use your local debug key, so switching between a Studio
build and a GitHub build on the same phone also needs an uninstall first.
