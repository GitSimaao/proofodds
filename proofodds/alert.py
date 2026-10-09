"""
Telling the operator, somewhere he will actually look.

Until now a failed run wrote to failures.log and a stale feed wrote to the
journal. Both are files on a server. The fixture feed went quiet for a week
and nobody was told, because nothing here could tell anybody.

An alert is one HTTP POST to PROOFODDS_ALERT_URL. The format is ntfy's
(https://ntfy.sh): the body is the message, the Title header is the subject.
Pointing it at an ntfy topic puts the message on a phone as a push
notification, with no account. Any endpoint that accepts a plain POST works.

Every alert is also appended to alerts.log, sent or not, so that an unset or
broken URL loses the notification but not the record.

    python -m proofodds.alert test
    python -m proofodds.alert failed proofodds.service
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import shutil
import sys

import requests

from . import config

log = logging.getLogger(__name__)

ALERT_URL = os.environ.get("PROOFODDS_ALERT_URL", "")
ALERT_LOG = config.ROOT / "alerts.log"
STATE = config.DATA_DIR / "alert_state.json"
# The same problem is reported once a day, not eight times.
REPEAT_HOURS = 24
# A feed has "collapsed" when it has left this share of the divisions it
# serves without fixtures, for reasons that are the feed's fault.
COLLAPSE_SHARE = 0.4
# A full disk stops sealing, and a held pick that cannot be written is lost.
DISK_ALERT_PERCENT = float(os.environ.get("PROOFODDS_DISK_ALERT_PERCENT", "85"))


def _state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def send(key: str, title: str, body: str, *, urgent: bool = False,
         now: dt.datetime | None = None, force: bool = False) -> bool:
    """Record an alert and deliver it. Returns True if it was delivered."""
    now = now or dt.datetime.now(dt.timezone.utc)
    state = _state()
    last = state.get(key)
    if last and not force:
        age = now - dt.datetime.fromisoformat(last)
        if age < dt.timedelta(hours=REPEAT_HOURS):
            return False

    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    delivered = False
    if ALERT_URL:
        try:
            resp = requests.post(
                ALERT_URL, data=body.encode("utf-8"), timeout=15,
                headers={"Title": f"ProofOdds: {title}",
                         "Priority": "urgent" if urgent else "default",
                         "Tags": "warning"})
            delivered = resp.status_code < 300
            if not delivered:
                log.warning("alert endpoint answered %s", resp.status_code)
        except requests.RequestException as exc:
            log.warning("alert could not be delivered: %s", exc)
    else:
        log.warning("PROOFODDS_ALERT_URL is not set — alert recorded in "
                    "alerts.log only: %s", title)

    try:
        ALERT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with ALERT_LOG.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp}  {'SENT    ' if delivered else 'NOT SENT'}"
                         f"  {title}\n    {body.replace(chr(10), chr(10) + '    ')}\n")
        state[key] = now.isoformat()
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError as exc:
        log.warning("could not record alert: %s", exc)
    return delivered


def clear(key: str) -> None:
    state = _state()
    if state.pop(key, None) is not None:
        try:
            STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except OSError:
            pass


def coverage_problem(coverage: dict | None) -> str | None:
    """
    Has a fixture source stopped serving the divisions that depend on it?

    A division with no match in the window is not a problem; an international
    break looks like that across every top flight at once. A division is
    counted only when its stated reason is that the feed is stale, failed or
    empty.
    """
    if not coverage:
        return None
    requested = coverage.get("requested") or []
    broken = [row for row in coverage.get("missing", [])
              if any(word in row.get("reason", "").lower()
                     for word in ("stale", "failed", "missing or empty", "unreachable", "no token",
                                  "returned", "error", "not set"))]
    if not requested or len(broken) / len(requested) < COLLAPSE_SHARE:
        return None
    codes = ", ".join(row["league"] for row in broken)
    return (f"{len(broken)} of {len(requested)} divisions have no fixtures "
            f"because their feed is not delivering: {codes}.\n"
            f"First reason given: {broken[0]['reason']}\n"
            "Nothing in those divisions is being sealed until this is fixed.")


def check_coverage(coverage: dict | None) -> None:
    problem = coverage_problem(coverage)
    if problem:
        send("coverage", "fixture coverage has collapsed", problem)
    else:
        clear("coverage")


def check_disk() -> float:
    """Alert when the disk holding the ledger is nearly full. Returns % used."""
    usage = shutil.disk_usage(config.ROOT)
    percent = round(100 * usage.used / usage.total, 1)
    if percent > DISK_ALERT_PERCENT:
        send("disk", f"disk is {percent:.0f}% full",
             f"{usage.free / 2**30:.1f} GB free of {usage.total / 2**30:.0f} GB. "
             "A full disk stops sealing and puts held picks at risk. "
             "Start with: du -xh --max-depth=2 / | sort -h | tail -25")
    else:
        clear("disk")
    return percent


def check_provenance(provenance: dict) -> None:
    """
    Alert when the live site was built from code a reader cannot clone.

    The provenance line on each page only helps someone who reads it, and
    three times nobody did. `dirty` and `published` are True, False or None;
    None means git could not say, and nothing is claimed from it.
    """
    commit = (provenance.get("commit") or "?")[:10]
    problems = []
    if provenance.get("dirty") is True:
        problems.append("the working tree had uncommitted changes")
    if provenance.get("published") is False:
        problems.append(f"commit {commit} is not on origin/main")
    if not problems:
        if provenance.get("dirty") is False and provenance.get("published"):
            clear("provenance")
        return
    send("provenance", "the site is built from code not in git",
         f"The live site was built from {commit} and "
         + " and ".join(problems)
         + ". The site says it can be reproduced from the repository; right "
           "now that is false. Commit and push from /opt/proofodds, as the "
           "proofodds user.")


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if len(argv) >= 2 and argv[1] == "test":
        ok = send("test", "test alert",
                  "If you can read this on your phone, alerts work.", force=True)
        print("delivered" if ok else
              "NOT delivered — is PROOFODDS_ALERT_URL set in .env?")
        return 0 if ok else 1
    if len(argv) >= 3 and argv[1] == "failed":
        send(f"failed:{argv[2]}", f"{argv[2]} failed",
             f"{argv[2]} exited non-zero. The chain did not verify, or "
             "fixtures came back and none were sealed, or sealing raised.\n"
             "Details: /opt/proofodds/failures.log", urgent=True, force=True)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
