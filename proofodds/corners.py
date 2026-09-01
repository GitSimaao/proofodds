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


@dataclass
class CornerModel:
    teams: list[str]
    attack: np.ndarray
    defence: np.ndarray
    home_advantage: float
    level: float
    dispersion: float
    n_train: int

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


def fit_from_frame(frame, teams: list[str], prior_sd: float = 0.6) -> CornerModel:
    clean = frame.dropna(subset=["HC", "AC"]).copy()
    if len(clean) < 20:
        raise RuntimeError("not enough HC/AC history")
    lookup = {t: i for i, t in enumerate(teams)}
    hi = clean["HomeTeam"].map(lookup).to_numpy(int)
    ai = clean["AwayTeam"].map(lookup).to_numpy(int)
    hc = clean["HC"].to_numpy(float); ac = clean["AC"].to_numpy(float)
    n = len(teams); prec = 1.0 / prior_sd ** 2

    def objective(theta):
        attack = theta[:n]; defence = theta[n:2*n]
        ha, level = theta[-2:]
        lh = level + ha + attack[hi] + defence[ai]
        la = level + attack[ai] + defence[hi]
        mh, ma = np.exp(lh), np.exp(la)
        loss = np.sum(mh - hc*lh + ma - ac*la)
        return loss + .5 * prec * (attack@attack + defence@defence)

    theta = np.zeros(2*n + 2); theta[-1] = np.log(max((hc.mean()+ac.mean())/2, .1))
    result = minimize(objective, theta, method="L-BFGS-B", options={"maxiter": 300})
    attack = result.x[:n]; defence = result.x[n:2*n]
    ha, level = result.x[-2:]
    mh = np.exp(level + ha + attack[hi] + defence[ai])
    ma = np.exp(level + attack[ai] + defence[hi])
    y = np.r_[hc, ac]; mu = np.r_[mh, ma]
    dispersion = float(np.clip(np.sum((y-mu)**2-mu) / max(np.sum(mu**2), 1), .02, 1.0))
    return CornerModel(teams, attack, defence, float(ha), float(level), dispersion, len(clean))
