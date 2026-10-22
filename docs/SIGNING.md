# Signing

There are two independent kinds of signature:

| | Update signing | Code signing (Windows `.exe`) |
|---|---|---|
| What | Ed25519 signature over `SHA256SUMS` | Authenticode signature on the `.exe` |
| Required? | **Yes** — the app refuses unsigned updates | No — only affects SmartScreen |
| Key | Your own key pair (free) | OV/EV certificate from a CA (paid) |
| Secret | `UPDATE_SIGNING_KEY` | `WINDOWS_CERT_PFX`, `WINDOWS_CERT_PASSWORD` |

## Update signing (required for in-app updates)

Every release must contain `SHA256SUMS` and `SHA256SUMS.sig`. `SHA256SUMS`
looks like this and is signed as a whole, so the version is covered too:

```text
version: 0.4.0
<sha256>  ClaudeQuotaTray.exe
<sha256>  ClaudeQuotaTray-macos.zip
<sha256>  claude-quota-tray-v0.4.0.zip
```

The app (`src/updater.py`) installs a release only if the signature matches a
key in `src/update_keys.py`, the signed version equals the tag and is newer
than the installed one, and the downloaded file matches its hash. Details:
[SECURITY.md](../SECURITY.md#signed-updates).

### One-time setup (before your first signed release)

Each repository — including a fork — needs its own key.

1. Generate the key pair (needs `pycryptodome`, already in `requirements.txt`):

   ```bash
   python scripts/gen_update_key.py
   ```

   It writes the private key **outside the repo** (default
   `~/.claude-quota-tray-signing/update_signing_key.pem`) plus a `.b64` copy for
   the secret, and prints the public key. It refuses to write inside the repo
   or overwrite an existing key. The private key is never printed.

2. Store the secret (GitHub CLI, or *Settings → Secrets and variables →
   Actions* in the repo):

   ```bash
   gh secret set UPDATE_SIGNING_KEY < ~/.claude-quota-tray-signing/update_signing_key.pem.b64
   ```

3. Paste the printed public key into `src/update_keys.py` and commit it
   **before** tagging:

   ```python
   UPDATE_PUBLIC_KEYS: list[str] = [
       "<64 hex characters printed by gen_update_key.py>",
   ]
   ```

4. Back up the `.pem` somewhere offline (password manager, encrypted USB),
   then delete the `.b64` copy. **Never commit either file.**

Until `UPDATE_PUBLIC_KEYS` has a key, the app refuses every update, and the
release workflow fails at the *verify* step.

### Checklist before tagging a release

1. `APP_VERSION` in `src/config.py` is plain `X.Y.Z`, newer than the last
   release, and the tag will be exactly `v` + that version (e.g. `0.4.0` →
   `v0.4.0`). Pre-release tags such as `v0.4.0-rc1` are rejected by the
   workflow and by the app.
2. Optional but recommended: run **Actions → Release → Run workflow** (a dry
   run). It builds and signs everything without publishing, and uploads
   `SHA256SUMS` + `SHA256SUMS.sig` as the `dry-run-release` artifact.
3. Push the tag: `git tag v0.4.0 && git push origin v0.4.0`.
4. Check the release has all five files: the `.exe`, the macOS zip, the
   source zip, `SHA256SUMS`, `SHA256SUMS.sig`.
5. From an installed copy: `python src/update_runner.py --check` should report
   *Update available (signature and version verified)*.

### How the workflow publishes

`release.yml` builds the Windows `.exe`, the macOS zip and the source zip as
**artifacts only**. The final `release` job downloads them, writes
`SHA256SUMS`, signs it, verifies the signature against `src/update_keys.py`,
and only then creates the GitHub release with all files at once — so a release
never exists without its signature. On Windows the artifact is uploaded after
the optional code-signing step, so the hash covers the exact file users get.

### Rotating the key

1. Generate a new key pair (`--out` to a new path).
2. **Append** the new public key to `UPDATE_PUBLIC_KEYS` (keep the old one)
   and ship a release signed with the **old** key. Installed apps now trust
   both keys.
3. Replace the `UPDATE_SIGNING_KEY` secret with the new key.
4. In a later release, remove the old public key.

### If the private key leaks

1. Report it privately (see [SECURITY.md](../SECURITY.md#reporting-a-vulnerability)).
2. Generate a new key, set the new secret, and **replace** the leaked public
   key in `src/update_keys.py`.
3. Publish a release signed with the new key and tell users to install it
   **manually** from the Releases page: copies that only trust the leaked key
   can't verify it (that is the point), and the leaked key could be used to
   sign a malicious "update" for them until they move on.

## Code signing (Windows .exe, optional)

Unsigned PyInstaller builds often trigger Windows Defender / SmartScreen. To publish a code-signed executable:

1. Purchase an **OV or EV** code-signing certificate from a public CA (Sectigo, DigiCert, etc.).
2. Export a `.pfx` file and keep the password secret.
3. Sign locally:

```bat
signtool sign /f your-cert.pfx /p YOUR_PASSWORD ^
  /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 ^
  dist\ClaudeQuotaTray.exe
```

### GitHub Actions (optional)

Add repository secrets:

- `WINDOWS_CERT_PFX` — base64-encoded `.pfx`
- `WINDOWS_CERT_PASSWORD` — PFX password

The release workflow code-signs the `.exe` when these secrets exist. Without them, the `.exe` is not code-signed (update signing still applies).

Self-signed certificates do **not** help end users bypass SmartScreen.

## macOS (.app)

This project does **not** use Apple Developer ID or notarization. macOS builds are **unsigned**.

- **Recommended:** run from source (`python3` + virtualenv).
- If using a release `.app`, you may need **Right-click → Open** the first time (Gatekeeper).

Notarization can be added later if you obtain an Apple Developer account.
