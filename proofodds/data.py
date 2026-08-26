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

import difflib
import logging
import time
import unicodedata

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
    # Seven divisions times twelve seasons is eighty-four files on a cold
    # start. football-data.co.uk is a free service run by one person; a short
    # pause costs us half a minute once and costs them nothing.
    time.sleep(0.25)
    return True


def refresh(leagues=None) -> None:
    """
    Make sure every season of every division is present and current.

    Only the last two seasons are re-fetched. The current one obviously
    changes; the one before it is re-fetched because football-data.co.uk keeps
    correcting a straggler for weeks after a season ends, and a corrected
    result we never picked up is a graded match scored against the wrong
    outcome. Everything older cannot change and is cached for good.
    """
    if leagues is None:
        leagues = list(config.ENABLED_LEAGUES)
    elif isinstance(leagues, str):
        leagues = [leagues]

    for league in leagues:
        for season in config.SEASONS[:-2]:
            try:
                download_season(league, season, force=False)
            except Exception as exc:
                log.warning("could not fetch %s %s: %s", league, season, exc)
        for season in config.SEASONS[-2:]:
            try:
                # The newest season may not exist yet in July; not an error.
                download_season(league, season, force=True)
            except Exception as exc:
                log.warning("%s %s not available: %s", league, season, exc)


# --------------------------------------------------------------------------- #
#  Team names
# --------------------------------------------------------------------------- #
#  Two feeds, two spellings, one join.
#
#  Results and closing odds come from football-data.co.uk, which uses terse
#  forms ("Ein Frankfurt", "M'gladbach", "Ath Madrid"). Fixtures come from
#  football-data.org, which uses full legal names ("Eintracht Frankfurt e.V.",
#  "Borussia Moenchengladbach", "Club Atletico de Madrid"). Every prediction we
#  seal is keyed on a club name, and if that name does not join to the results
#  file the prediction can never be graded. With one league that risk was a
#  hand-written list of twenty clubs. With seven it is roughly a hundred and
#  forty, changing every summer with promotion — a list nobody will maintain.
#
#  So the list is not written by hand. The canonical set of names for a
#  division is whatever appears in that division's own CSVs, read from the
#  files we already download. A feed name is resolved onto it in stages, most
#  certain first, and — this is the part that matters — when two candidates are
#  plausible the resolver returns nothing rather than picking one. An
#  unresolved fixture is loud and recoverable. A wrongly resolved one is
#  silent, sealed, and permanent.
# --------------------------------------------------------------------------- #

# Words that say what kind of organisation a club is, not which club it is.
# "Sporting" is deliberately absent: in Portugal it distinguishes clubs rather
# than describing them.
_NOISE = {
    "fc", "afc", "cf", "sc", "ac", "as", "ss", "us", "ud", "cd", "sd", "rc",
    "rcd", "sad", "cfc", "bc", "acf", "ssc", "ca", "cp", "sv", "tsv", "tsg",
    "vfl", "vfb", "bsc", "fsv", "sge", "kaa", "rsc", "ogc", "asse", "losc",
    "sco", "gd", "cs", "sl", "sad", "rcd", "aj", "rc", "acf", "ssc", "afc",
    "calcio", "club", "clube", "de", "del", "der", "di", "da", "das", "do",
    "dos", "e", "el", "la", "le", "les", "and", "the", "futebol", "futbol",
    "fussball", "football", "balompie", "deportivo", "deportiva", "societa",
    "sportiva", "sportive", "sportif", "olympique", "association", "verein",
    "ev", "spa", "srl", "aps", "kgaa", "team", "sports", "sport",
}

_PUNCT = str.maketrans({c: " " for c in "'’`´.,-–—_/\\()&+"})


def fold(name: str) -> str:
    """Lowercase, unaccented, punctuation-free. The comparison form."""
    decomposed = unicodedata.normalize("NFKD", (name or "").strip())
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    stripped = stripped.replace("ø", "o").replace("Ø", "O")
    stripped = stripped.replace("ß", "ss").replace("æ", "ae").replace("œ", "oe")
    return " ".join(stripped.translate(_PUNCT).lower().split())


def tokens(name: str) -> tuple[str, ...]:
    """
    Folded words with the organisational noise and bare years removed.

    Bare numbers go too: "Bologna FC 1909", "TSG 1899 Hoffenheim" and
    "Mainz 05" all carry a founding year that the results file never repeats.
    """
    raw = [t for t in fold(name).split() if t]
    keep = [t for t in raw if t not in _NOISE and not t.isdigit()]
    return tuple(keep) or tuple(raw)


# --------------------------------------------------------------------------- #
#  Overrides: the cases no rule can reach
# --------------------------------------------------------------------------- #
#  Keyed by division, then by the FOLDED feed name, so a change of punctuation
#  or accent upstream does not break the entry. Everything here is a pair the
#  stages below genuinely cannot connect — a city the short form names and the
#  long form does not ("Ath Bilbao" for Athletic Club), a nickname, a spelling
#  that differs by more than an abbreviation.
#
#  Keep it short on purpose. A rule that works for every club is worth more
#  than a table that works today, and scripts/check_names.py exists to prove
#  which is which against the real feeds before a division goes live.
OVERRIDES: dict[str, dict[str, str]] = {
    "E0": {
        "wolverhampton wanderers": "Wolves",
        "nottingham forest": "Nott'm Forest",
    },
    "E1": {
        "wolverhampton wanderers": "Wolves",
        "nottingham forest": "Nott'm Forest",
        "queens park rangers": "QPR",
        "sheffield wednesday": "Sheffield Weds",
        "west bromwich albion": "West Brom",
        "plymouth argyle": "Plymouth",
    },
    "SP1": {
        "athletic club": "Ath Bilbao",
        "athletic bilbao": "Ath Bilbao",
        "club atletico de madrid": "Ath Madrid",
        "atletico madrid": "Ath Madrid",
        "rcd espanyol de barcelona": "Espanol",
        "rayo vallecano de madrid": "Vallecano",
    },
    "I1": {
        "fc internazionale milano": "Inter",
        "inter milan": "Inter",
        "hellas verona fc": "Verona",
    },
    "D1": {
        "borussia moenchengladbach": "M'gladbach",
        "borussia monchengladbach": "M'gladbach",
        "eintracht frankfurt": "Ein Frankfurt",
        "1 fc koln": "FC Koln",
        "fc koln": "FC Koln",
        "sv werder bremen": "Werder Bremen",
        "fc st pauli 1910": "St Pauli",
        "1 fsv mainz 05": "Mainz",
        "rasenballsport leipzig": "RB Leipzig",
        "bayer 04 leverkusen": "Leverkusen",
        "bayer leverkusen": "Leverkusen",
        "1 fc union berlin": "Union Berlin",
        "hamburger sv": "Hamburg",
        "fc bayern munchen": "Bayern Munich",
        "fc bayern munich": "Bayern Munich",
        "borussia dortmund": "Dortmund",
        "sc freiburg": "Freiburg",
        "1 fc heidenheim 1846": "Heidenheim",
        "vfb stuttgart": "Stuttgart",
        "fc augsburg": "Augsburg",
        "vfl wolfsburg": "Wolfsburg",
        "tsg 1899 hoffenheim": "Hoffenheim",
    },
    "F1": {
        "paris saint germain fc": "Paris SG",
        "paris saint germain": "Paris SG",
        "olympique de marseille": "Marseille",
        "olympique lyonnais": "Lyon",
        "as saint etienne": "St Etienne",
        "stade rennais fc 1901": "Rennes",
        "fc lorient": "Lorient",
        "paris fc": "Paris FC",
    },
    "P1": {
        "sporting clube de portugal": "Sp Lisbon",
        "sporting cp": "Sp Lisbon",
        "sporting clube de braga": "Sp Braga",
        "sc braga": "Sp Braga",
        "fc porto": "Porto",
        "sl benfica": "Benfica",
        "vitoria sc": "Guimaraes",
        "vitoria sc guimaraes": "Guimaraes",
        "vitoria guimaraes": "Guimaraes",
        "cd nacional": "Nacional",
        "cs maritimo": "Maritimo",
        "gd estoril praia": "Estoril",
        "cd santa clara": "Santa Clara",
        "cf estrela da amadora": "Estrela",
        "sc farense": "Farense",
        "casa pia ac": "Casa Pia",
        "fc arouca": "Arouca",
        "moreirense fc": "Moreirense",
        "rio ave fc": "Rio Ave",
        "gil vicente fc": "Gil Vicente",
        "fc famalicao": "Famalicao",
        "boavista fc": "Boavista",
        "cd tondela": "Tondela",
        "fc alverca": "Alverca",
        "avs futebol sad": "AVS",
    },
}

# football-data.co.uk's own historical inconsistencies, applied when the
# results files are loaded so the canonical set has one spelling per club.
SELF_ALIASES: dict[str, dict[str, str]] = {
    "E1": {"Sheffield Wednesday": "Sheffield Weds"},
}

# Names that are too short to display on a narrow card.
SHORT = {
    "Nott'm Forest": "Forest", "Crystal Palace": "Palace",
    "Sheffield United": "Sheff Utd", "Sheffield Weds": "Sheff Weds",
    "Man United": "Man Utd", "Bayern Munich": "Bayern",
    "Ein Frankfurt": "Frankfurt", "M'gladbach": "Gladbach",
    "Paris SG": "PSG", "Sp Lisbon": "Sporting", "Sp Braga": "Braga",
}

_known_cache: dict[str, tuple[tuple, frozenset]] = {}


def _cache_key(league: str) -> tuple:
    """Mtimes of the division's CSVs — the set changes when the data does."""
    out = []
    for season in config.SEASONS:
        path = season_path(league, season)
        out.append((season, path.stat().st_mtime_ns) if path.exists() else (season, 0))
    return tuple(out)


def known_teams(league: str) -> frozenset[str]:
    """
    Every club that has appeared in this division's results files.

    This is the canonical set: not a list somebody typed, the actual contents
    of the data we grade against. Promotion adds a name the day the first
    result is published, which is the day before anyone needs it.
    """
    key = _cache_key(league)
    cached = _known_cache.get(league)
    if cached and cached[0] == key:
        return cached[1]

    names: set[str] = set()
    self_aliases = SELF_ALIASES.get(league, {})
    for season in config.SEASONS:
        path = season_path(league, season)
        if not path.exists():
            continue
        try:
            raw = pd.read_csv(path, encoding="utf-8-sig",
                              usecols=lambda c: c in ("HomeTeam", "AwayTeam"))
        except Exception as exc:                       # a truncated download
            log.warning("could not read %s: %s", path.name, exc)
            continue
        for col in ("HomeTeam", "AwayTeam"):
            if col in raw.columns:
                names.update(str(v).strip() for v in raw[col].dropna())

    names = {self_aliases.get(n, n) for n in names if n and n != "nan"}
    result = frozenset(names)
    _known_cache[league] = (key, result)
    return result


def _prefix_match(short_tokens, long_tokens) -> bool:
    """
    Is the short name an abbreviation of the long one?

    Every word of the results-file name must open a distinct word of the feed
    name: "Ein Frankfurt" against "Eintracht Frankfurt", "Ath Madrid" against
    "Atletico Madrid". Two letters is not enough evidence on its own, so a
    fragment shorter than three characters must match a whole word.
    """
    remaining = list(long_tokens)
    for tok in short_tokens:
        hit = None
        for cand in remaining:
            if cand == tok or (len(tok) >= 3 and cand.startswith(tok)):
                hit = cand
                break
        if hit is None:
            return False
        remaining.remove(hit)
    return True


def resolve(name: str, league: str) -> tuple[str | None, str]:
    """
    Map a fixture-feed club name onto this division's canonical spelling.

    Returns (canonical_name_or_None, how). `how` names the stage that decided
    it, which is what scripts/check_names.py prints for review: a name matched
    by "fuzzy" deserves a human glance in a way an "exact" one does not.
    """
    name = (name or "").strip()
    if not name:
        return None, "empty"

    known = known_teams(league)
    if not known:
        return None, "no data for league"

    if name in known:
        return name, "exact"

    # Overrides are looked up both raw-folded and noise-stripped, so an entry
    # written as "athletic club" also catches "Athletic Club FC" and one
    # written as "1 fc koln" also catches "1. FC Köln".
    folded = fold(name)
    table = OVERRIDES.get(league, {})
    override = table.get(folded) or table.get(" ".join(tokens(name)))
    if override:
        if override not in known:
            log.warning("override %r -> %r but %r is not in %s's results files",
                        name, override, override, league)
        return override, "override"

    by_fold: dict[str, list[str]] = {}
    for k in known:
        by_fold.setdefault(fold(k), []).append(k)
        by_fold.setdefault(" ".join(tokens(k)), []).append(k)
    for probe in (folded, " ".join(tokens(name))):
        hits = {h for h in by_fold.get(probe, [])}
        if len(hits) == 1:
            return hits.pop(), "folded"

    feed_tokens = tokens(name)
    candidates = [k for k in known if _prefix_match(tokens(k), feed_tokens)]
    if len(candidates) == 1:
        return candidates[0], "abbreviation"
    if len(candidates) > 1:
        # Real and dangerous: "Milan" opens "Milano", so Internazionale looks
        # like AC Milan. Refusing here is the whole point.
        log.warning("%s: %r is ambiguous between %s — add an entry to "
                    "data.OVERRIDES", league, name, sorted(candidates))
        return None, "ambiguous"

    target = "".join(feed_tokens)
    scored = sorted(
        ((difflib.SequenceMatcher(None, target, "".join(tokens(k))).ratio(), k)
         for k in known), reverse=True)
    best, runner = scored[0], (scored[1] if len(scored) > 1 else (0.0, None))
    if best[0] >= 0.86 and best[0] - runner[0] >= 0.06:
        return best[1], f"fuzzy {best[0]:.2f}"

    return None, (f"unmatched (closest {best[1]!r} at {best[0]:.2f})"
                  if best[0] > 0.5 else "unmatched")


def canonical(name: str, league: str | None = None) -> str:
    """
    Best-effort canonical spelling, for display and for reading old entries.

    Unlike `resolve` this never returns None: it falls back to the name it was
    given. Use it where an imperfect label is acceptable — the front page, the
    grading join on names that were already sealed — and `resolve` where a
    wrong answer would be worse than no answer.
    """
    name = (name or "").strip()
    if not name:
        return name
    leagues = [league] if league else config.ENABLED_LEAGUES
    for code in leagues:
        hit, _ = resolve(name, code)
        if hit:
            return hit
    return name


def display_from_feed(name: str) -> str:
    """
    A readable label for a club the results files have not seen yet.

    Used only when `resolve` finds nothing — a genuinely new promotion, before
    its first result is published. The words that say what kind of organisation
    it is are dropped and the rest is kept exactly as the feed wrote it, so
    "Wrexham AFC" becomes "Wrexham" and "FC Alverca" becomes "Alverca".

    This is a label, never the only key: the raw feed name is sealed alongside
    it, which is what makes the guess recoverable instead of permanent.
    """
    words = [w for w in (name or "").split()
             if fold(w) not in _NOISE and not fold(w).strip(".").isdigit()]
    return " ".join(words).strip() or (name or "").strip()


def is_known(name: str, league: str) -> bool:
    return name in known_teams(league)


def short_name(name: str) -> str:
    return SHORT.get(name, name)


# --------------------------------------------------------------------------- #
#  Load
# --------------------------------------------------------------------------- #
_matches_cache: dict[str, tuple[tuple, pd.DataFrame]] = {}


def load_matches(league: str = "E0") -> pd.DataFrame:
    """
    Every played match in a division, sorted by date, with team ids attached.

    Cached on the CSVs' modification times. Seven divisions means seven fits
    per run and a grading pass that touches all of them; re-reading a hundred
    files each time turns a three-second job into a thirty-second one for no
    reason.

    Note what this function does NOT do: it does not canonicalise club names.
    These files *are* the canon — every other name in the project is mapped
    onto them. The only exception is SELF_ALIASES, for the rare occasions
    football-data.co.uk has changed its own mind about a spelling.
    """
    key = _cache_key(league)
    cached = _matches_cache.get(league)
    if cached and cached[0] == key:
        return cached[1].copy()

    self_aliases = SELF_ALIASES.get(league, {})
    frames = []
    for season in config.SEASONS:
        path = season_path(league, season)
        if not path.exists():
            continue
        raw = pd.read_csv(path, encoding="utf-8-sig")
        keep = [c for c in CORE_COLS + ODDS_COLS if c in raw.columns]
        missing = [c for c in CORE_COLS if c not in raw.columns]
        if missing:
            log.warning("%s is missing %s — skipping it", path.name, missing)
            continue
        df = raw[keep].copy()
        for col in ODDS_COLS:
            if col not in df.columns:
                df[col] = np.nan
        df["Season"] = f"20{season[:2]}/{season[2:]}"
        frames.append(df)

    if not frames:
        raise FileNotFoundError(
            f"No cached CSVs for {league}. Run data.refresh({league!r}) first.")

    matches = pd.concat(frames, ignore_index=True)
    matches["Date"] = pd.to_datetime(matches["Date"], dayfirst=True, format="mixed")
    matches = matches.dropna(subset=["FTHG", "FTAG", "FTR"])
    matches["FTHG"] = matches["FTHG"].astype(int)
    matches["FTAG"] = matches["FTAG"].astype(int)
    for col in ("HomeTeam", "AwayTeam"):
        matches[col] = (matches[col].astype(str).str.strip()
                        .map(lambda n: self_aliases.get(n, n)))
    matches["League"] = league
    matches = matches.sort_values(["Date", "HomeTeam"]).reset_index(drop=True)

    teams = sorted(set(matches["HomeTeam"]) | set(matches["AwayTeam"]))
    lookup = {t: i for i, t in enumerate(teams)}
    matches["home_id"] = matches["HomeTeam"].map(lookup)
    matches["away_id"] = matches["AwayTeam"].map(lookup)
    matches.attrs["teams"] = teams

    _matches_cache[league] = (key, matches)
    return matches.copy()


def load_all_matches(leagues=None) -> pd.DataFrame:
    """Every division stacked into one frame, for grading across all of them."""
    leagues = list(leagues or config.ENABLED_LEAGUES)
    frames = []
    for league in leagues:
        try:
            frames.append(load_matches(league))
        except FileNotFoundError as exc:
            log.warning("%s", exc)
    if not frames:
        raise FileNotFoundError("no cached results for any enabled division")
    return pd.concat(frames, ignore_index=True)


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
