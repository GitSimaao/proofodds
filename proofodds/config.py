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
# Guest chains — other people's predictions sealed under the same hash rule,
# one directory per guest. Empty (or absent) until the first guest exists.
GUESTS_DIR = ROOT / "guests"
TIMESTAMPS_DIR = ROOT / "timestamps"
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
# matter: the first is where results and closing prices come from, the
# second is where next weekend's fixtures come from, and a division is only
# usable when BOTH cover it. That is why the Champions League is absent — the
# fixtures are available, the closing prices are not, so it could be predicted
# but never scored, and an unscoreable prediction is the one thing this site
# does not publish.
LEAGUES = {
    "E0":  {"name": "Premier League", "short": "PL",   "country": "England",  "flag": "england",     "fdorg": "PL",  "tier": 1},
    "E1":  {"name": "Championship",   "short": "EFL",  "country": "England",  "flag": "england",     "fdorg": "ELC", "tier": 2},
    "SP1": {"name": "La Liga",        "short": "LIGA", "country": "Spain",    "flag": "spain",       "fdorg": "PD",  "tier": 1},
    "I1":  {"name": "Serie A",        "short": "SA",   "country": "Italy",    "flag": "italy",       "fdorg": "SA",  "tier": 1},
    "D1":  {"name": "Bundesliga",     "short": "BUN",  "country": "Germany",  "flag": "germany",     "fdorg": "BL1", "tier": 1},
    "F1":  {"name": "Ligue 1",        "short": "L1",   "country": "France",   "flag": "france",      "fdorg": "FL1", "tier": 1},
    "P1":  {"name": "Primeira Liga",  "short": "LPT",  "country": "Portugal", "flag": "portugal",    "fdorg": "PPL", "tier": 1},
    "N1":  {"name": "Eredivisie",     "short": "ERE",  "country": "Netherlands", "flag": "netherlands", "fdorg": "DED", "tier": 1},
    # Belgium is not in football-data.org's free-tier competition set.  The
    # Football-Data fixture CSV carries B1, so this division can still use a
    # single auditable source for upcoming names and closing prices.
    "B1":  {"name": "Jupiler Pro League", "short": "JPL", "country": "Belgium", "flag": "belgium", "fdorg": None, "tier": 1, "source": "season", "fixtures": "fdco"},
    "SC0": {"name": "Scottish Premiership", "short": "SPL", "country": "Scotland", "flag": "scotland", "fdorg": None, "tier": 1, "source": "season", "fixtures": "fdco"},
    "BRA": {"name": "Brasileirao Serie A", "short": "BRA", "country": "Brazil", "flag": "brazil", "fdorg": "BSA", "tier": 1, "source": "extra", "fixtures": "fdorg"},
}

# Which of them are actually live. The default is deliberately just one: a
# division goes live when somebody has read `scripts/check_names.py` and seen
# its club names resolve, not when somebody has deployed. Adding a league is
# one line; a league whose names do not join seals predictions that can never
# be graded, and the ledger is never rewritten.
#
#   PROOFODDS_LEAGUES=E0,E1,SP1,I1,D1,F1,P1,N1,B1,SC0,BRA
ENABLED_LEAGUES = [c.strip().upper() for c in os.environ.get(
    "PROOFODDS_LEAGUES", "E0").split(",") if c.strip()]
ENABLED_LEAGUES = [c for c in ENABLED_LEAGUES if c in LEAGUES] or ["E0"]

# Display order for the site, independent of which are enabled.
LEAGUE_ORDER = list(LEAGUES)


def league_name(code: str) -> str:
    return LEAGUES.get(code, {}).get("name", code)


# --- creator ledger coverage -----------------------------------------------
# The Dixon-Coles model above deliberately stays separate from the creator
# ledger: each configured model division gets its own fit, while the creator
# ledger can cover every measurable competition football-data.co.uk publishes.
# Adding a competition here means fitting, validating and publishing another
# model.  The creator ledger has a different job.  It only needs a result and
# a market-average closing price for the exact selection that was sealed, so
# it can cover every competition football-data.co.uk currently publishes.
#
# The two source families do not carry the same markets.  The season-by-season
# European files publish a closing 1X2, O/U 2.5 and one main Asian-handicap
# line.  The "new leagues" files publish a closing 1X2 only.  This registry is
# therefore also the permission boundary: a market is never accepted merely
# because we know how to settle it; it must have a closing benchmark here.
_GUEST_EUROPE = {
    "E0":  ("Premier League", "England"),
    "E1":  ("Championship", "England"),
    "E2":  ("League One", "England"),
    "E3":  ("League Two", "England"),
    "EC":  ("National League", "England"),
    "SC0": ("Scottish Premiership", "Scotland"),
    "SC1": ("Scottish Championship", "Scotland"),
    "SC2": ("Scottish League One", "Scotland"),
    "SC3": ("Scottish League Two", "Scotland"),
    "D1":  ("Bundesliga", "Germany"),
    "D2":  ("2. Bundesliga", "Germany"),
    "I1":  ("Serie A", "Italy"),
    "I2":  ("Serie B", "Italy"),
    "SP1": ("La Liga", "Spain"),
    "SP2": ("Segunda Division", "Spain"),
    "F1":  ("Ligue 1", "France"),
    "F2":  ("Ligue 2", "France"),
    "N1":  ("Eredivisie", "Netherlands"),
    "B1":  ("Belgian Pro League", "Belgium"),
    "P1":  ("Primeira Liga", "Portugal"),
    "T1":  ("Super Lig", "Turkey"),
    "G1":  ("Super League Greece", "Greece"),
}

_GUEST_EXTRA = {
    "ARG": ("Liga Profesional / Copa de la Liga", "Argentina"),
    "AUT": ("Austrian Bundesliga", "Austria"),
    "BRA": ("Brasileirao Serie A", "Brazil"),
    "CHN": ("Chinese Super League", "China"),
    "DNK": ("Danish Superliga", "Denmark"),
    "FIN": ("Veikkausliiga", "Finland"),
    "IRL": ("League of Ireland Premier Division", "Ireland"),
    "JPN": ("J1 League", "Japan"),
    "MEX": ("Liga MX", "Mexico"),
    "NOR": ("Eliteserien", "Norway"),
    "POL": ("Ekstraklasa", "Poland"),
    "ROU": ("Romanian SuperLiga", "Romania"),
    "RUS": ("Russian Premier League", "Russia"),
    "SWE": ("Allsvenskan", "Sweden"),
    "SWZ": ("Swiss Super League", "Switzerland"),
    "USA": ("Major League Soccer", "USA"),
}

GUEST_COMPETITIONS = {
    code: {"name": name, "country": country, "source": "season",
           "markets": ("1X2", "OU2.5", "AH")}
    for code, (name, country) in _GUEST_EUROPE.items()
}
GUEST_COMPETITIONS.update({
    code: {"name": name, "country": country, "source": "extra",
           "markets": ("1X2",)}
    for code, (name, country) in _GUEST_EXTRA.items()
})


# --- what is scored, and what is only forecast ------------------------------
# The site publishes more than it can grade, and the difference has to be
# visible on every page that shows a number rather than buried in a footnote.
#
#   SCORED    a public closing price exists for this exact selection, so the
#             forecast is compared with the market and can be shown to lose.
#   FORECAST  no free closing benchmark exists anywhere, so the number is
#             measured against guessing (log loss vs the coin flip) and is
#             never presented as an edge claim.
#
# Which bucket a market falls into depends on the DIVISION, not just the
# market: the season-by-season European files carry a closing 1X2, over/under
# 2.5 and one main Asian-handicap line, while the "new leagues" files (the
# Brasileirao among them) carry a closing 1X2 and nothing else. Sealing an
# over/under for Brazil is fine; calling it scored would not be.
SCORED_BY_SOURCE = {
    "season": ("1X2", "OU2.5", "AH"),
    "extra": ("1X2",),
}

# Published everywhere, graded against the close nowhere: football-data.co.uk
# publishes no closing price for any of them. They earn their place by being
# falsifiable against the coin flip, not by being free to compute.
FORECAST_MARKETS = ("BTTS", "TOTALS_LADDER", "CORNERS", "SCORELINES")

MARKET_LABELS = {
    "1X2": "Result",
    "OU2.5": "Over/under 2.5 goals",
    "AH": "Asian handicap",
    "BTTS": "Both teams to score",
    "TOTALS_LADDER": "Goal totals other than 2.5",
    "CORNERS": "Corners Lab",
    "SCORELINES": "Correct score",
}


def scored_markets(code: str) -> tuple:
    """Markets with a closing benchmark in this division's source."""
    source = LEAGUES.get(code, {}).get("source", "season")
    return SCORED_BY_SOURCE.get(source, ())


def is_scored(code: str, market: str) -> bool:
    return market in scored_markets(code)


def guest_competition_name(code: str) -> str:
    return GUEST_COMPETITIONS.get(code, {}).get("name", code)


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

UNIFORM_LOG_LOSS = 1.0986122886681098        # -log(1/3), three-way
UNIFORM_LOG_LOSS_BINARY = 0.6931471805599453  # -log(1/2), two-way

# The goals line we publish. A half-goal, so no match can push.
TOTALS_LINE = 2.5
GOAL_TOTAL_LINES = (0.5, 1.5, 2.5, 3.5, 4.5, 5.5)
ASIAN_HANDICAP_LINES = tuple(x / 4 for x in range(-12, 13))
CORNER_TOTAL_LINES = (6.5, 7.5, 8.5, 9.5, 10.5, 11.5, 12.5, 13.5)
CORNER_MIN_MATCHES = 100
CORNER_MAX = 30

# --- the prior --------------------------------------------------------------
# Walk-forward backtest of this exact model, reproducible from
# the research repository. This is a BACKTEST and the site must always label it
# as one: it is not the live record, and it never appears on the scorecard page.
BACKTEST = {
    # Graded against the market-average close (AvgC*), which football-data
    # publishes from 2019/20. The walk-forward itself starts in 2017/18 (after
    # two seasons of burn-in); the first two of its seasons carry no average
    # close and so train the model without being scored. On the same
    # predictions the old Pinnacle benchmark (n=3250, 2017/18–2025/26, incl.
    # only 210 of the 380 matches of 2025/26) read 0.9654 vs 0.9484.
    "period": "2019/20 – 2025/26",
    "n": 2660,
    "model_log_loss": 0.9827,
    "market_log_loss": 0.9639,
    "gap": 0.0189,
    "model_accuracy": 0.527,
    "market_accuracy": 0.550,
    "test_n": 1900,
    "test_model": 0.9783,
    "test_market": 0.9556,
    "repo": "https://github.com/GitSimaao/pl-dixon-coles",
}
