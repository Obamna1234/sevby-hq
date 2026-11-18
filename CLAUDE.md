# Working rules for this repo (SEVBY / SEVBY HQ)

These apply to every Claude session that works in this repository.

## Security (non-negotiable)
- **Never** create or change a workflow, script or file that outputs, uploads, prints, encrypts-and-uploads, or "backs up" any
  secret (signing keys, keystore passwords, API keys, tokens) – not as an artifact, release asset, log line, issue, comment or
  commit, not even password-protected or "temporary". This repo is public: Actions artifacts and logs can be downloaded by
  anyone signed in to GitHub. (On 1 Nov 2026 a "key backup" workflow did exactly this; the key had to be replaced.)
- If the owner asks for a key or password back: say GitHub never shows saved secrets again, and that the only copies are the
  ones on the owner's own computer / password manager. Do not extract them.
- Never commit keystores (`*.jks`, `*.keystore`), `*.b64` key files, passwords or API keys. Signing keys are created and backed
  up only on the owner's own computer.
- Signing secrets are only given to builds of `main` and release tags – never to other branches or pull requests. Keep it so.
- Every release gets an independent security review of what changed (see the release checklist below).

## Release checklist
Follow the owner's checklist (claude.ai doc "Sevby and Sevby HQ Release Checklist"): tests and builds on every push; an
end-to-end download test; updating keeps settings; an independent security review; publish only after the owner says
"release it", beta first.

## How the two apps relate
- SEVBY (green) and SEVBY HQ (gold) share their Android code: change SEVBY first, then sync HQ (hq_sync.py in the
  "Sevby and SevbyHQ" Claude project), keeping HQ's name, colours, icons, app id and secret names.
- A release is made by changing `VERSION` in sevby_app.py on `main`; bump it in the same push that brings the changes.
