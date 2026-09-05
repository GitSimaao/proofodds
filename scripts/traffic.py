#!/usr/bin/env python3
"""Summarise the nginx access log: where readers came from, and what they wanted.

The site's Content-Security-Policy allows no third-party script, which rules
out every hosted analytics product — deliberately, since a page arguing for
verifiable claims should not ship a tracker. The access log answers the same
questions without one, and without sending a single reader to anybody else.

    python scripts/traffic.py                     # today
    python scripts/traffic.py --since 2026-09-08  # launch day
    python scripts/traffic.py --all --top 30

A launch is a one-off source of evidence about which of the three things this
site offers people actually want: the weekly scorecard, the offer to be
measured, or the numbers themselves. Each has its own URL so the answer is
readable here rather than guessed at.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

LOG_DIR = Path("/var/log/nginx")
LOG_STEM = "proofodds.access.log"

LINE = re.compile(
    r'^(?P<ip>\S+) \S+ \S+ \[(?P<when>[^\]]+)\] '
    r'"(?P<method>[A-Z]+) (?P<path>[^ "]*) [^"]*" '
    r'(?P<status>\d{3}) \S+ "(?P<ref>[^"]*)" "(?P<ua>[^"]*)"')

# Matched against a lowercased user agent. Crawlers and preview fetchers are
# not readers, and on launch day they arrive in bulk: every social platform
# unfurls the link once per share.
BOTS = ("bot", "crawler", "spider", "slurp", "curl/", "wget", "python-requests",
        "headless", "monitor", "scan", "preview", "fetcher", "facebookexternalhit",
        "gptbot", "chatgpt-user", "claudebot", "perplexity", "bytespider")

# The three things the site asks for, each on its own URL on purpose.
ASKS = {
    "/referee/": "seal your own record",
    "/data/":    "use the numbers",
    "/subscribed/": "newsletter — form submitted",
    "/confirmed/":  "newsletter — double opt-in confirmed",
}


def logs(use_all: bool) -> list[Path]:
    if not use_all:
        return [LOG_DIR / LOG_STEM]
    found = sorted(LOG_DIR.glob(f"{LOG_STEM}*"))
    # Newest first reads more naturally when something goes wrong mid-parse.
    return sorted(found, key=lambda p: p.name)


def lines(paths: list[Path]):
    for path in paths:
        if not path.is_file():
            continue
        opener = gzip.open if path.suffix == ".gz" else open
        try:
            with opener(path, "rt", errors="replace") as handle:
                yield from handle
        except OSError as exc:
            print(f"  (skipped {path.name}: {exc})")


def source(referrer: str) -> str:
    """Collapse a referrer to something worth counting."""
    if not referrer or referrer == "-":
        return "direct / unknown"
    host = (urlsplit(referrer).hostname or referrer).lower()
    host = host.removeprefix("www.")
    if host.endswith("proofodds.com"):
        return "(internal)"
    known = {
        "news.ycombinator.com": "Hacker News",
        "t.co": "X / Twitter",
        "x.com": "X / Twitter",
        "twitter.com": "X / Twitter",
        "old.reddit.com": "Reddit",
        "reddit.com": "Reddit",
        "out.reddit.com": "Reddit",
        "lnkd.in": "LinkedIn",
        "linkedin.com": "LinkedIn",
        "news.google.com": "Google News",
        "google.com": "Google",
        "bing.com": "Bing",
        "duckduckgo.com": "DuckDuckGo",
        "github.com": "GitHub",
        "lobste.rs": "Lobsters",
    }
    return known.get(host, host)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", help="YYYY-MM-DD (default: today)")
    parser.add_argument("--all", action="store_true",
                        help="include rotated logs")
    parser.add_argument("--top", type=int, default=15)
    parser.add_argument("--bots", action="store_true",
                        help="count crawlers too")
    args = parser.parse_args()

    start = (dt.date.fromisoformat(args.since) if args.since
             else dt.date.today())

    hits = 0
    skipped_bots = 0
    people: set[str] = set()
    sources: Counter[str] = Counter()
    pages: Counter[str] = Counter()
    hourly: Counter[str] = Counter()
    reach: dict[str, set[str]] = {path: set() for path in ASKS}

    for line in lines(logs(args.all)):
        match = LINE.match(line)
        if not match:
            continue
        row = match.groupdict()
        if row["method"] != "GET" or row["status"] not in ("200", "304"):
            continue
        try:
            when = dt.datetime.strptime(row["when"][:20], "%d/%b/%Y:%H:%M:%S")
        except ValueError:
            continue
        if when.date() < start:
            continue

        agent = row["ua"].lower()
        if not args.bots and any(sign in agent for sign in BOTS):
            skipped_bots += 1
            continue

        path = row["path"].split("?")[0]
        # Assets are requests, not readings. Only pages say what someone wanted.
        if not path.endswith("/") and not path.endswith(".json"):
            continue

        hits += 1
        people.add(row["ip"])
        sources[source(row["ref"])] += 1
        pages[path] += 1
        hourly[when.strftime("%d %b %H:00")] += 1
        if path in reach:
            reach[path].add(row["ip"])

    label = "since " + start.isoformat()
    print(f"\nProofOdds traffic, {label}")
    print(f"{hits} page views from {len(people)} distinct addresses "
          f"({skipped_bots} bot requests excluded)\n")

    def table(title: str, counter: Counter, limit: int):
        print(title)
        if not counter:
            print("  (nothing yet)\n")
            return
        width = max(len(key) for key, _ in counter.most_common(limit))
        for key, n in counter.most_common(limit):
            share = 100 * n / max(hits, 1)
            print(f"  {key:<{width}}  {n:>6}  {share:5.1f}%")
        print()

    table("Where they came from", sources, args.top)
    table("What they read", pages, args.top)

    print("Did anyone want what the site offers")
    for path, name in ASKS.items():
        n = len(reach[path])
        share = 100 * n / max(len(people), 1)
        print(f"  {name:<38} {n:>5} people  {share:5.1f}% of all")
    print()

    if hourly:
        table("By hour", hourly, 24)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
