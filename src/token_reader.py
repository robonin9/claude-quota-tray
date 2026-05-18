"""
Reads the Claude Code OAuth token from the local credentials file.

Cross-platform: looks in ~/.claude/.credentials.json on Linux/macOS,
and %USERPROFILE%\\.claude\\.credentials.json on Windows.

The file format is JSON. The token may be stored under a few possible keys
depending on Claude Code version; we try them in order.
"""

import json
import os
import sys
from pathlib import Path
from typing import Optional


# Candidate locations for the credentials file, in order of preference.
def _candidate_paths():
    home = Path.home()
    candidates = [
        home / ".claude" / ".credentials.json",
        home / ".config" / "claude" / "credentials.json",
    ]
    # On Windows, also check AppData/Roaming just in case
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            candidates.append(Path(appdata) / "Claude" / "credentials.json")
    return candidates


# Candidate keys inside the JSON file where the token might live.
_TOKEN_KEYS = [
    "accessToken",
    "access_token",
    "oauth_token",
    "token",
    "bearerToken",
]


class TokenError(Exception):
    """Raised when the OAuth token cannot be read."""
    pass


def find_credentials_path() -> Optional[Path]:
    """Return the first existing credentials file path, or None."""
    for p in _candidate_paths():
        if p.exists():
            return p
    return None


def _extract_token(data) -> Optional[str]:
    """Recursively search a dict/list for a likely token value."""
    if isinstance(data, dict):
        # Direct hit on a known key
        for key in _TOKEN_KEYS:
            if key in data and isinstance(data[key], str) and data[key]:
                return data[key]
        # Recurse
        for value in data.values():
            result = _extract_token(value)
            if result:
                return result
    elif isinstance(data, list):
        for item in data:
            result = _extract_token(item)
            if result:
                return result
    return None


_PLAN_KEYS = ("subscriptionType", "subscription_type", "plan", "tier")
_RATE_TIER_KEYS = ("rateLimitTier", "rate_limit_tier")


def _find_first(data, keys: tuple[str, ...]):
    """Recursively find the first matching key in a dict/list tree."""
    if isinstance(data, dict):
        for k in keys:
            if k in data and isinstance(data[k], str) and data[k]:
                return data[k]
        for v in data.values():
            r = _find_first(v, keys)
            if r:
                return r
    elif isinstance(data, list):
        for item in data:
            r = _find_first(item, keys)
            if r:
                return r
    return None


def _format_plan(subscription: Optional[str], tier: Optional[str]) -> Optional[str]:
    """Make a short display string like 'Max 5x' or 'Pro'."""
    if tier:
        # Examples: default_claude_max_5x, default_claude_max_20x, default_claude_pro
        low = tier.lower()
        multiplier = ""
        for token in low.split("_"):
            if token.endswith("x") and token[:-1].isdigit():
                multiplier = " " + token
                break
        if "max" in low:
            return "Max" + multiplier
        if "pro" in low:
            return "Pro"
        if "team" in low:
            return "Team" + multiplier
    if subscription:
        return subscription[:1].upper() + subscription[1:]
    return None


def read_token() -> str:
    """Read the OAuth token from the credentials file."""
    return read_credentials()["token"]


def read_credentials() -> dict:
    """
    Read the credentials file and return a dict:
        {"token": str, "plan": Optional[str], "raw": dict}

    'plan' is a short display label like 'Max 5x' / 'Pro' / None.

    Raises TokenError if the file is missing, unparseable, or has no token.
    """
    path = find_credentials_path()
    if path is None:
        searched = "\n  - ".join(str(p) for p in _candidate_paths())
        raise TokenError(
            "Claude Code credentials file not found.\n"
            "Searched:\n  - " + searched + "\n\n"
            "Make sure Claude Code is installed and you have signed in at least once."
        )

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise TokenError(f"Credentials file at {path} is not valid JSON: {e}")
    except OSError as e:
        raise TokenError(f"Could not read credentials file at {path}: {e}")

    token = _extract_token(data)
    if not token:
        raise TokenError(
            f"Credentials file at {path} was read, but no recognisable "
            f"OAuth token field was found. Expected one of: {_TOKEN_KEYS}"
        )

    plan = _format_plan(
        _find_first(data, _PLAN_KEYS),
        _find_first(data, _RATE_TIER_KEYS),
    )
    return {"token": token, "plan": plan, "raw": data}


if __name__ == "__main__":
    # Manual test: print the path and a redacted preview of the token.
    try:
        path = find_credentials_path()
        print(f"Credentials file: {path}")
        token = read_token()
        print(f"Token: {token[:8]}...{token[-4:]} (length {len(token)})")
    except TokenError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
