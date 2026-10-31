# Sevby HQ — release notes draft (security hardening)

> Draft for the next release. No functional changes; hardening only. Review before publishing.

## Recommended version — security hardening

This release adds small security hardening fixes from an October 2026 code review. **Nothing about how the
app works changes.** If you use Sevby HQ, this is the recommended version.

**What changed**
- Download engine: a crafted or tampered search result can no longer slip options into the downloader.
- Your config file is now kept private to your user account.
- Temporary cover/thumbnail files now use unpredictable names.

**Severity:** low/medium hardening. No critical or high-severity issues were found.

## Keeping a stable fallback

The previous release remains available as a known-good fallback. Earlier versions work but lack the
hardening above — see `SECURITY.md`, including a note on the "Free HQ" sources, for details.
