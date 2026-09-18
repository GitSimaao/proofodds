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

Exit code
---------
proofodds-failed@ fires only on a non-zero exit, so the exit code is the
alarm and the list of what trips it is a real decision, not a detail.

FATAL (exit 1):
  * the chain does not verify;
  * fixtures came back and none of them were sealed;
  * sealing raised.

NOT fatal (exit 0), each for a stated reason:
  * no fixtures at all — a quiet day. An international break, a winter
    shutdown and the gap between seasons are indistinguishable from this, and
    an alarm that fires through all of them gets muted before the real one;
  * today's entry already on disk — the timer fires eight times and only the
    00:07 run seals;
  * some divisions missing while others sealed — partial coverage is the
    current, disclosed reality; on 18 September that was 14 of 23, and paging
    on it would page every run;
  * anchoring failed — a proof is an addition to the record, never a gate;
  * git commit or push failed — the entry is sealed on disk and the next run
    pushes it. A network blip must not page. (A push failing for DAYS is a
    real problem, but it is a different alarm: remote staleness, not this run.)
  * the build came from a commit that is not on the remote — a publishing gap
    a push closes, not a bad run. It is warned about here and stated on the
    site itself, which is the half that gets noticed.

A fatal condition is recorded and the run continues to the end. The site is
still graded and rebuilt, because it reports the chain's state honestly and a
page saying "broken" is more use than yesterday's page saying nothing.
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

    # Conditions that make this run a FAILURE, i.e. exit non-zero so that
    # proofodds-failed@ fires. They are collected rather than raised: a broken
    # chain must still be graded and rebuilt, because the site reports the
    # chain's state honestly and a page saying "broken" is more use than
    # yesterday's page saying nothing. The run reports the failure at the end,
    # having done everything it could still do.
    failures: list[str] = []

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
        # The coverage report travels with the fixtures into the sealed entry,
        # so a division whose feed failed is a line in the file rather than a
        # line in a log nobody reads.
        upcoming, coverage = fixtures.upcoming_with_coverage(leagues)

        now = dt.datetime.now(dt.timezone.utc)

        # Sealing is the one step whose failure this job must not survive
        # quietly. Distinguishing its outcomes needs one fact `publish` cannot
        # return, because it returns None for three different situations: was
        # today's entry already on disk before this run?
        #
        #   already on disk        -> a later run of the same day. Normal:
        #                             the timer fires eight times and only the
        #                             00:07 run seals.
        #   no fixtures at all     -> a quiet day. NORMAL, NOT AN ERROR. An
        #                             international break, a winter shutdown
        #                             and the gap between seasons all look
        #                             like this, and a job that pages through
        #                             every one of them is a job whose alarms
        #                             get muted before the real one arrives.
        #   fixtures, nothing sealed -> FATAL. We were handed matches we could
        #                             have priced and the chain got none of
        #                             them: every division held by the
        #                             unresolved-name guard, every model
        #                             unfittable, or every fixture already
        #                             kicked off. Whatever the cause, today's
        #                             forecasts do not exist and cannot be
        #                             made later — the ledger is append-only
        #                             and a prediction after kickoff is worse
        #                             than none.
        entry_path = config.PREDICTIONS_DIR / f"{now.date().isoformat()}.json"
        sealed_before = entry_path.exists()
        try:
            path = ledger.publish(upcoming, now=now, coverage=coverage)
        except Exception:
            log.exception("SEALING FAILED — no entry written")
            path = None
            failures.append("sealing raised an exception")
        else:
            if path is None and not sealed_before:
                if not upcoming:
                    log.warning("no fixture in the next %d days — nothing to "
                                "seal. A quiet day is a normal outcome, not a "
                                "failure.", config.LOOKAHEAD_DAYS)
                else:
                    log.error("SEALING FAILED — %d fixture(s) came back and "
                              "NONE were sealed; today has no entry",
                              len(upcoming))
                    failures.append(
                        f"{len(upcoming)} fixture(s) returned but none sealed")

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
        # The single most serious thing this job can discover, and until now
        # the only consequence was a line in a journal. Everything the site
        # claims rests on the chain recomputing; if it does not, nothing else
        # that happened on this run matters.
        log.error("LEDGER CHAIN BROKEN: %s", report["broken"])
        failures.append(f"chain does not verify: {report['broken']}")
    else:
        log.info("chain ok, %d entries, head %s",
                 report["n_entries"], report["head"][:12])

    log.info("building site")
    # Build into staging and swap, rather than deleting the directory nginx is
    # serving and refilling it page by page.
    render.publish_site()

    # Did this build come from code a reader can actually clone?
    #
    # On a normal run it always does: the push at step 5 happens before this
    # build. It is false when somebody deployed by hand without pushing, which
    # is how the site twice ended up serving code that was not in the
    # repository. Logged here, and said out loud on the site itself, because a
    # log line alone is exactly what nobody read the first two times.
    #
    # NOT fatal, for the same reason a failed push is not: the run sealed,
    # anchored and graded correctly, and the entries are on disk. An unpushed
    # commit is a publishing gap that a push closes, not a bad run, and paging
    # for it would train people to ignore the page that means a broken chain.
    provenance = render.build_provenance()
    if provenance["published"] is False:
        log.warning("the site was built from %s, which is NOT on origin/main — "
                    "a reader cloning the repository cannot reproduce this "
                    "build. The site now says so on /ledger/. Push it.",
                    (provenance["commit"] or "?")[:10])

    if failures:
        log.error("RUN FAILED — %s", "; ".join(failures))
        return 1

    log.info("done — %s", config.SITE_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
