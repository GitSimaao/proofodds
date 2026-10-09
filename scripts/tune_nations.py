#!/usr/bin/env python3
"""
Pick the time decay for the international model, by holdout rather than taste.

`config.XI` is 0.002 -- a 347-day half-life -- and it was tuned by grid search
on Premier League seasons, where a club plays 38 matches a year. A national
side plays about ten. Transferring the club value would leave a team's rating
resting on barely more than one campaign, which is the sort of choice that is
easy to make silently and impossible to defend afterwards.

So this walks forward over Nations League matchdays: fit on every
international match strictly before the matchday, predict it, score the 1X2
log loss, and repeat. The winner is whichever XI predicts matches it was not
fitted on. Predicting 1/3-1/3-1/3 scores 1.0986, and any candidate that cannot
beat that is telling you the model has found nothing.

    python scripts/tune_nations.py
    python scripts/tune_nations.py --xi 0.0002,0.0005,0.001,0.002

Nothing here writes to the site, the ledger, or config. It prints a table; you
decide, and then you put the number in `config.NATIONS_XI` with this output as
the reason.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from proofodds import dixon_coles, nations   # noqa: E402

UNIFORM = math.log(3)


def result_index(home_goals, away_goals) -> int:
    if home_goals > away_goals:
        return 0
    if home_goals == away_goals:
        return 1
    return 2


def walk_forward(frame, xi: float, prior_sd: float, target_competition: str,
                 min_train: int = 400, verbose: bool = False):
    """
    Expanding-window holdout over one competition's matchdays.

    The fit never sees the matchday it is scored on, which is the only
    property that makes this number mean anything.
    """
    frame = frame.sort_values("Date").reset_index(drop=True)
    teams = sorted(set(frame["HomeTeam"]) | set(frame["AwayTeam"]))
    index = {name: i for i, name in enumerate(teams)}

    target = frame[frame["competition"] == target_competition]
    dates = sorted(target["Date"].unique())

    losses: list[float] = []
    warm = None
    for cut in dates:
        train = frame[frame["Date"] < cut]
        if len(train) < min_train:
            continue
        test = target[target["Date"] == cut]
        if test.empty:
            continue

        model = dixon_coles.fit(
            home_id=train["HomeTeam"].map(index).to_numpy(),
            away_id=train["AwayTeam"].map(index).to_numpy(),
            home_goals=train["FTHG"].to_numpy(),
            away_goals=train["FTAG"].to_numpy(),
            teams=teams,
            match_dates=train["Date"].to_numpy(),
            ref_date=cut,
            xi=xi, prior_sd=prior_sd, init=warm)
        warm = model.theta

        probs = model.predict(test["HomeTeam"].map(index).to_numpy(),
                              test["AwayTeam"].map(index).to_numpy())
        for row, prob in zip(test.itertuples(), probs):
            outcome = result_index(row.FTHG, row.FTAG)
            losses.append(-math.log(max(float(prob[outcome]), 1e-15)))
        if verbose:
            print(f"    {str(cut)[:10]}  {len(test):3d} matches  "
                  f"running {np.mean(losses):.4f}", file=sys.stderr)

    if not losses:
        return None
    losses_arr = np.array(losses)
    return {
        "n": len(losses_arr),
        "log_loss": float(losses_arr.mean()),
        "se": float(losses_arr.std(ddof=1) / math.sqrt(len(losses_arr))),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--xi", default="0.0002,0.0005,0.001,0.002",
                    help="comma-separated decay candidates, 1/day")
    ap.add_argument("--prior-sd", default="0.4,0.6",
                    help="comma-separated prior widths")
    ap.add_argument("--competition", default="UEFA Nations League",
                    help="which competition's matchdays to score on")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    frame = nations.load_results()
    if frame.empty:
        print("No cached international results. Run:\n"
              "  python -c 'from proofodds import nations; nations.refresh(force=True)'",
              file=sys.stderr)
        return 2

    print(f"pool: {len(frame)} matches, "
          f"{len(set(frame['HomeTeam']) | set(frame['AwayTeam']))} teams, "
          f"{frame['Date'].min().date()} -> {frame['Date'].max().date()}")
    print(f"scoring on: {args.competition}")
    print(f"no-knowledge baseline: {UNIFORM:.4f}\n")

    rows = []
    for prior_sd in [float(x) for x in args.prior_sd.split(",")]:
        for xi in [float(x) for x in args.xi.split(",")]:
            half_life = int(round(math.log(2) / xi)) if xi else 0
            print(f"xi={xi:<8} (half-life {half_life:>5}d) prior_sd={prior_sd} ...",
                  end=" ", flush=True)
            got = walk_forward(frame, xi, prior_sd, args.competition,
                               verbose=args.verbose)
            if not got:
                print("no holdout matches")
                continue
            got.update(xi=xi, prior_sd=prior_sd, half_life=half_life)
            rows.append(got)
            print(f"log loss {got['log_loss']:.4f} ±{1.96*got['se']:.4f} "
                  f"over {got['n']}")

    if not rows:
        return 1

    rows.sort(key=lambda r: r["log_loss"])
    best = rows[0]
    print("\nbest:")
    print(f"  NATIONS_XI       = {best['xi']}        # half-life {best['half_life']} days")
    print(f"  NATIONS_PRIOR_SD = {best['prior_sd']}")
    print(f"  holdout log loss = {best['log_loss']:.4f} over {best['n']} "
          f"Nations League matches")
    edge = UNIFORM - best["log_loss"]
    print(f"  versus no knowledge ({UNIFORM:.4f}): {edge:+.4f} per match")
    if edge <= 0:
        print("\n  That is not better than guessing. Do not publish this.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
