#!/usr/bin/env python3
"""
The audit that has to pass before a division goes live.

    python scripts/check_names.py                 # every enabled division
    python scripts/check_names.py --leagues P1,F1
    python scripts/check_names.py --days 120      # look further ahead
    python scripts/check_names.py --no-refresh    # use the cached CSVs

Adding a league is one line of configuration. Getting its club names wrong is
a prediction sealed under a name the grader cannot join — published, hashed,
and then never scored. The ledger is immutable, so that is not something to
discover in October.

So this script does the discovering, in advance and out loud. For each
division it prints every club the fixture feed will send in the next few
months, the spelling the results file uses, and WHICH RULE connected the two.
Read the fuzzy lines. Everything else is arithmetic; a fuzzy match is the
script telling you it made a judgement call.

Exit status is 0 only when every name in every division resolved.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from proofodds import config, data, fixtures            # noqa: E402

BOLD, DIM, RED, YELLOW, GREEN, OFF = (
    "\033[1m", "\033[2m", "\033[31m", "\033[33m", "\033[32m", "\033[0m")


def check_data(league: str) -> tuple[bool, str]:
    """Is there enough of this division cached to fit and to grade?"""
    try:
        matches = data.load_matches(league)
    except FileNotFoundError as exc:
        return False, str(exc)

    priced = data.add_market_probabilities(matches)
    have_odds = int(priced["has_odds"].sum())
    if have_odds == 0:
        return False, ("no Pinnacle closing odds in any season — this division "
                       "cannot be graded and must not be published")

    latest = matches["Date"].max().date()
    return True, (f"{len(matches):,} matches, {have_odds:,} with closing odds, "
                  f"{len(known := data.known_teams(league))} club names, "
                  f"latest result {latest}"
                  + ("" if known else " — NO NAMES"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--leagues", default="")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--no-refresh", action="store_true")
    args = ap.parse_args()

    # Every configured division by default, not only the enabled ones. The
    # point of this script is to look at a league BEFORE turning it on.
    leagues = ([c.strip().upper() for c in args.leagues.split(",") if c.strip()]
               or list(config.LEAGUES))

    if not args.no_refresh:
        print(f"{DIM}downloading results for {', '.join(leagues)} — "
              f"this takes a minute the first time{OFF}")
        data.refresh(leagues)

    total_unresolved = 0
    total_fuzzy = 0

    for league in leagues:
        meta = config.LEAGUES.get(league, {})
        print(f"\n{BOLD}{league}  {meta.get('name', '?')}{OFF}")

        ok, note = check_data(league)
        print(f"  results   {GREEN if ok else RED}{note}{OFF}")
        if not ok:
            total_unresolved += 1
            continue

        try:
            got = fixtures.from_football_data_org(league, args.days)
        except Exception as exc:
            print(f"  fixtures  {RED}{exc}{OFF}")
            total_unresolved += 1
            continue
        if got is None:
            print(f"  fixtures  {YELLOW}no PROOFODDS_FDORG_TOKEN — cannot "
                  f"check the names that matter most{OFF}")
            total_unresolved += 1
            continue
        if not got:
            print(f"  fixtures  {YELLOW}nothing scheduled in the next "
                  f"{args.days} days — try a longer window{OFF}")
            continue

        # Every distinct club the feed will send us, with its raw spelling.
        seen: dict[str, str] = {}
        for fx in got:
            seen.setdefault(fx.home_raw, fx.home)
            seen.setdefault(fx.away_raw, fx.away)

        print(f"  fixtures  {len(got)} matches, {len(seen)} clubs\n")
        bad, fuzzy = [], []
        for raw in sorted(seen):
            hit, how = data.resolve(raw, league)
            if hit is None:
                bad.append((raw, how))
                colour = RED
            elif how.startswith("fuzzy"):
                fuzzy.append((raw, hit, how))
                colour = YELLOW
            else:
                colour = ""
            shown = hit if hit else f"?? (would publish as {seen[raw]!r})"
            print(f"    {colour}{raw:<34}{OFF} -> {colour}{str(shown):<20}{OFF}"
                  f" {DIM}{how}{OFF}")

        if fuzzy:
            total_fuzzy += len(fuzzy)
            print(f"\n  {YELLOW}{len(fuzzy)} name(s) matched by similarity. "
                  f"Check each one by eye; if any is wrong, pin it in "
                  f"data.OVERRIDES[{league!r}].{OFF}")
        if bad:
            total_unresolved += len(bad)
            print(f"\n  {RED}{len(bad)} name(s) did not resolve. Each is a "
                  f"prediction that will not be graded until it does.{OFF}")
            print(f"  {DIM}Add to data.OVERRIDES[{league!r}]:{OFF}")
            for raw, _ in bad:
                print(f'      "{data.fold(raw)}": "<results-file spelling>",')

    print()
    if total_unresolved:
        print(f"{RED}{BOLD}NOT READY{OFF} — {total_unresolved} problem(s). "
              f"Leave these divisions out of PROOFODDS_LEAGUES until this is "
              f"clean.")
        return 1
    if total_fuzzy:
        print(f"{YELLOW}{BOLD}CHECK THE FUZZY MATCHES{OFF} — everything "
              f"resolved, but {total_fuzzy} by similarity rather than by rule.")
        return 0
    print(f"{GREEN}{BOLD}ALL CLEAR{OFF} — every club in every division "
          f"resolves by an exact rule.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
