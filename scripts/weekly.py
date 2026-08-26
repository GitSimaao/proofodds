#!/usr/bin/env python3
"""
The Monday email. Sends the week's scorecard to the list.

    python scripts/weekly.py                # dry run: prints, sends nothing
    python scripts/weekly.py --send         # creates the broadcast in Kit
    python scripts/weekly.py --send --week 2026-08-31

Dry run is the DEFAULT, deliberately. This is the one job in the project whose
mistakes cannot be taken back: a site can be rebuilt and a ledger entry is
immutable by design, but an email that has gone out has gone out.

Two guards beyond that: a week already sent is never sent twice, and a week
with no graded matches produces no email rather than an empty one.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from proofodds import config, newsletter  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("weekly")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true",
                    help="actually create the broadcast (default is a dry run)")
    ap.add_argument("--week", help="a date inside the week to report, YYYY-MM-DD; "
                                   "defaults to the week that just finished")
    args = ap.parse_args()

    today = dt.date.fromisoformat(args.week) + dt.timedelta(days=7) if args.week else None

    if args.send and not config.KIT_API_KEY:
        log.error("PROOFODDS_KIT_API_KEY is not set — refusing to pretend to send")
        return 2

    result = newsletter.weekly(dry_run=not args.send, today=today)

    if result["sent"]:
        log.info("scheduled: %s", result["subject"])
        log.info("Kit will send it in %d minutes — cancel there if it looks wrong",
                 config.NEWSLETTER_DELAY_MIN)
    else:
        log.info("nothing sent (%s)", result["reason"])
        if result["reason"] == "dry run":
            log.info("subject would be: %s", result["subject"])
            log.info("run again with --send when it reads right")
    return 0


if __name__ == "__main__":
    sys.exit(main())
