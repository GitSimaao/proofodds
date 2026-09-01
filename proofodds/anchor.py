"""
External time anchors for sealed prediction files.

The SHA-256 chain proves order and internal consistency.  It does not prove
when a chain head existed, because its owner can rebuild the chain and rewrite
git history.  An OpenTimestamps proof closes that separate gap by committing a
prediction file to public calendar servers and, after upgrade, to a Bitcoin
block header.

Proofs start with the first entry successfully submitted after this module is
deployed.  Older entries are deliberately not stamped after the fact: doing so
would turn "anchored now" into something that looked like "anchored when
published".  The public report keeps chain coverage and anchor coverage apart.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import config, ledger

log = logging.getLogger(__name__)

BLOCK_RE = re.compile(r"BitcoinBlockHeaderAttestation\((\d+)\)")
PENDING_MARKER = "PendingAttestation("
FILE_HASH_RE = re.compile(r"File sha256 hash:\s*([0-9a-f]{64})", re.IGNORECASE)


def command() -> str | None:
    """Find the client installed beside this Python, with an env override."""
    override = os.environ.get("PROOFODDS_OTS_COMMAND", "").strip()
    if override:
        return override
    beside_python = Path(sys.executable).with_name("ots")
    if beside_python.exists():
        return str(beside_python)
    return shutil.which("ots")


def proof_path(entry_path: Path) -> Path:
    return config.TIMESTAMPS_DIR / f"{entry_path.name}.ots"


def _run(*args: str, timeout: int = 90) -> subprocess.CompletedProcess | None:
    executable = command()
    if not executable:
        log.warning("OpenTimestamps client is not installed — no external anchor created")
        return None
    try:
        return subprocess.run(
            [executable, *args], cwd=config.ROOT, capture_output=True,
            text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("OpenTimestamps %s failed: %s", args[0], exc)
        return None


def inspect(proof: Path, entry_path: Path | None = None) -> dict:
    """Classify a proof, and only attest a block if it matches the JSON."""
    if not proof.exists():
        return {"status": "none", "blocks": []}
    result = _run("info", str(proof), timeout=20)
    if result is None or result.returncode != 0:
        return {"status": "proof", "blocks": []}
    text = f"{result.stdout}\n{result.stderr}"
    if entry_path is not None:
        recorded = {value.lower() for value in FILE_HASH_RE.findall(text)}
        expected = hashlib.sha256(Path(entry_path).read_bytes()).hexdigest()
        if recorded and expected not in recorded:
            return {"status": "mismatch", "blocks": []}
        if not recorded:
            # A block inside an opaque or unexpected client response is not
            # enough to claim that this particular JSON was timestamped.
            return {"status": "proof", "blocks": []}
    blocks = sorted({int(n) for n in BLOCK_RE.findall(text)})
    if blocks:
        return {"status": "attested", "blocks": blocks}
    if PENDING_MARKER in text:
        return {"status": "pending", "blocks": []}
    return {"status": "proof", "blocks": []}


def _earliest_kickoff(entry_path: Path) -> dt.datetime | None:
    entry = ledger.read(entry_path)
    values = [row.get("kickoff", "") for row in entry.get("predictions", [])]
    if not values and entry.get("kickoff"):
        # A guest entry seals a single match, so its kickoff sits at the top
        # level rather than inside a predictions list. Same eligibility rule.
        values = [entry["kickoff"]]
    parsed = []
    for value in values:
        try:
            parsed.append(dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
                                      .replace(tzinfo=dt.timezone.utc))
        except (TypeError, ValueError):
            continue
    return min(parsed) if parsed else None


def stamp(entry_path: Path, now: dt.datetime | None = None) -> Path | None:
    """
    Submit one still-pre-kickoff entry and retain its detached proof.

    Entries whose first match has started are not backfilled.  A later proof
    would be real, but presenting it beside the publication date would invite
    exactly the false inference this feature exists to remove.
    """
    entry_path = Path(entry_path)
    target = proof_path(entry_path)
    if target.exists():
        return None

    now = now or dt.datetime.now(dt.timezone.utc)
    earliest = _earliest_kickoff(entry_path)
    if earliest is None or earliest <= now:
        log.warning("%s is not eligible for a new external anchor — its first "
                    "kickoff is missing or has passed", entry_path.name)
        return None

    # The client writes beside the source.  Move the successful detached proof
    # into its own directory so /predictions/ remains JSON-only.
    adjacent = entry_path.with_name(f"{entry_path.name}.ots")
    if adjacent.exists():
        state = inspect(adjacent, entry_path)["status"]
        if state not in {"pending", "attested"}:
            log.warning("leftover proof beside %s could not be validated; "
                        "leaving it in place for inspection", entry_path.name)
            return None
        config.TIMESTAMPS_DIR.mkdir(parents=True, exist_ok=True)
        adjacent.replace(target)
        return target

    result = _run("stamp", str(entry_path))
    if result is None or result.returncode != 0 or not adjacent.exists():
        detail = "" if result is None else (result.stderr or result.stdout).strip()
        log.warning("could not anchor %s%s", entry_path.name,
                    f": {detail[:300]}" if detail else "")
        return None

    state = inspect(adjacent, entry_path)["status"]
    if state not in {"pending", "attested"}:
        log.warning("new proof for %s does not identify the sealed JSON; "
                    "leaving it in place for inspection", entry_path.name)
        return None

    config.TIMESTAMPS_DIR.mkdir(parents=True, exist_ok=True)
    adjacent.replace(target)
    log.info("submitted %s to OpenTimestamps calendars", entry_path.name)
    return target


def upgrade(proof: Path) -> Path | None:
    """Add a Bitcoin attestation when the calendar has one; return if changed."""
    proof = Path(proof)
    if inspect(proof)["status"] == "attested":
        return None
    before = proof.read_bytes()
    result = _run("upgrade", str(proof))
    if result is None or result.returncode != 0:
        return None
    after = proof.read_bytes()
    if after != before:
        log.info("upgraded OpenTimestamps proof %s", proof.name)
        return proof
    return None


def maintain(now: dt.datetime | None = None) -> list[Path]:
    """
    Stamp eligible gaps and upgrade pending proofs.

    This runs on every three-hour job.  Retrying a temporary calendar failure
    is safe while every kickoff in that entry is still in the future; after
    that boundary the gap stays visible instead of being backdated.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    changed: list[Path] = []
    for entry_path in ledger.ledger_files():
        # Same-day retries only.  Stamping an older entry now would produce a
        # valid proof, but placing it beside an older publication date would
        # blur the exact coverage boundary the ledger page promises to show.
        published = ledger.read(entry_path).get("published_at", "")[:10]
        if published == now.date().isoformat() and not proof_path(entry_path).exists():
            made = stamp(entry_path, now=now)
            if made:
                changed.append(made)

    if config.TIMESTAMPS_DIR.exists():
        for proof in sorted(config.TIMESTAMPS_DIR.glob("*.json.ots")):
            updated = upgrade(proof)
            if updated:
                changed.append(updated)
    return list(dict.fromkeys(changed))


def report() -> dict:
    """Public, JSON-safe separation of chain coverage and anchor coverage."""
    rows = []
    for entry_path in ledger.ledger_files():
        proof = proof_path(entry_path)
        detail = inspect(proof, entry_path)
        rows.append({
            "entry": entry_path.name,
            "published_at": ledger.read(entry_path).get("published_at", ""),
            "status": detail["status"],
            "blocks": detail["blocks"],
            "proof": proof.name if proof.exists() else None,
        })

    present = [row["proof"] is not None for row in rows]
    first_index = next((i for i, value in enumerate(present) if value), None)
    gaps = (sum(not value for value in present[first_index:])
            if first_index is not None else 0)
    proofs = sum(present)
    attested = sum(row["status"] == "attested" for row in rows)
    pending = sum(row["status"] == "pending" for row in rows)
    mismatched = sum(row["status"] == "mismatch" for row in rows)

    return {
        "chain_entries": len(rows),
        "chain_start": rows[0]["published_at"][:10] if rows else None,
        "proofs": proofs,
        "attested": attested,
        "pending": pending,
        "mismatched": mismatched,
        "unclassified": proofs - attested - pending - mismatched,
        "proof_entry_start": (rows[first_index]["published_at"][:10]
                              if first_index is not None else None),
        "chain_only_before": first_index if first_index is not None else len(rows),
        "gaps_after_start": gaps,
        "continuous_after_start": first_index is not None and gaps == 0,
        "entries": rows,
    }


def main() -> int:
    changed = maintain()
    summary = report()
    print(f"Chain entries : {summary['chain_entries']}")
    print(f"Proof files   : {summary['proofs']}")
    print(f"Attested      : {summary['attested']}")
    print(f"Pending       : {summary['pending']}")
    print(f"Mismatched    : {summary['mismatched']}")
    print(f"Proof entries : {summary['proof_entry_start'] or 'not started'}")
    if changed:
        print("Changed       : " + ", ".join(path.name for path in changed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
