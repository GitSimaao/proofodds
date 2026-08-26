"""
Central configuration. Everything tunable lives here or in the environment.

Nothing in this file is a secret. Real secrets (API keys) come from the
environment, so the repository can stay public — which it must, because the
public repository is half the credibility argument.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = ROOT / "data"
PREDICTIONS_DIR = ROOT / "predictions"
SITE_DIR = ROOT / "site"
OUTPUT_DIR = ROOT / "outputs"
TEMPLATE_DIR = ROOT / "templates"
STATIC_DIR = ROOT / "static"

# --- brand -----------------------------------------------------------------
SITE_NAME = "ProofOdds"
SITE_URL = os.environ.get("PROOFODDS_URL", "https://proofodds.com")
SITE_TAGLINE = "Every prediction published before kickoff. Every score kept."
REPO_URL = os.environ.get("PROOFODDS_REPO", "https://github.com/GitSimaao/proofodds")
CONTACT_EMAIL = os.environ.get("PROOFODDS_EMAIL", "hello@proofodds.com")

# --- newsletter -------------------------------------------------------------
# The signup box only renders when there is somewhere for it to post. Leave
# this unset and the site simply has no form — better than a box that swallows
# addresses into nothing.
#
# Get it from Kit: Grow -> Landing Pages & Forms -> your form -> Embed -> HTML.
# The action looks like https://app.kit.com/forms/1234567/subscriptions
SIGNUP_ACTION = os.environ.get("PROOFODDS_SIGNUP_ACTION", "")
KIT_API_KEY = os.environ.get("PROOFODDS_KIT_API_KEY", "")

# Minutes between creating the broadcast and Kit sending it. A small delay is
# a free undo: if the Monday summary looks wrong, you can still kill it in Kit.
NEWSLETTER_DELAY_MIN = int(os.environ.get("PROOFODDS_NEWSLETTER_DELAY_MIN", "15"))

# Who is responsible for the personal data, for the privacy page.
DATA_CONTROLLER = os.environ.get("PROOFODDS_CONTROLLER", "the operator of ProofOdds")

# --- leagues ---------------------------------------------------------------
# Keys are football-data.co.uk division codes; `fdorg` is the competition code
# football-data.org uses for the same division on its free tier. Both sides
# matter: the first is where results and Pinnacle closing prices come from, the
# second is where next weekend's fixtures come from, and a division is only
# usable when BOTH cover it. That is why the Champions League is absent — the
# fixtures are available, the closing prices are not, so it could be predicted
# but never scored, and an unscoreable prediction is the one thing this site
# does not publish.
LEAGUES = {
    "E0":  {"name": "Premier League", "short": "PL",   "country": "England",  "fdorg": "PL",  "tier": 1},
    "E1":  {"name": "Championship",   "short": "EFL",  "country": "England",  "fdorg": "ELC", "tier": 2},
    "SP1": {"name": "La Liga",        "short": "LIGA", "country": "Spain",    "fdorg": "PD",  "tier": 1},
    "I1":  {"name": "Serie A",        "short": "SA",   "country": "Italy",    "fdorg": "SA",  "tier": 1},
    "D1":  {"name": "Bundesliga",     "short": "BUN",  "country": "Germany",  "fdorg": "BL1", "tier": 1},
    "F1":  {"name": "Ligue 1",        "short": "L1",   "country": "France",   "fdorg": "FL1", "tier": 1},
    "P1":  {"name": "Primeira Liga",  "short": "LPT",  "country": "Portugal", "fdorg": "PPL", "tier": 1},
}

# Which of them are actually live. The default is deliberately just one: a
# division goes live when somebody has read `scripts/check_names.py` and seen
# its club names resolve, not when somebody has deployed. Adding a league is
# one line; a league whose names do not join seals predictions that can never
# be graded, and the ledger is never rewritten.
#
#   PROOFODDS_LEAGUES=E0,E1,SP1,I1,D1,F1,P1
ENABLED_LEAGUES = [c.strip().upper() for c in os.environ.get(
    "PROOFODDS_LEAGUES", "E0").split(",") if c.strip()]
ENABLED_LEAGUES = [c for c in ENABLED_LEAGUES if c in LEAGUES] or ["E0"]

# Display order for the site, independent of which are enabled.
LEAGUE_ORDER = list(LEAGUES)


def league_name(code: str) -> str:
    return LEAGUES.get(code, {}).get("name", code)


# Seasons to download, oldest first. "2526" means 2025/26.
SEASONS = ["1516", "1617", "1718", "1819", "1920", "2021",
           "2122", "2223", "2324", "2425", "2526", "2627"]

FOOTBALL_DATA_BASE = "https://www.football-data.co.uk/mmz4281"

# --- model -----------------------------------------------------------------
# Tuned by grid search on 2017/18-2020/21 only. See the research repo.
XI = 0.002            # time decay, 1/day -> half-life 347 days
PRIOR_SD = 0.6        # gaussian prior on team ratings
MAX_GOALS = 10

# Odds provider for pre-kickoff market comparison. Optional in phase 0:
# grading uses football-data.co.uk closing prices, which are free.
ODDS_PROVIDER = os.environ.get("PROOFODDS_ODDS_PROVIDER", "none")  # none|theoddsapi
ODDS_API_KEY = os.environ.get("PROOFODDS_ODDS_API_KEY", "")

# Fixtures provider. football-data.org has a free tier that covers the PL.
FIXTURES_PROVIDER = os.environ.get("PROOFODDS_FIXTURES_PROVIDER", "auto")  # auto|fdorg|csv
FDORG_TOKEN = os.environ.get("PROOFODDS_FDORG_TOKEN", "")

# A club with fewer than this many matches in the training window is priced at
# (or near) league average. It still gets a prediction — silently dropping a
# fixture would be worse — but the ledger and the site flag it.
COLD_START_MATCHES = 6

# How many days ahead to publish predictions for.
LOOKAHEAD_DAYS = int(os.environ.get("PROOFODDS_LOOKAHEAD_DAYS", "8"))

# Predictions before this date are excluded from the public scorecard: the
# model needs history before it can say anything. Two seasons of burn-in.
SCORECARD_START = "2017-08-01"

UNIFORM_LOG_LOSS = 1.0986122886681098   # -log(1/3)

# --- the prior --------------------------------------------------------------
# Walk-forward backtest of this exact model, 2017/18-2025/26, reproducible from
# the research repository. This is a BACKTEST and the site must always label it
# as one: it is not the live record, and it never appears on the scorecard page.
BACKTEST = {
    "period": "2017/18 – 2025/26",
    "n": 3250,
    "model_log_loss": 0.9654,
    "market_log_loss": 0.9484,
    "gap": 0.0170,
    "model_accuracy": 0.543,
    "market_accuracy": 0.554,
    "test_n": 1730,
    "test_model": 0.9693,
    "test_market": 0.9468,
    "repo": "https://github.com/GitSimaao/pl-dixon-coles",
}
