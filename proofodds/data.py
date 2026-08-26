"""
Match history: download, cache, clean.

Source is football-data.co.uk, which publishes results plus closing odds for
the major European leagues, updated a few times a week during the season. It is
free, it has been reliable for two decades, and — importantly for us — it
carries Pinnacle's closing prices, which is the benchmark the whole site is
built around.

The current season's file changes as matches are played, so it is re-downloaded
on every run. Finished seasons are cached forever.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import requests

from . import config

log = logging.getLogger(__name__)

CORE_COLS = ["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR"]
ODDS_COLS = ["PSCH", "PSCD", "PSCA"]
OUTCOME_INDEX = {"H": 0, "D": 1, "A": 2}


# --------------------------------------------------------------------------- #
#  Download
# --------------------------------------------------------------------------- #
def season_path(league: str, season: str):
    return config.DATA_DIR / f"{league}_{season}.csv"


def download_season(league: str, season: str, force: bool = False) -> bool:
    """Fetch one season CSV. Returns True if the file was written."""
    path = season_path(league, season)
    if path.exists() and not force:
        return False

    url = f"{config.FOOTBALL_DATA_BASE}/{season}/{league}.csv"
    log.info("downloading %s", url)
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    path.write_bytes(resp.content)
    return True


def refresh(league: str = "E0") -> None:
    """
    Make sure every season is present and the current one is up to date.

    Only the last season in the list is re-fetched: the others cannot change.
    """
    for season in config.SEASONS[:-1]:
        try:
            download_season(league, season, force=False)
        except Exception as exc:
            log.warning("could not fetch %s %s: %s", league, season, exc)
    try:
        # The newest season may not exist yet in August; that is not an error.
        download_season(league, config.SEASONS[-1], force=True)
    except Exception as exc:
        log.warning("current season %s not available yet: %s",
                    config.SEASONS[-1], exc)


# --------------------------------------------------------------------------- #
#  Team names
# --------------------------------------------------------------------------- #
# football-data.co.uk uses short forms; fixture feeds use full club names.
# One canonical spelling per club, and everything else maps onto it.
ALIASES = {
    "Manchester City FC": "Man City", "Manchester City": "Man City",
    "Manchester United FC": "Man United", "Manchester United": "Man United",
    "Manchester Utd": "Man United",
    "Tottenham Hotspur FC": "Tottenham", "Tottenham Hotspur": "Tottenham", "Spurs": "Tottenham",
    "Arsenal FC": "Arsenal",
    "Chelsea FC": "Chelsea",
    "Liverpool FC": "Liverpool",
    "Everton FC": "Everton",
    "Newcastle United FC": "Newcastle", "Newcastle United": "Newcastle",
    "Aston Villa FC": "Aston Villa",
    "Brighton & Hove Albion FC": "Brighton", "Brighton & Hove Albion": "Brighton",
    "Brighton and Hove Albion": "Brighton",
    "West Ham United FC": "West Ham", "West Ham United": "West Ham",
    "Wolverhampton Wanderers FC": "Wolves", "Wolverhampton Wanderers": "Wolves",
    "Nottingham Forest FC": "Nott'm Forest", "Nottingham Forest": "Nott'm Forest",
    "Crystal Palace FC": "Crystal Palace",
    "Brentford FC": "Brentford",
    "Fulham FC": "Fulham",
    "AFC Bournemouth": "Bournemouth", "Bournemouth AFC": "Bournemouth",
    "Leicester City FC": "Leicester", "Leicester City": "Leicester",
    "Leeds United FC": "Leeds", "Leeds United": "Leeds",
    "Southampton FC": "Southampton",
    "Ipswich Town FC": "Ipswich", "Ipswich Town": "Ipswich",
    "Burnley FC": "Burnley",
    "Sheffield United FC": "Sheffield United", "Sheffield Utd": "Sheffield United",
    "Luton Town FC": "Luton", "Luton Town": "Luton",
    "Sunderland AFC": "Sunderland",
    "Norwich City FC": "Norwich", "Norwich City": "Norwich",
    "Watford FC": "Watford",
    "Stoke City FC": "Stoke", "Stoke City": "Stoke",
    "Swansea City FC": "Swansea", "Swansea City": "Swansea",
    "West Bromwich Albion FC": "West Brom", "West Bromwich Albion": "West Brom",
    "Huddersfield Town FC": "Huddersfield", "Huddersfield Town": "Huddersfield",
    "Cardiff City FC": "Cardiff", "Cardiff City": "Cardiff",
    "Hull City FC": "Hull", "Hull City": "Hull",
    "Middlesbrough FC": "Middlesbrough",
}

# Short display names for narrow screens.
SHORT = {
    "Nott'm Forest": "Forest", "Crystal Palace": "Palace",
    "Sheffield United": "Sheff Utd", "Man United": "Man Utd",
    "Bournemouth": "Bournemouth", "Wolves": "Wolves",
}


# Clubs that fixture feeds name in full and results files name short. This list
# runs well past the current Premier League on purpose: promotion is the moment
# a new spelling arrives, and a spelling this table does not know is the exact
# failure that leaves a sealed prediction ungradeable for ever.
ALIASES.update({
    "Coventry City": "Coventry", "Coventry City FC": "Coventry",
    "Wrexham": "Wrexham", "Wrexham AFC": "Wrexham",
    "Blackburn Rovers": "Blackburn", "Birmingham City": "Birmingham",
    "Bristol City": "Bristol City", "Preston North End": "Preston",
    "Queens Park Rangers": "QPR", "Sheffield Wednesday": "Sheffield Weds",
    "Derby County": "Derby", "Charlton Athletic": "Charlton",
    "Oxford United": "Oxford", "Portsmouth": "Portsmouth",
    "Millwall": "Millwall", "Middlesbrough": "Middlesbrough",
    "Plymouth Argyle": "Plymouth", "Stoke City": "Stoke",
    "Swansea City": "Swansea", "West Bromwich Albion": "West Brom",
})

# Suffixes a feed appends and a results file does not.
_CLUB_SUFFIXES = (" FC", " AFC", " CF", " SC", " AC", " F.C.", " A.F.C.")

# Every canonical spelling this project recognises.
KNOWN = set(ALIASES.values())


def _strip_club_suffix(name: str) -> str:
    for suffix in _CLUB_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)].strip()
    return name


def canonical(name: str) -> str:
    """
    Map any spelling of a club to the one this project uses.

    Order matters: exact alias, then alias after dropping a club suffix, then
    the bare name if it is already canonical. Anything left over is returned
    unchanged and the caller is expected to complain loudly — a name that falls
    through here is a prediction that will never be graded.
    """
    name = (name or "").strip()
    if not name:
        return name
    if name in ALIASES:
        return ALIASES[name]
    if name in KNOWN:
        return name

    bare = _strip_club_suffix(name)
    if bare in ALIASES:
        return ALIASES[bare]
    if bare in KNOWN:
        return bare
    return bare or name


def is_known(name: str) -> bool:
    """True when a canonicalised name is one this project recognises."""
    return canonical(name) in KNOWN


def short_name(name: str) -> str:
    return SHORT.get(name, name)


# --------------------------------------------------------------------------- #
#  Load
# --------------------------------------------------------------------------- #
def load_matches(league: str = "E0") -> pd.DataFrame:
    """Every played match, sorted by date, with team ids attached."""
    frames = []
    for season in config.SEASONS:
        path = season_path(league, season)
        if not path.exists():
            continue
        raw = pd.read_csv(path, encoding="utf-8-sig")
        keep = [c for c in CORE_COLS + ODDS_COLS if c in raw.columns]
        df = raw[keep].copy()
        for col in ODDS_COLS:
            if col not in df.columns:
                df[col] = np.nan
        df["Season"] = f"20{season[:2]}/{season[2:]}"
        frames.append(df)

    if not frames:
        raise FileNotFoundError(f"No cached CSVs for {league}. Run data.refresh() first.")

    matches = pd.concat(frames, ignore_index=True)
    matches["Date"] = pd.to_datetime(matches["Date"], dayfirst=True, format="mixed")
    matches = matches.dropna(subset=["FTHG", "FTAG", "FTR"])
    matches["FTHG"] = matches["FTHG"].astype(int)
    matches["FTAG"] = matches["FTAG"].astype(int)
    matches["HomeTeam"] = matches["HomeTeam"].map(canonical)
    matches["AwayTeam"] = matches["AwayTeam"].map(canonical)
    matches = matches.sort_values(["Date", "HomeTeam"]).reset_index(drop=True)

    teams = sorted(set(matches["HomeTeam"]) | set(matches["AwayTeam"]))
    lookup = {t: i for i, t in enumerate(teams)}
    matches["home_id"] = matches["HomeTeam"].map(lookup)
    matches["away_id"] = matches["AwayTeam"].map(lookup)
    matches.attrs["teams"] = teams
    return matches


def add_market_probabilities(matches: pd.DataFrame) -> pd.DataFrame:
    """
    Closing decimal odds -> probabilities that sum to one.

    Three prices imply more than 100%; the excess is the bookmaker's margin.
    Dividing through by the total removes it proportionally.
    """
    out = matches.copy()
    has = out[ODDS_COLS].notna().all(axis=1)
    inv = 1.0 / out.loc[has, ODDS_COLS].to_numpy(dtype=float)
    total = inv.sum(axis=1, keepdims=True)

    for col in ["mkt_H", "mkt_D", "mkt_A"]:
        out[col] = np.nan
    out.loc[has, ["mkt_H", "mkt_D", "mkt_A"]] = inv / total
    out["has_odds"] = has
    out["overround"] = np.nan
    out.loc[has, "overround"] = total.ravel() - 1.0
    return out


def result_index(results) -> np.ndarray:
    return pd.Series(results).map(OUTCOME_INDEX).to_numpy(dtype=int)


def log_loss(probs, results) -> float:
    probs = np.asarray(probs, dtype=float)
    idx = result_index(results)
    picked = probs[np.arange(len(idx)), idx]
    return float(-np.log(np.clip(picked, 1e-15, 1.0)).mean())
