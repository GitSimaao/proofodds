"""
The Dixon-Coles model (Dixon & Coles, 1997, *Applied Statistics* 46(2), 265-280).

The idea in one paragraph
-------------------------
Every team gets an attack rating and a defence rating. A home team's expected
goals is its attack times the opponent's defence times a league-wide home
advantage. Feed those two expected-goal numbers into two Poisson distributions,
multiply them into a grid of scorelines, and sum the grid into P(home), P(draw),
P(away). Dixon and Coles add two things to that: a correction that fixes the
Poisson model's known failure on 0-0 / 1-0 / 0-1 / 1-1, and an exponential time
decay so that a match from 2016 barely counts today.

Parameterisation
----------------
Everything lives on the log scale, which keeps the optimiser unconstrained and
makes the multiplicative story exact:

    log lambda_home = mu + h + a[home] + d[away]
    log lambda_away = mu +     a[away] + d[home]

    mu = league scoring level,  h = home advantage,
    a  = attack (higher = scores more),  d = defence (higher = concedes more).

Exponentiating recovers the textbook form
`lambda_home = alpha_home * beta_away * gamma * league_mean`.

Attack and defence are only identified up to a common shift, so we add a
zero-mean Gaussian prior (a ridge penalty) on both. That does two jobs at once:
it pins the parameters down, and it gives newly promoted teams -- which have no
Premier League history at all -- a sensible starting point at league average
that then shrinks towards their real level as matches arrive.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import minimize
from scipy.stats import poisson

MAX_GOALS = 10          # scoreline grid size; P(11+ goals for one side) is ~1e-7
_TAU_FLOOR = 1e-10      # keeps log(tau) finite if rho wanders too far


# --------------------------------------------------------------------------- #
#  The low-score correction
# --------------------------------------------------------------------------- #
def tau(home_goals, away_goals, lam, mu, rho):
    """
    Dixon-Coles' `tau`: a multiplier applied to four scorelines only.

        tau(0,0) = 1 - lam*mu*rho     tau(0,1) = 1 + lam*rho
        tau(1,0) = 1 + mu*rho         tau(1,1) = 1 - rho
        tau(x,y) = 1  everywhere else

    Independent Poissons say the two teams' goals are unrelated. They are not:
    in real football 0-0 and 1-1 happen more often than the maths predicts and
    1-0 / 0-1 less often, because a game that is level or tightly poised is
    played differently from one that is not. A negative `rho` (the usual
    estimate is about -0.1) inflates the two level scorelines and deflates the
    two one-goal ones.

    Note this is a proper probability distribution -- the four adjustments
    cancel exactly, so the grid still sums to one.
    """
    hg = np.asarray(home_goals)
    ag = np.asarray(away_goals)
    out = np.ones(np.broadcast(hg, ag, lam, mu).shape, dtype=float)

    m00 = (hg == 0) & (ag == 0)
    m01 = (hg == 0) & (ag == 1)
    m10 = (hg == 1) & (ag == 0)
    m11 = (hg == 1) & (ag == 1)

    lam_b, mu_b = np.broadcast_arrays(np.asarray(lam, dtype=float),
                                      np.asarray(mu, dtype=float))
    lam_b = np.broadcast_to(lam_b, out.shape)
    mu_b = np.broadcast_to(mu_b, out.shape)

    out = np.where(m00, 1.0 - lam_b * mu_b * rho, out)
    out = np.where(m01, 1.0 + lam_b * rho, out)
    out = np.where(m10, 1.0 + mu_b * rho, out)
    out = np.where(m11, 1.0 - rho, out)
    return np.maximum(out, _TAU_FLOOR)


def score_matrix_from_xg(lam: float, mu: float, rho: float,
                         max_goals: int = MAX_GOALS) -> np.ndarray:
    """A Dixon-Coles score grid when the two expected-goal values are known."""
    lam, mu, rho = float(lam), float(mu), float(rho)
    goals = np.arange(max_goals + 1)
    grid = np.outer(poisson.pmf(goals, lam), poisson.pmf(goals, mu))
    grid *= tau(goals[:, None], goals[None, :], lam, mu, rho)
    return grid / grid.sum()


def time_weights(match_dates, ref_date, xi: float) -> np.ndarray:
    """
    Exponential decay: weight = exp(-xi * days_before_ref).

    `xi` is in units of 1/day. A useful way to read it: the half-life is
    ln(2)/xi days, so xi = 0.0019 means a match counts half as much as a fresh
    one after roughly one year.
    """
    age_days = (np.datetime64(ref_date) - np.asarray(match_dates, dtype="datetime64[D]"))
    age_days = age_days.astype("timedelta64[D]").astype(float)
    return np.exp(-xi * np.maximum(age_days, 0.0))


# --------------------------------------------------------------------------- #
#  Objective (negative penalised log-likelihood) and its analytic gradient
# --------------------------------------------------------------------------- #
def _objective(theta, home_id, away_id, hg, ag, w, n_teams, prior_prec):
    """
    Returns (negative weighted log-likelihood + ridge penalty, gradient).

    Supplying the gradient rather than letting scipy difference it numerically
    is what makes the walk-forward backtest feasible: we refit the model on
    every single match date, roughly a thousand times per run.
    """
    a = theta[:n_teams]
    d = theta[n_teams:2 * n_teams]
    home_adv, level, rho = theta[2 * n_teams], theta[2 * n_teams + 1], theta[2 * n_teams + 2]

    log_lam = level + home_adv + a[home_id] + d[away_id]
    log_mu = level + a[away_id] + d[home_id]
    lam = np.exp(log_lam)
    mu = np.exp(log_mu)

    # --- tau and its derivatives, computed only on the four special cells ---
    t = np.ones_like(lam)
    m00 = (hg == 0) & (ag == 0)
    m01 = (hg == 0) & (ag == 1)
    m10 = (hg == 1) & (ag == 0)
    m11 = (hg == 1) & (ag == 1)
    t[m00] = 1.0 - lam[m00] * mu[m00] * rho
    t[m01] = 1.0 + lam[m01] * rho
    t[m10] = 1.0 + mu[m10] * rho
    t[m11] = 1.0 - rho
    t = np.maximum(t, _TAU_FLOOR)

    dlogt_dloglam = np.zeros_like(lam)
    dlogt_dlogmu = np.zeros_like(lam)
    dlogt_drho = np.zeros_like(lam)

    dlogt_dloglam[m00] = -lam[m00] * mu[m00] * rho / t[m00]
    dlogt_dlogmu[m00] = dlogt_dloglam[m00]
    dlogt_drho[m00] = -lam[m00] * mu[m00] / t[m00]

    dlogt_dloglam[m01] = lam[m01] * rho / t[m01]
    dlogt_drho[m01] = lam[m01] / t[m01]

    dlogt_dlogmu[m10] = mu[m10] * rho / t[m10]
    dlogt_drho[m10] = mu[m10] / t[m10]

    dlogt_drho[m11] = -1.0 / t[m11]

    # --- log-likelihood (Poisson terms drop the constant -log(x!)) ---
    ll = np.sum(w * (hg * log_lam - lam + ag * log_mu - mu + np.log(t)))
    penalty = 0.5 * prior_prec * (a @ a + d @ d)

    # --- gradient: d(ll)/d(log lambda) and d(ll)/d(log mu) per match ---
    g_home = w * (hg - lam + dlogt_dloglam)
    g_away = w * (ag - mu + dlogt_dlogmu)

    grad = np.zeros_like(theta)
    # attack enters log_lam through the home team and log_mu through the away team
    grad[:n_teams] = (np.bincount(home_id, g_home, n_teams)
                      + np.bincount(away_id, g_away, n_teams))
    # defence is the mirror image
    grad[n_teams:2 * n_teams] = (np.bincount(away_id, g_home, n_teams)
                                 + np.bincount(home_id, g_away, n_teams))
    grad[2 * n_teams] = g_home.sum()                       # home advantage
    grad[2 * n_teams + 1] = g_home.sum() + g_away.sum()    # league level
    grad[2 * n_teams + 2] = np.sum(w * dlogt_drho)         # rho

    grad = -grad
    grad[:n_teams] += prior_prec * a
    grad[n_teams:2 * n_teams] += prior_prec * d

    return -ll + penalty, grad


# --------------------------------------------------------------------------- #
#  The fitted model
# --------------------------------------------------------------------------- #
@dataclass
class DixonColes:
    """A fitted model. Everything you need to score a fixture lives here."""

    teams: list[str]
    attack: np.ndarray          # log scale, one per team
    defence: np.ndarray         # log scale, higher = leakier
    home_advantage: float       # log scale
    level: float                # log scale, league goal level
    rho: float
    xi: float
    prior_sd: float
    ref_date: np.datetime64 | None = None
    n_train: int = 0
    effective_n: float = 0.0
    converged: bool = True
    theta: np.ndarray = field(default=None, repr=False)

    # -- readable, multiplicative versions of the parameters -----------------
    @property
    def alpha(self) -> np.ndarray:
        """Attack multiplier; 1.0 is league average."""
        return np.exp(self.attack - self.attack.mean())

    @property
    def beta(self) -> np.ndarray:
        """Defence multiplier; below 1.0 means concedes less than average."""
        return np.exp(self.defence - self.defence.mean())

    @property
    def gamma(self) -> float:
        """Home advantage as a multiplier on expected goals (~1.2-1.4)."""
        return float(np.exp(self.home_advantage))

    @property
    def league_mean(self) -> float:
        """Goals per team per game for an average side away from home."""
        return float(np.exp(self.level + self.attack.mean() + self.defence.mean()))

    # -- prediction ----------------------------------------------------------
    def expected_goals(self, home_id, away_id):
        """The two lambdas for a fixture (or arrays of fixtures)."""
        home_id = np.asarray(home_id)
        away_id = np.asarray(away_id)
        lam = np.exp(self.level + self.home_advantage
                     + self.attack[home_id] + self.defence[away_id])
        mu = np.exp(self.level + self.attack[away_id] + self.defence[home_id])
        return lam, mu

    def score_matrix(self, home_id, away_id, max_goals: int = MAX_GOALS) -> np.ndarray:
        """
        The (max_goals+1) x (max_goals+1) grid of scoreline probabilities.

        Row = home goals, column = away goals. Cell (2, 1) is P(2-1).
        """
        lam, mu = self.expected_goals(int(home_id), int(away_id))
        lam, mu = float(lam), float(mu)
        return score_matrix_from_xg(lam, mu, self.rho, max_goals)

    def outcome_probs(self, home_id, away_id, max_goals: int = MAX_GOALS) -> np.ndarray:
        """[P(home win), P(draw), P(away win)] for one fixture."""
        grid = self.score_matrix(home_id, away_id, max_goals)
        draw = float(np.trace(grid))
        home = float(np.tril(grid, -1).sum())      # home goals > away goals
        away = float(np.triu(grid, 1).sum())
        return np.array([home, draw, away])

    def totals_probs(self, home_id, away_id, line: float = 2.5,
                     max_goals: int = MAX_GOALS) -> np.ndarray:
        """
        [P(over the line), P(under)] for total goals in the match.

        Nothing new is estimated here. The scoreline grid already contains the
        whole distribution; this reads a different sum out of it, which is why
        a second market costs no extra modelling and carries exactly the same
        assumptions — including the low-score correction, which matters more
        for totals than for 1X2 because it moves 0-0 and 1-1 specifically.

        The line is a half-goal, so no match can push: every result is one side
        or the other. Under is summed and over taken as the remainder, so the
        two published numbers add to exactly one.
        """
        grid = self.score_matrix(home_id, away_id, max_goals)
        goals = np.arange(max_goals + 1)
        total = goals[:, None] + goals[None, :]
        under = float(grid[total < line].sum())
        return np.array([1.0 - under, under])

    def predict(self, home_ids, away_ids, max_goals: int = MAX_GOALS) -> np.ndarray:
        """
        1X2 probabilities for many fixtures at once, shape (n, 3).

        Vectorised over fixtures -- this is called for every match in the
        backtest, so the per-fixture Python loop is worth avoiding.
        """
        lam, mu = self.expected_goals(np.asarray(home_ids), np.asarray(away_ids))
        goals = np.arange(max_goals + 1)

        p_home = poisson.pmf(goals[None, :], lam[:, None])      # (n, G)
        p_away = poisson.pmf(goals[None, :], mu[:, None])
        grid = p_home[:, :, None] * p_away[:, None, :]          # (n, G, G)

        adj = tau(goals[None, :, None], goals[None, None, :],
                  lam[:, None, None], mu[:, None, None], self.rho)
        grid = grid * adj
        grid /= grid.sum(axis=(1, 2), keepdims=True)

        eye = np.eye(max_goals + 1, dtype=bool)
        lower = np.tril(np.ones((max_goals + 1, max_goals + 1), dtype=bool), -1)
        upper = np.triu(np.ones((max_goals + 1, max_goals + 1), dtype=bool), 1)

        draw = grid[:, eye].sum(axis=1)
        home = grid[:, lower].sum(axis=1)
        away = grid[:, upper].sum(axis=1)
        return np.column_stack([home, draw, away])

    def sample_scores(self, home_ids, away_ids, rng=None, max_goals: int = MAX_GOALS):
        """
        Draw scorelines from the fitted distribution.

        Used by the tests to check the fitter can recover parameters it was
        given, and the building block for Monte-Carlo season simulations
        (title odds, top-four odds, relegation odds).
        """
        rng = np.random.default_rng() if rng is None else rng
        home_ids = np.asarray(home_ids)
        away_ids = np.asarray(away_ids)

        lam, mu = self.expected_goals(home_ids, away_ids)
        goals = np.arange(max_goals + 1)
        grid = (poisson.pmf(goals[None, :], lam[:, None])[:, :, None]
                * poisson.pmf(goals[None, :], mu[:, None])[:, None, :])
        grid = grid * tau(goals[None, :, None], goals[None, None, :],
                          lam[:, None, None], mu[:, None, None], self.rho)
        flat = grid.reshape(len(home_ids), -1)
        flat /= flat.sum(axis=1, keepdims=True)

        # inverse-CDF sampling, one uniform per fixture
        cdf = np.cumsum(flat, axis=1)
        picks = (cdf < rng.random((len(home_ids), 1))).sum(axis=1)
        picks = np.clip(picks, 0, flat.shape[1] - 1)
        return picks // (max_goals + 1), picks % (max_goals + 1)

    def ratings_table(self):
        """Attack / defence / expected goals per team, as a tidy DataFrame."""
        import pandas as pd
        return pd.DataFrame({
            "team": self.teams,
            "attack": self.alpha,
            "defence": self.beta,
        }).sort_values("attack", ascending=False).reset_index(drop=True)


# --------------------------------------------------------------------------- #
#  Fitting
# --------------------------------------------------------------------------- #
def fit(
    home_id: np.ndarray,
    away_id: np.ndarray,
    home_goals: np.ndarray,
    away_goals: np.ndarray,
    teams: list[str],
    match_dates=None,
    ref_date=None,
    xi: float = 0.0018,
    prior_sd: float = 0.35,
    init: np.ndarray | None = None,
    maxiter: int = 500,
) -> DixonColes:
    """
    Maximum penalised likelihood fit.

    The 2*n_teams + 3 parameters are left as unknowns and we search for the
    combination that makes the observed results most likely -- weighted so that
    recent matches count more. Nothing here knows anything about football; it is
    numbers being pushed around until the data stops being surprising.

    `init` warm-starts the optimiser from a previous fit, which is what makes
    refitting on ~1000 successive dates cheap.
    """
    n = len(teams)
    home_id = np.asarray(home_id, dtype=np.intp)
    away_id = np.asarray(away_id, dtype=np.intp)
    hg = np.asarray(home_goals, dtype=float)
    ag = np.asarray(away_goals, dtype=float)

    if match_dates is None or ref_date is None:
        w = np.ones(len(hg))
    else:
        w = time_weights(match_dates, ref_date, xi)

    prior_prec = 1.0 / (prior_sd ** 2)

    if init is None:
        theta0 = np.zeros(2 * n + 3)
        theta0[2 * n] = 0.25                                    # home adv ~ x1.28
        theta0[2 * n + 1] = np.log(max(hg.mean(), 0.2)) if len(hg) else 0.2
        theta0[2 * n + 2] = -0.05                               # rho
    else:
        theta0 = np.asarray(init, dtype=float).copy()

    bounds = [(-2.5, 2.5)] * (2 * n) + [(-1.0, 1.5), (-2.5, 1.5), (-0.25, 0.25)]

    res = minimize(
        _objective,
        theta0,
        args=(home_id, away_id, hg, ag, w, n, prior_prec),
        jac=True,
        method="L-BFGS-B",
        bounds=bounds,
        options={"maxiter": maxiter, "ftol": 1e-11, "gtol": 1e-8},
    )
    theta = res.x

    return DixonColes(
        teams=list(teams),
        attack=theta[:n].copy(),
        defence=theta[n:2 * n].copy(),
        home_advantage=float(theta[2 * n]),
        level=float(theta[2 * n + 1]),
        rho=float(theta[2 * n + 2]),
        xi=xi,
        prior_sd=prior_sd,
        ref_date=ref_date,
        n_train=int(len(hg)),
        effective_n=float(w.sum()),
        converged=bool(res.success),
        theta=theta,
    )


def fit_from_frame(matches, teams, ref_date=None, xi=0.0018, prior_sd=0.35,
                   init=None) -> DixonColes:
    """Convenience wrapper around `fit` for a DataFrame of matches."""
    return fit(
        home_id=matches["home_id"].to_numpy(),
        away_id=matches["away_id"].to_numpy(),
        home_goals=matches["FTHG"].to_numpy(),
        away_goals=matches["FTAG"].to_numpy(),
        teams=teams,
        match_dates=matches["Date"].to_numpy(),
        ref_date=ref_date,
        xi=xi,
        prior_sd=prior_sd,
        init=init,
    )
