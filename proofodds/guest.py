"""
Guest ledger: other people's predictions, sealed under our rules.

The product question this answers: a tipster says they have edge; nobody can
check, because their record lives in edited Discord messages. Here they seal
each entry BEFORE kickoff — pick, odds taken, nothing else — into a hash chain
with exactly the same rule as the main ledger, OpenTimestamps anchor included,
and the record is graded in public whichever way it points. First publication
wins; nothing is ever rewritten.

What is measured is **closing line value**, not profit. An entry's odds are
compared with the market-average closing odds for the same selection: beating
the close consistently is the accepted evidence of edge, and it converges in
hundreds of entries where profit-and-loss is still noise. Flat-stakes P/L is
shown too, labelled as the noisier number.

One chain per guest, in guests/<slug>/, files named <slug>--<UTC time>.json.
The verifier needs no new rule: `python proofodds/verify.py guests/<slug>`
checks a guest chain exactly as it checks ours.

Two ways in. The operator can seal from the command line, as before. A guest
with an invite token can seal for themselves through proofodds/sealapi.py,
which is what /seal/ posts to.

Reveal at kickoff. Someone who sells picks cannot have them published before
the match, so an entry may be sealed now and shown later. The entry is
written, hashed, chained and timestamped at the moment of sealing; only its
publication waits. Until then it sits in data/embargo/<slug>/, outside the
repository, while its OpenTimestamps proof is committed at once. Entries are
released strictly in chain order, so the public chain never has a hole in it.

Sealed-before-kickoff is checked twice: against the kickoff the guest states,
when sealing, and against the kickoff time in the results feed, when grading.
The second check is the one that counts, because the first trusts the guest.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import hashlib
import os
import re
import secrets
import shutil
import sys
from pathlib import Path

from . import anchor, config, guest_data

log = logging.getLogger(__name__)

SCHEMA = "guest-2"
GENESIS = "0" * 64

# Entries sealed but not yet public. Under data/, which is not in the
# repository: the whole point is that nobody can read them before kickoff.
EMBARGO_DIR = config.DATA_DIR / "embargo"
TOKENS_FILE = config.DATA_DIR / "guest_tokens.json"
# A reveal-at-kickoff entry holds back everything sealed after it, so the
# wait has to be bounded.
EMBARGO_MAX_DAYS = 7

MARKETS = {
    "1X2": {"H", "D", "A"},
    "OU2.5": {"over", "under"},
    "AH": {"H", "A"},
}
# The closing column for each selection — the same benchmark and the same raw
# market-average price a bettor compares the taken price against.  It is not
# de-vigged: price CLV compares like with like; model log loss is where the
# multi-selection closing market is de-vigged into probabilities.
CLOSE_COLS = {
    ("1X2", "H"): "AvgCH", ("1X2", "D"): "AvgCD", ("1X2", "A"): "AvgCA",
    ("OU2.5", "over"): "AvgC>2.5", ("OU2.5", "under"): "AvgC<2.5",
    ("AH", "H"): "AvgCAHH", ("AH", "A"): "AvgCAHA",
}


def slugify(value: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not out:
        raise ValueError(f"cannot make a slug from {value!r}")
    return out


def guest_dir(slug: str) -> Path:
    return config.GUESTS_DIR / slug


def guest_slugs() -> list[str]:
    """Everyone with a record, including one whose entries are all still held."""
    public = set()
    if config.GUESTS_DIR.exists():
        public = {p.name for p in config.GUESTS_DIR.iterdir()
                  if p.is_dir() and list(p.glob("*.json"))}
    return sorted(public | set(embargoed_slugs()))


def entry_files(slug: str) -> list[Path]:
    return sorted(guest_dir(slug).glob("*.json"))


def read_entries(slug: str) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in entry_files(slug)]


def used_competitions() -> list[str]:
    """Only feeds that can affect an existing creator record, held or public."""
    used = {entry["league"] for slug in guest_slugs()
            for entry in read_entries(slug)}
    for slug in embargoed_slugs():
        for path in embargoed_files(slug):
            used.add(json.loads(path.read_text(encoding="utf-8"))["league"])
    return sorted(used)


def embargo_dir(slug: str) -> Path:
    return EMBARGO_DIR / slug


def embargoed_files(slug: str) -> list[Path]:
    return sorted(embargo_dir(slug).glob("*.json"))


def embargoed_slugs() -> list[str]:
    if not EMBARGO_DIR.exists():
        return []
    return sorted(p.name for p in EMBARGO_DIR.iterdir()
                  if p.is_dir() and list(p.glob("*.json")))


def commitments(slug: str) -> list[dict]:
    """What may be said in public about entries that are not public yet."""
    out = []
    for path in embargoed_files(slug):
        entry = json.loads(path.read_text(encoding="utf-8"))
        out.append({"sealed_at": entry["sealed_at"], "hash": entry["hash"],
                    "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "proof": f"{path.name}.ots"})
    return out


def _utc(value: str) -> dt.datetime:
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=dt.timezone.utc)


def release_due(now: dt.datetime | None = None) -> list[Path]:
    """
    Publish every embargoed entry whose time has come, oldest first.

    Stops at the first entry that is not due. An entry links to the one sealed
    before it, so publishing out of order would put a broken link in public
    until the earlier one caught up.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    released = []
    for slug in embargoed_slugs():
        for path in embargoed_files(slug):
            entry = json.loads(path.read_text(encoding="utf-8"))
            due = (_utc(entry["kickoff"]) if entry.get("reveal") == "kickoff"
                   else _utc(entry["sealed_at"]))
            if due > now:
                break
            target = guest_dir(slug) / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(target))
            released.append(target)
            log.info("released %s", target.name)
    return released


def _parse_kickoff(value: str) -> dt.datetime:
    """Accept '2026-09-12T18:30Z' or '2026-09-12 18:30' — always UTC."""
    text = value.strip().replace(" ", "T").removesuffix("Z")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"unreadable kickoff {value!r} — use "
                         f"YYYY-MM-DDTHH:MMZ, UTC") from exc
    if parsed.tzinfo is not None:
        return parsed.astimezone(dt.timezone.utc)
    return parsed.replace(tzinfo=dt.timezone.utc)


def seal(*, guest_name: str, league: str, home: str, away: str, kickoff: str,
         market: str, selection: str, odds: float, book: str = "",
         line: float | None = None, note: str = "",
         now: dt.datetime | None = None,
         stamp: bool = True, reveal: str = "",
         slug: str | None = None) -> Path:
    """
    Seal one guest entry. Refuses anything that could later need "fixing":
    a started match, an unknown club, a placeholder price, an unknown market.
    A refused entry costs a minute; a corrected ledger entry costs the point
    of the whole site.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    slug = slug or slugify(guest_name)
    league = league.upper()
    if reveal not in ("", "kickoff"):
        raise ValueError(f"reveal must be empty or 'kickoff', got {reveal!r}")

    if league not in config.GUEST_COMPETITIONS:
        raise ValueError(f"unknown creator competition {league!r} — use "
                         "`python -m proofodds.guest coverage`")
    if market not in MARKETS:
        raise ValueError(f"unknown market {market!r} — one of {sorted(MARKETS)}")
    available = guest_data.markets(league)
    if market not in available:
        raise ValueError(
            f"{market} has no published closing benchmark for {league}; "
            f"measurable here: {', '.join(available)}")
    if selection not in MARKETS[market]:
        raise ValueError(f"selection {selection!r} is not valid for {market} — "
                         f"one of {sorted(MARKETS[market])}")
    odds = float(odds)
    if not odds > 1.0:
        raise ValueError(f"odds must be a real decimal price > 1, got {odds}")

    if market == "AH":
        if line is None:
            raise ValueError("Asian handicap requires --line, expressed for "
                             "the selected team (for example -0.5 or +0.25)")
        line = float(line)
        if abs(line) > 5 or abs(line * 4 - round(line * 4)) > 1e-8:
            raise ValueError("Asian-handicap line must be between -5 and +5 "
                             "in quarter-goal increments")
        line = round(line * 4) / 4
    elif line is not None:
        raise ValueError("--line is only valid for the AH market")

    ko = _parse_kickoff(kickoff)
    if ko <= now:
        raise ValueError(f"kickoff {ko.isoformat()} is not in the future — "
                         "sealed-before-kickoff is the one promise; refused")
    if reveal == "kickoff" and ko - now > dt.timedelta(days=EMBARGO_MAX_DAYS):
        raise ValueError(
            f"reveal-at-kickoff is limited to matches within "
            f"{EMBARGO_MAX_DAYS} days, because everything sealed afterwards "
            "waits behind it; seal this one publicly or nearer the time")

    resolved = {}
    for side, raw in (("home", home), ("away", away)):
        hit, how = guest_data.resolve(raw, league)
        if not hit:
            raise ValueError(f"cannot resolve {side} club {raw!r} in {league} "
                             f"({how}) — fix the spelling; guessing here is "
                             "how a record gets attached to the wrong match")
        resolved[side] = hit
        if how == "fuzzy":
            log.warning("%s %r resolved to %r by fuzzy match — check it",
                        side, raw, hit)

    # The chain runs through embargoed entries too: the newest entry is the
    # newest of both, and anything sealed while one is waiting waits behind it.
    waiting = embargoed_files(slug)
    existing = sorted(entry_files(slug) + waiting, key=lambda p: p.name)
    prev = (json.loads(existing[-1].read_text(encoding="utf-8"))["hash"]
            if existing else GENESIS)

    held = reveal == "kickoff" or bool(waiting)
    directory = embargo_dir(slug) if held else guest_dir(slug)
    directory.mkdir(parents=True, exist_ok=True)
    name = f"{slug}--{now.strftime('%Y-%m-%dT%H%M%SZ')}.json"
    path = directory / name
    if path.exists() or (guest_dir(slug) / name).exists():
        raise FileExistsError(f"{path.name} already exists — an entry is "
                              "never modified; wait a second and re-run")

    entry = {
        "schema": SCHEMA,
        "guest": slug,
        "guest_name": guest_name,
        "sealed_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "league": league,
        "kickoff": ko.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "home": resolved["home"],
        "away": resolved["away"],
        "home_raw": home,
        "away_raw": away,
        "market": market,
        "selection": selection,
        "odds_taken": round(odds, 3),
        "book": book,
        "note": note,
        "prev_hash": prev,
    }
    if market == "AH":
        entry["line"] = line
    if reveal == "kickoff":
        # The hash is published before the entry is. A pick has a few hundred
        # possible values and could be guessed from its hash; the nonce is
        # what stops that.
        entry["reveal"] = "kickoff"
        entry["nonce"] = secrets.token_hex(16)
    # Import here, not at the top: ledger pulls in the model stack, and the
    # hash rule is the only thing needed from it.
    from .ledger import compute_hash
    entry["hash"] = compute_hash(entry)

    path.write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    log.info("sealed %s: %s %s v %s, %s %s @ %.3f", slug, league,
             entry["home"], entry["away"], market, selection, odds)

    if stamp:
        try:
            anchor.stamp(path, now=now)
        except Exception:
            log.exception("OpenTimestamps anchoring failed for %s — the "
                          "chain entry stands; the anchor gap stays visible",
                          path.name)
    return path


# --------------------------------------------------------------------------- #
#  Grading — closing line value and settlement
# --------------------------------------------------------------------------- #
def _match_for_entry(frame, entry: dict):
    """Join on the two clubs and tolerate a one-day source timezone shift."""
    hit = frame[(frame["HomeTeam"] == entry["home"])
                & (frame["AwayTeam"] == entry["away"])]
    if hit.empty:
        return None
    target = dt.date.fromisoformat(entry["kickoff"][:10])
    hit = hit.copy()
    hit["_date_gap"] = hit["Date"].dt.date.map(
        lambda value: abs((value - target).days))
    hit = hit[hit["_date_gap"] <= 1]
    if hit.empty:
        return None
    nearest = hit[hit["_date_gap"] == hit["_date_gap"].min()]
    return nearest.iloc[0] if len(nearest) == 1 else None


def _sealed_late(match, entry: dict, league: str) -> str | None:
    """
    Was this entry really sealed before the match started?

    The kickoff inside an entry is whatever the guest typed. Trusting it would
    let someone seal at half-time by stating an evening kickoff for an
    afternoon match. So the results feed's own kickoff time decides, and where
    the feed carries no time the entry must have been sealed before the match
    day began, in the latest timezone that could matter.

    Returns None when the entry stands, "late" when it was sealed at or after
    the recorded kickoff, and "time_unverified" when the feed has no time and
    the seal falls on the match day or after.
    """
    sealed = _utc(entry["sealed_at"])
    day = match["Date"].date()
    actual = guest_data.kickoff_utc(league, day, entry["home"], entry["away"])
    if actual is not None:
        return "late" if sealed >= actual else None
    # No time in the feed. Midnight UTC-12 is the earliest instant at which it
    # is this date anywhere, so a seal before it precedes any kickoff that day.
    earliest = dt.datetime.combine(day, dt.time(), dt.timezone.utc) \
        - dt.timedelta(hours=14)
    return None if sealed < earliest else "time_unverified"


def _asian_legs(line: float) -> list[float]:
    """A quarter line is two half-stakes on the adjacent half-goal lines."""
    quarters = round(float(line) * 4)
    if quarters % 2 == 0:  # integer or half goal: one ordinary bet
        return [quarters / 4]
    lower = (quarters // 2) / 2
    return [lower, lower + 0.5]


def _asian_settlement(goal_difference: int, line: float,
                      odds: float) -> tuple[str, float]:
    """Settle an Asian handicap from the selected team's perspective."""
    legs = []
    for leg in _asian_legs(line):
        adjusted = goal_difference + leg
        if adjusted > 1e-9:
            legs.append(("won", odds - 1.0))
        elif adjusted < -1e-9:
            legs.append(("lost", -1.0))
        else:
            legs.append(("push", 0.0))

    pnl = sum(value for _, value in legs) / len(legs)
    outcomes = [name for name, _ in legs]
    if outcomes == ["won", "push"] or outcomes == ["push", "won"]:
        result = "half won"
    elif outcomes == ["lost", "push"] or outcomes == ["push", "lost"]:
        result = "half lost"
    elif all(name == "won" for name in outcomes):
        result = "won"
    elif all(name == "lost" for name in outcomes):
        result = "lost"
    else:
        result = "push"
    return result, pnl


def _settle(match, entry: dict) -> tuple[str, float]:
    market, selection = entry["market"], entry["selection"]
    odds = float(entry["odds_taken"])
    if market == "1X2":
        won = match["FTR"] == selection
        return ("won" if won else "lost", odds - 1.0 if won else -1.0)
    if market == "OU2.5":
        over = int(match["FTHG"]) + int(match["FTAG"]) > 2
        won = over == (selection == "over")
        return ("won" if won else "lost", odds - 1.0 if won else -1.0)
    goal_difference = (int(match["FTHG"]) - int(match["FTAG"])
                       if selection == "H"
                       else int(match["FTAG"]) - int(match["FTHG"]))
    return _asian_settlement(goal_difference, float(entry["line"]), odds)


def _closing_quote(match, entry: dict) -> tuple[float | None,
                                                 float | None]:
    close = match.get(CLOSE_COLS[(entry["market"], entry["selection"])])
    if close is None or close != close or float(close) <= 1:
        return None, None
    close_line = None
    if entry["market"] == "AH":
        raw_line = match.get("AHCh")
        if raw_line is None or raw_line != raw_line:
            return None, None
        # AHCh is always written for the home side.  Entries store the line
        # from the selected team's perspective, so invert it for an away pick.
        close_line = float(raw_line)
        if entry["selection"] == "A":
            close_line = -close_line
    return float(close), close_line


def grade_guest(slug: str) -> dict:
    """
    Every sealed entry, joined to results, scored by CLV against the
    market-average close. Entries whose match has no closing price stay
    visibly pending rather than being quietly dropped.
    """
    entries = read_entries(slug)
    results_cache: dict[str, object] = {}
    rows = []

    for entry in entries:
        league = entry["league"]
        if league not in results_cache:
            try:
                results_cache[league] = guest_data.load_matches(league)
            except FileNotFoundError:
                results_cache[league] = None
        frame = results_cache[league]

        row = {
            "sealed_at": entry["sealed_at"],
            "kickoff": entry["kickoff"],
            "league": league,
            "competition": config.guest_competition_name(league),
            "home": entry["home"],
            "away": entry["away"],
            "market": entry["market"],
            "selection": entry["selection"],
            "line": entry.get("line"),
            "odds_taken": entry["odds_taken"],
            "book": entry.get("book", ""),
            "status": "pending",
            "close": None, "clv": None, "beat_close": None,
            "close_line": None, "line_advantage": None,
            "result": None, "won": None, "pnl": None,
        }
        if frame is not None:
            match = _match_for_entry(frame, entry)
            late = (_sealed_late(match, entry, league)
                    if match is not None else None)
            if late:
                # Shown, never scored. The guest stated a kickoff later than
                # the one the results feed records, or the feed gives no time
                # and the entry was sealed on the day.
                row["status"] = late
            elif match is not None:
                result, pnl = _settle(match, entry)
                row["result"], row["pnl"] = result, pnl
                if result in ("won", "lost"):
                    row["won"] = result == "won"  # guest-1 compatibility
                close, close_line = _closing_quote(match, entry)
                row["close"], row["close_line"] = close, close_line
                if close is None:
                    row["status"] = "no_close"
                elif entry["market"] == "AH" and abs(
                        float(entry["line"]) - float(close_line)) > 1e-8:
                    # Prices at different handicaps are different bets.  Show
                    # both lines and settle the entry, but never calculate a
                    # fake price CLV by dividing unlike selections.
                    row["status"] = "line_changed"
                    row["line_advantage"] = (float(entry["line"])
                                             - float(close_line))
                else:
                    row["status"] = "graded"
                    row["clv"] = entry["odds_taken"] / close - 1.0
                    row["beat_close"] = entry["odds_taken"] > close
        rows.append(row)

    graded = [r for r in rows if r["status"] == "graded"]
    settled = [r for r in rows if r["result"] is not None]
    if entries:
        name = entries[-1]["guest_name"]
    else:
        # Every entry is still held. The name is not a secret; the picks are.
        held = embargoed_files(slug)
        name = (json.loads(held[0].read_text(encoding="utf-8"))["guest_name"]
                if held else slug)
    return {
        "slug": slug,
        "name": name,
        "n_sealed": len(rows),
        "n_graded": len(graded),
        "n_pending": sum(r["status"] == "pending" for r in rows),
        "n_no_close": sum(r["status"] == "no_close" for r in rows),
        "n_line_changed": sum(r["status"] == "line_changed" for r in rows),
        "n_late": sum(r["status"] in ("late", "time_unverified") for r in rows),
        "n_embargoed": len(embargoed_files(slug)),
        "n_settled": len(settled),
        "beat_close": sum(r["beat_close"] for r in graded),
        "beat_close_pct": (sum(r["beat_close"] for r in graded) / len(graded)
                           if graded else None),
        "avg_clv": (sum(r["clv"] for r in graded) / len(graded)
                    if graded else None),
        "pnl": sum(r["pnl"] for r in settled) if settled else None,
        "first_sealed": rows[0]["sealed_at"][:10] if rows else None,
        "entries": rows,
    }


def all_guests() -> list[dict]:
    return [grade_guest(slug) for slug in guest_slugs()]


# --------------------------------------------------------------------------- #
#  Invite tokens — who may seal through the web form
# --------------------------------------------------------------------------- #
def _tokens() -> dict:
    if not TOKENS_FILE.exists():
        return {}
    return json.loads(TOKENS_FILE.read_text(encoding="utf-8"))


def _token_key(token: str) -> str:
    return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()


def invite(guest_name: str, contact: str = "",
           now: dt.datetime | None = None) -> str:
    """
    Issue a sealing token for one guest and return it. It is shown once.

    Only its SHA-256 is stored, so the file leaking does not let anyone seal
    under somebody else's name. `contact` stays in data/, never in the
    repository or on the site.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    slug = slugify(guest_name)
    table = _tokens()
    if any(row["slug"] == slug and not row.get("revoked")
           for row in table.values()):
        raise ValueError(f"{slug} already has a live token — revoke it first")
    token = "po_" + secrets.token_urlsafe(24)
    table[_token_key(token)] = {
        "slug": slug, "name": guest_name, "contact": contact,
        "created": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "revoked": False}
    _write_tokens(table)
    return token


def revoke(slug: str) -> int:
    table = _tokens()
    hits = [row for row in table.values()
            if row["slug"] == slug and not row.get("revoked")]
    for row in hits:
        row["revoked"] = True
    _write_tokens(table)
    return len(hits)


def _write_tokens(table: dict) -> None:
    TOKENS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = TOKENS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(table, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(TOKENS_FILE)


def guest_for_token(token: str) -> dict | None:
    row = _tokens().get(_token_key(token or ""))
    return row if row and not row.get("revoked") else None


# --------------------------------------------------------------------------- #
#  Publishing — released entries and proofs into the public repository
# --------------------------------------------------------------------------- #
def publish(now: dt.datetime | None = None, push: bool = True) -> list[Path]:
    """
    Release what is due, then commit every guest entry and proof not yet in
    the repository. Safe to run as often as wanted; it does nothing when
    there is nothing to do.
    """
    import subprocess

    released = release_due(now)

    def git(*args):
        return subprocess.run(["git", *args], cwd=str(config.ROOT),
                              capture_output=True, text=True)

    paths = []
    if config.GUESTS_DIR.exists():
        paths.append("guests")
    slugs = set(guest_slugs()) | set(embargoed_slugs())
    proofs = [p for p in config.TIMESTAMPS_DIR.glob("*--*.json.ots")
              if p.name.split("--")[0] in slugs]
    paths += [str(p.relative_to(config.ROOT)) for p in proofs]
    if not paths or not (config.ROOT / ".git").exists():
        return released
    git("add", "--", *paths)
    staged = git("diff", "--cached", "--name-only", "--", *paths).stdout.split()
    if not staged:
        return released
    done = git("commit", "-m", f"guests: {len(staged)} file(s)", "--", *paths)
    if done.returncode != 0:
        log.warning("guest commit failed: %s", done.stderr or done.stdout)
        return released
    if push:
        pushed = git("push")
        if pushed.returncode != 0:
            log.warning("guest push failed (committed locally): %s",
                        pushed.stderr.strip())
    return released


# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(
        prog="python -m proofodds.guest",
        description="Seal and grade guest predictions.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("seal", help="seal one guest entry before kickoff")
    s.add_argument("--guest", required=True, help='display name, e.g. "Theo Borges"')
    s.add_argument("--league", required=True, help="division code, e.g. E0")
    s.add_argument("--home", required=True)
    s.add_argument("--away", required=True)
    s.add_argument("--kickoff", required=True, help="UTC, e.g. 2026-09-12T18:30Z")
    s.add_argument("--market", required=True, choices=sorted(MARKETS))
    s.add_argument("--selection", required=True,
                   help="H/D/A for 1X2, over/under for OU2.5, H/A for AH")
    s.add_argument("--odds", required=True, type=float,
                   help="decimal odds actually taken")
    s.add_argument("--line", type=float, default=None,
                   help="selected-team Asian handicap, e.g. -0.5 or +0.25")
    s.add_argument("--book", default="", help="where the price was taken")
    s.add_argument("--note", default="")

    s.add_argument("--reveal", default="", choices=["", "kickoff"],
                   help="'kickoff' keeps the entry private until the match starts")

    inv = sub.add_parser("invite", help="issue a web-form sealing token")
    inv.add_argument("--guest", required=True)
    inv.add_argument("--contact", default="",
                     help="how to reach them; kept in data/, never published")
    rev = sub.add_parser("revoke", help="revoke a guest's sealing token")
    rev.add_argument("--guest", required=True)
    sub.add_parser("release", help="publish due entries, commit and push")

    sub.add_parser("show", help="grade and print every guest record")
    sync = sub.add_parser("sync", help="download creator-ledger result feeds")
    sync.add_argument("--leagues", default="",
                      help="comma-separated codes; default: every supported feed")
    sub.add_parser("coverage", help="list every measurable competition/market")

    args = ap.parse_args(argv)
    if args.cmd == "coverage":
        for code, meta in config.GUEST_COMPETITIONS.items():
            print(f"{code:>3}  {meta['country']:<12}  {meta['name']:<38}  "
                  f"{', '.join(meta['markets'])}")
        print(f"\n{len(config.GUEST_COMPETITIONS)} feeds. BTTS is absent because "
              "football-data.co.uk publishes no market-average BTTS close.")
        return 0

    if args.cmd == "sync":
        codes = ([part.strip().upper() for part in args.leagues.split(",")
                  if part.strip()] or list(config.GUEST_COMPETITIONS))
        unknown = sorted(set(codes) - set(config.GUEST_COMPETITIONS))
        if unknown:
            ap.error(f"unknown competition code(s): {', '.join(unknown)}")
        guest_data.refresh_many(codes)
        print(f"synced {len(codes)} creator competition feed(s)")
        return 0

    if args.cmd == "invite":
        token = invite(args.guest, args.contact)
        print(f"guest:  {args.guest}  ({slugify(args.guest)})")
        print(f"token:  {token}")
        print(f"form:   {config.SITE_URL.rstrip('/')}/seal/")
        print("Send the token privately. It is not stored and cannot be "
              "shown again; revoke and re-invite if it is lost.")
        return 0

    if args.cmd == "revoke":
        print(f"revoked {revoke(slugify(args.guest))} token(s)")
        return 0

    if args.cmd == "release":
        released = publish()
        print(f"released {len(released)} entr{'y' if len(released) == 1 else 'ies'}")
        return 0

    if args.cmd == "seal":
        path = seal(guest_name=args.guest, league=args.league, home=args.home,
                    away=args.away, kickoff=args.kickoff, market=args.market,
                    selection=args.selection, odds=args.odds, line=args.line,
                    book=args.book, note=args.note, reveal=args.reveal)
        print(f"sealed: {path}")
        print(f"verify: python proofodds/verify.py {path.parent}")
        return 0

    for record in all_guests():
        print(f"\n{record['name']} ({record['slug']}) — "
              f"{record['n_sealed']} sealed, {record['n_graded']} graded, "
              f"{record['n_pending']} pending")
        if record["n_graded"]:
            print(f"  beat close: {record['beat_close']}/{record['n_graded']} "
                  f"({record['beat_close_pct']:.0%})   "
                  f"avg CLV: {record['avg_clv']:+.2%}   "
                  f"flat P/L: {record['pnl']:+.2f}u")
        for r in record["entries"]:
            line = (f"  {r['kickoff'][:16]}Z {r['league']} "
                    f"{r['home']} v {r['away']} — {r['market']} "
                    f"{r['selection']} @ {r['odds_taken']}")
            if r["line"] is not None:
                line += f" line {r['line']:+g}"
            if r["status"] == "graded":
                line += (f" | close {r['close']:.3f} clv {r['clv']:+.2%} "
                         f"{r['result']}")
            else:
                line += f" | {r['status']}"
                if r["result"]:
                    line += f" {r['result']}"
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
