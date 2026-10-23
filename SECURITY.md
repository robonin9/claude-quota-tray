# Security Policy

## Supported versions

Only the latest release line receives fixes. This is a small single-developer
fork, so please always run the most recent version before reporting an issue.

| Version | Supported          |
| ------- | ------------------ |
| 0.6.x   | :white_check_mark: |
| < 0.6   | :x:                |

0.5.0 is this fork's first release with signed in-app updates; earlier
versions install updates without verification, so upgrade to 0.5.0 or later
manually.
(This fork skipped 0.4.0 because upstream already released a different 0.4.0.)

## How the app handles your credentials

Claude Quota Tray is designed to touch your OAuth credentials as little as
possible:

- **Read-only for existing credentials.** The app only *reads* the Claude OAuth
  token that already exists on your machine (Claude Desktop, Windows Credential
  Manager, environment variables, or Claude Code credential files). It never
  writes, rotates, or modifies those credentials.
- **One app-owned token, kept in the OS secret store.** If you use *Connect
  Claude account…*, the token you paste is stored under `ClaudeQuotaTray/oauth-token`
  in Windows Credential Manager (DPAPI, per user) or the macOS login Keychain —
  only on other systems in a `0600` file in the data folder. *Disconnect*
  deletes it.
- **Never in plain app files.** Tokens are **never** written to `settings.json`,
  `history.db`, or the log. Settings store only a *path* or the `auto`
  discovery mode.
- **Single destination.** The token is sent only to `https://api.anthropic.com`
  over HTTPS, in a minimal request whose response body is ignored — the app
  reads usage from the `anthropic-ratelimit-unified-*` response headers only.
- **Local-only data.** History (`history.db`) stores just timestamps and
  utilisation percentages. No prompts, completions, account identifiers, or
  token material are recorded.
- **Logs are safe to share.** `error.log` records diagnostics and token-refresh
  events but never the token itself (probes print only a short fingerprint such
  as `sk-ant...GAAA`).

All of this is auditable — the project is open source (MIT). You can verify the
auth flow in `src/auth_discovery.py`, `src/token_reader.py`, and the request in
`src/api_client.py`, or run `python src/token_reader.py --probe` to see exactly
which source is used (the probe masks the token).

## Signed updates

*Install latest release…* in the tray menu, `Update claude quota tray.bat` and
`python src/update_runner.py --apply` all go through the same verification in
`src/updater.py`. A release is installed only if **every** check passes; if any
fails, nothing is downloaded into, extracted to, or moved inside the install
folder (a partly downloaded `.exe` is deleted).

1. The release has `SHA256SUMS` and `SHA256SUMS.sig`. GitHub's auto-generated
   source zipball is never used — it isn't covered by the signature.
2. `SHA256SUMS.sig` is a valid **Ed25519** signature of `SHA256SUMS` by one of
   the public keys in `src/update_keys.py`. An empty key list refuses every
   update.
3. The signed `version:` line equals the release tag, uses plain `X.Y.Z`
   (no pre-release suffixes), and is **newer** than the installed version — a
   signed but older release can't be pushed as "latest" to downgrade users.
4. The downloaded file's SHA-256 matches its line in the signed file
   (constant-time comparison).
5. Downloads start at `https://github.com/<owner>/<repo>/releases/download/`
   for the configured repo, and every redirect (GitHub serves assets from its
   CDN) must stay on HTTPS.

`release.yml` builds every asset first and publishes the release only once
`SHA256SUMS` is signed, so a release never exists without its signature. The
private key lives only in the `UPDATE_SIGNING_KEY` repository secret and the
maintainer's offline backup — see [docs/SIGNING.md](docs/SIGNING.md).

**What this protects against:** corrupted or truncated downloads, files
modified in transit or replaced in the release, and forced downgrades to an
older signed release.

**What it does not protect against:** a leaked signing key (anyone with it can
sign releases — report a suspected leak privately, see below); and a *freeze*,
where someone hides newer releases so the app keeps the version it has (it
will never move to an older one).

## Known limitations

- **Python dependencies are hash-pinned (since 0.6.0).** `requirements.lock`
  lists every package with an exact version and SHA-256 hashes;
  `Setup claude quota tray.bat`, `scripts/setup_mac.sh`, source-mode updates and
  CI install it with `pip install --require-hashes`, so pip refuses any file
  that doesn't match. Release builds use `requirements-dev.lock` (incl.
  PyInstaller) and the signing job `requirements-sign.lock`. Because the lock
  ships inside the signed source zip, its hashes are covered by the release
  signature. Remaining gaps: the first update *from 0.5.0* still runs 0.5.0's
  updater, which installs from `requirements.txt` without hashes (later updates
  use the lock), and `pip` itself is whatever the local Python bundles.
- **The `.exe` swap from 0.5.0 can fail without saying so.** The file is only
  swapped after the app quits, by a script the *running* version writes.
  0.5.0's script waits a fixed 3 seconds; if the old `.exe` is still locked it
  restarts 0.5.0 and leaves the verified `ClaudeQuotaTray.exe.new` behind (quit
  the app and `move /y ClaudeQuotaTray.exe.new ClaudeQuotaTray.exe`). From 0.6.0
  the script retries for 30 seconds and logs a failure to `error.log`. Either
  way nothing unverified is run: the `.new` file passed every check above.
- **Manual updates are not verified automatically.** If you replace files by
  hand, compare them against the release's `SHA256SUMS` yourself (for example
  `certutil -hashfile <file> SHA256` on Windows).

## Reporting a vulnerability

If you believe you have found a security issue:

1. **Preferred:** open a private [GitHub Security Advisory](https://github.com/robonin9/claude-quota-tray/security/advisories/new)
   on this repository.
2. Alternatively, open a regular [issue](https://github.com/robonin9/claude-quota-tray/issues)
   — but please do **not** include tokens, credentials, or other secrets in a
   public issue.

If you suspect the **update-signing key** has leaked, always use the private
advisory: the maintainer will ship a release that removes that key from
`src/update_keys.py` and ask users to update manually.

Please include the version, your OS, reproduction steps, and the impact you
observed. As a hobby project, responses are best-effort: expect an initial reply
within a couple of weeks. Accepted issues will be fixed in the next release and
credited (if you wish); out-of-scope reports will get a short explanation.

## Scope notes

- **Two different signatures.** *Update signing* (Ed25519 over `SHA256SUMS`,
  above) is **mandatory** and is what the app checks. *Code signing* of the
  `.exe` with an OV/EV certificate (signtool) is **optional** and only affects
  Windows SmartScreen. Released `.exe` / `.app` builds are not code-signed
  unless a maintainer has a certificate — see [docs/SIGNING.md](docs/SIGNING.md).
  Antivirus/SmartScreen warnings on a PyInstaller build without code signing
  are expected and are not themselves a vulnerability. When in doubt, run from
  source.
- The app's security depends on the integrity of the machine it runs on. If the
  local credential stores it reads from are already compromised, this tool
  cannot protect them.
