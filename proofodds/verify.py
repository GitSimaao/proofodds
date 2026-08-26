"""
Independent verification of the prediction ledger.

    python -m proofodds.verify

Anyone can clone the repository and run this. It recomputes every SHA-256 and
every link in the chain, and exits non-zero if anything fails — which makes it
usable as a CI check as well as a reader's tool.
"""

from __future__ import annotations

import sys

from .ledger import GENESIS, ledger_files, read, verify_chain


def main() -> int:
    report = verify_chain()

    if report["n_entries"] == 0:
        print("Ledger is empty — nothing to verify.")
        return 0

    print(f"Entries : {report['n_entries']}")
    first = read(ledger_files()[0])
    last = read(ledger_files()[-1])
    print(f"Range   : {first['published_at'][:10]} → {last['published_at'][:10]}")
    total = sum(len(read(p)["predictions"]) for p in ledger_files())
    print(f"Sealed  : {total} predictions")
    print(f"Genesis : {GENESIS[:16]}…")
    print(f"Head    : {report['head']}")
    print()

    if report["ok"]:
        print("CHAIN OK — every hash recomputes and every link matches.")
        return 0

    print(f"CHAIN BROKEN — {len(report['broken'])} problem(s):")
    for item in report["broken"]:
        print(f"  {item['file']}: {item['reason']}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
