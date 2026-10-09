#!/usr/bin/env python3
"""
After a reboot: is everything that should be running, running?

Run once per boot by deploy/proofodds-postboot.service, two minutes after the
network is up, as the proofodds user. Six checks. The result goes to
alerts.log and out through alert.py either way — "all came back" is worth a
notification after a reboot, because silence could mean the check never ran.

    python scripts/postboot_check.py

CTFd is checked because it shares this machine and a reboot takes it down
too. It is only looked at, never touched.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from proofodds import alert, config, ledger  # noqa: E402

CTFD_URL = os.environ.get("PROOFODDS_POSTBOOT_CTFD_URL",
                          "https://ctf.packetlab.blog/")
UNITS = ("proofodds.timer", "proofodds-release.timer", "proofodds-traffic.timer",
         "proofodds-weekly.timer", "proofodds-seal.service", "fail2ban.service")


def http_ok(url: str) -> tuple[bool, str]:
    try:
        status = requests.get(url, timeout=20).status_code
    except requests.RequestException as exc:
        return False, type(exc).__name__
    return status == 200, f"HTTP {status}"


def unit_active(unit: str) -> tuple[bool, str]:
    done = subprocess.run(["systemctl", "is-active", unit],
                          capture_output=True, text=True)
    state = done.stdout.strip() or "unknown"
    return state == "active", state


def chain_ok() -> tuple[bool, str]:
    report = ledger.verify_chain()
    if report["ok"]:
        return True, f"CHAIN OK, {report['n_entries']} entries"
    return False, f"CHAIN BROKEN: {report['broken']}"


def run_checks() -> list[tuple[str, bool, str]]:
    site = config.SITE_URL.rstrip("/")
    checks = [("proofodds.com answers", *http_ok(site + "/")),
              ("/api/health answers", *http_ok(site + "/api/health"))]
    checks += [(f"{unit} active", *unit_active(unit)) for unit in UNITS]
    checks += [("CTFd answers", *http_ok(CTFD_URL)),
               ("ledger chain", *chain_ok())]
    return checks


def summarise(checks: list[tuple[str, bool, str]]) -> tuple[bool, str, str]:
    """(all passed, title, body) for the alert."""
    failed = [name for name, ok, _ in checks if not ok]
    body = "\n".join(f"{'ok  ' if ok else 'FAIL'}  {name} ({detail})"
                     for name, ok, detail in checks)
    if failed:
        return False, f"after reboot: {len(failed)} check(s) FAILED", body
    return True, f"after reboot: all {len(checks)} checks passed", body


def main() -> int:
    ok, title, body = summarise(run_checks())
    alert.send("postboot", title, body, urgent=not ok, force=True)
    print(title)
    print(body)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
