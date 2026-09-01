#!/usr/bin/env python3
"""
The daily job. One command, run by a systemd timer.

    python scripts/daily.py            # everything
    python scripts/daily.py --no-git   # don't commit the ledger
    python scripts/daily.py --build-only

Order matters and is deliberate:

  1. refresh results (yesterday's matches are now history)
  2. fetch fixtures
  3. seal today's predictions   <- must happen before any kickoff
  4. timestamp the entry        <- independent proof of time, from this point on
  5. commit the entry + proof   <- makes the history publicly observable
  6. grade and rebuild the site

If step 3 fails, nothing is published rather than something being published
late. A prediction that appears after kickoff is worse than no prediction: it
quietly poisons the one claim the whole site rests on.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from proofodds import (anchor, config, data, fixtures, guest, guest_data,
                       ledger, render)  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("daily")


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(config.ROOT),
                          capture_output=True, text=True)


def commit_ledger(paths: list[Path], entry_path: Path | None = None) -> None:
    """
    Commit the new entry.

    Pushing makes the history observable — hiding a change would mean rewriting
    every later file and force-pushing, in public. It is not proof of *when*:
    a commit date is a setting. The detached OpenTimestamps proof supplies
    that separate evidence once it has a Bitcoin block attestation.
    """
    if not (config.ROOT / ".git").exists():
        log.warning("not a git repository — skipping commit (the public repo "
                    "is half the credibility argument; set one up)")
        return

    paths = list(dict.fromkeys(path for path in paths if path.exists()))
    if not paths:
        return
    git("add", *(str(path.relative_to(config.ROOT)) for path in paths))
    message = (f"predictions: {entry_path.stem}"
               if entry_path else "timestamps: upgrade proofs")
    result = git("commit", "-m", message)
    if result.returncode != 0 and "nothing to commit" not in result.stdout:
        log.warning("git commit failed: %s", result.stdout or result.stderr)
        return

    push = git("push")
    if push.returncode != 0:
        log.warning("git push failed (entry is committed locally): %s",
                    push.stderr.strip())
    else:
        log.info("ledger pushed")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-git", action="store_true")
    ap.add_argument("--build-only", action="store_true")
    ap.add_argument("--leagues", default="",
                    help="comma-separated division codes; default is "
                         "config.ENABLED_LEAGUES")
    args = ap.parse_args()

    leagues = ([c.strip().upper() for c in args.leagues.split(",") if c.strip()]
               or list(config.ENABLED_LEAGUES))

    if not args.build_only:
        log.info("refreshing results for %s", ", ".join(leagues))
        data.refresh(leagues)

        # Model leagues on this run were refreshed one line above.  Do not
        # download their last two files twice merely because a creator also
        # has an entry there.
        creator_leagues = [code for code in guest.used_competitions()
                           if code not in leagues]
        if creator_leagues:
            log.info("refreshing creator-ledger results for %s",
                     ", ".join(creator_leagues))
            try:
                guest_data.refresh_many(creator_leagues)
            except Exception:
                # A stale creator table is explicit (pending/no close).  It
                # must not stop the main prediction ledger being sealed and
                # published on time.
                log.exception("creator result refresh incomplete — continuing "
                              "with the last good cache")

        log.info("fetching fixtures")
        upcoming = fixtures.upcoming(leagues)

        now = dt.datetime.now(dt.timezone.utc)
        path = ledger.publish(upcoming, now=now)

        # Timestamping is an addition to the record, never a gate on it.
        #
        # anchor.maintain() guards the OpenTimestamps subprocess itself, which
        # covers the network failure it was written for. It does not cover the
        # rest: a malformed entry, an unreadable proof, a failed replace. An
        # unhandled error here would leave the seal safely on disk from the
        # line above and then skip the commit, the push and the site rebuild —
        # every run, three hours apart, until somebody read the logs. The
        # record would keep being sealed and quietly stop being published,
        # which is the one failure this project cannot afford to have running
        # unattended.
        try:
            proof_changes = anchor.maintain(now=now)
        except Exception:
            log.exception("anchoring failed — publishing the entry anyway")
            proof_changes = []

        artifacts = ([path] if path else []) + proof_changes
        if artifacts and not args.no_git:
            commit_ledger(artifacts, entry_path=path)

    log.info("verifying chain")
    report = ledger.verify_chain()
    if not report["ok"]:
        log.error("LEDGER CHAIN BROKEN: %s", report["broken"])
    else:
        log.info("chain ok, %d entries, head %s",
                 report["n_entries"], report["head"][:12])

    log.info("building site")
    render.build()
    log.info("done — %s", config.SITE_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
