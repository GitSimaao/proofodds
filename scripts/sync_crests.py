#!/usr/bin/env python3
"""Refresh display-only club crest URLs without touching the ledger."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from proofodds import config, fixtures  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(levelname)-7s %(name)s: %(message)s")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--leagues", default="",
                        help="comma-separated division codes; defaults to "
                             "PROOFODDS_LEAGUES")
    args = parser.parse_args()
    leagues = ([code.strip().upper() for code in args.leagues.split(",")
                if code.strip()] or list(config.ENABLED_LEAGUES))
    unknown = [code for code in leagues if code not in config.LEAGUES]
    if unknown:
        parser.error("unknown division(s): " + ", ".join(unknown))

    report = fixtures.sync_crests(leagues)
    for league in leagues:
        if league in report:
            print(f"{league}: {report[league]} crest URL(s)")
    print(f"cache: {config.DATA_DIR / 'club_crests.json'}")
    missing = [league for league in leagues if report.get(league, 0) == 0]
    if missing:
        print("crest sync incomplete for: " + ", ".join(missing),
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
