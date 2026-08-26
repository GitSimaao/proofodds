"""
Upcoming fixtures, for every division we publish.

Two sources, tried in order:

1. football-data.org. The free tier covers exactly the seven divisions in
   config.LEAGUES. Needs a token in PROOFODDS_FDORG_TOKEN.
2. data/fixtures.csv — a plain file you can maintain by hand. Columns:
   league,date,time,home,away  (date YYYY-MM-DD, time HH:MM UTC, both optional
   except date; `league` may be omitted when only one division is enabled)

The fallback exists so the pipeline never breaks because a third party is down.
A prediction published late is worthless — the whole claim is that it was
published *before* kickoff.

Club names arrive here in the fixture feed's spelling and leave in the results
file's spelling, because that is the only spelling that can ever be graded. The
raw name travels with the fixture either way: sealing both is what keeps a name
we resolved badly from becoming a prediction nobody can score.
"""

from __future__ import annotations

import csv
import datetime as dt
import logging
import time

import requests

from . import config
from .data import display_from_feed, resolve

log = logging.getLogger(__name__)

FDORG_URL = "https://api.football-data.org/v4/competitions/{code}/matches"


class Fixture:
    __slots__ = ("kickoff", "league", "home", "away", "home_raw", "away_raw",
                 "resolved", "matchday")

    def __init__(self, kickoff: dt.datetime, home: str, away: str,
                 league: str = "E0", home_raw: str = "", away_raw: str = "",
                 resolved: bool = True, matchday=None):
        self.kickoff = kickoff
        self.league = league
        self.home = home
        self.away = away
        self.home_raw = home_raw or home
        self.away_raw = away_raw or away
        self.resolved = resolved
        self.matchday = matchday

    @property
    def date(self) -> dt.date:
        return self.kickoff.date()

    def __repr__(self):
        mark = "" if self.resolved else " ?"
        return f"<Fixture {self.league} {self.date} {self.home} v {self.away}{mark}>"


# --------------------------------------------------------------------------- #
# A DENY-list, deliberately, not an allow-list.
#
# An allow-list of ("SCHEDULED", "TIMED") looks tighter and is worse: anything
# the feed puts in that field which we did not anticipate — a renamed enum, a
# provider quirk, a value that is not an enum at all — silently drops every
# fixture, and a fixture dropped in silence is a match missing from a ledger
# that claims to be complete. So we name only what must NOT be published and
# treat everything else as upcoming.
#
# This is safe because it is not the real guard: ledger.build_entry publishes a
# match only when its kickoff is still in the future. That check is on data we
# parse ourselves, not on a string a third party chose.
NOT_UPCOMING_STATUSES = frozenset({
    "FINISHED", "IN_PLAY", "PAUSED", "POSTPONED",
    "CANCELLED", "CANCELED", "SUSPENDED", "AWARDED",
})


def _name(raw: str, league: str, unresolved: list) -> tuple[str, bool]:
    """Feed spelling in, results-file spelling out — or an honest guess."""
    hit, how = resolve(raw, league)
    if hit:
        return hit, True
    guess = display_from_feed(raw)
    unresolved.append(f"{league} {raw!r} -> provisionally {guess!r} ({how})")
    return guess, False


def from_football_data_org(league: str, days_ahead: int) -> list[Fixture] | None:
    """
    Fetch one division's fixtures.

    Returns None when the source could not be consulted at all, and a
    (possibly empty) list when it answered — so the caller can tell "nothing
    configured" and "genuinely no matches" apart in the log.
    """
    token = config.FDORG_TOKEN
    if not token:
        log.warning("PROOFODDS_FDORG_TOKEN is not set — cannot ask "
                    "football-data.org for fixtures")
        return None

    code = config.LEAGUES[league]["fdorg"]
    today = dt.date.today()
    params = {
        "dateFrom": today.isoformat(),
        "dateTo": (today + dt.timedelta(days=days_ahead)).isoformat(),
    }
    url = FDORG_URL.format(code=code)
    headers = {"X-Auth-Token": token}

    resp = requests.get(url, params=params, headers=headers, timeout=20)
    if resp.status_code == 429:
        # The free tier allows ten calls a minute and we make seven. Being
        # throttled anyway means something else shares the token, so wait
        # rather than dropping a division from the run.
        wait = min(int(resp.headers.get("Retry-After", 15) or 15), 70)
        log.warning("%s: rate limited, waiting %ds", league, wait)
        time.sleep(wait)
        resp = requests.get(url, params=params, headers=headers, timeout=20)
    if resp.status_code != 200:
        raise RuntimeError(f"football-data.org returned {resp.status_code} for "
                           f"{code}: {resp.text[:300]}")

    payload = resp.json().get("matches", [])
    seen: dict[str, int] = {}
    for m in payload:
        status = m.get("status", "?")
        seen[status] = seen.get(status, 0) + 1
    log.info("%s (%s): %d matches for %s..%s [%s]",
             league, code, len(payload), params["dateFrom"], params["dateTo"],
             ", ".join(f"{k}={v}" for k, v in sorted(seen.items())) or "empty")

    out: list[Fixture] = []
    unresolved: list[str] = []
    for m in payload:
        if str(m.get("status", "")).upper() in NOT_UPCOMING_STATUSES:
            continue
        kickoff = dt.datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00"))
        home_raw = m["homeTeam"]["name"]
        away_raw = m["awayTeam"]["name"]
        home, ok_h = _name(home_raw, league, unresolved)
        away, ok_a = _name(away_raw, league, unresolved)
        out.append(Fixture(kickoff=kickoff, league=league, home=home, away=away,
                           home_raw=home_raw, away_raw=away_raw,
                           resolved=ok_h and ok_a, matchday=m.get("matchday")))

    if unresolved:
        # An error, not a warning. Every one of these is a club whose sealed
        # prediction will not join to a result until somebody teaches the
        # resolver about it — usually one line in data.OVERRIDES. The raw name
        # is sealed too, so the fix works retroactively, but it still has to
        # be made.
        log.error("UNRESOLVED CLUB NAME(S): %s — run scripts/check_names.py "
                  "and add them to data.OVERRIDES", "; ".join(sorted(unresolved)))

    if payload and not out:
        log.info("%s: every match in the window is already played, postponed "
                 "or cancelled", league)
    return out


def from_csv(days_ahead: int, leagues: list[str]) -> list[Fixture]:
    path = config.DATA_DIR / "fixtures.csv"
    if not path.exists():
        return []

    today = dt.date.today()
    horizon = today + dt.timedelta(days=days_ahead)
    default_league = leagues[0] if len(leagues) == 1 else ""
    out, unresolved = [], []

    with path.open() as fh:
        for row in csv.DictReader(fh):
            league = (row.get("league") or default_league).strip().upper()
            if not league:
                log.error("data/fixtures.csv needs a `league` column when more "
                          "than one division is enabled — skipping a row")
                continue
            if league not in config.LEAGUES:
                log.error("data/fixtures.csv: unknown division %r", league)
                continue
            date = dt.date.fromisoformat(row["date"].strip())
            if not (today <= date <= horizon):
                continue
            time_s = (row.get("time") or "").strip() or "12:00"
            hh, mm = (int(x) for x in time_s.split(":"))
            kickoff = dt.datetime.combine(date, dt.time(hh, mm),
                                          tzinfo=dt.timezone.utc)
            home, ok_h = _name(row["home"].strip(), league, unresolved)
            away, ok_a = _name(row["away"].strip(), league, unresolved)
            out.append(Fixture(kickoff, home, away, league=league,
                               home_raw=row["home"].strip(),
                               away_raw=row["away"].strip(),
                               resolved=ok_h and ok_a,
                               matchday=row.get("matchday")))

    if unresolved:
        log.error("UNRESOLVED CLUB NAME(S) in data/fixtures.csv: %s",
                  "; ".join(sorted(unresolved)))
    return out


def upcoming(leagues: list[str] | str | None = None,
             days_ahead: int | None = None) -> list[Fixture]:
    """
    Every fixture we can price in the next `days_ahead` days, all divisions.

    One division failing does not take the others down: a run that publishes
    six leagues and logs the seventh is far better than a run that publishes
    nothing because Serie A's request timed out.
    """
    if leagues is None:
        leagues = list(config.ENABLED_LEAGUES)
    elif isinstance(leagues, str):
        leagues = [leagues]
    days_ahead = config.LOOKAHEAD_DAYS if days_ahead is None else days_ahead
    provider = config.FIXTURES_PROVIDER

    fixtures: list[Fixture] = []
    consulted = False
    reasons: list[str] = []

    if provider in ("auto", "fdorg"):
        for league in leagues:
            try:
                got = from_football_data_org(league, days_ahead)
            except Exception as exc:              # never let the job die here
                log.warning("%s: football-data.org failed: %s", league, exc)
                reasons.append(f"{league}: source unreachable")
                continue
            if got is None:
                reasons.append(f"{league}: no token")
                continue
            consulted = True
            if not got:
                reasons.append(f"{league}: no unplayed match in the window")
            fixtures.extend(got)

    if not fixtures and provider in ("auto", "csv"):
        from_file = from_csv(days_ahead, leagues)
        if from_file:
            fixtures = from_file
        elif not consulted:
            reasons.append("data/fixtures.csv is missing or empty")

    if not fixtures:
        # Say WHICH of several very different situations this is. Blaming the
        # token when the window is simply empty sends you looking in the wrong
        # place for an hour.
        log.warning("nothing to publish — %s", "; ".join(reasons) or "no source")
        return []

    per_league: dict[str, int] = {}
    for f in fixtures:
        per_league[f.league] = per_league.get(f.league, 0) + 1
    log.info("%d upcoming fixtures in the next %d days (%s)",
             len(fixtures), days_ahead,
             ", ".join(f"{k}={v}" for k, v in sorted(per_league.items())))
    if reasons:
        log.info("quiet divisions — %s", "; ".join(reasons))
    return sorted(fixtures, key=lambda f: (f.kickoff, f.league, f.home))
