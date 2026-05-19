# Changelog

All notable changes to this fork are documented here.

**Upstream baseline:** [kpcrmv4/claude-quota-tray](https://github.com/kpcrmv4/claude-quota-tray) v0.2.0  
**Concept inspiration:** [Clawdmeter](https://github.com/HermannBjorgvin/Clawdmeter) (ESP32 hardware meter)

---

## [Unreleased] — fork (robonin9)

Changes compared to upstream v0.2.0. Tray UI, polling, history, and notifications are otherwise the same unless noted.

### Added

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
