# Changelog

All notable changes to this fork are documented here.

**Upstream baseline:** [kpcrmv4/claude-quota-tray](https://github.com/kpcrmv4/claude-quota-tray) v0.2.0  
**Concept inspiration:** [Clawdmeter](https://github.com/HermannBjorgvin/Clawdmeter) (ESP32 hardware meter)

---

## [Unreleased] — fork (robonin9)

Changes compared to upstream v0.2.0. Tray UI, polling, history, and notifications are otherwise the same unless noted.

### Security: signed updates (2026-10) — breaking for releases

- **In-app updates are verified before anything is installed.** Releases must include `SHA256SUMS` (a `version: X.Y.Z` line plus `<sha256>  <file>` lines) and `SHA256SUMS.sig`, an **Ed25519** signature over the whole file by a key in `src/update_keys.py`. The updater checks the signature, that the signed version equals the tag and is newer than the installed version, and the downloaded file's SHA-256 (constant-time compare) — and only then extracts / moves anything. A failed `.exe` download is deleted.
- **No downgrades, no pre-releases.** A signed but older release set as "latest" is refused (`UpdateNotNewerError`). Versions must be plain `X.Y.Z`: previously `0.4.0-rc1` compared equal to `0.4.0`.
- **GitHub's auto-generated zipball is no longer used** (it can't be covered by the signature). Asset downloads must start at `https://github.com/<owner>/<repo>/releases/download/` for the configured repo, and redirects must stay on HTTPS.
- **"Update available" only for installable releases.** `check_for_update` downloads and verifies `SHA256SUMS`/`.sig` first; a newer release without them reports *unsigned* instead of offering an update that would then fail. New toasts (en/th): `toast.update_unsigned`, `toast.update_verify_failed`, `toast.update_not_newer`.
- **The tray app no longer vanishes on a refused update.** In source mode it verifies before quitting for the updater, and `update_runner.py --apply` restarts the tray app even when the update fails.
- **Release workflow publishes once, fully signed.** Build jobs upload artifacts only; a final job writes and signs `SHA256SUMS`, verifies it against `src/update_keys.py`, and creates the release with all files at once — no window where a release exists without its signature. A `guard` job requires a `vX.Y.Z` tag equal to `config.APP_VERSION`. Manual runs (`workflow_dispatch`) are dry runs that upload the signed files as an artifact. Fixed the existing `if: ${{ secrets... }}` (not allowed by GitHub) on the code-signing step.
- **Breaking:** releases without `SHA256SUMS` + `SHA256SUMS.sig` cannot be installed from the app or `Update claude quota tray.bat`. Maintainers must create a key and the `UPDATE_SIGNING_KEY` secret first — `scripts/gen_update_key.py`, `scripts/sign_release.py`, see `docs/SIGNING.md`.
- Known limitation: `pip install -r requirements.txt` (Setup, source updates) is not hash-pinned yet — see `SECURITY.md`.
- Tests: `tests/test_updater_verify.py` (tampering, wrong key, missing files, version mismatch, downgrade, pre-release, foreign repo, HTTP redirect, and the CI signing script round-trip).

### Added: CI and `--self-test` (2026-10)

- **`python src/main.py --self-test`** opens the tray icon and every window (status, history, desktop widget, accounts, thresholds, connect) against fake usage data, checks that only one Tk root was created, and exits `0` pass / `1` fail / `2` hung (30 s watchdog). It uses a temporary data folder, never calls the API and never reads a token, so it is safe to run next to the real app — and lets people with a Mac report results we can't reproduce. `CQT_SELFTEST_SCREENSHOT=<dir>` saves a screenshot.
- **`CQT_DATA_DIR`** moves every data file (settings, history, `error.log`, token fallback) to another folder. Paths are resolved when used, not at import (`settings.data_dir()`, `settings_path()`, `error_log_path()`, `history.db_path()`), replacing five hard-coded `error.log` paths.
- **CI** (`.github/workflows/ci.yml`): unit tests + `--self-test` on `windows-latest` and `macos-latest` for every push and pull request; uploads `error.log` and the screenshot as artifacts. The macOS job is report-only (`continue-on-error`) until the macOS main-thread follow-up lands.

### Stability: one Tk root, live tray menu (2026-10)

- **All windows share one Tk root on one UI thread** (`src/ui_host.py`). Status popup, history window, desktop widget and every settings dialog — including *Connect Claude account* — were each a separate `tk.Tk()` running `mainloop()` on its own thread, which crashes Tcl at random on Windows (`Tcl_AsyncDelete: async handler deleted by the wrong thread`). They are now `Toplevel`s of a single hidden root; other threads hand work over with `ui_host.call()` / `call_sync()` and never touch Tk, not even `.after()`. `call_sync()` from the UI thread runs inline, so a window button calling back into the app cannot deadlock. Tk objects are released on the UI thread when the app quits.
- **Connect Claude account dialog** no longer calls `root.after()` from its token-verification thread, and ignores results that arrive after the dialog was closed.
- **Tray menu is rebuilt every time it opens** (`src/tray_icon.py`). pystray's Windows backend caches the native menu, so the % lines in the right-click menu were stale until you clicked something, and accounts added in *Manage accounts* didn't appear. The menu is now rebuilt on right-click, on pystray's own thread; radio/check marks, the schedule label and the account list are read live. `pystray` is pinned `<0.20` because this hooks its internals (falls back to plain behaviour if they change).
- **Dialogs no longer modify the tray menu from the wrong thread.** Saving a dialog only flags a refresh; the poll thread reloads the token and repaints the icon.
- **Message boxes stay on top of their dialog** (they were owned by the hidden root and could open behind it).
- **Confirming an update or uninstall no longer freezes all windows** while it downloads / launches the script.
- **Copy status to clipboard** uses the shared root instead of creating a throwaway `tk.Tk()`.
- Tests: `tests/test_ui_host.py`, `tests/test_tray_icon.py`.
- **Known limitation (macOS):** Tk and AppKit both need the main thread on macOS, which pystray currently owns, so windows on macOS remain beta. `ui_host.run_forever()` exists for a follow-up that lets Tk own the main thread with `icon.run_detached()`.

### Review fixes (2026-10)

- **No duplicate poll threads after a tray crash.** When pystray's loop died and `main()` rebuilt the icon, `_start_poller` spawned a *second* poller each time (double API calls, double toasts). The poller now starts once and follows the live icon via `_IconProxy`.
- **Restart / language change no longer races the single-instance lock.** The relaunched process gets `--restarted` and waits up to 15 s for the old one to release the mutex instead of popping "already running" and exiting. Mutex check now uses `use_last_error` (reliable `GetLastError`) and keeps the handle.
- **OAuth header.** Requests send `anthropic-beta: oauth-2025-04-20` with Bearer tokens (as Claude Code does); `sk-ant-api*` keys use `x-api-key`.
- **Token expiry detected for Claude Code credential files.** `expiresAt` is nested under `claudeAiOauth`; new `token_reader.find_expiry_ms()` searches recursively, so proactive reload, the tooltip warning and `--probe` now work for that source.
- **Corrupt `settings.json` is backed up** (`settings.corrupt-<ts>.json`) instead of being silently replaced by defaults (losing accounts).
- **Menu no longer freezes on "Open releases"** (GitHub call moved off the tray thread); update check also catches socket timeouts.
- **.exe self-update batch** now `cd`s to its own folder before `move`.
- **New menu item: Claude.ai usage (Pro/Max)** → `claude.ai/settings/usage` (Console pages only cover API billing).
- **Every per-model limit is shown, not just Opus.** All `anthropic-ratelimit-unified-*` windows beyond 5h/weekly (Opus, Sonnet, Fable, …) become `UsageSnapshot.scoped` entries with readable labels; they appear in the tooltip, tray menu (up to 4 rows), status popup (one bar each), "max" tray metric, threshold alerts (weekly thresholds) and near-reset toasts. `opus_*` fields kept for history/back-compat.
- **"Limit reached" marker** (⛔ in tooltip) when any window reports `rejected`/`exceeded`.
- **Model fallback.** If the probe model is retired (404 / not_found), the next of `FALLBACK_MODELS` is tried and remembered — a Haiku deprecation no longer breaks the tray. Auth errors are not retried across models.
- **Expired tokens are skipped** during discovery when another source has a fresh one (e.g. stale Claude Code file vs. live Claude Desktop); the expired one is only used as a last resort.
- **Diagnostics:** `python src/api_client.py --raw` prints the raw rate-limit headers and the `/api/oauth/usage` JSON (token never printed).
- Tests: `tests/test_review_fixes.py`.

### Accuracy (api_client.py, history.py)

- **Quota parsing grounded in the real header format.** Verified against Anthropic docs and observed Claude Code/Max responses: `anthropic-ratelimit-unified-*-utilization` is a **fraction 0.0–1.0** (e.g. `0.0184` = ~2 %, `1` = 100 %), and `*-reset` is a **Unix epoch timestamp**. `_pct_from_utilization` now documents this, rounds half-up so tiny usage (`0.005` → 1 %) is not swallowed, and only falls back to "already a percentage" for values > 1. (Note: the previously suspected "`1` read as 100 %" is in fact *correct* for the real fractional format — `1` genuinely means full.)
- **Robust reset parsing.** `_seconds_until_reset` now tries numeric epoch first (the real format) with a clear 2020-01-01 epoch/relative split, then RFC 3339 / ISO 8601, then relative seconds — instead of a `now/2` heuristic.
- **Separate weekly Opus limit captured.** Unified windows are now discovered dynamically by scanning header names, so the Max-plan weekly **Opus** cap (`…-7d_oauth_opus-utilization`, or any future rename) is read and shown in the tooltip and tray menu instead of being silently dropped. New optional `opus_*` fields on `UsageSnapshot` (back-compatible).
- **No hallucinated burn-rate ETAs.** `history.burn_rate` now ignores samples before the most recent quota reset (a drop in %), reports 0 (not negative) when flat/recovering, requires a minimum growth rate before projecting an ETA, and caps projections — so "time to full" no longer appears when usage is flat or a window just rolled over.
- **`UsageSnapshot.http_status`** recorded for diagnostics; 4xx/5xx responses that still carry usable unified headers (e.g. a 429) remain `ok` and keep updating the badge.
- **Tests:** new `tests/` suite (`test_api_client.py`, `test_history_burn.py`) covering `_pct_from_utilization`, `_seconds_until_reset`, header→snapshot mapping, and burn-rate edge cases. Run with `python -m unittest discover -s tests`.

### New features (UX)

- **Near-reset notification.** When a limit you actually leaned on (≥ 40 %) is within `reset_notice_minutes` (default 10) of resetting, a toast tells you it's almost back. Fires at most once per limit per cycle and re-arms when the window rolls over. Toggle with `notify_before_reset`. Honours the alert snooze.
- **Adaptive poll interval.** New **Auto (adaptive)** option under *Settings → Check frequency* (`poll_interval_seconds = 0`): polls fast (30 s) when usage is high, climbing quickly, or a window is seconds from resetting; eases off (up to 5 min) when usage is low and flat. Saves request cost while staying responsive when it matters. The fixed 30 s / 1 m / 2 m / 5 m options are unchanged.
- **Weekly & monthly usage summary.** New tray items **Export weekly summary…** (7 days) and **Export monthly summary…** (30 days) write a Markdown report (peak 5-hour / weekly / Opus utilisation, busiest hour of day, and how many samples hit your alert threshold) and open it. The report states the actual covered range, so it's honest when history is shorter than the window. Choosing monthly transparently raises `history_retention_days` to at least 31 (only ever upward, with a toast) so future monthly reports are complete. History now also stores per-snapshot Opus % via an additive `opus_pct` column (old DBs migrate automatically; old rows stay valid). Internals: `history.period_summary(days, …)` / `export_period_summary(…)`, with `weekly_summary` / `export_weekly_summary` kept as 7-day wrappers.
- **Opus weekly bar + auth health in the status popup.** The left-click popup now shows a third progress bar for the weekly Opus limit (only when the account exposes it), plus a footer line with the active auth source and token expiry. The tray tooltip also shows a token-expiry warning when it's within 24 hours.

### Stability / bug fixes (main.py, history.py, desktop_widget.py)

- **CSV export no longer crashes.** `history.export_csv` used the `csv` module without importing it — any "Export CSV" click raised `NameError`. Added the import (covered by a regression test).
- **Poll loop is now crash-proof per iteration.** The fetch → record → burn-rate → notify path is wrapped so a network drop, DNS failure, lost token, unexpected API schema change, or SQLite hiccup logs to `error.log` and backs off (exponential, capped) instead of bubbling out and forcing a full poller restart.
- **SQLite concurrency hardened.** History opens in **WAL** mode with a `busy_timeout`, so the chart window's reads don't block the poller's writes (and vice-versa) and brief locks resolve instead of erroring.
- **Tray/Tk race hardened.** `desktop_widget.refresh()` (called from the poll thread) now snapshots the window reference under its lock before `.after()`, so a concurrent `hide()` on the UI thread can't trigger an `AttributeError`. Confirms the existing rule: the poll thread only ever marshals UI work via `after()`/badge updates, never touches the Win32 menu directly. *(Superseded by the single Tk root above.)*

### Auth coverage (auth_discovery.py, token_reader.py, accounts.py, main.py)

- **Fallback past invalid tokens.** When the active token returns 401/403, the app now records it as bad and falls through to the next discovery source (env → Desktop → Credential Manager → credential files) instead of getting stuck retrying the dead one. `read_credentials` / `get_credentials` take an `exclude_tokens` set; the bad-token list is cleared on manual re-auth, account switch, and settings changes (so a fresh login at the same source is retried). Exclusion is by exact token value, so a re-login auto-heals.
- **Clearer auth errors.** Auth failures are detected via `http_status` (401/403) as well as the message text, and when every discovered token is rejected the error explicitly says so and tells the user to sign in again.
- **Richer `--probe` diagnostics.** `python src/token_reader.py --probe` now reports, per source: the resolved sub-source, a token fingerprint (so two sources sharing one token are obvious), the detected plan, and expiry status (`expires in 319d` / `EXPIRED`). Output is ASCII-safe for the Windows console.

### Added

- **History UI** — separate Session / Weekly chart panels, **24h / 7d / 30d (monthly)** range, CSV export, hover tooltips (`chart_widget.py`). Selecting the 30-day range bumps `history_retention_days` upward (never down) so the monthly chart fills over time instead of being pruned at the 7-day default.
- **Desktop widget (Windows)** — always-on-top on-screen quota bar (`desktop_widget.py`).
- **UX** — status popup → open history; last poll in tooltip; snooze alerts 1h; copy status; open error.log; notify on new release; tray metric (5h / weekly / max); separate alert thresholds per limit; poll backoff on errors; Tk light/dark via `ui_theme.py`.
- **macOS (beta)** — `app_platform` / `platform_darwin`, unsigned `.app` CI artifact, `scripts/setup_mac.sh`.
- **Signing docs** — `docs/SIGNING.md`, optional `signtool` in release workflow when `WINDOWS_CERT_PFX` secret is set.
- **`requirements-dev.txt`** — includes PyInstaller for builds.
- **Custom app icon** — `assets/app.ico` for Startup shortcut and `.exe` (`scripts/generate_app_icon.py`); no more default Python icon after Setup/build.

- **GitHub auto-update** — tray menu **Install / update**: check releases, set `owner/repo`, download latest source zip or `.exe` (`src/updater.py`, `src/update_runner.py`).
- **Maintenance menu** — run Setup / Update / Uninstall `.bat`, open install folder.
- **`src/app_paths.py`** — detect source install vs frozen `.exe`.
- **`src/auth_discovery.py`** — unified OAuth discovery with platform-aware provider order.
- **`src/desktop_auth.py`** — read and decrypt Claude Desktop `oauth:tokenCache` (Chromium v10; DPAPI on Windows, Keychain on macOS).
- **Multi-source auth** (first match wins on Windows):
  1. `CLAUDE_CODE_OAUTH_TOKEN` / `ANTHROPIC_AUTH_TOKEN`
  2. Claude Desktop (`%LOCALAPPDATA%\Packages\Claude_*\...\Claude\config.json`)
  3. Windows Credential Manager (`Claude Code-credentials`, etc.)
  4. Claude Code credential files (`~/.claude/.credentials.json`, `CLAUDE_CONFIG_DIR`, …)
- **macOS auth paths** (for discovery helpers; tray app remains Windows-focused): Keychain + Desktop decryption.
- **Diagnostics:** `python src/token_reader.py --probe` — test each provider independently.
- **Plan detection** from credentials (subscription / rate-limit tier) for tooltip and menu.
- **Single-instance guard** on Windows (mutex + message if already running).
- **Tray auto-restart** — if `pystray` exits without Quit, wait 3s and restart the icon loop.
- **Setup toast** when no OAuth token is found (one-time, en + th via `i18n.py`).
- **`pycryptodome`** in `requirements.txt` (Windows/macOS) for Desktop token decryption.
- **PyInstaller** hidden imports for `auth_discovery`, `desktop_auth`, and `Crypto` (`build.bat` / `build.sh`).

### Changed

- **`src/token_reader.py`** — helpers + delegates `read_credentials()` to `auth_discovery`; expanded file search paths.
- **`src/main.py`** — poll thread updates icon/tooltip only; menu rebuild runs on the UI thread (`update_menu=True`) to avoid Win32 tray crashes; `icon.run(setup=_start_poller)`.
- **`src/i18n.py`** — setup toast strings; account dialog text documents all discovery sources (en + th).
- **`src/settings.py`** — migration sets `active_account_id` to the first account when missing.
- **`README.md`** — auth flow, project layout, system requirements (Desktop or Code).
- **`Setup claude quota tray.bat`** — install message mentions `pycryptodome`.
- **`Uninstall claude quota tray.bat`** — stop running `pythonw`/`python` for this project before deleting `.venv`.

### Unchanged from upstream (same behaviour)

- API polling via minimal Haiku request + `anthropic-ratelimit-unified-*` headers.
- Tray badge, popup, history chart, burn rate, thresholds, schedule, multi-account UI.
- Windows toast notifications, winsound alerts, theme detection, SQLite history.

### Notes

- Files that show as modified in git but match upstream content are line-ending only (CRLF/LF), not functional changes.

---

## [0.2.0] — upstream (kpcrmv4)

Initial release: Windows system tray quota monitor for Claude Code OAuth credentials.

See [upstream releases](https://github.com/kpcrmv4/claude-quota-tray/releases).
