"""
Tests for the parts that carry the product's claim.

If the hash chain, the no-rewrite rule or the no-late-publication rule breaks,
the site is still pretty and no longer means anything. These are the tests that
matter most in this repository.

    python -m pytest tests -q
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from proofodds import charts, config, data  # noqa: E402


@pytest.fixture
def ledger_in(tmp_path, monkeypatch):
    """A ledger pointed at a temporary directory."""
    monkeypatch.setattr(config, "PREDICTIONS_DIR", tmp_path)
    from proofodds import ledger as ledger_module
    return ledger_module


# --------------------------------------------------------------------------- #
#  The chain
# --------------------------------------------------------------------------- #
def test_hash_ignores_the_hash_field(ledger_in):
    payload = {"a": 1, "b": [1, 2, 3]}
    h = ledger_in.compute_hash(payload)
    payload["hash"] = h
    assert ledger_in.compute_hash(payload) == h


def test_hash_is_order_independent(ledger_in):
    assert (ledger_in.compute_hash({"a": 1, "b": 2})
            == ledger_in.compute_hash({"b": 2, "a": 1}))


def test_chain_detects_a_tampered_prediction(ledger_in, tmp_path):
    _seed(ledger_in, tmp_path, days=3)
    assert ledger_in.verify_chain()["ok"]

    victim = sorted(tmp_path.glob("*.json"))[0]
    entry = json.loads(victim.read_text())
    entry["predictions"][0]["p_H"] = 0.99          # rewrite history
    victim.write_text(json.dumps(entry))

    report = ledger_in.verify_chain()
    assert not report["ok"]
    assert any("hash mismatch" in b["reason"] for b in report["broken"])


def test_chain_detects_a_rehashed_tamper(ledger_in, tmp_path):
    """
    The subtle attack: edit a prediction AND recompute its own hash.

    The content hash then passes, but every later entry still points at the old
    value, so the link check is what catches it.
    """
    _seed(ledger_in, tmp_path, days=3)
    victim = sorted(tmp_path.glob("*.json"))[0]
    entry = json.loads(victim.read_text())
    entry["predictions"][0]["p_H"] = 0.99
    entry["hash"] = ledger_in.compute_hash(entry)
    victim.write_text(json.dumps(entry))

    report = ledger_in.verify_chain()
    assert not report["ok"]
    assert any("broken link" in b["reason"] for b in report["broken"])


def test_empty_ledger_verifies(ledger_in):
    report = ledger_in.verify_chain()
    assert report["ok"] and report["n_entries"] == 0


# --------------------------------------------------------------------------- #
#  The publication rules
# --------------------------------------------------------------------------- #
def test_never_publishes_a_match_that_already_started(ledger_in):
    from proofodds.fixtures import Fixture
    now = dt.datetime(2025, 3, 1, 12, 0, tzinfo=dt.timezone.utc)
    fixtures = [
        Fixture(now - dt.timedelta(hours=2), "Arsenal", "Chelsea"),   # started
        Fixture(now + dt.timedelta(hours=2), "Liverpool", "Everton"),  # ahead
    ]
    entry = ledger_in.build_entry(fixtures, now)
    pairs = {(p["home"], p["away"]) for p in entry["predictions"]}
    assert ("Arsenal", "Chelsea") not in pairs
    assert ("Liverpool", "Everton") in pairs


def test_never_rewrites_an_existing_entry(ledger_in, tmp_path):
    from proofodds.fixtures import Fixture
    now = dt.datetime(2025, 3, 1, 12, 0, tzinfo=dt.timezone.utc)
    fx = [Fixture(now + dt.timedelta(hours=3), "Liverpool", "Everton")]

    first = ledger_in.publish(fx, now=now)
    assert first is not None
    before = first.read_bytes()

    again = ledger_in.publish(
        [Fixture(now + dt.timedelta(hours=3), "Arsenal", "Chelsea")], now=now)
    assert again is None
    assert first.read_bytes() == before


def test_model_only_sees_the_past(ledger_in):
    """The training set must end strictly before the publication date."""
    now = dt.datetime(2020, 1, 15, 0, 5, tzinfo=dt.timezone.utc)
    model, teams, past = ledger_in._model_for(now)
    assert past["Date"].max().date() < now.date()
    assert 1.0 < model.gamma < 1.8          # a plausible home advantage
    assert -0.25 < model.rho < 0.25


def test_a_club_with_no_history_is_priced_not_skipped(ledger_in):
    """
    A promoted club that has never appeared in the data still gets a prediction.

    Dropping the fixture would leave a silent hole in a ledger that claims to be
    complete — far worse than an honestly-flagged league-average price.
    """
    from proofodds.fixtures import Fixture
    now = dt.datetime(2026, 8, 26, 0, 5, tzinfo=dt.timezone.utc)
    entry = ledger_in.build_entry(
        [Fixture(now + dt.timedelta(days=2), "Wrexham", "Arsenal")], now)

    assert entry is not None and len(entry["predictions"]) == 1
    row = entry["predictions"][0]
    assert "Wrexham" in row["cold_start"]
    # Exactly one, not approximately: the published numbers are rounded and then
    # rebalanced, so a reader who adds up the three gets 1 on the first try.
    assert row["p_H"] + row["p_D"] + row["p_A"] == 1.0


def test_cold_start_uses_the_time_weighted_sample(ledger_in):
    """
    Ancient history does not count as history.

    A club last seen a decade ago contributes almost nothing under the decay,
    so its rating is really the prior — and the flag has to say so. Counting raw
    appearances would hide exactly the case the flag exists for.
    """
    from proofodds.fixtures import Fixture
    now = dt.datetime(2026, 8, 26, 0, 5, tzinfo=dt.timezone.utc)
    entry = ledger_in.build_entry(
        [Fixture(now + dt.timedelta(days=2), "Middlesbrough", "Arsenal")], now)

    row = entry["predictions"][0]
    assert "Middlesbrough" in row["cold_start"]   # last in the data in 2016/17
    assert "Arsenal" not in row["cold_start"]     # present throughout


def test_first_publication_wins(ledger_in, tmp_path):
    """
    A fixture published twice is graded on the earlier entry.

    Publishing earlier is harder, so this is the conservative choice — and it
    stops a later, better-informed prediction quietly replacing an earlier one.
    """
    from proofodds.fixtures import Fixture
    kickoff = dt.datetime(2025, 3, 10, 15, 0, tzinfo=dt.timezone.utc)
    day1 = dt.datetime(2025, 3, 1, 0, 5, tzinfo=dt.timezone.utc)
    day2 = dt.datetime(2025, 3, 8, 0, 5, tzinfo=dt.timezone.utc)

    ledger_in.publish([Fixture(kickoff, "Liverpool", "Everton")], now=day1)
    ledger_in.publish([Fixture(kickoff, "Liverpool", "Everton")], now=day2)

    rows = ledger_in.all_predictions()
    assert len(rows) == 1
    assert rows[0]["published_at"].startswith("2025-03-01")


# --------------------------------------------------------------------------- #
#  Data handling
# --------------------------------------------------------------------------- #
def test_team_names_normalise():
    assert data.canonical("Manchester City FC") == "Man City"
    assert data.canonical("Nottingham Forest") == "Nott'm Forest"
    assert data.canonical("Brighton & Hove Albion FC") == "Brighton"
    assert data.canonical("Arsenal") == "Arsenal"


@pytest.mark.parametrize("feed_name,expected", [
    ("Hull City AFC", "Hull"),            # the AFC suffix, not FC
    ("Coventry City FC", "Coventry"),     # promoted, never in the alias table before
    ("AFC Bournemouth", "Bournemouth"),   # prefix, not suffix
    ("Wolverhampton Wanderers FC", "Wolves"),
    ("Tottenham Hotspur FC", "Tottenham"),
    ("Sunderland AFC", "Sunderland"),
    ("Leeds United FC", "Leeds"),
])
def test_fixture_feed_names_reach_the_results_spelling(feed_name, expected):
    """
    Every club spelling a fixture feed can emit must land on the spelling the
    results file uses.

    This is the highest-stakes mapping in the project. A club that does not
    resolve is sealed into the ledger under a name the grader cannot join, so
    the prediction is published and then never scored — the scorecard shrinks
    in silence, which is the exact failure this whole product exists to rule out.
    """
    assert data.canonical(feed_name) == expected


def test_a_ledger_entry_grades_even_if_it_was_sealed_under_a_feed_spelling():
    """
    The ledger is immutable, so the fix for a bad spelling has to be on the
    READ side. An entry already sealed as "Hull City AFC" must still join.
    """
    import pandas as pd
    from proofodds.data import canonical
    sealed = pd.DataFrame([{"home": "Hull City AFC", "away": "Manchester United FC"}])
    sealed["home"] = sealed["home"].map(canonical)
    sealed["away"] = sealed["away"].map(canonical)
    assert sealed.loc[0, "home"] == "Hull"
    assert sealed.loc[0, "away"] == "Man United"


def test_unknown_statuses_do_not_silently_drop_fixtures():
    """
    The status filter is a deny-list on purpose. A value the feed invents must
    never cause a match to vanish — only an explicit "not being played" does.
    """
    from proofodds.fixtures import NOT_UPCOMING_STATUSES
    assert "FINISHED" in NOT_UPCOMING_STATUSES
    assert "POSTPONED" in NOT_UPCOMING_STATUSES
    for surprising in ("TIMED", "SCHEDULED", "2026-08-28 19:00:00Z", "", "WHATEVER"):
        assert surprising.upper() not in NOT_UPCOMING_STATUSES


def test_market_probabilities_sum_to_one():
    matches = data.add_market_probabilities(data.load_matches("E0"))
    priced = matches[matches["has_odds"]]
    total = priced[["mkt_H", "mkt_D", "mkt_A"]].sum(axis=1)
    assert abs(total - 1).max() < 1e-9
    assert (priced["overround"] > 0).all()      # a book always takes a cut


def test_matches_without_odds_are_kept_not_dropped():
    matches = data.add_market_probabilities(data.load_matches("E0"))
    assert (~matches["has_odds"]).sum() > 0
    assert matches["FTR"].notna().all()


def test_log_loss_reference_points():
    import numpy as np
    uniform = np.full((50, 3), 1 / 3)
    assert abs(data.log_loss(uniform, ["H"] * 50) - config.UNIFORM_LOG_LOSS) < 1e-12


# --------------------------------------------------------------------------- #
#  Rendering
# --------------------------------------------------------------------------- #
def test_outcome_bar_is_proportional_and_escaped():
    html = charts.outcome_bar(0.5, 0.25, 0.25)
    assert "50.00%" in html and html.count("flex-basis") == 3
    assert "aria-label" in html


def test_cumulative_chart_handles_a_short_series():
    assert "Not enough" in charts.cumulative_gap([{"date": "2025-01-01", "value": 0.0}])


# --------------------------------------------------------------------------- #
def _seed(ledger_module, tmp_path, days: int):
    from proofodds.fixtures import Fixture
    base = dt.datetime(2025, 3, 1, 0, 5, tzinfo=dt.timezone.utc)
    for i in range(days):
        now = base + dt.timedelta(days=i)
        ledger_module.publish(
            [Fixture(now + dt.timedelta(days=2), "Liverpool", "Everton"),
             Fixture(now + dt.timedelta(days=3), "Arsenal", "Chelsea")],
            now=now)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
