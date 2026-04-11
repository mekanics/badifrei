#!/usr/bin/env python3
"""Fetch badifrei.ch HTML and confirm the Umami tracker snippet is present.

Usage:
  python3 scripts/verify_umami_snippet.py
  python3 scripts/verify_umami_snippet.py --base-url https://staging.example.com
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Umami snippet in public HTML.")
    parser.add_argument(
        "--base-url",
        default="https://badifrei.ch",
        help="Origin to fetch (default: https://badifrei.ch)",
    )
    args = parser.parse_args()
    url = args.base_url.rstrip("/") + "/"
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "badifrei-verify-umami/1.0"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        print(f"ERROR: could not fetch {url}: {e}", file=sys.stderr)
        return 2

    has_id = "data-website-id" in body
    has_umami = "umami" in body.lower()
    if has_id and has_umami:
        print(f"OK: {url} contains Umami loader (data-website-id + umami reference).")
        return 0
    if has_id or has_umami:
        print(
            f"WARN: {url} partial match (data-website-id={has_id}, umami ref={has_umami}).",
            file=sys.stderr,
        )
        return 1
    print(
        f"FAIL: {url} has no Umami snippet (tracking disabled or different HTML).",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
