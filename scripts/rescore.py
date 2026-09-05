#!/usr/bin/env python3
"""
Recompute the published scorecard from the sealed ledger and the raw CSVs.

    python scripts/rescore.py                 # headline numbers, recomputed
    python scripts/rescore.py --csv audit.csv # every graded match, one row each
    python scripts/rescore.py --refresh       # download the results first

Why this exists
---------------
`proofodds.verify` proves nothing in the ledger was altered. It says nothing
about whether the SCORE on the front page is the right score for those sealed
files, and until this script there was no way for a reader to check that half
at all. A site arguing that nobody keeps the score should not ask anyone to
take the score on trust.

So this walks the same road by a different path. The join to results reuses the
project's club-name resolution, because that is a data-cleaning problem rather
than a claim — but every number is recomputed here, in a few lines anyone can
read, rather than imported from `grade.py`:

    de-vig     p_i = (1/odds_i) / sum_j (1/odds_j)
    log loss   -log(p) for the outcome that actually happened, averaged
    interval   mean(diff) +- 1.96 * sd(diff) / sqrt(n), diff paired per match

and then it compares its own answer with what `grade.py` produces. If the two
ever disagree the exit status is non-zero and both numbers are printed. Our
code checking our code can agree while both are wrong; two implementations
disagreeing is at least a fact.

`--csv` writes one row per graded match — sealed probabilities, which entry
sealed them, the result, the closing prices, both log losses. That file is the
whole scorecard. Anyone can average a column in a spreadsheet and get the
number on the front page, or find the match where we are wrong.
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from proofodds import config, data, grade  # noqa: E402

OUTCOMES = ("H", "D", "A")
TOLERANCE = 5e-7


def devig(prices: list[float]) -> list[float]:
    """Three decimal prices to three probabilities summing to one."""
    inverse = [1.0 / p for p in prices]
    total = sum(inverse)
    return [x / total for x in inverse]


def surprise(probability: float) -> float:
    """-log(p), the only scoring rule on this site."""
    return -math.log(max(probability, 1e-15))


def interval(differences: list[float]) -> tuple[float, float, float]:
    """Mean, standard error and half-width of a paired per-match difference."""
    n = len(differences)
    mean = sum(differences) / n
    if n < 2:
        return mean, float("nan"), float("nan")
    variance = sum((d - mean) ** 2 for d in differences) / (n - 1)
    se = math.sqrt(variance / n)
    return mean, se, 1.959963984540054 * se


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--csv", type=Path,
                    help="write one row per graded match to this file")
    ap.add_argument("--refresh", action="store_true",
                    help="download the results CSVs before scoring "
                         "(needed on a fresh clone: data/ is not committed)")
    ap.add_argument("--leagues", default="",
                    help="comma-separated division codes; default is every "
                         "division that appears in the ledger")
    args = ap.parse_args()

    leagues = [c.strip().upper() for c in args.leagues.split(",") if c.strip()]
    if args.refresh:
        data.refresh(leagues or None)

    frame = grade.graded_frame(leagues or None)
    if frame.empty:
        print("Nothing sealed yet.")
        return 0

    done = frame[frame["graded"]]
    if done.empty:
        print(f"{len(frame)} sealed, none graded yet.")
        return 0

    rows, model_losses, market_losses = [], [], []
    for match in done.itertuples():
        sealed = [match.p_H, match.p_D, match.p_A]
        closing = [match.AvgCH, match.AvgCD, match.AvgCA]
        market = devig(closing)
        hit = OUTCOMES.index(match.FTR)

        model_loss = surprise(sealed[hit])
        market_loss = surprise(market[hit])
        model_losses.append(model_loss)
        market_losses.append(market_loss)

        rows.append({
            "league": match.league,
            "date": match.date.date().isoformat(),
            "home": match.home,
            "away": match.away,
            "sealed_in": match.entry_file,
            "published_at": match.published_at,
            "p_H": f"{sealed[0]:.6f}", "p_D": f"{sealed[1]:.6f}",
            "p_A": f"{sealed[2]:.6f}",
            "closing_H": closing[0], "closing_D": closing[1],
            "closing_A": closing[2],
            "mkt_H": f"{market[0]:.6f}", "mkt_D": f"{market[1]:.6f}",
            "mkt_A": f"{market[2]:.6f}",
            "score": f"{int(match.FTHG)}-{int(match.FTAG)}",
            "result": match.FTR,
            "model_loss": f"{model_loss:.6f}",
            "market_loss": f"{market_loss:.6f}",
            "difference": f"{model_loss - market_loss:.6f}",
        })

    n = len(rows)
    model = sum(model_losses) / n
    market = sum(market_losses) / n
    differences = [a - b for a, b in zip(model_losses, market_losses)]
    mean, se, half = interval(differences)

    print(f"Recomputed from {len(list(config.PREDICTIONS_DIR.glob('*.json')))} "
          f"sealed entries and the raw football-data.co.uk CSVs.\n")
    print(f"Graded matches : {n}")
    print(f"ProofOdds      : {model:.4f}")
    print(f"Closing line   : {market:.4f}")
    print(f"Gap per match  : {mean:+.4f} ± {half:.4f} at 95% "
          f"(se {se:.4f}, t {mean / se:.2f})")
    print(f"                 interval {mean - half:+.4f} to {mean + half:+.4f}"
          f" — {'excludes' if (mean - half) * (mean + half) > 0 else 'contains'}"
          " zero")
    print(f"No knowledge   : {config.UNIFORM_LOG_LOSS:.4f}\n")

    if args.csv:
        with args.csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        print(f"Wrote {n} graded matches to {args.csv}. Average the "
              f"`model_loss` and `market_loss` columns and you have the two "
              f"numbers above.\n")

    # The point of the exercise: does the published pipeline agree?
    published = grade.scorecard(frame)
    drift = [("n", published["n"], n),
             ("model log loss", published["model_log_loss"], model),
             ("market log loss", published["market_log_loss"], market),
             ("gap", published["gap"], mean)]
    disagreements = [row for row in drift if abs(row[1] - row[2]) > TOLERANCE]
    if disagreements:
        print("MISMATCH — the published pipeline and this recomputation "
              "disagree:")
        for label, was, now in disagreements:
            print(f"  {label}: site {was!r}, recomputed {now!r}")
        return 1

    print("AGREES — every published figure recomputes from the sealed files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
