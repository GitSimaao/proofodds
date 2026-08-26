"""
Upcoming fixtures.

Two sources, tried in order:

1. football-data.org (free tier covers the Premier League). Needs a token in
   PROOFODDS_FDORG_TOKEN — free, takes two minutes to get.
2. data/fixtures.csv — a plain file you can maintain by hand. Columns:
   date,time,home,away  (date as YYYY-MM-DD, time as HH:MM in UTC, optional)

The fallback exists so the pipeline never breaks because a third party is down.
A prediction that is published late is worthless — the whole claim is that it
was published *before* kickoff.
"""

from __future__ import annotations

import csv
import datetime as dt
import logging

import requests

from . import config
from .data import canonical, is_known

log = logging.getLogger(__name__)

FDORG_URL = "https://api.football-data.org/v4/competitions/{code}/matches"


class Fixture:
    __slots__ = ("kickoff", "home", "away", "matchday")

    def __init__(self, kickoff: dt.datetime, home: str, away: str, matchday=None):
        self.kickoff = kickoff
        self.home = home
        self.away = away
        self.matchday = matchday

    @property
    def date(self) -> dt.date:
        return self.kickoff.date()

    def __repr__(self):
        return f"<Fixture {self.date} {self.home} v {self.away}>"


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


def from_football_data_org(league: str, days_ahead: int) -> list[Fixture] | None:
    """
    Fetch fixtures. Returns None when the source could not be consulted at all,
    and a (possibly empty) list when it answered — so the caller can tell
    "nothing configured" and "genuinely no matches" apart in the log.
    """
    token = config.FDORG_TOKEN
    if not token:
        log.warning("PROOFODDS_FDORG_TOKEN is not set — cannot ask "
                    "football-data.org for fixtures")
        return None

    code = config.LEAGUES[league]["fdorg_code"]
    today = dt.date.today()
    params = {
        "dateFrom": today.isoformat(),
        "dateTo": (today + dt.timedelta(days=days_ahead)).isoformat(),
    }
    resp = requests.get(FDORG_URL.format(code=code), params=params,
                        headers={"X-Auth-Token": token}, timeout=20)
    if resp.status_code != 200:
        # The API explains itself in the body; put that in the log rather than
        # a bare status code.
        raise RuntimeError(f"football-data.org returned {resp.status_code}: "
                           f"{resp.text[:300]}")

    payload = resp.json().get("matches", [])
    seen: dict[str, int] = {}
    for m in payload:
        status = m.get("status", "?")
        seen[status] = seen.get(status, 0) + 1
    log.info("football-data.org: %d matches for %s..%s [%s]",
             len(payload), params["dateFrom"], params["dateTo"],
             ", ".join(f"{k}={v}" for k, v in sorted(seen.items())) or "empty")

    out, unknown = [], set()
    for m in payload:
        if str(m.get("status", "")).upper() in NOT_UPCOMING_STATUSES:
            continue
        kickoff = dt.datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00"))
        home = canonical(m["homeTeam"]["name"])
        away = canonical(m["awayTeam"]["name"])
        for raw, mapped in ((m["homeTeam"]["name"], home),
                            (m["awayTeam"]["name"], away)):
            if not is_known(mapped):
                unknown.add(f"{raw!r} -> {mapped!r}")
        out.append(Fixture(kickoff=kickoff, home=home, away=away,
                           matchday=m.get("matchday")))

    if unknown:
        # Not a warning — an error. A club this project cannot name is a
        # prediction that will be sealed and then never join to a result.
        log.error("UNRECOGNISED CLUB NAME(S): %s — add them to data.ALIASES "
                  "before these fixtures are graded", "; ".join(sorted(unknown)))

    if payload and not out:
        log.warning("every match in the window was already played, postponed "
                    "or cancelled — nothing to publish")
    return out


def from_csv(days_ahead: int) -> list[Fixture]:
    path = config.DATA_DIR / "fixtures.csv"
    if not path.exists():
        return []

    today = dt.date.today()
    horizon = today + dt.timedelta(days=days_ahead)
    out = []
    with path.open() as fh:
        for row in csv.DictReader(fh):
            date = dt.date.fromisoformat(row["date"].strip())
            if not (today <= date <= horizon):
                continue
            time_s = (row.get("time") or "").strip() or "12:00"
            hh, mm = (int(x) for x in time_s.split(":"))
            kickoff = dt.datetime.combine(
                date, dt.time(hh, mm), tzinfo=dt.timezone.utc)
            out.append(Fixture(kickoff, canonical(row["home"]),
                               canonical(row["away"]), row.get("matchday")))
    return out


def upcoming(league: str = "E0", days_ahead: int | None = None) -> list[Fixture]:
    days_ahead = config.LOOKAHEAD_DAYS if days_ahead is None else days_ahead
    provider = config.FIXTURES_PROVIDER

    fixtures: list[Fixture] | None = None
    reason = "no fixture source is configured"

    if provider in ("auto", "fdorg"):
        try:
            fixtures = from_football_data_org(league, days_ahead)
            if fixtures is None:
                reason = "football-data.org has no token"
            elif not fixtures:
                reason = (f"football-data.org has no unplayed match in the next "
                          f"{days_ahead} days")
        except Exception as exc:                      # never let the job die here
            log.warning("football-data.org failed: %s", exc)
            fixtures = None
            reason = "football-data.org could not be reached"

    if not fixtures and provider in ("auto", "csv"):
        from_file = from_csv(days_ahead)
        if from_file:
            fixtures = from_file
        elif fixtures is None:
            reason += " and data/fixtures.csv is missing or empty"

    if not fixtures:
        # Say WHICH of the two very different situations this is. The old
        # message blamed the token in both cases, which sends you looking in
        # the wrong place when the window is simply empty.
        log.warning("nothing to publish: %s", reason)
        return []

    log.info("%d upcoming fixtures in the next %d days", len(fixtures), days_ahead)
    return sorted(fixtures, key=lambda f: (f.kickoff, f.home))
