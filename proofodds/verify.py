"""
Independent verification of the prediction ledger.

    python -m proofodds.verify            # from a clone of this repository
    python proofodds/verify.py [DIR]      # or point it at any ledger directory

This file deliberately imports nothing but the Python standard library, and
deliberately reimplements the hashing rather than calling the code that wrote
it. Both choices are the point.

Nothing to install. `pip install -r requirements.txt` fetches pandas, numpy and
requests, which the model needs and an auditor does not. A verification step
that first requires a scientific Python stack is a verification step most
people will never run, and this project's whole claim is that a stranger can
check the arithmetic in under a minute.

Nothing shared. If this file called ledger.compute_hash, it would be our code
checking our code — it could agree with itself while both were wrong. Written
out separately, it is a second implementation of the rule, short enough to read
in one sitting, and a test asserts the two agree on every sealed entry.

The rule, in full:

    hash = SHA-256 of the entry with its own "hash" key removed, serialised as
           JSON with sorted keys, no whitespace, and non-ASCII left as-is

and each entry's prev_hash must equal the hash of the entry before it, the
first pointing at sixty-four zeros.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

GENESIS = "0" * 64


def is_entry(payload) -> bool:
    """
    Is this file a ledger entry at all?

    Asked because the answer used to be assumed. A directory of sealed entries
    may legitimately also hold a summary, a README, or whatever a web server
    put beside them, and reporting "CHAIN BROKEN" for one of those is a false
    alarm on the one claim this file exists to make true. An entry is a JSON
    object carrying the three fields the rule is defined over.
    """
    return (isinstance(payload, dict)
            and isinstance(payload.get("hash"), str)
            and isinstance(payload.get("prev_hash"), str)
            and "predictions" in payload)


def entry_hash(entry: dict) -> str:
    body = {k: v for k, v in entry.items() if k != "hash"}
    text = json.dumps(body, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def verify(directory: Path) -> tuple[bool, list[str], dict]:
    problems: list[str] = []
    skipped: list[str] = []
    prev = GENESIS
    sealed = 0
    first = last = ""
    files = []

    for path in sorted(directory.glob("*.json")):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            problems.append(f"{path.name}: unreadable ({exc})")
            continue

        if not is_entry(entry):
            skipped.append(path.name)
            continue
        files.append(path)

        recomputed = entry_hash(entry)
        if recomputed != entry.get("hash"):
            problems.append(f"{path.name}: content hash mismatch "
                            f"(file says {str(entry.get('hash'))[:16]}…, "
                            f"recomputed {recomputed[:16]}…)")
        elif entry.get("prev_hash") != prev:
            problems.append(f"{path.name}: broken link — points at "
                            f"{str(entry.get('prev_hash'))[:16]}…, previous "
                            f"entry hashes to {prev[:16]}…")

        prev = entry.get("hash", "")
        sealed += len(entry.get("predictions", []))
        stamp = entry.get("published_at", "")[:10]
        first = first or stamp
        last = stamp

    return not problems, problems, {
        "entries": len(files), "sealed": sealed,
        "first": first, "last": last, "head": prev if files else GENESIS,
        "skipped": skipped,
    }


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parent.parent
    directory = Path(argv[1]) if len(argv) > 1 else root / "predictions"

    if not directory.is_dir():
        print(f"No ledger directory at {directory}")
        return 2

    # With no argument, sweep the guest chains too. Each guest directory is a
    # chain under exactly the same rule, so the same code verifies it; a guest
    # record that could not be checked this way would not deserve the page it
    # is printed on.
    exit_code = 0
    if len(argv) <= 1 and (root / "guests").is_dir():
        for guest_dir in sorted(p for p in (root / "guests").iterdir()
                                if p.is_dir()):
            g_ok, g_problems, g_stats = verify(guest_dir)
            label = f"guests/{guest_dir.name}"
            if g_stats["entries"] == 0:
                continue
            if g_ok:
                print(f"{label}: CHAIN OK — {g_stats['entries']} entr"
                      f"{'y' if g_stats['entries'] == 1 else 'ies'}, "
                      f"head {g_stats['head'][:16]}…")
            else:
                exit_code = 1
                print(f"{label}: CHAIN BROKEN — {len(g_problems)} problem(s):")
                for item in g_problems:
                    print(f"  {item}")
        print()

    ok, problems, stats = verify(directory)

    if stats["entries"] == 0:
        print(f"{directory} is empty — nothing to verify.")
        return exit_code

    print(f"Ledger  : {directory}")
    print(f"Entries : {stats['entries']}")
    print(f"Range   : {stats['first']} → {stats['last']}")
    print(f"Sealed  : {stats['sealed']} predictions")
    print(f"Genesis : {GENESIS[:16]}…")
    print(f"Head    : {stats['head']}")
    if stats["skipped"]:
        print(f"Ignored : {', '.join(stats['skipped'])} "
              f"(not ledger entries)")
    print()

    if ok:
        print("CHAIN OK — every hash recomputes and every link matches.")
        return exit_code

    print(f"CHAIN BROKEN — {len(problems)} problem(s):")
    for item in problems:
        print(f"  {item}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
