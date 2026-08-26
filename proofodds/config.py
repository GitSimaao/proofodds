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
TEMPLATE_DIR = ROOT / "templates"
STATIC_DIR = ROOT / "static"

# --- brand -----------------------------------------------------------------
SITE_NAME = "ProofOdds"
SITE_URL = os.environ.get("PROOFODDS_URL", "https://proofodds.com")
SITE_TAGLINE = "Every prediction published before kickoff. Every score kept."
REPO_URL = os.environ.get("PROOFODDS_REPO", "https://github.com/yourname/proofodds")
CONTACT_EMAIL = os.environ.get("PROOFODDS_EMAIL", "hello@proofodds.com")

# --- leagues ---------------------------------------------------------------
# football-data.co.uk division codes. Phase 0 is one league on purpose:
# a scorecard for one league that is genuinely honest beats five that are rushed.
LEAGUES = {
    "E0": {
        "name": "Premier League",
        "country": "England",
        "fd_code": "E0",
        # football-data.org competition code, used for upcoming fixtures
        "fdorg_code": "PL",
    },
}

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
    "repo": "https://github.com/yourname/pl-dixon-coles",
}
