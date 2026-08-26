"""
Grading: match published predictions to results and closing prices, then score.

The scorecard answers one question and refuses to dress it up: over the matches
we published in advance, is our log loss below Pinnacle's closing line or above
it? Two reference points frame every number on the page —

    1.0986   predicting 1/3-1/3-1/3 every week
    ~0.95    the closing line

— so a reader can see immediately how much of the available knowledge the model
captures, and how much it gives away.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from . import config
from .data import (add_market_probabilities, canonical, load_matches, log_loss,
                   result_index)
from .ledger import all_predictions

log = logging.getLogger(__name__)

PROB_COLS = ["p_H", "p_D", "p_A"]
MKT_COLS = ["mkt_H", "mkt_D", "mkt_A"]


def graded_frame(league: str = "E0") -> pd.DataFrame:
    """
    Join published predictions to finished matches.

    Only matches that have been played AND have closing odds can be graded —
    everything else stays in the table with `graded = False` so the site can
    show what is pending rather than silently dropping it.
    """
    preds = pd.DataFrame(all_predictions())
    if preds.empty:
        return preds

    preds["date"] = pd.to_datetime(preds["kickoff"]).dt.tz_convert(None).dt.normalize()

    # Canonicalise the LEDGER side at read time, never at write time.
    #
    # The ledger is immutable: an entry sealed with a club spelled the way some
    # fixture feed spelled it that day stays exactly as it was published. But
    # the join to results is by name, so a feed that says "Hull City AFC" while
    # the results file says "Hull" would leave that prediction unmatched — and
    # an unmatched prediction is one that never gets scored, which is the one
    # outcome this project cannot allow. Normalising here fixes the past
    # without rewriting it.
    preds["home"] = preds["home"].map(canonical)
    preds["away"] = preds["away"].map(canonical)

    results = add_market_probabilities(load_matches(league))
    results = results.rename(columns={"HomeTeam": "home", "AwayTeam": "away"})
    results["date"] = results["Date"].dt.normalize()

    cols = ["date", "home", "away", "FTHG", "FTAG", "FTR", "Season",
            "PSCH", "PSCD", "PSCA", "has_odds"] + MKT_COLS
    merged = preds.merge(results[cols], on=["date", "home", "away"], how="left")

    merged["played"] = merged["FTR"].notna()
    merged["graded"] = merged["played"] & merged["has_odds"].fillna(False)

    g = merged["graded"].to_numpy()
    merged["model_loss"] = np.nan
    merged["market_loss"] = np.nan
    if g.any():
        sub = merged[g]
        idx = result_index(sub["FTR"])
        rows = np.arange(len(sub))
        p_model = sub[PROB_COLS].to_numpy(float)[rows, idx]
        p_market = sub[MKT_COLS].to_numpy(float)[rows, idx]
        merged.loc[g, "model_loss"] = -np.log(np.clip(p_model, 1e-15, 1))
        merged.loc[g, "market_loss"] = -np.log(np.clip(p_market, 1e-15, 1))
        merged.loc[g, "hit"] = (sub[PROB_COLS].to_numpy(float).argmax(axis=1) == idx)

    # A prediction whose match kicked off days ago and still has no result is
    # almost always a name that failed to join, not a fixture that vanished.
    # Say so out loud rather than letting the scorecard quietly shrink.
    stale = merged[(~merged["played"]) &
                   (merged["date"] < pd.Timestamp.utcnow().tz_localize(None)
                    - pd.Timedelta(days=3))]
    if not stale.empty:
        pairs = ", ".join(f"{r.home} v {r.away}" for r in stale.head(6).itertuples())
        log.warning("%d sealed prediction(s) still unmatched more than 3 days "
                    "after kickoff — check club spellings: %s",
                    len(stale), pairs)

    return merged.sort_values("date").reset_index(drop=True)


def scorecard(graded: pd.DataFrame) -> dict:
    """The headline numbers. Everything here goes on the public page."""
    if graded.empty or "graded" not in graded.columns:
        return {"n": 0, "pending": 0, "live": False}

    done = graded[graded["graded"]]
    if done.empty:
        return {"n": 0, "pending": int((~graded["graded"]).sum()), "live": False}

    model = float(done["model_loss"].mean())
    market = float(done["market_loss"].mean())
    cumulative = (done["model_loss"] - done["market_loss"]).cumsum()

    return {
        "live": True,
        "n": int(len(done)),
        "pending": int((~graded["graded"]).sum()),
        "first_date": done["date"].min().date().isoformat(),
        "last_date": done["date"].max().date().isoformat(),
        "model_log_loss": model,
        "market_log_loss": market,
        "uniform_log_loss": config.UNIFORM_LOG_LOSS,
        "gap": model - market,
        "gap_total": float(cumulative.iloc[-1]),
        "beats_market": model < market,
        "accuracy": float(done["hit"].mean()) if "hit" in done else None,
        "market_accuracy": float(
            (done[MKT_COLS].to_numpy(float).argmax(axis=1) == result_index(done["FTR"])).mean()
        ),
        "share_of_available": (config.UNIFORM_LOG_LOSS - model) /
                              (config.UNIFORM_LOG_LOSS - market) if market < config.UNIFORM_LOG_LOSS else None,
        "curve": [
            {"date": d.date().isoformat(), "value": float(v)}
            for d, v in zip(done["date"], cumulative)
        ],
    }


def by_week(graded: pd.DataFrame) -> list[dict]:
    """Weekly rollup — enough resolution to see form without being noisy."""
    if graded.empty or "graded" not in graded.columns:
        return []
    done = graded[graded["graded"]].copy()
    if done.empty:
        return []
    done["week"] = done["date"].dt.to_period("W").dt.start_time
    rows = []
    for week, block in done.groupby("week"):
        rows.append({
            "week": week.date().isoformat(),
            "n": int(len(block)),
            "model": float(block["model_loss"].mean()),
            "market": float(block["market_loss"].mean()),
            "gap": float(block["model_loss"].mean() - block["market_loss"].mean()),
        })
    return rows


def calibration(graded: pd.DataFrame, n_bins: int = 10) -> list[dict]:
    """Of the matches where we said ~30%, did ~30% happen?"""
    if graded.empty or "graded" not in graded.columns:
        return []
    done = graded[graded["graded"]]
    if len(done) < 30:
        return []

    p = done[PROB_COLS].to_numpy(float).ravel()
    obs = np.zeros((len(done), 3))
    obs[np.arange(len(done)), result_index(done["FTR"])] = 1.0
    obs = obs.ravel()

    edges = np.linspace(0, 1, n_bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, n_bins - 1)

    out = []
    for b in range(n_bins):
        sel = idx == b
        if sel.sum() < 5:
            continue
        out.append({"predicted": float(p[sel].mean()),
                    "observed": float(obs[sel].mean()),
                    "n": int(sel.sum())})
    return out


def backfill_scorecard(league: str = "E0") -> dict:
    """
    The historical walk-forward record, for the page that explains the method.

    This is NOT the live scorecard and the site must never present it as one:
    it is a backtest, reproducible from the public repo, and it exists to give
    a reader a prior before the live sample is big enough to mean anything.
    """
    matches = add_market_probabilities(load_matches(league))
    graded = matches[matches["has_odds"] & (matches["Date"] >= config.SCORECARD_START)]
    if graded.empty:
        return {}
    return {
        "n": int(len(graded)),
        "market_log_loss": log_loss(graded[MKT_COLS].to_numpy(float), graded["FTR"]),
        "first_date": graded["Date"].min().date().isoformat(),
        "last_date": graded["Date"].max().date().isoformat(),
    }
