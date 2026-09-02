#!/usr/bin/env python3
"""
The gate: is what TheStatsAPI calls "Pinnacle" priced like Pinnacle?

    python scripts/check_pinnacle.py --matches 200
    python scripts/check_pinnacle.py --competition comp_3039 --matches 100

Why this exists instead of a direct comparison
----------------------------------------------
Every benchmark change on this site has been measured before it was made. The
last one — Pinnacle to market average, January 2026 — was checked match by
match on the thousands of games carrying both prices, and the measurement was
published on the method page.

That is impossible here. TheStatsAPI has no Pinnacle history (older seasons
carry Bet365 only) and football-data stopped publishing Pinnacle in January
2026. The two sources never overlap, so there is no match on which to compare
them, and no amount of care produces one.

What can still be tested is the *shape* of the prices. A sharp book runs a
thin margin: roughly 2.5-3% over on a three-way market. A market average runs
4-5%. If the feed labelled Pinnacle prices like a soft book or like an
average, it is not Pinnacle, and the benchmark must not move — the whole point
of the switch is that Pinnacle's close is the harder, sharper number.

This does not prove the prices are correct. It proves they are the right
*kind* of price, which is the strongest available check with no overlap, and
the method page says exactly that rather than implying more.

Exit status is 0 only when the median overround falls inside the band.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from proofodds import config, statsapi  # noqa: E402

# A three-way sharp close. Pinnacle sits near 2.5%; anything at or above 4%
# is an average or a soft book wearing the name.
SHARP_MIN, SHARP_MAX = 0.015, 0.038


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--competition", default=None,
                    help="competition id; default is every mapped division")
    ap.add_argument("--matches", type=int, default=100,
                    help="finished matches to sample (1 request each)")
    ap.add_argument("--book", default=config.STATSAPI_BENCHMARK_BOOK)
    ap.add_argument("--days", type=int, default=45,
                    help="look back this many days for finished matches")
    args = ap.parse_args()

    import datetime as dt
    since = (dt.datetime.now(dt.timezone.utc)
             - dt.timedelta(days=args.days)).date().isoformat()

    comps = ([args.competition] if args.competition
             else list(config.STATSAPI_COMPETITIONS.values()))
    if not comps:
        print("No competitions mapped yet. Run "
              "`python -m proofodds.statsapi map-divisions` and fill "
              "config.STATSAPI_COMPETITIONS first, or pass --competition.")
        return 2

    budget = statsapi._budget
    print(f"quota: {budget.used()}/{budget.monthly} used, "
          f"{budget.remaining()} left. Sampling up to {args.matches} matches "
          f"finished since {since}.\n")

    rows, missing = [], 0
    per_comp = max(1, args.matches // len(comps))
    for comp in comps:
        found = statsapi.matches(competition_id=comp, status="finished",
                                 date_from=since, limit=per_comp)
        for match in found[:per_comp]:
            if not match.get("odds_available"):
                missing += 1
                continue
            try:
                prices = statsapi.closing_odds(match, args.book)
            except statsapi.QuotaExhausted as exc:
                print(f"\nstopped: {exc}")
                break
            except statsapi.StatsAPIError as exc:
                print(f"  ! {match.get('id')}: {exc}")
                missing += 1
                continue
            if "1X2" not in prices:
                missing += 1
                continue
            rows.append({
                "id": match["id"],
                "comp": comp,
                "over": statsapi.overround(prices["1X2"]),
                "markets": sorted(prices),
            })

    if not rows:
        print("No 1X2 closing prices came back for this book. The migration "
              "cannot proceed on this evidence.")
        return 1

    overs = sorted(r["over"] for r in rows)
    median = statistics.median(overs)
    print(f"{args.book} closing 1X2 overround over {len(rows)} matches"
          f"{f' ({missing} skipped)' if missing else ''}:")
    print(f"  median {median:.3%}   mean {statistics.fmean(overs):.3%}")
    print(f"  p10 {overs[len(overs)//10]:.3%}   "
          f"p90 {overs[-max(1, len(overs)//10)]:.3%}")

    seen: dict[str, int] = {}
    for row in rows:
        for market in row["markets"]:
            seen[market] = seen.get(market, 0) + 1
    print("\n  markets present, by share of sampled matches:")
    for market, count in sorted(seen.items(), key=lambda kv: -kv[1]):
        print(f"    {market:8s} {count}/{len(rows)} ({count/len(rows):.0%})")

    ok = SHARP_MIN <= median <= SHARP_MAX
    print(f"\nband for a sharp book: {SHARP_MIN:.1%}–{SHARP_MAX:.1%}")
    print("result:", "PASS — prices like a sharp book; the benchmark may move"
          if ok else
          "FAIL — this does not price like Pinnacle. Do NOT move the "
          "benchmark; use the API for coverage only.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
