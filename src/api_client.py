"""
Makes a minimal Anthropic Messages API call and extracts usage information
from the `anthropic-ratelimit-unified-*` response headers.

The request body is intentionally as small as possible — one token of the
cheapest available Haiku model — because we only care about the response
headers. The body still incurs a tiny cost, but in practice this is
fractions of a fraction of a cent per poll.

Header format (subscription / OAuth "unified" rate limits, as used by Claude
Code and Claude Pro/Max — distinct from the developer-API tier headers):

  anthropic-ratelimit-unified-5h-utilization     fraction 0.0–1.0  (e.g. "0.0184")
  anthropic-ratelimit-unified-5h-reset           Unix epoch seconds (e.g. "1764554400")
  anthropic-ratelimit-unified-5h-status          status string ("allowed", …)
  anthropic-ratelimit-unified-7d-utilization     weekly fraction 0.0–1.0
  anthropic-ratelimit-unified-7d-reset / -status
  anthropic-ratelimit-unified-7d_oauth_opus-…    separate Opus weekly limit (Max)

The "-utilization" headers report a FRACTION in [0, 1], NOT a percentage
0–100 (confirmed against observed Claude Code / Max responses). We therefore
treat any value in [0, 1] as a fraction; values above 1 are interpreted
defensively as an already-expressed percentage so the app degrades sanely if
the format ever changes. The exact window segment for the Opus weekly limit
is discovered dynamically rather than hard-coded, so a server-side rename
does not silently drop it.
"""

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import httpx


API_URL = "https://api.anthropic.com/v1/messages"
# Read-only usage endpoint used by Claude Code's /usage (diagnostics only).
OAUTH_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
ANTHROPIC_VERSION = "2023-06-01"

# Cheapest, smallest model. The exact string can be updated as new Haikus ship.
DEFAULT_MODEL = "claude-haiku-4-5"
# Tried in order when the configured model is rejected as unknown/retired, so
# a model deprecation doesn't silently break the tray.
FALLBACK_MODELS = ("claude-haiku-4-5", "claude-haiku-5-5", "claude-sonnet-4-5")
_working_model: Optional[str] = None

# Any Unix timestamp from this app's lifetime is far larger than any plausible
# "relative seconds" reset (a weekly window is ~604800 s). 2020-01-01 is a safe
# splitter between "absolute epoch" and "relative duration".
_EPOCH_THRESHOLD = 1_577_836_800  # 2020-01-01T00:00:00Z

_UNIFIED_PREFIX = "anthropic-ratelimit-unified-"
_UNIFIED_UTIL_RE = re.compile(
    rf"^{re.escape(_UNIFIED_PREFIX)}(?P<window>.+)-utilization$"
)


@dataclass
class ScopedLimit:
    """An extra limit beyond the 5-hour / weekly pair — usually a per-model
    weekly cap (Opus, Sonnet, Fable, …). Discovered from header names."""
    key: str                        # window id, e.g. "7d_oauth_opus"
    label: str                      # display name, e.g. "Opus"
    pct: Optional[int]
    reset_seconds: Optional[int]
    status: Optional[str]


@dataclass
class UsageSnapshot:
    """One reading of Claude usage limits."""
    # 5-hour rolling limit
    session_pct: Optional[int]              # 0-100
    session_reset_seconds: Optional[int]    # seconds until reset
    session_status: Optional[str]           # 'allowed', 'allowed_warning', etc.

    # 7-day weekly limit
    weekly_pct: Optional[int]
    weekly_reset_seconds: Optional[int]
    weekly_status: Optional[str]

    # Meta
    ok: bool
    error: Optional[str] = None
    fetched_at: float = 0.0
    http_status: Optional[int] = None

    # Separate weekly Opus limit (Max plans). None when the account/plan does
    # not expose it. Kept at the end of the dataclass so existing positional /
    # keyword construction stays backward compatible.
    opus_pct: Optional[int] = None
    opus_reset_seconds: Optional[int] = None
    opus_status: Optional[str] = None

    # Every limit other than 5h / weekly (Opus included), in display order.
    scoped: list = field(default_factory=list)

    @property
    def has_data(self) -> bool:
        return (
            self.session_pct is not None
            or self.weekly_pct is not None
            or self.opus_pct is not None
            or any(s.pct is not None for s in self.scoped)
        )

    @property
    def limited(self) -> bool:
        """True when any limit reports it is blocking requests right now."""
        statuses = [self.session_status, self.weekly_status, self.opus_status]
        statuses += [s.status for s in self.scoped]
        return any(
            (st or "").lower() in ("rejected", "exceeded", "blocked")
            for st in statuses
        )


class APIError(Exception):
    """Raised when the API call itself fails (network, auth, etc.)."""
    pass


def _pct_from_utilization(raw: Optional[str]) -> Optional[int]:
    """Convert a unified ``-utilization`` header to an integer percent 0-100.

    The header is a FRACTION in [0, 1] (e.g. ``'0.0184'`` → 2, ``'1'`` → 100).
    Values above 1 are treated defensively as an already-expressed percentage,
    so the reading stays sane if Anthropic ever switches the encoding.

    Returns None on parse failure.
    """
    if raw is None:
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if val != val:  # NaN guard
        return None
    if val < 0:
        return 0
    if val <= 1.0:
        # Fraction → percent. Round half-up so 0.005 → 1, not 0.
        return min(100, int(val * 100 + 0.5))
    # Defensive: looks like it is already a percentage.
    return min(100, int(val + 0.5))


def _seconds_until_reset(raw: Optional[str]) -> Optional[int]:
    """
    Parse a reset header value into seconds-from-now (never negative).

    Accepts, in priority order:
      - Unix epoch seconds as a number (the real unified-header format)
      - ISO 8601 / RFC 3339 timestamps (with or without 'Z')
      - Plain integer seconds-from-now (small values)
    """
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None

    # Numeric first — the unified headers send an absolute Unix timestamp.
    try:
        val = float(raw)
    except ValueError:
        val = None
    if val is not None:
        if val != val:  # NaN
            return None
        now = time.time()
        if val >= _EPOCH_THRESHOLD:
            return max(0, int(val - now))
        # Small number: treat as relative seconds-from-now.
        return max(0, int(val))

    # Fall back to ISO 8601 / RFC 3339.
    try:
        ts = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = dt - datetime.now(timezone.utc)
        return max(0, int(delta.total_seconds()))
    except (ValueError, TypeError):
        return None


def _parse_unified_windows(headers) -> dict:
    """Discover every unified rate-limit window present in the response.

    Returns ``{window: {"pct", "reset", "status"}}`` keyed by the window
    segment of the header name (e.g. ``"5h"``, ``"7d"``, ``"7d_oauth_opus"``).
    Discovering windows dynamically means a new or renamed limit (notably the
    Opus weekly limit) is captured automatically instead of being dropped.
    """
    windows: dict = {}
    for key in headers.keys():
        m = _UNIFIED_UTIL_RE.match(key.lower())
        if not m:
            continue
        window = m.group("window")
        base = f"{_UNIFIED_PREFIX}{window}"
        windows[window] = {
            "pct": _pct_from_utilization(headers.get(key)),
            "reset": _seconds_until_reset(headers.get(f"{base}-reset")),
            "status": headers.get(f"{base}-status"),
        }
    return windows


def _weekly_window_name(windows: dict) -> Optional[str]:
    """Name of the general (all-models) weekly window."""
    if "7d" in windows:
        return "7d"
    for name in ("weekly", "week", "7d_oauth", "7d_all"):
        if name in windows:
            return name
    return None


def _select_weekly(windows: dict) -> tuple[dict, Optional[dict]]:
    """Split the discovered windows into (weekly, opus-weekly).

    The plain 7-day window is the general weekly limit; any window whose name
    mentions "opus" is the separate Opus weekly cap that Max plans expose.
    """
    name = _weekly_window_name(windows)
    weekly = windows.get(name) if name else None
    opus = None
    for wname, data in windows.items():
        if "opus" in wname:
            opus = data
            break
    return (weekly or {}), opus


_PERIOD_RE = re.compile(r"^\d+[hdwm]$")
_LABEL_NOISE = {"oauth", "limit", "unified"}


def window_label(window: str) -> str:
    """Human label for a window id: "7d_oauth_opus" → "Opus",
    "7d_sonnet" → "Sonnet", "5h_opus" → "Opus (5h)"."""
    parts = [p for p in re.split(r"[_\-]", window) if p]
    period = parts[0] if parts and _PERIOD_RE.match(parts[0]) else None
    rest = [p for p in (parts[1:] if period else parts)
            if p.lower() not in _LABEL_NOISE]
    name = " ".join(p.capitalize() for p in rest) or window
    if period and period != "7d":
        name += f" ({period})"
    return name


def _scoped_limits(windows: dict) -> list:
    """All windows except 5h and the general weekly, Opus first."""
    weekly_name = _weekly_window_name(windows)
    out = []
    for wname, data in windows.items():
        if wname in ("5h", weekly_name):
            continue
        out.append(ScopedLimit(
            key=wname, label=window_label(wname), pct=data.get("pct"),
            reset_seconds=data.get("reset"), status=data.get("status"),
        ))
    out.sort(key=lambda s: (0 if "opus" in s.key else 1, s.label))
    return out


# Beta flag required for OAuth (Claude Pro/Max subscription) bearer tokens on
# the Messages API — the same flag Claude Code sends. Without it the API may
# reject subscription tokens ("OAuth authentication is currently not supported").
OAUTH_BETA = "oauth-2025-04-20"


def build_auth_headers(token: str) -> dict:
    """Request headers for an OAuth token (Bearer) or a Console API key."""
    headers = {
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    try:
        import config
        headers["user-agent"] = f"claude-quota-tray/{config.APP_VERSION}"
    except Exception:
        pass
    if token.startswith("sk-ant-api"):
        # Console API keys authenticate via x-api-key, not Bearer.
        headers["x-api-key"] = token
    else:
        headers["Authorization"] = f"Bearer {token}"
        headers["anthropic-beta"] = OAUTH_BETA
    return headers


def fetch_usage(token: str, model: str = DEFAULT_MODEL,
                timeout: float = 10.0) -> UsageSnapshot:
    """
    Make one minimal API call and return a UsageSnapshot.

    Network / auth errors are returned inside the snapshot (ok=False) rather
    than raised, so the tray loop can keep running and display the error in
    the tooltip.
    """
    global _working_model
    headers = build_auth_headers(token)

    # Configured model first, then a remembered working one, then fallbacks.
    models: list[str] = []
    for m in (model, _working_model, *FALLBACK_MODELS):
        if m and m not in models:
            models.append(m)

    snapshot: Optional[UsageSnapshot] = None
    try:
        with httpx.Client(timeout=timeout) as client:
            for m in models:
                payload = {
                    "model": m,
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "."}],
                }
                resp = client.post(API_URL, headers=headers, json=payload)
                snapshot = snapshot_from_headers(resp.headers, resp)
                if snapshot.has_data or not _is_model_error(resp):
                    if resp.is_success or snapshot.has_data:
                        _working_model = m
                    return snapshot
    except httpx.RequestError as e:
        return UsageSnapshot(
            session_pct=None, session_reset_seconds=None, session_status=None,
            weekly_pct=None, weekly_reset_seconds=None, weekly_status=None,
            ok=False, error=f"Network error: {e}", fetched_at=time.time(),
        )
    return snapshot  # every model rejected — last error is in the snapshot


def _is_model_error(resp) -> bool:
    """True when the request failed because the model id is unknown/retired."""
    if resp.status_code not in (400, 404):
        return False
    try:
        err = (resp.json() or {}).get("error", {}) or {}
    except Exception:
        return False
    msg = str(err.get("message", "")).lower()
    return err.get("type") == "not_found_error" or "model" in msg


def snapshot_from_headers(rh, resp=None) -> UsageSnapshot:
    """Build a UsageSnapshot from response headers (+ optional httpx response).

    Split out from ``fetch_usage`` so the parsing is unit-testable without a
    live HTTP call. Even on 4xx/5xx the unified headers may still be present
    (e.g. a 429 still reports utilization), so we always try to read them.
    """
    windows = _parse_unified_windows(rh)
    session = windows.get("5h", {})
    weekly, opus = _select_weekly(windows)

    is_success = bool(resp.is_success) if resp is not None else True
    status_code = resp.status_code if resp is not None else None

    snapshot = UsageSnapshot(
        session_pct=session.get("pct"),
        session_reset_seconds=session.get("reset"),
        session_status=session.get("status"),

        weekly_pct=weekly.get("pct"),
        weekly_reset_seconds=weekly.get("reset"),
        weekly_status=weekly.get("status"),

        ok=is_success or snapshot_has_headers(rh),
        fetched_at=time.time(),
        http_status=status_code,

        opus_pct=(opus or {}).get("pct"),
        opus_reset_seconds=(opus or {}).get("reset"),
        opus_status=(opus or {}).get("status"),
        scoped=_scoped_limits(windows),
    )

    if resp is not None and not is_success and not snapshot.has_data:
        # No usable headers — surface a meaningful error.
        try:
            body = resp.json()
            err_msg = body.get("error", {}).get("message", resp.text[:200])
        except Exception:
            err_msg = resp.text[:200] or f"HTTP {resp.status_code}"
        snapshot.ok = False
        snapshot.error = f"API error ({resp.status_code}): {err_msg}"

    return snapshot


def snapshot_has_headers(headers) -> bool:
    """True if at least one unified ratelimit header is present."""
    return any(k.lower().startswith(_UNIFIED_PREFIX) for k in headers.keys())


def format_reset(seconds: Optional[int]) -> str:
    """Render seconds-until-reset as a compact human string."""
    if seconds is None:
        return "—"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        m = seconds // 60
        return f"{m}m"
    if seconds < 86400:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h}h {m}m" if m else f"{h}h"
    d = seconds // 86400
    h = (seconds % 86400) // 3600
    return f"{d}d {h}h" if h else f"{d}d"


def fetch_raw_diagnostics(token: str, model: str = DEFAULT_MODEL,
                          timeout: float = 10.0) -> dict:
    """Raw rate-limit headers + the /api/oauth/usage JSON (never the token).

    For troubleshooting a new header format or a new per-model limit:
    ``python src/api_client.py --raw``.
    """
    out: dict = {}
    headers = build_auth_headers(token)
    with httpx.Client(timeout=timeout) as client:
        try:
            r = client.post(API_URL, headers=headers, json={
                "model": model, "max_tokens": 1,
                "messages": [{"role": "user", "content": "."}],
            })
            out["messages_status"] = r.status_code
            out["messages_headers"] = {
                k: v for k, v in r.headers.items()
                if k.lower().startswith("anthropic-ratelimit")
            }
            if not r.is_success:
                out["messages_body"] = r.text[:500]
        except httpx.RequestError as e:
            out["messages_error"] = str(e)
        try:
            r = client.get(OAUTH_USAGE_URL, headers=headers)
            out["oauth_usage_status"] = r.status_code
            try:
                out["oauth_usage_json"] = r.json()
            except Exception:
                out["oauth_usage_body"] = r.text[:500]
        except httpx.RequestError as e:
            out["oauth_usage_error"] = str(e)
    return out


if __name__ == "__main__":
    # Manual test:  python src/api_client.py [--raw]
    import json as _json
    import sys
    from token_reader import read_token, TokenError

    try:
        token = read_token()
    except TokenError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if "--raw" in sys.argv:
        print(_json.dumps(fetch_raw_diagnostics(token), indent=2, ensure_ascii=False))
        sys.exit(0)

    snap = fetch_usage(token)
    print(f"ok: {snap.ok}")
    if snap.error:
        print(f"error: {snap.error}")
    print(f"session: {snap.session_pct}% (reset in {format_reset(snap.session_reset_seconds)})")
    print(f"weekly:  {snap.weekly_pct}% (reset in {format_reset(snap.weekly_reset_seconds)})")
    for sc in snap.scoped:
        print(f"{sc.label}: {sc.pct}% (reset in {format_reset(sc.reset_seconds)}) [{sc.key}]")
