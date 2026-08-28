"""
Test setup.

Most of this suite runs anywhere. Some of it does not: those tests fit a real
model on real results, or build the site, which grades. The results CSVs are a
download, not part of the repository — they belong to football-data.co.uk and
there is no reason to vendor tens of megabytes of somebody else's data into git.

Those tests are marked `needs_data` and SKIP, loudly and with instructions, when
the download has not been run. They must not fail. This project invites
strangers to clone it and check the arithmetic themselves, and a fresh clone
that greets them with red lines is a bad answer to an invitation we issued.

The count is deliberately not written here. It was "twelve" for about six hours,
until two new tests rendered the site — which grades, which reads the CSVs — and
started failing on a clean clone. The marker is the contract; the number is not.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

HOW = ("results CSVs not downloaded — run:\n"
       "    python -c \"from proofodds import data; data.refresh('E0')\"")


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "needs_data: needs the football-data.co.uk CSVs on disk")


def pytest_collection_modifyitems(config, items):
    from proofodds import config as cfg, data
    if any(data.season_path("E0", s).exists() for s in cfg.SEASONS):
        return
    skip = pytest.mark.skip(reason=HOW)
    for item in items:
        if "needs_data" in item.keywords:
            item.add_marker(skip)
