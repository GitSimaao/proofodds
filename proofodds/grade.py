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
from .data import (add_market_probabilities, load_all_matches, log_loss,
                   result_index, sealed_name)
from .ledger import all_predictions

log = logging.getLogger(__name__)

PROB_COLS = ["p_H", "p_D", "p_A"]
MKT_COLS = ["mkt_H", "mkt_D", "mkt_A"]
OU_PROB_COLS = ["p_over25", "p_under25"]
OU_MKT_COLS = ["mkt_over25", "mkt_under25"]


def _canonical_for(name: str, raw: str, league: str) -> str:
    """Read-side club-name resolution. Lives in data.sealed_name; see there."""
    return sealed_name(name, league, raw)


def graded_frame(leagues=None) -> pd.DataFrame:
    """
    Join published predictions to finished matches, across every division.

    Only matches that have been played AND have closing odds can be graded —
    everything else stays in the table with `graded = False` so the site can
    show what is pending rather than silently dropping it.
    """
    preds = pd.DataFrame(all_predictions())
    if preds.empty:
        return preds

    preds["date"] = pd.to_datetime(preds["kickoff"]).dt.tz_convert(None).dt.normalize()
    if "league" not in preds.columns:
        preds["league"] = "E0"
    for col in ("home_raw", "away_raw"):
        if col not in preds.columns:
            preds[col] = ""
    preds[["home_raw", "away_raw"]] = preds[["home_raw", "away_raw"]].fillna("")

    # Canonicalise the LEDGER side at read time, never at write time.
    #
    # The ledger is immutable: an entry sealed with a club spelled the way some
    # fixture feed spelled it that day stays exactly as it was published. But
    # the join to results is by name, so a feed that says "Hull City AFC" while
    # the results file says "Hull" would leave that prediction unmatched — and
    # an unmatched prediction is one that never gets scored, which is the one
    # outcome this project cannot allow. Normalising here fixes the past
    # without rewriting it.
    preds["home"] = [_canonical_for(h, r, lg) for h, r, lg
                     in zip(preds["home"], preds["home_raw"], preds["league"])]
    preds["away"] = [_canonical_for(a, r, lg) for a, r, lg
                     in zip(preds["away"], preds["away_raw"], preds["league"])]

    # Load the results for every division that is enabled AND every division
    # that appears in the ledger. Turning a league off must never make its past
    # predictions vanish from the score — that would be the most flattering
    # possible bug.
    wanted = list(dict.fromkeys(list(leagues or config.ENABLED_LEAGUES)
                                + sorted(preds["league"].unique())))
    results = add_market_probabilities(load_all_matches(wanted))
    results = results.rename(columns={"HomeTeam": "home", "AwayTeam": "away",
                                      "League": "league"})
    results["date"] = results["Date"].dt.normalize()

    cols = (["league", "date", "home", "away", "FTHG", "FTAG", "FTR", "Season",
             "PSCH", "PSCD", "PSCA", "has_odds", "has_ou_odds", "over25"]
            + MKT_COLS + OU_MKT_COLS)
    merged = preds.merge(results[cols], on=["league", "date", "home", "away"],
                         how="left")

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

    # The totals market is graded separately, on its own subset. A match can
    # be gradeable on 1X2 and not on over/under — Pinnacle's closing total is
    # only published from 2019/20, and an entry sealed before this market
    # existed carries no probability for it at all. Neither gap is allowed to
    # borrow matches from the other.
    for col in OU_PROB_COLS:
        if col not in merged.columns:
            merged[col] = np.nan
    merged["ou_graded"] = (merged["played"]
                           & merged["has_ou_odds"].fillna(False)
                           & merged[OU_PROB_COLS].notna().all(axis=1))
    merged["ou_model_loss"] = np.nan
    merged["ou_market_loss"] = np.nan
    o = merged["ou_graded"].to_numpy()
    if o.any():
        sub = merged[o]
        idx = np.where(sub["over25"].to_numpy().astype(bool), 0, 1)
        rows = np.arange(len(sub))
        pm = sub[OU_PROB_COLS].to_numpy(float)[rows, idx]
        pk = sub[OU_MKT_COLS].to_numpy(float)[rows, idx]
        merged.loc[o, "ou_model_loss"] = -np.log(np.clip(pm, 1e-15, 1))
        merged.loc[o, "ou_market_loss"] = -np.log(np.clip(pk, 1e-15, 1))

    # A prediction whose match kicked off days ago and still has no result is
    # almost always a name that failed to join, not a fixture that vanished.
    # Say so out loud rather than letting the scorecard quietly shrink.
    stale = merged[(~merged["played"]) &
                   (merged["date"] < pd.Timestamp.utcnow().tz_localize(None)
                    - pd.Timedelta(days=3))]
    if not stale.empty:
        pairs = ", ".join(f"{r.league} {r.home} v {r.away}"
                          for r in stale.head(8).itertuples())
        log.warning("%d sealed prediction(s) still unmatched more than 3 days "
                    "after kickoff — check club spellings: %s",
                    len(stale), pairs)

    return merged.sort_values(["date", "league"]).reset_index(drop=True)


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


def totals_scorecard(graded: pd.DataFrame) -> dict:
    """
    The over/under 2.5 record, kept apart from the 1X2 one on purpose.

    Two markets, two reference points: guessing 1/3-1/3-1/3 scores 1.0986,
    guessing a coin flip on a half-goal line scores 0.6931. Averaging the two
    would produce a number that means nothing.

    The gaps are not comparable either, and that is the trap this function
    exists to defuse. Everything anyone knows about a football result is worth
    about 0.150 nats on 1X2; on total goals it is worth about 0.020. There is
    roughly seven times less to know, so a model will sit closer to the closing
    line on totals almost regardless of how good it is — and reading that as
    "we are better at goals" would be exactly backwards. `share_of_available`
    divides each gap by what was there to win, which is the only comparison
    between the two that means anything.
    """
    if graded.empty or "ou_graded" not in graded.columns:
        return {"n": 0, "live": False}
    done = graded[graded["ou_graded"]]
    if done.empty:
        return {"n": 0, "live": False}

    model = float(done["ou_model_loss"].mean())
    market = float(done["ou_market_loss"].mean())
    uniform = config.UNIFORM_LOG_LOSS_BINARY
    return {
        "live": True,
        "n": int(len(done)),
        "line": config.TOTALS_LINE,
        "model_log_loss": model,
        "market_log_loss": market,
        "uniform_log_loss": uniform,
        "gap": model - market,
        "beats_market": model < market,
        "over_rate": float(done["over25"].mean()),
        "share_of_available": ((uniform - model) / (uniform - market)
                               if market < uniform else None),
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


def by_league(graded: pd.DataFrame) -> list[dict]:
    """
    The same question, division by division.

    Worth its own table for a reason beyond curiosity: with one league the
    sample is too small to separate a real edge from noise for years. Seven
    divisions is roughly 2,400 matches a season instead of 380, which is the
    difference between a scorecard that means something this season and one
    that means something in 2031. The per-division rows also show whether any
    apparent edge is a real pattern or one league's lucky autumn.
    """
    if graded.empty or "graded" not in graded.columns:
        return []
    rows = []
    order = {code: i for i, code in enumerate(config.LEAGUE_ORDER)}
    for code, block in graded.groupby("league"):
        done = block[block["graded"]]
        row = {
            "league": code,
            "name": config.league_name(code),
            "n": int(len(done)),
            "pending": int((~block["graded"]).sum()),
        }
        if len(done):
            row.update({
                "model": float(done["model_loss"].mean()),
                "market": float(done["market_loss"].mean()),
                "gap": float((done["model_loss"] - done["market_loss"]).mean()),
                "accuracy": float(done["hit"].mean()) if "hit" in done else None,
            })
        rows.append(row)
    return sorted(rows, key=lambda r: order.get(r["league"], 99))


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


def backfill_scorecard(leagues=None) -> dict:
    """
    The historical walk-forward record, for the page that explains the method.

    This is NOT the live scorecard and the site must never present it as one:
    it is a backtest, reproducible from the public repo, and it exists to give
    a reader a prior before the live sample is big enough to mean anything.
    """
    matches = add_market_probabilities(load_all_matches(leagues))
    graded = matches[matches["has_odds"] & (matches["Date"] >= config.SCORECARD_START)]
    if graded.empty:
        return {}
    return {
        "n": int(len(graded)),
        "market_log_loss": log_loss(graded[MKT_COLS].to_numpy(float), graded["FTR"]),
        "first_date": graded["Date"].min().date().isoformat(),
        "last_date": graded["Date"].max().date().isoformat(),
    }
