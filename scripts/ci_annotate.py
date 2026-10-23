"""
Print the tail of a log file as a GitHub Actions annotation.

    python scripts/ci_annotate.py LOG --level error|warning|notice --title TITLE [--tail 40]

Job logs and artifacts need a GitHub login to read, but annotations are public
(check-runs API), so CI uses this to surface test output from every runner.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

MAX_CHARS = 3500  # annotations are truncated by GitHub; keep the useful tail


def escape(text: str) -> str:
    """Workflow-command escaping for the message part."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def escape_property(text: str) -> str:
    return escape(text).replace(":", "%3A").replace(",", "%2C")


def build(log_text: str, level: str, title: str, tail: int) -> str:
    lines = log_text.splitlines()[-tail:]
    body = "\n".join(lines).strip() or "(empty log)"
    if len(body) > MAX_CHARS:
        body = "…" + body[-MAX_CHARS:]
    return f"::{level} title={escape_property(title)}::{escape(body)}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("log", type=Path)
    p.add_argument("--level", choices=("error", "warning", "notice"), default="error")
    p.add_argument("--title", required=True)
    p.add_argument("--tail", type=int, default=40)
    args = p.parse_args(argv)
    try:
        text = args.log.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        text = f"could not read {args.log}: {e}"
    print(build(text, args.level, args.title, args.tail))
    return 0


if __name__ == "__main__":
    sys.exit(main())
