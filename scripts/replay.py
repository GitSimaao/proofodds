#!/usr/bin/env python3
"""
Replay the machinery over past matchdays — a SIMULATION, never the real ledger.

    python scripts/replay.py --from 2026-01-01 --to 2026-05-24

Why this exists: the live ledger starts empty and stays small for months, but
the pipeline still has to be proved end to end before launch. Replay runs the
exact publish → chain → grade → build path over historical fixtures and writes
everything into a throwaway directory.

Why the output must never be presented as a track record: these entries were
not published before kickoff, they were generated afterwards from known
fixtures. The model itself is still strictly walk-forward — it only ever sees
matches before the simulated publication date — so the numbers are a valid
backtest. They are not evidence that anything was published in advance, and
this project's entire claim is about publishing in advance.

The output goes to _replay/ and site-preview/, both git-ignored.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from proofodds import config  # noqa: E402

REPLAY_DIR = config.ROOT / "_replay"
PREVIEW_DIR = config.ROOT / "site-preview"

# Redirect the ledger before anything imports it for real.
config.PREDICTIONS_DIR = REPLAY_DIR

from proofodds import data, ledger, render  # noqa: E402
from proofodds.fixtures import Fixture  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s %(message)s")
log = logging.getLogger("replay")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", default="2026-01-01")
    ap.add_argument("--to", dest="end", default="2026-05-24")
    ap.add_argument("--every", type=int, default=3,
                    help="publish every N days (the real job runs daily)")
    ap.add_argument("--league", default="E0",
                    help="which division to replay")
    args = ap.parse_args()

    matches = data.load_matches(args.league)
    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)

    if REPLAY_DIR.exists():
        shutil.rmtree(REPLAY_DIR)
    REPLAY_DIR.mkdir(parents=True)

    print(f"Replaying {start} → {end}, publishing every {args.every} days")
    print("(simulation — these entries were NOT published before kickoff)\n")

    published = 0
    day = start
    while day <= end:
        now = dt.datetime.combine(day, dt.time(0, 5), tzinfo=dt.timezone.utc)
        horizon = day + dt.timedelta(days=config.LOOKAHEAD_DAYS)

        window = matches[(matches["Date"].dt.date > day) &
                         (matches["Date"].dt.date <= horizon)]
        fx = [Fixture(
                  kickoff=dt.datetime.combine(row.Date.date(), dt.time(15, 0),
                                              tzinfo=dt.timezone.utc),
                  home=row.HomeTeam, away=row.AwayTeam, league=args.league)
              for row in window.itertuples()]

        if fx:
            path = ledger.publish(fx, now=now)
            if path:
                published += 1
                if published % 10 == 0:
                    print(f"  {published} entries… ({day})")

        day += dt.timedelta(days=args.every)

    print(f"\n{published} simulated entries written to {REPLAY_DIR.name}/")

    report = ledger.verify_chain()
    print(f"Chain    : {'OK' if report['ok'] else 'BROKEN ' + str(report['broken'])}")
    print(f"Head     : {report['head'][:24]}…")

    from proofodds import grade
    graded = grade.graded_frame()
    score = grade.scorecard(graded)
    if score.get("live"):
        print(f"\nGraded   : {score['n']} matches ({score['first_date']} → {score['last_date']})")
        print(f"Model    : {score['model_log_loss']:.4f}")
        print(f"Market   : {score['market_log_loss']:.4f}")
        print(f"Gap      : {score['gap']:+.4f} per match "
              f"({score['gap_total']:+.1f} nats total)")
        print(f"Accuracy : {score['accuracy']:.1%} vs market {score['market_accuracy']:.1%}")
    else:
        print("\nNothing graded — check that results and closing odds are present.")

    render.build(out_dir=PREVIEW_DIR)
    print(f"\nPreview site built into {PREVIEW_DIR.name}/  (open index.html)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
