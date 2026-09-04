"""
TheStatsAPI client — the paid source, kept behind one door.

Why this exists: football-data.co.uk stopped publishing Pinnacle's closing
prices in January 2026, and Pinnacle's close is the benchmark this site was
built around. TheStatsAPI carries it for current matches. It also covers ~160
competitions, which is what lets the guest ledger measure a creator who bets
outside our own divisions.

What it deliberately does NOT do:

  * touch the backtest.  The research figures stay on the free CSVs, which
    carry Pinnacle to January 2026 and can be re-downloaded by anyone.
  * touch the ledger rule.  Predictions are sealed before kickoff exactly as
    before; this module only ever supplies the number they are graded against.
  * treat a live price as a closing price.  See `closing_odds`.

The one subtlety worth reading twice
------------------------------------
There is no field called "closing".  Every price is `{opening, last_seen}`.
On a FINISHED match `last_seen` is the close; on a match that has not kicked
off it is simply the latest price, and grading against it would be scoring
ourselves against a number that was still moving.  So `closing_odds` refuses
any match whose status is not `finished`, and the cache marks which is which.
That refusal is the whole safety property of this module.
"""

from __future__ import annotations

import json
import logging
import time
import datetime as dt
from pathlib import Path

import requests

from . import config

log = logging.getLogger(__name__)

# Cloudflare answers unknown agents with error 1010 BEFORE authentication, so a
# missing User-Agent looks exactly like a bad key. Send a real one.
USER_AGENT = ("ProofOdds/1.0 (+https://proofodds.com; football forecast "
              "scoring; contact hello@proofodds.com)")

FINISHED = "finished"
MARKET_MATCH_ODDS = "match_odds"
MARKET_BTTS = "btts"
MARKET_TOTALS = "total_goals"
MARKET_CORNERS = "match_corners"
MARKET_AH = "asian_handicap"


class StatsAPIError(RuntimeError):
    pass


class QuotaExhausted(StatsAPIError):
    pass


class RateLimited(StatsAPIError):
    """
    Distinct from a missing price, and the distinction matters.

    A sweep that catches every StatsAPIError alike counts a throttled request
    as "this match has no Pinnacle price". That understates coverage in
    exactly the measurement being used to decide whether the benchmark moves —
    it happened on the first Ligue 1 sweep, where eight matches were recorded
    as missing when they had simply been refused. A rate limit is a fact about
    us, not about the data.
    """


# --------------------------------------------------------------------------- #
#  Budget: this plan is far smaller than the brochure says
# --------------------------------------------------------------------------- #
class Budget:
    """
    Requests per minute and per calendar month, both persisted.

    The trial reports `x-ratelimit-limit: 12` and `x-monthly-quota-limit:
    10000` — a tenth of the advertised Starter allowance. Burning a month's
    quota on a backfill would leave the daily job unable to grade, so the
    counter lives on disk and the caller is stopped rather than warned once
    and forgotten.
    """

    def __init__(self, path: Path | None = None, per_min: int | None = None,
                 monthly: int | None = None):
        self.path = Path(path or config.STATSAPI_DIR / "_budget.json")
        self.per_min = per_min or config.STATSAPI_RATE_PER_MIN
        self.monthly = monthly or config.STATSAPI_MONTHLY_QUOTA
        self._recent: list[float] = []

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _month(self) -> str:
        return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m")

    def used(self) -> int:
        state = self._load()
        return int(state.get("used", 0)) if state.get("month") == self._month() else 0

    def remaining(self) -> int:
        return max(0, self.monthly - self.used())

    def spend(self, n: int = 1) -> None:
        used = self.used() + n
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"month": self._month(), "used": used}),
                       encoding="utf-8")
        tmp.replace(self.path)
        if used == self.monthly or used % 500 == 0:
            log.warning("TheStatsAPI: %d of %d monthly requests used",
                        used, self.monthly)

    def check(self) -> None:
        if self.remaining() <= 0:
            raise QuotaExhausted(
                f"monthly quota of {self.monthly} requests is spent "
                f"({self._month()}). Grading falls back to the free source "
                "until it resets; nothing is graded against a guess.")

    def throttle(self) -> None:
        """
        Space requests evenly instead of bursting to the limit and stalling.

        A rolling-window limiter fires its whole allowance back to back and
        then waits out the minute. That is inside the documented limit and
        still drew 429s: a server with a token bucket sees ten requests in two
        seconds as a burst whatever the per-minute figure says. One request
        every `60/per_min` seconds is the same throughput, gentler, and it is
        what stopped the Ligue 1 sweep losing matches to throttling.
        """
        gap = 60.0 / max(1, self.per_min)
        now = time.monotonic()
        if self._recent:
            wait = self._recent[-1] + gap - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
        self._recent = [now]


_budget = Budget()


# --------------------------------------------------------------------------- #
#  Transport
# --------------------------------------------------------------------------- #
def _cache_path(path: str, params: dict | None) -> Path:
    key = path.strip("/").replace("/", "_")
    if params:
        bits = "_".join(f"{k}-{v}" for k, v in sorted(params.items())
                        if k not in ("page",))
        page = params.get("page")
        key = f"{key}__{bits}" + (f"__p{page}" if page else "")
    return config.STATSAPI_DIR / f"{key}.json"


def _read_cache(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_cache(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)


def get(path: str, params: dict | None = None, *, cache: bool = True,
        budget: Budget | None = None) -> dict:
    """
    One GET. Cached on disk, rate-limited, quota-counted.

    `cache=False` is for things that legitimately change — a fixture list, a
    match that has not finished. Anything final is cached forever, because
    re-requesting immutable history is how a 10,000-request month disappears.
    """
    budget = budget or _budget
    target = _cache_path(path, params)
    if cache:
        hit = _read_cache(target)
        if hit is not None:
            return hit

    if not config.STATSAPI_KEY:
        raise StatsAPIError(
            "PROOFODDS_STATSAPI_KEY is not set — refusing to call the API. "
            "Set it in the environment; it must never be committed.")

    budget.check()
    budget.throttle()
    url = f"{config.STATSAPI_BASE.rstrip('/')}/{path.lstrip('/')}"
    try:
        response = requests.get(
            url, params=params, timeout=30,
            headers={"Authorization": f"Bearer {config.STATSAPI_KEY}",
                     "User-Agent": USER_AGENT, "Accept": "application/json"})
    except requests.RequestException as exc:
        raise StatsAPIError(f"{path}: {exc}") from exc
    budget.spend()

    if response.status_code == 429:
        # One patient retry. There is no Retry-After header, so wait a whole
        # window rather than guessing something shorter and being refused
        # again — and if it still fails, say so in a type the caller can tell
        # apart from "this match has no price".
        delay = float(response.headers.get("retry-after") or 60.0)
        log.warning("TheStatsAPI: rate limited on %s, waiting %.0fs for one "
                    "retry", path, delay)
        time.sleep(delay)
        budget.check()
        try:
            response = requests.get(
                url, params=params, timeout=30,
                headers={"Authorization": f"Bearer {config.STATSAPI_KEY}",
                         "User-Agent": USER_AGENT,
                         "Accept": "application/json"})
        except requests.RequestException as exc:
            raise RateLimited(f"{path}: {exc}") from exc
        budget.spend()
        if response.status_code == 429:
            raise RateLimited(
                f"{path}: still rate limited after {delay:.0f}s — lower "
                "PROOFODDS_STATSAPI_RPM. This match was NOT checked and must "
                "not be counted as having no price.")
    if response.status_code >= 400:
        # Never echo the key, and never echo a whole error page.
        raise StatsAPIError(f"{path}: HTTP {response.status_code} "
                            f"{response.text[:200]!r}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise StatsAPIError(f"{path}: response was not JSON") from exc

    if cache:
        _write_cache(target, payload)
    return payload


def paged(path: str, params: dict | None = None, *, limit: int = 1000,
          cache: bool = True) -> list[dict]:
    """Follow `meta.total_pages`, stopping at `limit` rows."""
    params = dict(params or {})
    params.setdefault("per_page", 100)
    out: list[dict] = []
    page = 1
    while len(out) < limit:
        params["page"] = page
        payload = get(path, params, cache=cache)
        rows = payload.get("data") or []
        out.extend(rows)
        meta = payload.get("meta") or {}
        if page >= int(meta.get("total_pages") or 1) or not rows:
            break
        page += 1
    return out[:limit]


# --------------------------------------------------------------------------- #
#  Endpoints we actually use
# --------------------------------------------------------------------------- #
def competitions(**filters) -> list[dict]:
    return paged("football/competitions", filters)


def matches(**filters) -> list[dict]:
    """Fixture/result list. Never cached: statuses and scores move."""
    return paged("football/matches", filters, cache=False)


def match_odds(match_id: str, *, cache: bool = True) -> dict:
    return get(f"football/matches/{match_id}/odds", cache=cache)


def shotmap(match_id: str) -> dict:
    return get(f"football/matches/{match_id}/shotmap")


# --------------------------------------------------------------------------- #
#  Reading a price out of the payload
# --------------------------------------------------------------------------- #
def _price(node, field: str = "last_seen") -> float | None:
    """`{opening, last_seen}` -> one of the two prices, as a float."""
    if not isinstance(node, dict):
        return None
    raw = node.get(field)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    # A decimal price is > 1 by definition; 0 and 1 are placeholders, and one
    # of those getting through is worth 34 nats of log loss on that match.
    return value if value > 1.0 else None


def book_markets(payload: dict, bookmaker: str) -> dict:
    for entry in ((payload.get("data") or {}).get("bookmakers") or []):
        if str(entry.get("bookmaker", "")).lower() == bookmaker.lower():
            return entry.get("markets") or {}
    return {}


def opening_and_closing_1x2(match: dict, bookmaker: str | None = None) -> dict:
    """
    Both prices for the result market, for the drift check.

    The payload carries no timestamp, so there is no way to ask when
    `last_seen` was captured — which matters, because a price collected hours
    before kickoff is not a close, and grading against it would quietly weaken
    the benchmark. What can be inferred: a market tightens as it approaches
    kickoff, so if `last_seen` is genuinely later than `opening` its margin
    should be systematically thinner. If the two margins are indistinguishable,
    `last_seen` is probably not a closing price at all.
    """
    markets = book_markets(match_odds(match["id"]),
                           bookmaker or config.STATSAPI_BENCHMARK_BOOK)
    node = markets.get(MARKET_MATCH_ODDS) or {}
    out = {}
    for field in ("opening", "last_seen"):
        prices = {k: _price(node.get(v), field)
                  for k, v in (("H", "home"), ("D", "draw"), ("A", "away"))}
        if all(prices.values()):
            out[field] = prices
    return out


def closing_odds(match: dict, bookmaker: str | None = None) -> dict:
    """
    The closing prices for a FINISHED match, as plain floats.

    `match` is a row from `matches()` — the status matters, so the whole row
    is required rather than an id. A match that has not finished raises:
    `last_seen` on a live or scheduled match is the latest price, not the
    close, and grading against it would quietly score us against a moving
    number. That refusal is the point of this function.

    Returns {"1X2": {...}, "OU": {line: {...}}, "AH": {...}, "BTTS": {...},
    "CORNERS": {line: {...}}} with only the markets this book actually prices.
    """
    status = str(match.get("status", "")).lower()
    if status != FINISHED:
        raise StatsAPIError(
            f"{match.get('id')} is {status!r}, not finished — its last_seen "
            "price is not a closing price and must not be graded against")

    bookmaker = bookmaker or config.STATSAPI_BENCHMARK_BOOK
    markets = book_markets(match_odds(match["id"]), bookmaker)
    out: dict[str, dict] = {}

    one_x_two = markets.get(MARKET_MATCH_ODDS) or {}
    prices = {k: _price(one_x_two.get(v))
              for k, v in (("H", "home"), ("D", "draw"), ("A", "away"))}
    if all(prices.values()):
        out["1X2"] = prices

    btts = markets.get(MARKET_BTTS) or {}
    yes, no = _price(btts.get("yes")), _price(btts.get("no"))
    if yes and no:
        out["BTTS"] = {"yes": yes, "no": no}

    for key, label in ((MARKET_TOTALS, "OU"), (MARKET_CORNERS, "CORNERS")):
        lines = {}
        for line, sides in (markets.get(key) or {}).items():
            over, under = _price((sides or {}).get("over")), _price((sides or {}).get("under"))
            if over and under:
                lines[str(line)] = {"over": over, "under": under}
        if lines:
            out[label] = lines

    ah = markets.get(MARKET_AH) or {}
    handicaps = {}
    for side in ("home", "away"):
        for line, node in (ah.get(side) or {}).items():
            price = _price(node)
            if price:
                handicaps.setdefault(str(line), {})[side] = price
    if handicaps:
        out["AH"] = handicaps
    return out


def overround(prices: dict) -> float:
    """
    Bookmaker margin implied by a set of prices, as a fraction.

    This is the validation that replaces the one we cannot do. The API has no
    Pinnacle history and the free files stopped carrying Pinnacle in January
    2026, so the two sources never overlap and cannot be compared match by
    match. What can still be checked is the shape of the number: a sharp book
    runs about 2.5-3% over on a 1X2, a market average about 4-5%. If what this
    API calls Pinnacle prices like a soft book, it is not Pinnacle, and the
    benchmark must not move.
    """
    values = [v for v in prices.values() if v and v > 1]
    if len(values) < 2:
        return float("nan")
    return sum(1.0 / v for v in values) - 1.0


# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #
def _division_search_terms() -> dict[str, tuple[str, str]]:
    """Our division codes and the (name, country) to search the catalogue by."""
    return {code: (meta["name"], meta.get("country", ""))
            for code, meta in config.LEAGUES.items()}


def map_divisions() -> dict[str, dict]:
    """
    Find each of our divisions in the API catalogue.

    Names do not match ours exactly — La Liga is published as "LaLiga" and the
    Primeira Liga as "Liga Portugal Betclic" — so this searches by country and
    reports every candidate rather than guessing. A wrong mapping would grade
    a division against another country's prices, which is the worst failure
    available, so a human reads this and pastes the result into config.
    """
    found: dict[str, dict] = {}
    by_country: dict[str, list[dict]] = {}
    for code, (name, country) in _division_search_terms().items():
        if country not in by_country:
            by_country[country] = competitions(country=country, type="league")
        candidates = by_country[country]
        exact = [c for c in candidates
                 if c["name"].lower().replace(" ", "") == name.lower().replace(" ", "")]
        found[code] = {
            "want": f"{name} ({country})",
            "match": exact[0] if exact else None,
            "candidates": [{"id": c["id"], "name": c["name"],
                            "odds": c.get("odds_available"),
                            "xg": c.get("xg_available")}
                           for c in candidates],
        }
    return found


def main(argv=None) -> int:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(prog="python -m proofodds.statsapi",
                                 description=__doc__.strip().splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("budget", help="how much of the monthly quota is spent")
    sub.add_parser("map-divisions",
                   help="find our divisions in the catalogue, for config")
    args = ap.parse_args(argv)

    if args.cmd == "budget":
        print(f"used {_budget.used()} of {_budget.monthly} this month "
              f"({_budget.remaining()} left), max {_budget.per_min}/min")
        return 0

    mapping = map_divisions()
    print("\nSTATSAPI_COMPETITIONS = {")
    for code, row in mapping.items():
        hit = row["match"]
        if hit:
            print(f'    {code!r}: {hit["id"]!r},  # {hit["name"]}'
                  f'{"" if hit.get("odds_available") else "  ** NO ODDS **"}')
        else:
            print(f'    # {code}: NOT MATCHED for {row["want"]} — candidates:')
            for c in row["candidates"][:8]:
                print(f'    #     {c["id"]}  {c["name"]}  '
                      f'odds={c["odds"]} xg={c["xg"]}')
    print("}")
    missing = [c for c, r in mapping.items() if not r["match"]]
    if missing:
        print(f"\n{len(missing)} unmatched: {', '.join(missing)} — pick from "
              "the candidates above by hand. Do not guess.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
