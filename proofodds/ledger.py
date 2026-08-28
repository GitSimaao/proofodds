"""
The prediction ledger — the part of this project that makes the claim credible.

Every run writes one JSON file per publication day into predictions/. A file is
never rewritten. Each file carries the SHA-256 of the previous one, so the
whole directory is a hash chain: change any past prediction without rebuilding
every later hash and verification fails, visibly, on the public scorecard.

Every run also pushes to a public git repository, which makes the history
observable: rebuilding the chain to hide a change means rewriting every later
file and force-pushing, and anyone who cloned it earlier can see that.

What the chain does NOT prove is *when* an entry existed. A commit date is a
setting and a repository owner can rewrite history, so git gives observability,
not proof of time. New entries therefore receive a detached OpenTimestamps
proof. The public ledger keeps older chain-only entries, pending calendar
proofs, and matching proofs with Bitcoin attestations visibly separate.

Two rules are enforced in code, not by discipline:

  1. A prediction is only written for a match whose kickoff is in the future.
  2. A file for a date that already exists is never modified.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import subprocess
from pathlib import Path

import numpy as np

from . import config, dixon_coles as dc
from .data import load_matches, sealed_name
from .fixtures import Fixture

log = logging.getLogger(__name__)

SCHEMA_VERSION = 4
GENESIS = "0" * 64

# Files whose contents can change the numbers sealed into an entry.  The git
# commit is the readable identifier; this digest is the exact one.  Together
# they avoid a subtle lie: a commit names the checked-in source, but a process
# can be running with an uncommitted edit.  `dirty` says that happened and the
# digest still identifies the bytes that actually ran.
GENERATOR_FILES = (
    "proofodds/config.py",
    "proofodds/data.py",
    "proofodds/dixon_coles.py",
    "proofodds/fixtures.py",
    "proofodds/ledger.py",
    "requirements.txt",
)


# --------------------------------------------------------------------------- #
#  Hashing
# --------------------------------------------------------------------------- #
def canonical_json(payload: dict) -> str:
    """Stable serialisation: sorted keys, no whitespace surprises."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def compute_hash(payload: dict) -> str:
    body = {k: v for k, v in payload.items() if k != "hash"}
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def generator_source_hash(paths=None) -> str:
    """SHA-256 of the exact source files that can produce a prediction."""
    paths = paths or [config.ROOT / name for name in GENERATOR_FILES]
    digest = hashlib.sha256()
    for path in sorted((Path(p) for p in paths), key=lambda p: str(p)):
        try:
            name = path.relative_to(config.ROOT).as_posix()
        except ValueError:
            name = path.name
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def generator_identity() -> dict:
    """
    Identify both the checked-in generator and the bytes actually executed.

    The source digest works without git.  In a normal deployment `commit`
    makes the version easy to inspect on GitHub, while `dirty` prevents that
    friendly name from being mistaken for an exact description of a modified
    working tree.
    """
    commit = None
    dirty = None
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=config.ROOT,
            capture_output=True, text=True, timeout=5)
        if head.returncode == 0:
            commit = head.stdout.strip() or None
            status = subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=no", "--",
                 *GENERATOR_FILES],
                cwd=config.ROOT, capture_output=True, text=True, timeout=5)
            dirty = bool(status.stdout.strip()) if status.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        pass

    return {
        "commit": commit,
        "dirty": dirty,
        "source_sha256": generator_source_hash(),
    }


def ledger_files() -> list[Path]:
    return sorted(config.PREDICTIONS_DIR.glob("*.json"))


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def last_entry() -> dict | None:
    files = ledger_files()
    return read(files[-1]) if files else None


def verify_chain() -> dict:
    """
    Recompute every hash and every link. Returns a report the site renders.

    `ok` false means somebody edited history — which is exactly what this
    machinery exists to make impossible to do quietly.
    """
    files = ledger_files()
    prev = GENESIS
    broken = []

    for path in files:
        entry = read(path)
        recomputed = compute_hash(entry)
        if recomputed != entry.get("hash"):
            broken.append({"file": path.name, "reason": "content hash mismatch"})
        elif entry.get("prev_hash") != prev:
            broken.append({"file": path.name, "reason": "broken link to previous entry"})
        prev = entry.get("hash", "")

    return {
        "ok": not broken,
        "n_entries": len(files),
        "head": prev if files else GENESIS,
        "broken": broken,
    }


# --------------------------------------------------------------------------- #
#  Publishing
# --------------------------------------------------------------------------- #
def _model_for(now: dt.datetime, league: str = "E0", extra_teams=()):
    """
    Fit on everything played strictly before `now`. No exceptions, ever.

    `extra_teams` lets a club with no history at all — freshly promoted, never
    in this league during the data window — still be priced. It is appended to
    the team list (never re-sorted, so existing ids stay valid) and the Gaussian
    prior puts it at exactly league average, which is what the prior is for.
    Skipping such a fixture would be worse: a match silently missing from a
    ledger that claims to be complete.
    """
    matches = load_matches(league)
    teams = list(matches.attrs["teams"])
    for name in extra_teams:
        if name and name not in teams:
            teams.append(name)
            log.warning("no history for %r — pricing it at league average", name)

    cutoff = np.datetime64(now.date())
    past = matches[matches["Date"].to_numpy(dtype="datetime64[D]") < cutoff]
    if len(past) < 100:
        raise RuntimeError("not enough history to fit a model")

    model = dc.fit_from_frame(past, teams, ref_date=cutoff,
                              xi=config.XI, prior_sd=config.PRIOR_SD)
    return model, teams, past


def build_entry(fixtures: list[Fixture], now: dt.datetime) -> dict | None:
    """
    Score every fixture that has not kicked off yet, across every division.

    One entry per publication day covers all divisions: the chain stays a
    single line, which is what makes it checkable, and each row carries the
    division it belongs to. A division whose model cannot be fitted — too
    little history, a download that failed — is recorded as skipped inside the
    entry rather than quietly left out. The file has to say what it does not
    contain, or "complete" means nothing.
    """
    future = [f for f in fixtures if f.kickoff > now]
    if not future:
        log.info("no future fixtures to publish")
        return None

    by_league: dict[str, list[Fixture]] = {}
    for fx in future:
        by_league.setdefault(fx.league, []).append(fx)

    rows: list[dict] = []
    models: dict[str, dict] = {}
    skipped: list[dict] = []

    for league in sorted(by_league):
        block = by_league[league]
        needed = sorted({f.home for f in block} | {f.away for f in block})
        try:
            model, teams, past = _model_for(now, league, extra_teams=needed)
        except Exception as exc:
            log.error("%s: cannot fit a model (%s) — %d fixtures not published",
                      league, exc, len(block))
            skipped.append({"league": league, "n": len(block),
                            "reason": str(exc)[:200]})
            continue

        index = {t: i for i, t in enumerate(teams)}

        # How much each club actually contributes to the fit — the TIME-WEIGHTED
        # count, not the raw one. A club that played 76 matches ten years ago has
        # an effective sample of almost nothing under a 347-day half-life, and its
        # rating is really the prior. Counting raw appearances would hide that.
        weights = dc.time_weights(past["Date"].to_numpy(),
                                  np.datetime64(now.date()), config.XI)
        effective: dict[str, float] = {}
        for name, w in zip(past["HomeTeam"], weights):
            effective[name] = effective.get(name, 0.0) + w
        for name, w in zip(past["AwayTeam"], weights):
            effective[name] = effective.get(name, 0.0) + w

        for fx in block:
            h, a = index[fx.home], index[fx.away]
            probs = model.outcome_probs(h, a)
            lam, mu = model.expected_goals(h, a)
            thin = [name for name in (fx.home, fx.away)
                    if effective.get(name, 0.0) < config.COLD_START_MATCHES]

            # Round to six places, then put the rounding residue back into the
            # largest component so the three published numbers sum to exactly 1.
            # Three independently rounded values can sum to 1.000001, and a
            # ledger that invites people to check its arithmetic should not ship
            # numbers that fail the first check anyone would run.
            p = [round(float(x), 6) for x in probs]
            big = max(range(3), key=lambda i: p[i])
            p[big] = round(1.0 - sum(p[i] for i in range(3) if i != big), 6)

            # Total goals, read out of the same scoreline grid. No second
            # model, no second set of assumptions — one fit, two markets.
            totals = model.totals_probs(h, a, config.TOTALS_LINE)
            p_over = round(float(totals[0]), 6)
            p_under = round(1.0 - p_over, 6)

            row = {
                "league": league,
                "kickoff": fx.kickoff.astimezone(dt.timezone.utc)
                                     .strftime("%Y-%m-%dT%H:%M:%SZ"),
                "home": fx.home,
                "away": fx.away,
                "p_H": p[0],
                "p_D": p[1],
                "p_A": p[2],
                "p_over25": p_over,
                "p_under25": p_under,
                "xg_home": round(float(lam), 4),
                "xg_away": round(float(mu), 4),
                "cold_start": thin,
            }
            # Seal the fixture feed's own spelling whenever it differs from the
            # one we grade on. It costs a few bytes and it means no naming
            # mistake is ever permanent: the entry always carries enough to
            # redo the mapping later, without rewriting a single past file.
            if fx.home_raw != fx.home:
                row["home_raw"] = fx.home_raw
            if fx.away_raw != fx.away:
                row["away_raw"] = fx.away_raw
            if not fx.resolved:
                row["name_provisional"] = True
            # Sealed only when it is true, so entries where every kickoff is
            # confirmed stay exactly as they were before this existed.
            if not fx.time_confirmed:
                row["kickoff_tbc"] = True
            rows.append(row)

        models[league] = {
            "name": "dixon-coles",
            "xi": config.XI,
            "prior_sd": config.PRIOR_SD,
            "trained_through": str(past["Date"].max().date()),
            "n_train": int(len(past)),
            "home_advantage": round(model.gamma, 4),
            "rho": round(model.rho, 4),
            "league_mean_goals": round(model.league_mean, 4),
            "totals_line": config.TOTALS_LINE,
        }

    if not rows:
        return None

    rows.sort(key=lambda r: (r["kickoff"], r["league"], r["home"]))
    previous = last_entry()
    payload = {
        "version": SCHEMA_VERSION,
        "leagues": sorted(models),
        "published_at": now.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "prev_hash": previous["hash"] if previous else GENESIS,
        "generator": generator_identity(),
        "models": models,
        "predictions": rows,
    }
    if skipped:
        payload["skipped"] = skipped
    payload["hash"] = compute_hash(payload)
    return payload


def publish(fixtures: list[Fixture], now: dt.datetime | None = None) -> Path | None:
    """
    Write today's entry. Refuses to touch a file that already exists.

    Returns the path written, or None if there was nothing new.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    config.PREDICTIONS_DIR.mkdir(parents=True, exist_ok=True)

    path = config.PREDICTIONS_DIR / f"{now.date().isoformat()}.json"
    if path.exists():
        log.info("%s already published — leaving it alone", path.name)
        return None

    entry = build_entry(fixtures, now)
    if entry is None:
        return None

    path.write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    log.info("published %d predictions across %s to %s (hash %s)",
             len(entry["predictions"]), ",".join(entry["leagues"]),
             path.name, entry["hash"][:12])
    return path


def all_predictions() -> list[dict]:
    """
    Every prediction ever published, flattened, oldest first.

    Where the same fixture was published on several days, the FIRST publication
    wins. Publishing earlier is harder, so grading the earliest entry is the
    conservative choice — and it stops a later, better-informed prediction from
    quietly replacing an earlier one.

    The key is the club names as they will be GRADED, never as they were
    sealed. Those differ the moment a name that could not be resolved in
    August resolves in September: Coventry was sealed as "Coventry City FC" on
    the 26th and as "Coventry" on the 27th, and keying on the raw strings let
    the same match through twice — two cards on the front page with different
    probabilities, and worse, one match counted twice in the log loss.
    """
    seen, out = set(), []
    for path in ledger_files():
        entry = read(path)
        # Schema 1 named the division once, at entry level, because there was
        # only ever one. Schema 2 names it per row. Reading both is what lets
        # the earlier entries stay exactly as they were sealed.
        default_league = entry.get("league", "E0")
        for row in entry["predictions"]:
            league = row.get("league", default_league)
            key = (league, row["kickoff"][:10],
                   sealed_name(row["home"], league, row.get("home_raw", "")),
                   sealed_name(row["away"], league, row.get("away_raw", "")))
            if key in seen:
                continue
            seen.add(key)
            out.append({**row,
                        "published_at": entry["published_at"],
                        "entry_hash": entry["hash"],
                        "entry_file": path.name,
                        "league": league})
    return out
