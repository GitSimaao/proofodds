"""
Match history: download, cache, clean.

Source is football-data.co.uk, which publishes results plus closing odds for
the major European leagues, updated a few times a week during the season. It is
free, it has been reliable for two decades, and — importantly for us — it
carries the market-average closing price (AvgC*), which is the benchmark the
whole site is built around.

Why the market average and not one book: the site graded against Pinnacle's
close (PSC*) until mid-January 2026, when football-data.co.uk stopped carrying
those columns. Before switching we measured the two benchmarks against each
other on every division we publish — the difference in de-vigged log loss is
within ±0.002 everywhere (E0: 0.0001) — and published the measurement on the
method page. Where Pinnacle's columns still exist in cached files they are
kept, unused, as a cross-check.

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
# The market-average CLOSING prices — the benchmark. Published from 2019/20
# onwards; earlier seasons are still loaded (they train the model) but cannot
# be graded, and the method page says so rather than quietly averaging them.
ODDS_COLS = ["AvgCH", "AvgCD", "AvgCA"]
OU_COLS = ["AvgC>2.5", "AvgC<2.5"]
# The creator ledger can benchmark the main closing Asian-handicap line.  The
# model does not use these columns; carrying them through this loader keeps one
# canonical result frame without turning Asian handicap into a model claim.
AH_COLS = ["AHCh", "AvgCAHH", "AvgCAHA"]
CORNER_COLS = ["HC", "AC"]
# Pinnacle's closing prices, kept where the files still carry them purely as a
# cross-check against the average. Never graded against.
PINNACLE_COLS = ["PSCH", "PSCD", "PSCA", "PC>2.5", "PC<2.5"]
OUTCOME_INDEX = {"H": 0, "D": 1, "A": 2}


def read_csv(path, **kwargs) -> pd.DataFrame:
    """Read a results CSV, including legacy files containing Latin-1 bytes."""
    try:
        return pd.read_csv(path, encoding="utf-8-sig", **kwargs)
    except UnicodeDecodeError:
        # A small number of old football-data.co.uk files contain a literal
        # 0xA0 non-breaking space in an otherwise ASCII row. Re-downloading
        # reproduces the same byte, so decoding the publisher's legacy format
        # is the correct recovery rather than treating the cache as corrupt.
        log.warning("%s is not UTF-8 — reading the source as Latin-1", path.name)
        return pd.read_csv(path, encoding="latin-1", **kwargs)


# --------------------------------------------------------------------------- #
#  Download
# --------------------------------------------------------------------------- #
def season_path(league: str, season: str):
    return config.DATA_DIR / f"{league}_{season}.csv"


def extra_path(league: str):
    return config.DATA_DIR / f"guest_{league}.csv"


def download_extra(league: str, force: bool = False) -> bool:
    from . import guest_data
    return guest_data.download_extra(league, force=force)


def looks_like_results(raw: bytes) -> bool:
    """
    Is this actually a football-data.co.uk results file?

    It has to be asked, because `raise_for_status` does not ask it. A season
    that has not been published yet answers 300 Multiple Choices with an HTML
    page, and 300 is not an error status, so the page sails through and gets
    written to disk as `D1_2627.csv`. Nothing complains until pandas trips over
    line 7 of some HTML weeks later, in a division nobody was looking at.

    The header row is the cheap, honest test: every one of these files starts
    with a line naming the two teams.
    """
    head = raw[:2000].lstrip().lstrip(b"\xef\xbb\xbf")
    if head[:1] in (b"<", b"{"):
        return False
    first = head.split(b"\n", 1)[0]
    return b"HomeTeam" in first and b"AwayTeam" in first


def is_cached(path) -> bool:
    """A cached file only counts if it is readable data, not a saved error."""
    if not path.exists() or path.stat().st_size == 0:
        return False
    with path.open("rb") as fh:
        return looks_like_results(fh.read(2000))


def download_season(league: str, season: str, force: bool = False) -> bool:
    """
    Fetch one season CSV. Returns True if the file was written.

    Two rules, both learned the hard way. Only a 200 carrying a real header row
    is written at all; and it is written to a temporary file and moved into
    place, so a bad answer can never overwrite a good file that is already
    cached. Losing nine seasons of Bundesliga results because the tenth was not
    published yet is not a trade worth making.
    """
    path = season_path(league, season)
    if is_cached(path) and not force:
        return False

    url = f"{config.FOOTBALL_DATA_BASE}/{season}/{league}.csv"
    log.info("downloading %s", url)
    resp = requests.get(url, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(
            f"{url} returned {resp.status_code} "
            f"({resp.reason or 'no reason given'}) — most likely this season "
            f"is not published yet")
    if not looks_like_results(resp.content):
        raise RuntimeError(
            f"{url} returned {len(resp.content)} bytes that are not a results "
            f"file — refusing to cache it")

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".csv.part")
    tmp.write_bytes(resp.content)
    tmp.replace(path)
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
        if config.LEAGUES.get(league, {}).get("source") == "extra":
            try:
                download_extra(league, force=True)
            except Exception as exc:
                log.warning("could not fetch %s: %s", league, exc)
            continue
        for season in config.SEASONS[:-2]:
            try:
                download_season(league, season, force=False)
            except Exception as exc:
                log.warning("could not fetch %s %s: %s", league, season, exc)
        for season in config.SEASONS[-2:]:
            try:
                # The newest season may not exist yet in July, and a division
                # can lag its neighbours by weeks — football-data.co.uk had
                # the 2026/27 Bundesliga 2 up before the Bundesliga. Whatever
                # was cached before stays exactly as it was.
                download_season(league, season, force=True)
            except Exception as exc:
                log.info("%s %s not refreshed: %s", league, season, exc)


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
        # Promoted for 2026/27. football-data.co.uk has not published the
        # 2026/27 Bundesliga file yet, so the name is not in D1's canonical
        # set — but the same publisher has spelled this club "Elversberg" in
        # every 2. Bundesliga file since 2023/24, so this is their spelling
        # rather than a guess at it.
        "sv 07 elversberg": "Elversberg",
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
    "N1": {
        # Two clubs whose short form names a city the long form never says.
        # Everything else in this division falls out of the rules: "Fortuna"
        # opens "For", "De Graafschap" loses its article, "FC Twente '65"
        # loses its year.
        "psv": "PSV Eindhoven",
        "az": "AZ Alkmaar",
        "az alkmaar": "AZ Alkmaar",
        # NEC is Nijmegen Eendracht Combinatie and the results file indexes it
        # by the city, exactly as it does Den Haag for ADO and Zwolle for PEC.
        # No rule can bridge "NEC" and "Nijmegen" — they share no letters in
        # order — and no rule should try.
        "nec": "Nijmegen",
    },
    "BRA": {
        # football-data.org's current BSA names versus football-data.co.uk's
        # rolling Brazil file. These are identities, not fuzzy guesses.
        "ca mineiro": "Atletico-MG",
        "ca mineuro": "Atletico-MG",  # observed upstream typo
        "cr flamengo": "Flamengo RJ",
        "botafogo fr": "Botafogo RJ",
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
_warned_pending: set[tuple[str, str]] = set()


def _cache_key(league: str) -> tuple:
    """Mtimes of the division's CSVs — the set changes when the data does."""
    if config.LEAGUES.get(league, {}).get("source") == "extra":
        path = extra_path(league)
        return (("extra", path.stat().st_mtime_ns if path.exists() else 0),)
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
    paths = ([extra_path(league)] if config.LEAGUES.get(league, {}).get("source") == "extra"
             else [season_path(league, season) for season in config.SEASONS])
    for path in paths:
        if not path.exists():
            continue
        try:
            raw = read_csv(path,
                           usecols=lambda c: c in ("HomeTeam", "AwayTeam", "Home", "Away"))
        except Exception as exc:                       # a truncated download
            log.warning("could not read %s: %s", path.name, exc)
            continue
        for col in ("HomeTeam", "AwayTeam"):
            if col in raw.columns:
                names.update(str(v).strip() for v in raw[col].dropna())
        for col in ("Home", "Away"):
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
        # Said once per division per run, not once per lookup. The Elversberg
        # notice fired eight times in a single build, and a warning repeated
        # for a known, expected condition is how people learn to scroll past
        # warnings — including the one that matters.
        if override not in known and (league, override) not in _warned_pending:
            _warned_pending.add((league, override))
            log.warning("override %r -> %r but %r is not in %s's results files "
                        "yet — sealed predictions for it cannot be graded until "
                        "that season's file is published",
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


def sealed_name(name: str, league: str, raw: str = "") -> str:
    """
    The results-file spelling for a club name that was sealed some time ago.

    Tried in order: the name as sealed, then the fixture feed's own spelling if
    the entry kept one, then the name unchanged. Every part of the project that
    READS the ledger must go through this one function — grading, the front
    page, and above all the de-duplication of repeated fixtures. If two of them
    disagree about what a sealed name means, the same match ends up counted
    twice, which is how a scorecard starts lying.
    """
    hit, _ = resolve(name, league)
    if hit:
        return hit
    if raw and raw != name:
        hit, _ = resolve(raw, league)
        if hit:
            return hit
    return name


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

    if config.LEAGUES.get(league, {}).get("source") == "extra":
        from . import guest_data
        matches = guest_data.load_matches(league)
        for col in ODDS_COLS + OU_COLS + AH_COLS + CORNER_COLS + PINNACLE_COLS:
            if col not in matches: matches[col] = np.nan
        if "Season" not in matches: matches["Season"] = "rolling"
        teams = sorted(set(matches["HomeTeam"]) | set(matches["AwayTeam"]))
        lookup = {t: i for i, t in enumerate(teams)}
        matches["home_id"] = matches["HomeTeam"].map(lookup)
        matches["away_id"] = matches["AwayTeam"].map(lookup)
        matches.attrs["teams"] = teams
        _matches_cache[league] = (key, matches)
        return matches.copy()

    self_aliases = SELF_ALIASES.get(league, {})
    frames = []
    paths = ([extra_path(league)] if config.LEAGUES.get(league, {}).get("source") == "extra"
             else [season_path(league, season) for season in config.SEASONS])
    for path in paths:
        if not path.exists():
            continue
        try:
            raw = read_csv(path)
        except Exception as exc:
            # Loud, and then carry on. Six divisions published with one season
            # missing beats a crash that publishes nothing, and the stale-
            # prediction warning in grade.py is the backstop if it persists.
            log.error("%s is unreadable (%s) — delete it and re-run to "
                      "re-download", path.name, exc)
            continue
        keep = [c for c in CORE_COLS + ODDS_COLS + OU_COLS + AH_COLS + CORNER_COLS
                + PINNACLE_COLS
                if c in raw.columns]
        missing = [c for c in CORE_COLS if c not in raw.columns]
        if missing:
            log.warning("%s is missing %s — skipping it", path.name, missing)
            continue
        df = raw[keep].copy()
        for col in ODDS_COLS + OU_COLS + AH_COLS + CORNER_COLS + PINNACLE_COLS:
            if col not in df.columns:
                df[col] = np.nan
            df[col] = pd.to_numeric(df[col], errors="coerce")
        season = (path.stem.rsplit("_", 1)[-1]
                  if config.LEAGUES.get(league, {}).get("source") != "extra" else "extra")
        df["Season"] = (f"20{season[:2]}/{season[2:]}" if season != "extra" else "rolling")
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
    # `> 1` because a 0.0 in an odds column is a placeholder, not a price —
    # notna() lets it through, and 1/0 turns one bad row into probabilities of
    # [0, 0, nan] and a log loss of 34 for that match. It has happened: three
    # such rows exist in the cached files. No real decimal price is ≤ 1.
    has = (out[ODDS_COLS].notna().all(axis=1)
           & (out[ODDS_COLS] > 1).all(axis=1))
    inv = 1.0 / out.loc[has, ODDS_COLS].to_numpy(dtype=float)
    total = inv.sum(axis=1, keepdims=True)

    for col in ["mkt_H", "mkt_D", "mkt_A"]:
        out[col] = np.nan
    out.loc[has, ["mkt_H", "mkt_D", "mkt_A"]] = inv / total
    out["has_odds"] = has
    out["overround"] = np.nan
    out.loc[has, "overround"] = total.ravel() - 1.0

    # The same de-vigging, applied to the two-way total. A two-way book has a
    # thinner margin than a three-way one, so this number is if anything a
    # harder benchmark to beat than the 1X2 close.
    has_ou = out[OU_COLS].notna().all(axis=1) & (out[OU_COLS] > 1).all(axis=1)
    for col in ["mkt_over25", "mkt_under25"]:
        out[col] = np.nan
    if has_ou.any():
        inv_ou = 1.0 / out.loc[has_ou, OU_COLS].to_numpy(dtype=float)
        out.loc[has_ou, ["mkt_over25", "mkt_under25"]] = (
            inv_ou / inv_ou.sum(axis=1, keepdims=True))
    out["has_ou_odds"] = has_ou
    for col in AH_COLS:
        if col not in out: out[col] = np.nan
    has_ah = (out[AH_COLS].notna().all(axis=1)
              & (out[["AvgCAHH", "AvgCAHA"]] > 1).all(axis=1))
    out["mkt_ah_home"] = np.nan; out["mkt_ah_away"] = np.nan
    if has_ah.any():
        inv_ah = 1.0 / out.loc[has_ah, ["AvgCAHH", "AvgCAHA"]].to_numpy(float)
        out.loc[has_ah, ["mkt_ah_home", "mkt_ah_away"]] = inv_ah / inv_ah.sum(axis=1, keepdims=True)
    out["has_ah_odds"] = has_ah
    out["total_goals"] = out["FTHG"] + out["FTAG"]
    out["over25"] = out["total_goals"] > 2
    return out


def result_index(results) -> np.ndarray:
    return pd.Series(results).map(OUTCOME_INDEX).to_numpy(dtype=int)


def log_loss(probs, results) -> float:
    probs = np.asarray(probs, dtype=float)
    idx = result_index(results)
    picked = probs[np.arange(len(idx)), idx]
    return float(-np.log(np.clip(picked, 1e-15, 1.0)).mean())
