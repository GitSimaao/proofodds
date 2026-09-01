#!/usr/bin/env python3
"""
The measurement behind the benchmark switch. Rerun it; don't take our word.

    python scripts/check_benchmark.py                 # every enabled division
    python scripts/check_benchmark.py --leagues E0,P1
    python scripts/check_benchmark.py --tolerance 0.005

In January 2026 football-data.co.uk stopped publishing Pinnacle's closing
prices (PSC*/PC>2.5), and the site switched to grading against the market
average (AvgC*). This script quantifies what that switch did to the benchmark:
for every cached match that carries BOTH prices, it de-vigs each the same way
the site does and scores both against the actual results with log loss.

A positive difference means the average is the SOFTER benchmark (higher log
loss = easier to beat). The published claim, which this script checks as an
exit status: no division differs by more than the tolerance (default 0.005
nats) on either market. When we ran it, the worst was the Primeira Liga at
+0.0020 on 1X2; the method page discloses that rather than hiding it.

Rows where any odds column is <= 1 are excluded — 0.0 in these files is a
placeholder, not a price, and 1/0 would poison the mean for both benchmarks
equally. The exclusion count is printed.

Exit status is 0 only when every division is inside the tolerance.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from proofodds import config, data  # noqa: E402

PS_1X2 = ["PSCH", "PSCD", "PSCA"]
AVG_1X2 = ["AvgCH", "AvgCD", "AvgCA"]
PS_OU = ["PC>2.5", "PC<2.5"]
AVG_OU = ["AvgC>2.5", "AvgC<2.5"]


def devig(frame, cols) -> np.ndarray:
    inv = 1.0 / frame[cols].to_numpy(dtype=float)
    return inv / inv.sum(axis=1, keepdims=True)


def score_1x2(frame, cols) -> float:
    return data.log_loss(devig(frame, cols), frame["FTR"])


def score_ou(frame, cols) -> float:
    probs = devig(frame, cols)                      # [over, under]
    over = (frame["FTHG"] + frame["FTAG"] > 2).to_numpy()
    picked = np.where(over, probs[:, 0], probs[:, 1])
    return float(-np.log(np.clip(picked, 1e-15, 1.0)).mean())


def both_priced(matches, cols):
    """Rows carrying every column, all with a real price (> 1)."""
    if not all(c in matches.columns for c in cols):
        return matches.iloc[0:0], 0
    have = matches[matches[cols].notna().all(axis=1)]
    ok = have[(have[cols] > 1).all(axis=1)]
    return ok, len(have) - len(ok)


def check_league(league: str, tolerance: float) -> bool:
    try:
        matches = data.load_matches(league)
    except FileNotFoundError as exc:
        print(f"{league}: SKIP — {exc}")
        return True

    passed = True
    for label, ps, avg, scorer in [("1X2", PS_1X2, AVG_1X2, score_1x2),
                                   ("O/U 2.5", PS_OU, AVG_OU, score_ou)]:
        sub, excluded = both_priced(matches, ps + avg)
        if len(sub) == 0:
            print(f"{league} {label}: no overlap (columns absent) — nothing to compare")
            continue
        ll_ps, ll_avg = scorer(sub, ps), scorer(sub, avg)
        diff = ll_avg - ll_ps
        ok = abs(diff) <= tolerance
        passed &= ok
        note = f", {excluded} zero-odds row(s) excluded" if excluded else ""
        print(f"{league} {label}: n={len(sub):5d}  Pinnacle {ll_ps:.4f}  "
              f"average {ll_avg:.4f}  diff {diff:+.4f}  "
              f"{'ok' if ok else 'OUTSIDE TOLERANCE'}{note}")
    return passed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--leagues", default=None,
                    help="comma-separated division codes (default: all enabled)")
    ap.add_argument("--tolerance", type=float, default=0.005,
                    help="max |log-loss difference| accepted per division (nats)")
    args = ap.parse_args()

    leagues = (args.leagues.split(",") if args.leagues
               else list(config.ENABLED_LEAGUES))

    all_ok = all([check_league(lg.strip(), args.tolerance) for lg in leagues])
    print("\nresult:", "PASS — benchmarks equivalent within tolerance" if all_ok
          else "FAIL — see lines above")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
