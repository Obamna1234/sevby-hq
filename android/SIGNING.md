# Signing the SEVBY HQ Android app

Release APKs are signed in CI from four repository **Secrets** (Settings → Secrets and variables → Actions):

- `SEVBYHQ_KEYSTORE_BASE64` — your keystore (.jks) as base64 (`base64 -w0 sevbyhq.jks`)
- `SEVBYHQ_KEYSTORE_PASSWORD`
- `SEVBYHQ_KEY_ALIAS`
- `SEVBYHQ_KEY_PASSWORD`

With these set, the `Build SEVBY HQ Android APK` workflow produces a properly signed release APK.
Without them, it falls back to a throwaway debug key (fine for testing, but each build must be
installed after uninstalling the previous one, and it is not suitable for distribution).

Use a **new, dedicated keystore** for SEVBY HQ (do not reuse SEVBY's), so the two apps have
distinct signing identities. Keep a safe backup of the .jks and passwords — they can't be recovered.
