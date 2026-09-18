"""
Grading: match published predictions to results and closing prices, then score.

The scorecard answers one question and refuses to dress it up: over the matches
we published in advance, is our log loss below the market-average closing
line or above it? Two reference points frame every number on the page —

    1.0986   predicting 1/3-1/3-1/3 every week
    ~0.95    the closing line

— so a reader can see immediately how much of the available knowledge the model
captures, and how much it gives away.
"""

from __future__ import annotations

import datetime as dt
import logging

import numpy as np
import pandas as pd

from . import config
from .data import (add_market_probabilities, load_all_matches, log_loss,
                   result_index, sealed_name)
from .ledger import all_predictions, requested_since

log = logging.getLogger(__name__)

PROB_COLS = ["p_H", "p_D", "p_A"]
MKT_COLS = ["mkt_H", "mkt_D", "mkt_A"]
OU_PROB_COLS = ["p_over25", "p_under25"]
OU_MKT_COLS = ["mkt_over25", "mkt_under25"]


def _canonical_for(name: str, raw: str, league: str) -> str:
    """Read-side club-name resolution. Lives in data.sealed_name; see there."""
    return sealed_name(name, league, raw)


# --------------------------------------------------------------------------- #
#  Uncertainty
# --------------------------------------------------------------------------- #
#  Every headline on this site is a mean over a sample, and a mean over a
#  sample has a width. Publishing 0.9386 against 0.9159 to four decimals and
#  saying nothing about the width invites exactly the reading this project
#  exists to argue against — that a number is a fact because it has decimals.
#
#  The right statistic is the PAIRED difference: the model and the market score
#  the same matches, so the match-to-match variation that dominates both means
#  cancels, and the standard error of (model - market) is several times smaller
#  than the standard error of either one alone. That is also why the gap is the
#  honest thing to quote a bound on, rather than the two log losses separately.
#
#  Deterministic on purpose. A bootstrap would be more exact and would move the
#  published interval a little on every rebuild for identical data; on a site
#  whose argument is reproducibility, an interval a reader cannot recompute
#  with a pocket calculator is the wrong trade.
Z95 = 1.959963984540054


def paired_interval(diff: np.ndarray, weights: np.ndarray | None = None) -> dict:
    """
    Mean, standard error and 95% interval for a paired per-match difference.

    `weights` carries the Asian handicap's half-win/half-loss stakes, where a
    quarter-line match contributes half a settled bet to each side; there the
    mean is stake-weighted and the effective sample is the stake total, not
    the row count.
    """
    diff = np.asarray(diff, dtype=float)
    diff = diff[np.isfinite(diff)]
    n = int(len(diff))
    if n == 0:
        return {"n": 0, "mean": None, "se": None, "ci_low": None,
                "ci_high": None, "t": None, "separated": False}
    if weights is None:
        mean = float(diff.mean())
        se = float(diff.std(ddof=1) / np.sqrt(n)) if n > 1 else None
    else:
        w = np.asarray(weights, dtype=float)[:len(diff)]
        total = float(w.sum())
        mean = float((diff * w).sum() / total) if total else float("nan")
        # Weighted standard error of a weighted mean, effective-n form.
        var = float((w * (diff - mean) ** 2).sum() / total) if total else float("nan")
        eff = (total ** 2) / float((w ** 2).sum()) if (w ** 2).sum() else 0.0
        se = float(np.sqrt(var / eff)) if eff > 1 else None
    if se is None or not np.isfinite(se) or se == 0:
        return {"n": n, "mean": mean, "se": None, "ci_low": None,
                "ci_high": None, "t": None, "separated": False}
    half = Z95 * se
    return {"n": n, "mean": mean, "se": se,
            "ci_low": mean - half, "ci_high": mean + half,
            "t": mean / se,
            # "separated", not "significant". The question this answers is
            # narrow: does the interval exclude zero, i.e. can this sample tell
            # the model and the closing line apart at all yet? It is not a
            # claim that any difference found is large enough to matter.
            "separated": bool((mean - half) * (mean + half) > 0)}


def _share_bounds(uniform: float, market: float, interval: dict) -> tuple:
    """
    Turn the gap's interval into an interval on "share of the available".

    share = (uniform - model) / (uniform - market) = 1 - gap / (uniform - market)
    so the bound is the gap bound, inverted. The denominator is itself an
    estimate; holding it fixed understates the width slightly, and the width
    that matters here is the numerator's by an order of magnitude.
    """
    available = uniform - market
    if not available or available <= 0 or interval.get("ci_low") is None:
        return None, None
    return (1 - interval["ci_high"] / available,
            1 - interval["ci_low"] / available)


def sealed_frame() -> pd.DataFrame:
    """
    Every sealed prediction, with its club names canonicalised for joining.

    Extracted from `graded_frame` so that coverage and grading cannot disagree
    about which sealed prediction corresponds to which played match. Two joins
    written twice drift, and here a drift would mean the scorecard and the
    coverage figure printed beside it contradicting each other about the same
    ledger — on a site whose entire argument is that its numbers are checkable.
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
    return preds


def graded_frame(leagues=None) -> pd.DataFrame:
    """
    Join published predictions to finished matches, across every division.

    Only matches that have been played AND have closing odds can be graded —
    everything else stays in the table with `graded = False` so the site can
    show what is pending rather than silently dropping it.
    """
    preds = sealed_frame()
    if preds.empty:
        return preds

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
             "AvgCH", "AvgCD", "AvgCA", "AHCh", "HC", "AC", "has_odds", "has_ou_odds",
             "has_ah_odds", "mkt_ah_home", "mkt_ah_away", "over25"]
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
    # be gradeable on 1X2 and not on over/under — a closing total is not
    # published for every match, and an entry sealed before this market
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

    merged["btts_graded"] = merged["played"] & merged.get("p_btts_yes", pd.Series(np.nan, index=merged.index)).notna()
    merged["btts_model_loss"] = np.nan
    b = merged["btts_graded"].to_numpy()
    if b.any():
        yes = ((merged.loc[b, "FTHG"] > 0) & (merged.loc[b, "FTAG"] > 0)).to_numpy()
        p = merged.loc[b, "p_btts_yes"].to_numpy(float)
        merged.loc[b, "btts_model_loss"] = -np.log(np.clip(np.where(yes, p, 1-p), 1e-15, 1))

    def ah_model(row):
        ladder = row.get("asian_handicap")
        if not isinstance(ladder, list) or pd.isna(row.get("AHCh")):
            return np.nan
        hit = min(ladder, key=lambda x: abs(float(x["line"])-float(row["AHCh"])))
        return float(hit["p_home"]) if abs(float(hit["line"])-float(row["AHCh"])) < 1e-6 else np.nan
    merged["ah_model_home"] = merged.apply(ah_model, axis=1)
    merged["ah_graded"] = merged["played"] & merged["has_ah_odds"].fillna(False) & merged["ah_model_home"].notna()
    merged["ah_model_loss"] = np.nan; merged["ah_market_loss"] = np.nan; merged["ah_weight"] = 0.0
    for idx, row in merged[merged["ah_graded"]].iterrows():
        line = float(row["AHCh"]); legs = [line] if int(round(line*4)) % 2 == 0 else [np.floor(line*2)/2, np.ceil(line*2)/2]
        lm = lk = weight = 0.0
        for leg in legs:
            margin = float(row["FTHG"]-row["FTAG"]+leg)
            if abs(margin) < 1e-9: continue
            weight += 1/len(legs); home_win = margin > 0
            lm += -np.log(np.clip(row["ah_model_home"] if home_win else 1-row["ah_model_home"], 1e-15, 1))/len(legs)
            lk += -np.log(np.clip(row["mkt_ah_home"] if home_win else 1-row["mkt_ah_home"], 1e-15, 1))/len(legs)
        merged.at[idx,"ah_model_loss"] = lm; merged.at[idx,"ah_market_loss"] = lk; merged.at[idx,"ah_weight"] = weight

    # A prediction whose match kicked off days ago and still has no result is
    # almost always a name that failed to join, not a fixture that vanished.
    # Say so out loud rather than letting the scorecard quietly shrink.
    stale = merged[(~merged["played"]) &
                   (merged["date"] < pd.Timestamp.now("UTC").tz_localize(None)
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
    interval = paired_interval((done["model_loss"] - done["market_loss"]).to_numpy())
    share_low, share_high = _share_bounds(config.UNIFORM_LOG_LOSS, market, interval)

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
        "share_low": share_low,
        "share_high": share_high,
        # The width of the headline. `separated` false means this sample cannot
        # yet tell the model apart from the closing line in either direction,
        # which is a fact about the sample and has to be said out loud.
        "se": interval["se"],
        "ci_low": interval["ci_low"],
        "ci_high": interval["ci_high"],
        "t": interval["t"],
        "separated": interval["separated"],
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
    about 0.135 nats on 1X2; on total goals it is worth about 0.020. There is
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
    interval = paired_interval(
        (done["ou_model_loss"] - done["ou_market_loss"]).to_numpy())
    share_low, share_high = _share_bounds(uniform, market, interval)
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
        "share_low": share_low,
        "share_high": share_high,
        "se": interval["se"],
        "ci_low": interval["ci_low"],
        "ci_high": interval["ci_high"],
        "t": interval["t"],
        "separated": interval["separated"],
    }


def btts_scorecard(graded: pd.DataFrame) -> dict:
    if graded.empty or "btts_graded" not in graded: return {"live": False, "n": 0}
    done = graded[graded["btts_graded"]]
    if done.empty: return {"live": False, "n": 0}
    # No benchmark to pair against, so the reference is the coin flip and the
    # interval is on (model - coin flip) per match. Same rule as everywhere
    # else: a number without a width is not a measurement.
    interval = paired_interval(
        (done.btts_model_loss - config.UNIFORM_LOG_LOSS_BINARY).to_numpy())
    return {"live": True, "n": len(done), "model_log_loss": float(done.btts_model_loss.mean()),
            "uniform_log_loss": config.UNIFORM_LOG_LOSS_BINARY,
            "se": interval["se"], "ci_low": interval["ci_low"],
            "ci_high": interval["ci_high"], "separated": interval["separated"],
            "note": "No closing BTTS benchmark in the free source."}


def ah_scorecard(graded: pd.DataFrame) -> dict:
    if graded.empty or "ah_graded" not in graded: return {"live": False, "n": 0}
    done = graded[graded["ah_graded"]]; active = done.ah_weight.sum()
    if not active: return {"live": False, "n": 0}
    model = float(done.ah_model_loss.sum()/active); market = float(done.ah_market_loss.sum()/active)
    # Per-match losses are already stake-scaled sums, so divide back out to a
    # per-unit-stake difference before taking the interval, and weight by the
    # stake each match actually settled.
    weight = done.ah_weight.to_numpy(float)
    per_unit = np.divide((done.ah_model_loss - done.ah_market_loss).to_numpy(float),
                         np.where(weight > 0, weight, np.nan))
    interval = paired_interval(per_unit, weights=weight[np.isfinite(per_unit)])
    return {"live": True, "n": len(done), "stake_equiv": float(active), "model_log_loss": model,
            "market_log_loss": market, "gap": model-market, "beats_market": model < market,
            "se": interval["se"], "ci_low": interval["ci_low"],
            "ci_high": interval["ci_high"], "separated": interval["separated"]}


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
        interval = paired_interval(
            (block["model_loss"] - block["market_loss"]).to_numpy())
        rows.append({
            "week": week.date().isoformat(),
            "n": int(len(block)),
            "model": float(block["model_loss"].mean()),
            "market": float(block["market_loss"].mean()),
            "gap": float(block["model_loss"].mean() - block["market_loss"].mean()),
            "se": interval["se"],
            "separated": interval["separated"],
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
            # Which scorecard group this division belongs to, carried on the
            # row so the table can say it next to the number rather than
            # leaving a reader to match two tables by eye.
            "cohort": config.cohort_of(code),
            "cohort_label": config.cohort_label(config.cohort_of(code)),
        }
        if len(done):
            interval = paired_interval(
                (done["model_loss"] - done["market_loss"]).to_numpy())
            row.update({
                "model": float(done["model_loss"].mean()),
                "market": float(done["market_loss"].mean()),
                "gap": float((done["model_loss"] - done["market_loss"]).mean()),
                "accuracy": float(done["hit"].mean()) if "hit" in done else None,
                "se": interval["se"],
                "ci_low": interval["ci_low"],
                "ci_high": interval["ci_high"],
                "separated": interval["separated"],
                # Below this, the row is a number with an interval several
                # times its own size. Printing it to four decimals invites a
                # reader to cherry-pick the division where we happen to be
                # ahead, which at these samples is a coin landing heads.
                "enough": len(done) >= config.MIN_LEAGUE_ROWS,
            })
        else:
            row["enough"] = False
        rows.append(row)
    return sorted(rows, key=lambda r: order.get(r["league"], 99))


def by_cohort(graded: pd.DataFrame) -> list[dict]:
    """
    The headline, split into the groups defined in config.COHORTS.

    The pooled figure on the scorecard answers "how is the model doing". It
    stops answering that cleanly the moment the set of divisions underneath it
    changes, because a pooled log loss moves when the mix moves whether or not
    anything about the model has. Twelve thinner divisions joined on
    17 September 2026; from the first of them that grades, the pooled line is a
    composition change and a performance change added together.

    So the pooled line stays — it is the honest total, and dropping it would be
    its own kind of selection — and these two lines sit beside it so a reader
    can see which part is which. Each carries its own interval, because the
    whole point is defeated if the split is shown without the width that says
    whether the difference between the groups is real.

    Written before any match in the new group had been graded. See the note on
    config.FOUNDING_LEAGUES for why that timing is part of the claim.
    """
    if graded.empty or "graded" not in graded.columns:
        return []
    done_all = graded[graded["graded"]]
    rows = []
    for spec in config.COHORTS:
        codes = set(spec["codes"])
        block = graded[graded["league"].isin(codes)]
        done = done_all[done_all["league"].isin(codes)]
        divisions = [c for c in config.LEAGUE_ORDER
                     if c in codes and (block["league"] == c).any()]
        row = {
            "key": spec["key"],
            "label": spec["label"],
            "note": spec["note"],
            "codes": list(spec["codes"]),
            # Divisions in this group with a sealed prediction in the sample,
            # not divisions configured into it. A group whose divisions have
            # not started yet reports zero, and says so.
            "divisions": divisions,
            "n_divisions": len(divisions),
            "n": int(len(done)),
            "pending": int((~block["graded"]).sum()) if len(block) else 0,
        }
        if len(done):
            diff = (done["model_loss"] - done["market_loss"]).to_numpy()
            interval = paired_interval(diff)
            row.update({
                "model": float(done["model_loss"].mean()),
                "market": float(done["market_loss"].mean()),
                "gap": float(diff.mean()),
                "accuracy": float(done["hit"].mean()) if "hit" in done else None,
                "se": interval["se"],
                "ci_low": interval["ci_low"],
                "ci_high": interval["ci_high"],
                "t": interval["t"],
                "separated": interval["separated"],
                "enough": len(done) >= config.MIN_LEAGUE_ROWS,
            })
        else:
            row["enough"] = False
        rows.append(row)
    return rows



def coverage_by_league(leagues=None) -> list[dict]:
    """
    Of the matches PLAYED in each division since we started asking about it,
    how many did we actually seal a prediction for.

    This is the figure the scorecard cannot supply. The scorecard reports on
    matches we sealed; it is silent, by construction, about matches we never
    sealed at all, because a prediction that does not exist cannot appear in a
    table of predictions. So a division whose fixture feed goes dark simply
    contributes less, and the headline gap does not so much as flinch.

    That silence is not survivable for a measurement service. On 18 September
    the site listed 23 configured divisions and sealed 9, and the two numbers
    that would have told a reader so — matches played, matches sealed — were
    both already on disk. The direction of the join is the whole point: every
    other table starts from the ledger and asks what happened; this one starts
    from what happened and asks whether the ledger has it.

    Counted from the results files (the played side) and the chain (the sealed
    side), using `sealed_frame` so the join is the same one grading uses. The
    window opens at `requested_since`, which is a lower bound — see its
    docstring — so these figures can only flatter us. Both endpoints are
    reported so a reader can check the arithmetic rather than trust the ratio.
    """
    since = requested_since()
    wanted = list(dict.fromkeys(list(leagues or config.ENABLED_LEAGUES)
                                + sorted(since)))
    wanted = [lg for lg in wanted if lg in since]
    if not wanted:
        return []

    preds = sealed_frame()
    sealed_keys = set() if preds.empty else set(
        zip(preds["league"], preds["date"], preds["home"], preds["away"]))

    results = load_all_matches(wanted).rename(
        columns={"HomeTeam": "home", "AwayTeam": "away", "League": "league"})
    results["date"] = results["Date"].dt.normalize()
    results = results[results["FTR"].notna()]
    today = pd.Timestamp(dt.date.today())

    # A played match with no sealed prediction has TWO possible causes and they
    # need entirely different fixes, so the figure must not merge them:
    #
    #   nothing was sealed  — the fixture feed never offered the match. This is
    #                         the fdco staleness failure, and it is permanent:
    #                         the ledger is append-only.
    #   something was sealed but the NAME does not join — the prediction exists
    #                         and is unscoreable until data.OVERRIDES learns the
    #                         spelling, at which point it grades retroactively.
    #
    # Measured on 18 September this is not a footnote: of 13 played matches with
    # no joined prediction, 6 are unjoined names in BRA and N1 — divisions that
    # take fixtures from football-data.org and were never affected by the stale
    # CSV at all. Reporting those as "not sealed" would have pointed the reader
    # squarely at the wrong defect.
    result_keys = set(zip(results["league"], results["date"],
                          results["home"], results["away"]))
    cutoff = pd.Timestamp(dt.date.today()) - pd.Timedelta(days=3)
    unjoined_by_league: dict[str, int] = {}
    if not preds.empty:
        for lg, date, home, away in zip(preds["league"], preds["date"],
                                        preds["home"], preds["away"]):
            if date < cutoff and (lg, date, home, away) not in result_keys:
                unjoined_by_league[lg] = unjoined_by_league.get(lg, 0) + 1

    out = []
    for code in wanted:
        start = pd.Timestamp(since[code])
        sub = results[(results["league"] == code)
                      & (results["date"] >= start)
                      & (results["date"] <= today)]
        played = int(len(sub))
        sealed = int(sum(key in sealed_keys for key in
                         zip(sub["league"], sub["date"],
                             sub["home"], sub["away"])))
        gap = played - sealed
        unjoined = min(unjoined_by_league.get(code, 0), gap)
        out.append({
            "league": code,
            "name": config.league_name(code),
            "cohort": config.cohort_of(code),
            "since": since[code],
            "played": played,
            "sealed": sealed,
            "missed": gap,
            # The split. `unsealed` is the one that can never be repaired.
            "unjoined": unjoined,
            "unsealed": gap - unjoined,
            # None, not 100%, when nothing has been played yet. A division
            # added yesterday has no coverage record, and printing a perfect
            # score for it would be the most flattering possible reading of
            # having done nothing at all.
            "pct": (100.0 * sealed / played) if played else None,
            "source": "fdco" if config.LEAGUES.get(code, {}).get("fixtures") == "fdco"
                      else "fdorg",
        })
    return out


def coverage_summary(rows: list[dict]) -> dict:
    """The same thing pooled, for a one-line statement of the whole position."""
    played = sum(r["played"] for r in rows)
    sealed = sum(r["sealed"] for r in rows)
    scored = [r for r in rows if r["played"]]
    return {
        "played": played,
        "sealed": sealed,
        "missed": played - sealed,
        "unjoined": sum(r["unjoined"] for r in rows),
        "unsealed": sum(r["unsealed"] for r in rows),
        "pct": (100.0 * sealed / played) if played else None,
        "n_configured": len(rows),
        # Divisions that have had a match to seal at all. The gap between this
        # and n_configured is the sentence the site was not saying.
        "n_with_matches": len(scored),
        "n_complete": sum(1 for r in scored if r["missed"] == 0),
        "n_incomplete": sum(1 for r in scored if r["missed"] > 0),
    }

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
