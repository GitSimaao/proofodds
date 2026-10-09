#!/usr/bin/env python3
"""
Keep daily visitor counts after the access log has rotated away.

nginx keeps 14 days of logs. The launch on 8 September 2026 had already
rotated out when somebody first asked how it went, and that answer is gone.
This writes one row per day to data/traffic_daily.csv: counts only, no IP
addresses and no user agents, so it can be kept indefinitely without keeping
anything about a reader.

    sudo python scripts/traffic_snapshot.py     # needs to read /var/log/nginx

Run daily by deploy/proofodds-traffic.timer. Every day still in the logs is
recomputed, so a day's row settles once the day is over.

A "person" is an upper bound: a browser user agent matching no crawler
signature, from an address that also fetched a stylesheet, image or font and
was not mostly producing errors. Headless browsers that load assets pass.
"""

from __future__ import annotations

import collections
import csv
import datetime as dt
import glob
import gzip
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "traffic_daily.csv"
LOGS = "/var/log/nginx/proofodds.access.log*"

LINE = re.compile(
    r'^(?P<ip>\S+) \S+ \S+ \[(?P<when>[^\]]+)\] '
    r'"(?P<method>[A-Z]+) (?P<path>[^ "]*) [^"]*" '
    r'(?P<status>\d{3}) \S+ "(?P<ref>[^"]*)" "(?P<ua>[^"]*)"')
BOTS = ("bot", "crawl", "spider", "slurp", "curl", "wget", "python", "headless",
        "monitor", "scan", "preview", "fetch", "facebookexternalhit", "gpt",
        "claude", "perplexity", "bytespider", "go-http", "java/", "okhttp",
        "axios", "node-", "libwww", "httpx", "aiohttp", "feed", "rss",
        "lighthouse", "pingdom", "uptime", "censys", "zgrab", "masscan", "nmap",
        "expanse", "palo", "dataprovider", "semrush", "ahrefs", "mj12",
        "petal", "yandex", "baidu", "bing", "google-", "googleother", "adsbot",
        "duckduck", "archive", "http://", "https://", "+http", "scrapy", "ruby",
        "php", "perl", "postman", "insomnia", "internet-measurement", "l9",
        "nuclei", "httrack")
ASSET = re.compile(r"\.(css|js|png|jpg|svg|ico|woff2?|webp|json|csv|txt|xml|ots|map|webmanifest)$")
COLUMNS = ["date", "people", "page_views", "home", "match_cards", "scorecard",
           "ledger", "method", "referee", "seal", "guest_records", "seal_posts",
           "from_search"]
SEARCH = ("google.", "bing.", "duckduckgo.", "yandex.", "ecosia.", "brave.")


def is_bot(ua: str) -> bool:
    low = ua.lower()
    return not low or low == "-" or "mozilla" not in low or any(b in low for b in BOTS)


def main() -> int:
    rows = []
    for name in glob.glob(LOGS):
        opener = gzip.open if name.endswith(".gz") else open
        with opener(name, "rt", errors="replace") as handle:
            for line in handle:
                hit = LINE.match(line)
                if not hit:
                    continue
                row = hit.groupdict()
                row["day"] = dt.datetime.strptime(
                    row["when"], "%d/%b/%Y:%H:%M:%S %z").date()
                row["p"] = urlsplit(row["path"]).path
                rows.append(row)
    if not rows:
        print("no log lines readable — run as root", file=sys.stderr)
        return 1

    seen = collections.defaultdict(lambda: {"pages": 0, "assets": 0, "bad": 0,
                                            "human_ua": False})
    for row in rows:
        s = seen[row["ip"]]
        s["human_ua"] = s["human_ua"] or not is_bot(row["ua"])
        s["bad"] += row["status"] in ("400", "403", "404", "405", "444")
        s["assets" if ASSET.search(row["p"]) else "pages"] += 1
    people = {ip for ip, s in seen.items()
              if s["human_ua"] and s["assets"] and s["pages"] <= 300
              and not (s["bad"] > 5 and s["bad"] > s["pages"])}

    days: dict = collections.defaultdict(
        lambda: {c: set() if c in ("people", "from_search") else 0
                 for c in COLUMNS[1:]})
    for row in rows:
        if row["ip"] not in people or is_bot(row["ua"]):
            continue
        day, path = days[row["day"]], row["p"]
        if row["method"] == "POST" and path == "/api/seal":
            day["seal_posts"] += 1
        if (row["method"] != "GET" or row["status"] not in ("200", "304")
                or ASSET.search(path)):
            continue
        day["people"].add(row["ip"])
        day["page_views"] += 1
        host = (urlsplit(row["ref"]).hostname or "").lower()
        if any(s in host for s in SEARCH):
            day["from_search"].add(row["ip"])
        for column, prefix in (("match_cards", "/match"), ("scorecard", "/scorecard"),
                               ("ledger", "/ledger"), ("method", "/method"),
                               ("referee", "/referee"), ("seal", "/seal"),
                               ("guest_records", "/guests")):
            if path.startswith(prefix):
                day[column] += 1
        if path == "/":
            day["home"] += 1

    kept = {}
    if OUT.exists():
        with OUT.open(newline="", encoding="utf-8") as handle:
            kept = {r["date"]: r for r in csv.DictReader(handle)}
    for date, day in days.items():
        kept[date.isoformat()] = {
            "date": date.isoformat(),
            **{c: (len(day[c]) if isinstance(day[c], set) else day[c])
               for c in COLUMNS[1:]}}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for date in sorted(kept):
            writer.writerow(kept[date])
    print(f"{len(days)} day(s) written to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
