"""Experimental, results-only corner model.

The model consumes only football-data.co.uk's final home/away corner counts
(`HC`, `AC`).  It deliberately does not ingest odds: Corners Lab measures
forecast quality, not a claimed betting edge.
"""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np
from scipy.optimize import minimize
from scipy.stats import nbinom

from .dixon_coles import time_weights


@dataclass
class CornerModel:
    teams: list[str]
    attack: np.ndarray
    defence: np.ndarray
    home_advantage: float
    level: float
    dispersion: float
    n_train: int
    #: Time-weighted row count: `sum(exp(-xi * age_days))` over the rows the
    #: fit actually saw. `n_train` counts rows, this counts evidence, and in a
    #: division whose corner coverage has a hole the two are nothing alike —
    #: the National League's 648 usable rows are 92 effective ones because 552
    #: of them are from 2015/16. Every gate on corner history counts THIS.
    n_effective: float = 0.0

    def expected(self, home_id: int, away_id: int) -> tuple[float, float]:
        h = np.exp(self.level + self.home_advantage + self.attack[home_id] + self.defence[away_id])
        a = np.exp(self.level + self.attack[away_id] + self.defence[home_id])
        return float(h), float(a)

    def total_pmf(self, home_id: int, away_id: int, maximum: int = 30) -> np.ndarray:
        h, a = self.expected(home_id, away_id)
        alpha = max(self.dispersion, 1e-6)
        def pmf(mu):
            size = 1.0 / alpha
            return nbinom.pmf(np.arange(maximum + 1), size, size / (size + mu))
        out = np.convolve(pmf(h), pmf(a))[:maximum + 1]
        out[-1] += max(0.0, 1.0 - out.sum())
        return out / out.sum()

    def totals(self, home_id: int, away_id: int, lines) -> list[dict]:
        pmf = self.total_pmf(home_id, away_id)
        return [{"line": float(line), "p_over": float(pmf[np.arange(len(pmf)) > line].sum()),
                 "p_under": float(pmf[np.arange(len(pmf)) < line].sum())} for line in lines]


def fit_from_frame(frame, teams: list[str], prior_sd: float = 0.6,
                   ref_date=None, xi: float = 0.0) -> CornerModel:
    """
    Fit the corner model, decaying each row by its age.

    `xi` is the decay in 1/day and the weight on a row is exp(-xi * age), the
    same shape `dixon_coles.time_weights` applies to goals. Until 18 September
    2026 this function applied none: a corner count from 2015/16 counted
    exactly as much as one from last week, in every division, and the models
    sealed into every entry since 28 August 2026 are unweighted ones. That was
    survivable while corners were sealed and hidden. It is not survivable on a
    card, so the weights are here and `ledger` passes XI.

    `xi=0.0` reproduces the old unweighted fit exactly, which is what the
    before/after test compares against — the default is 0.0 for that reason
    and callers that mean to decay have to say so.
    """
    clean = frame.dropna(subset=["HC", "AC"]).copy()
    if len(clean) < 20:
        raise RuntimeError("not enough HC/AC history")
    lookup = {t: i for i, t in enumerate(teams)}
    hi = clean["HomeTeam"].map(lookup).to_numpy(int)
    ai = clean["AwayTeam"].map(lookup).to_numpy(int)
    hc = clean["HC"].to_numpy(float); ac = clean["AC"].to_numpy(float)
    n = len(teams); prec = 1.0 / prior_sd ** 2

    if xi and ref_date is not None:
        w = time_weights(clean["Date"].to_numpy(), ref_date, xi)
    else:
        w = np.ones(len(clean))
    n_effective = float(w.sum())

    def objective(theta):
        attack = theta[:n]; defence = theta[n:2*n]
        ha, level = theta[-2:]
        lh = level + ha + attack[hi] + defence[ai]
        la = level + attack[ai] + defence[hi]
        mh, ma = np.exp(lh), np.exp(la)
        loss = np.sum(w * (mh - hc*lh) + w * (ma - ac*la))
        return loss + .5 * prec * (attack@attack + defence@defence)

    theta = np.zeros(2*n + 2)
    # The starting level is the weighted mean corner count, so a division whose
    # old rows say something different from its recent ones starts from the
    # recent ones rather than being walked there by the optimiser.
    theta[-1] = np.log(max(float(np.average(np.r_[hc, ac], weights=np.r_[w, w])) , .1))
    result = minimize(objective, theta, method="L-BFGS-B", options={"maxiter": 300})
    attack = result.x[:n]; defence = result.x[n:2*n]
    ha, level = result.x[-2:]
    mh = np.exp(level + ha + attack[hi] + defence[ai])
    ma = np.exp(level + attack[ai] + defence[hi])
    y = np.r_[hc, ac]; mu = np.r_[mh, ma]; ww = np.r_[w, w]
    # Weighted too: a dispersion read off 2015/16 residuals describes the
    # spread of a division that no longer exists.
    dispersion = float(np.clip(
        np.sum(ww * ((y-mu)**2 - mu)) / max(np.sum(ww * mu**2), 1), .02, 1.0))
    return CornerModel(teams, attack, defence, float(ha), float(level),
                       dispersion, len(clean), n_effective)


def effective_rows(frame, ref_date, xi: float) -> float:
    """Time-weighted count of the rows in `frame` that carry HC and AC."""
    if not {"HC", "AC"}.issubset(frame.columns):
        return 0.0
    clean = frame.dropna(subset=["HC", "AC"])
    if clean.empty:
        return 0.0
    return float(time_weights(clean["Date"].to_numpy(), ref_date, xi).sum())


def effective_appearances(frame, ref_date, xi: float) -> dict[str, float]:
    """
    Per club, the time-weighted number of matches with a corner count.

    A club appears once per match it played, home or away, and its weight is
    the same exp(-xi * age) the fit uses. This is the quantity the display gate
    is measured on: a division's corner ratings are only as good as the recent
    corner history of the clubs being priced, and a division-wide row count
    hides a club that has none.
    """
    if not {"HC", "AC"}.issubset(frame.columns):
        return {}
    clean = frame.dropna(subset=["HC", "AC"])
    if clean.empty:
        return {}
    w = time_weights(clean["Date"].to_numpy(), ref_date, xi)
    out: dict[str, float] = {}
    for column in ("HomeTeam", "AwayTeam"):
        for name, weight in zip(clean[column], w):
            out[name] = out.get(name, 0.0) + float(weight)
    return out
