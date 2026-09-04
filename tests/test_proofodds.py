"""
Tests for the parts that carry the product's claim.

If the hash chain, the no-rewrite rule or the no-late-publication rule breaks,
the site is still pretty and no longer means anything. These are the tests that
matter most in this repository.

    python -m pytest tests -q
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import league_names  # noqa: E402  (tests/ is on sys.path via conftest)
from proofodds import charts, config, data  # noqa: E402


@pytest.fixture
def ledger_in(tmp_path, monkeypatch):
    """A ledger pointed at a temporary directory."""
    monkeypatch.setattr(config, "PREDICTIONS_DIR", tmp_path)
    from proofodds import ledger as ledger_module
    return ledger_module


@pytest.fixture
def anchor_in(tmp_path, monkeypatch):
    """The external-anchor view pointed at throwaway proof storage."""
    monkeypatch.setattr(config, "TIMESTAMPS_DIR", tmp_path / "timestamps")
    from proofodds import anchor as anchor_module
    return anchor_module


def _minimal_chain(ledger_module, directory, days=3):
    """Write valid, dependency-free entries for chain and anchor tests."""
    prev = ledger_module.GENESIS
    base = dt.datetime(2025, 3, 1, 0, 5, tzinfo=dt.timezone.utc)
    for i in range(days):
        now = base + dt.timedelta(days=i)
        entry = {
            "version": 4,
            "leagues": ["E0"],
            "published_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "prev_hash": prev,
            "generator": {},
            "models": {},
            "predictions": [{
                "league": "E0",
                "kickoff": (now + dt.timedelta(days=2)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"),
                "home": "Arsenal",
                "away": "Chelsea",
                "p_H": 0.5,
                "p_D": 0.25,
                "p_A": 0.25,
            }],
        }
        entry["hash"] = ledger_module.compute_hash(entry)
        (directory / f"{now.date()}.json").write_text(json.dumps(entry))
        prev = entry["hash"]


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


@pytest.mark.needs_data
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


@pytest.mark.needs_data
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
#  External time anchors
# --------------------------------------------------------------------------- #
def test_anchor_report_keeps_chain_only_history_separate(
        ledger_in, anchor_in, tmp_path, monkeypatch):
    _minimal_chain(ledger_in, tmp_path, days=3)
    config.TIMESTAMPS_DIR.mkdir()
    files = ledger_in.ledger_files()
    for entry in files[1:]:
        anchor_in.proof_path(entry).write_bytes(b"detached proof")

    monkeypatch.setattr(
        anchor_in, "inspect",
        lambda proof, entry=None: {"status": "pending", "blocks": []}
        if proof.exists() else {"status": "none", "blocks": []})
    report = anchor_in.report()

    assert report["chain_entries"] == 3
    assert report["chain_start"] == "2025-03-01"
    assert report["proof_entry_start"] == "2025-03-02"
    assert report["chain_only_before"] == 1
    assert report["proofs"] == report["pending"] == 2
    assert report["continuous_after_start"] is True


def test_anchor_report_says_when_coverage_has_a_gap(
        ledger_in, anchor_in, tmp_path, monkeypatch):
    _minimal_chain(ledger_in, tmp_path, days=3)
    config.TIMESTAMPS_DIR.mkdir()
    files = ledger_in.ledger_files()
    for entry in (files[0], files[2]):
        anchor_in.proof_path(entry).write_bytes(b"detached proof")
    monkeypatch.setattr(
        anchor_in, "inspect",
        lambda proof, entry=None: {"status": "pending", "blocks": []}
        if proof.exists() else {"status": "none", "blocks": []})

    report = anchor_in.report()
    assert report["proof_entry_start"] == "2025-03-01"
    assert report["gaps_after_start"] == 1
    assert report["continuous_after_start"] is False


def test_a_started_entry_is_never_timestamped_after_the_fact(
        ledger_in, anchor_in, tmp_path, monkeypatch):
    _minimal_chain(ledger_in, tmp_path, days=1)
    entry = ledger_in.ledger_files()[0]

    def forbidden(*args, **kwargs):
        raise AssertionError("OpenTimestamps must not be called after kickoff")

    monkeypatch.setattr(anchor_in, "_run", forbidden)
    late = dt.datetime(2025, 3, 4, tzinfo=dt.timezone.utc)
    assert anchor_in.stamp(entry, now=late) is None
    assert not anchor_in.proof_path(entry).exists()


def test_maintain_does_not_backfill_an_older_entry_even_before_kickoff(
        ledger_in, anchor_in, tmp_path, monkeypatch):
    _minimal_chain(ledger_in, tmp_path, days=1)

    def forbidden(*args, **kwargs):
        raise AssertionError("an older publication must remain chain-only")

    monkeypatch.setattr(anchor_in, "stamp", forbidden)
    tomorrow = dt.datetime(2025, 3, 2, 0, 5, tzinfo=dt.timezone.utc)
    assert anchor_in.maintain(now=tomorrow) == []


def test_a_successful_stamp_is_moved_out_of_the_json_ledger(
        ledger_in, anchor_in, tmp_path, monkeypatch):
    _minimal_chain(ledger_in, tmp_path, days=1)
    entry = ledger_in.ledger_files()[0]

    def fake_run(operation, source, timeout=90):
        if operation == "stamp":
            assert Path(source) == entry
            Path(f"{source}.ots").write_bytes(b"valid detached proof")
            output = "submitted"
        else:
            assert operation == "info" and Path(source) == Path(f"{entry}.ots")
            output = (f"File sha256 hash: {hashlib.sha256(entry.read_bytes()).hexdigest()}\n"
                      "verify PendingAttestation('https://calendar.example')")
        return subprocess.CompletedProcess([], 0, output, "")

    monkeypatch.setattr(anchor_in, "_run", fake_run)
    proof = anchor_in.stamp(
        entry, now=dt.datetime(2025, 3, 1, 1, tzinfo=dt.timezone.utc))

    assert proof == anchor_in.proof_path(entry)
    assert proof.read_bytes() == b"valid detached proof"
    assert not Path(f"{entry}.ots").exists()
    assert ledger_in.ledger_files() == [entry]


def test_a_bitcoin_attestation_is_not_confused_with_pending(
        anchor_in, tmp_path, monkeypatch):
    proof = tmp_path / "one.json.ots"
    proof.write_bytes(b"proof")
    output = ("verify PendingAttestation('https://calendar.example')\n"
              "verify BitcoinBlockHeaderAttestation(900123)\n")
    monkeypatch.setattr(
        anchor_in, "_run",
        lambda *a, **k: subprocess.CompletedProcess([], 0, output, ""))

    assert anchor_in.inspect(proof) == {
        "status": "attested", "blocks": [900123]}


def test_a_bitcoin_attestation_for_different_bytes_is_a_mismatch(
        anchor_in, tmp_path, monkeypatch):
    entry = tmp_path / "one.json"
    proof = tmp_path / "one.json.ots"
    entry.write_bytes(b"the sealed entry")
    proof.write_bytes(b"proof")
    output = ("File sha256 hash: " + "0" * 64 + "\n"
              "verify BitcoinBlockHeaderAttestation(900123)\n")
    monkeypatch.setattr(
        anchor_in, "_run",
        lambda *a, **k: subprocess.CompletedProcess([], 0, output, ""))

    assert anchor_in.inspect(proof, entry) == {
        "status": "mismatch", "blocks": []}


def test_the_ledger_template_renders_proof_and_generator_without_results_data():
    """A clean clone can check this page without downloading the result CSVs."""
    from proofodds import ledger, render
    template = render.environment().get_template("ledger.html")
    commit = "a" * 40
    html = template.render(
        site_name="ProofOdds", site_url="https://proofodds.com",
        repo_url="https://github.com/GitSimaao/proofodds", asset_v="test",
        chain={"ok": True, "n_entries": 1, "head": "b" * 64},
        anchors={
            "proofs": 1, "continuous_after_start": True,
            "proof_entry_start": "2025-03-01", "chain_only_before": 0,
            "attested": 1, "pending": 0, "mismatched": 0,
            "unclassified": 0,
        },
        entries=[{
            "file": "2025-03-01.json", "published_at": "2025-03-01T00:05:00Z",
            "n": 1, "hash": "c" * 64, "prev_hash": ledger.GENESIS,
            "generator_commit": commit, "generator_dirty": False,
            "generator_source": "d" * 64,
            "anchor": {"status": "attested", "blocks": [900123],
                       "proof": "2025-03-01.json.ots"},
        }],
        genesis=ledger.GENESIS,
    )
    assert f"/commit/{commit}" in html
    assert "Attestation: block 900123" in html
    assert "Every chain entry has a proof file" in html


# --------------------------------------------------------------------------- #
#  The publication rules
# --------------------------------------------------------------------------- #
@pytest.mark.needs_data
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


@pytest.mark.needs_data
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


@pytest.mark.needs_data
def test_model_only_sees_the_past(ledger_in):
    """The training set must end strictly before the publication date."""
    now = dt.datetime(2020, 1, 15, 0, 5, tzinfo=dt.timezone.utc)
    model, teams, past = ledger_in._model_for(now)
    assert past["Date"].max().date() < now.date()
    assert 1.0 < model.gamma < 1.8          # a plausible home advantage
    assert -0.25 < model.rho < 0.25


@pytest.mark.needs_data
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


@pytest.mark.needs_data
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


@pytest.mark.needs_data
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
@pytest.fixture
def real_names(monkeypatch):
    """
    Pin the canonical sets to the club names really used in 2025/26.

    The resolver normally learns them from the downloaded CSVs, which means
    these tests would otherwise pass or fail depending on which files happen to
    be cached on the machine running them. Pinning makes the test about the
    algorithm. It is also the harder version: only one season of names is
    supplied, so the fuzzy stage has fewer near-misses available to reject.
    """
    for code, names in league_names.KNOWN.items():
        monkeypatch.setitem(data._known_cache, code,
                            (data._cache_key(code), frozenset(names)))
    return league_names


ALL_PAIRS = [(lg, src, want)
             for lg, feed in league_names.FEED.items()
             for src, want in feed.items()]


@pytest.mark.parametrize("league,feed_name,expected", ALL_PAIRS,
                         ids=[f"{lg}:{src}" for lg, src, _ in ALL_PAIRS])
def test_every_club_in_every_division_resolves(real_names, league, feed_name,
                                               expected):
    """
    Every club spelling the fixture feed can emit must land on the spelling the
    results file uses, in all seven divisions.

    This is the highest-stakes mapping in the project. A club that does not
    resolve is sealed into the ledger under a name the grader cannot join, so
    the prediction is published and then never scored — the scorecard shrinks
    in silence, which is the exact failure this whole product exists to rule
    out. One hundred and thirty-odd clubs is too many to check by eye every
    August, so they are checked here instead.
    """
    got, how = data.resolve(feed_name, league)
    assert got == expected, f"{feed_name!r} resolved to {got!r} via {how}"


@pytest.mark.parametrize("league,feed_name", [
    ("D1", "FC Schalke 04"),          # a division below
    ("D1", "Fortuna Düsseldorf"),
    ("SP1", "Real Valladolid CF"),    # shares a word with Real Madrid
    ("SP1", "Sporting Gijón"),
    ("I1", "Empoli FC"),              # rhymes with Napoli
    ("I1", "AC Monza"),
    ("F1", "Montpellier HSC"),
    ("E0", "Leicester City FC"),      # in E1 that season, not E0
    ("E1", "Luton Town FC"),
    ("P1", "Portimonense SC"),
])
def test_a_club_that_is_not_in_the_division_is_refused(real_names, league,
                                                       feed_name):
    """
    Refusing is the correct answer, and it has to beat guessing.

    An unresolved name is loud, recoverable and fixed by one line in
    OVERRIDES. A name resolved to the WRONG club is silent, sealed, and grades
    our prediction against somebody else's result — which is worse than not
    grading it at all.
    """
    got, how = data.resolve(feed_name, league)
    assert got is None, f"{feed_name!r} was wrongly matched to {got!r} via {how}"


def test_ambiguity_is_refused_rather_than_broken_by_coin_flip(real_names):
    """
    "Milan" opens "Milano", so Internazionale looks like AC Milan under the
    abbreviation rule. The override settles it; without one, the resolver must
    refuse rather than pick.
    """
    assert data.resolve("FC Internazionale Milano", "I1")[0] == "Inter"
    assert data.resolve("AC Milan", "I1")[0] == "Milan"

    stripped = dict(data.OVERRIDES["I1"])
    stripped.pop("fc internazionale milano", None)
    stripped.pop("internazionale milano", None)
    saved = data.OVERRIDES["I1"]
    data.OVERRIDES["I1"] = stripped
    try:
        got, how = data.resolve("FC Internazionale Milano", "I1")
    finally:
        data.OVERRIDES["I1"] = saved
    assert got is None and how == "ambiguous"


def test_names_are_never_resolved_across_divisions(real_names):
    """
    Bayern Munich is not a Championship club, however hard you squint. The
    canonical set is per division for exactly this reason.
    """
    assert data.resolve("FC Bayern München", "D1")[0] == "Bayern Munich"
    assert data.resolve("FC Bayern München", "E1")[0] is None


class _Resp:
    def __init__(self, status, content, reason=""):
        self.status_code, self.content, self.reason = status, content, reason


def test_a_page_that_is_not_a_results_file_is_never_cached(tmp_path, monkeypatch):
    """
    football-data.co.uk answers 300 Multiple Choices, with an HTML page, for a
    season it has not published yet. 300 is not an error status, so
    `raise_for_status` waves it through and the page lands on disk as a .csv.
    Nothing complains until pandas trips over line 7 of some HTML weeks later,
    in a division nobody was watching.

    Worse, that write would have replaced a good cached file. Ten seasons of
    Bundesliga results are not worth losing because the eleventh is late.
    """
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    good = b"Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\nD1,15/08/25,Bayern Munich,Mainz,3,1,H\n"
    (tmp_path / "D1_2627.csv").write_bytes(good)

    monkeypatch.setattr(data.requests, "get", lambda *a, **k: _Resp(
        300, b"<html><head><title>300 Multiple Choices</title></head>", "Multiple Choices"))
    with pytest.raises(RuntimeError, match="300"):
        data.download_season("D1", "2627", force=True)
    assert (tmp_path / "D1_2627.csv").read_bytes() == good

    # A 200 carrying an error page is the same problem wearing a better suit.
    monkeypatch.setattr(data.requests, "get", lambda *a, **k: _Resp(
        200, b"<!doctype html><h1>Not found</h1>"))
    with pytest.raises(RuntimeError, match="not a results file"):
        data.download_season("D1", "2627", force=True)
    assert (tmp_path / "D1_2627.csv").read_bytes() == good

    # And a cached file that is junk must not count as cached.
    (tmp_path / "D1_2526.csv").write_bytes(b"<html>300</html>")
    assert not data.is_cached(tmp_path / "D1_2526.csv")
    assert data.is_cached(tmp_path / "D1_2627.csv")


def test_the_header_row_is_what_makes_it_a_results_file():
    assert data.looks_like_results(
        b"Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG\nE0,...")
    assert data.looks_like_results(
        b"\xef\xbb\xbfDiv,Date,HomeTeam,AwayTeam,FTHG\n")      # BOM
    assert not data.looks_like_results(b"<html><title>300 Multiple Choices")
    assert not data.looks_like_results(b"")
    assert not data.looks_like_results(b"Div,Date,Time\nE0,15/08/25,15:00")


def test_a_promotion_the_results_file_has_not_caught_up_with_is_pinned(real_names):
    """
    SV 07 Elversberg went up to the Bundesliga for 2026/27, and
    football-data.co.uk had not published that division's file yet — so the
    club has no spelling in D1's canonical set to resolve onto.

    The override is not a guess at what they will call it. The same publisher
    has spelled this club "Elversberg" in every 2. Bundesliga file since
    2023/24. It resolves now, and it keeps resolving when the file lands.
    """
    got, how = data.resolve("SV 07 Elversberg", "D1")
    assert (got, how) == ("Elversberg", "override")
    # and even with no override at all, the fallback label is already correct
    assert data.display_from_feed("SV 07 Elversberg") == "Elversberg"


def test_a_club_the_results_files_have_never_seen_still_gets_a_usable_label():
    """
    A genuinely new promotion has no entry anywhere until its first result is
    published. It still has to be priced and published, so the feed name is
    reduced to something readable and the raw name is sealed alongside it.
    """
    assert data.display_from_feed("Wrexham AFC") == "Wrexham"
    assert data.display_from_feed("FC Alverca") == "Alverca"
    assert data.display_from_feed("Bologna FC 1909") == "Bologna"


def test_a_ledger_entry_grades_even_if_it_was_sealed_under_a_feed_spelling(real_names):
    """
    The ledger is immutable, so the fix for a bad spelling has to be on the
    READ side. An entry already sealed as "Hull City AFC" must still join.
    """
    from proofodds.grade import _canonical_for
    assert _canonical_for("Hull City AFC", "", "E1") == "Hull"
    assert _canonical_for("Manchester United FC", "", "E0") == "Man United"
    # and the raw name is the second chance, for a name sealed provisionally
    assert _canonical_for("Wolverhampton", "Wolverhampton Wanderers FC", "E0") == "Wolves"


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


def test_fixture_fetch_caches_only_the_providers_safe_crest_url(
        tmp_path, monkeypatch):
    """Crests are display metadata: cached beside data, never put on Fixture."""
    from proofodds import crests, fixtures

    class Response:
        status_code = 200
        headers = {}
        text = ""

        @staticmethod
        def json():
            return {"matches": [{
                "status": "TIMED",
                "utcDate": "2026-09-01T19:00:00Z",
                "matchday": 4,
                "homeTeam": {
                    "id": 65, "name": "Manchester City FC",
                    "crest": "https://crests.football-data.org/65.png",
                },
                "awayTeam": {
                    "id": 61, "name": "Chelsea FC",
                    "crest": "https://images.example/chelsea.png",
                },
            }]}

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "FDORG_TOKEN", "not-a-real-token")
    monkeypatch.setitem(data._known_cache, "E0", (
        data._cache_key("E0"), frozenset({"Man City", "Chelsea"})))
    monkeypatch.setattr(fixtures.requests, "get", lambda *args, **kwargs: Response())

    got = fixtures.from_football_data_org("E0", 8)

    assert got[0].home == "Man City" and got[0].away == "Chelsea"
    assert not hasattr(got[0], "home_crest")
    assert crests.lookup("E0", "Man City") == (
        "https://crests.football-data.org/65.png")
    assert crests.lookup("E0", "Chelsea") is None
    cache = json.loads((tmp_path / "club_crests.json").read_text())
    assert cache["provider"] == "football-data.org"
    assert set(cache) == {"version", "provider", "clubs"}


def test_full_crest_sync_uses_the_team_resource_without_sealing(
        tmp_path, monkeypatch):
    from proofodds import crests, fixtures

    class Response:
        status_code = 200
        headers = {}
        text = ""

        @staticmethod
        def json():
            return {"teams": [{
                "id": 57, "name": "Arsenal FC",
                "crest": "https://crests.football-data.org/57.png",
            }]}

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "FDORG_TOKEN", "not-a-real-token")
    monkeypatch.setitem(data._known_cache, "E0", (
        data._cache_key("E0"), frozenset({"Arsenal"})))
    monkeypatch.setattr(fixtures.requests, "get", lambda *args, **kwargs: Response())

    assert fixtures.sync_crests("E0") == {"E0": 1}
    assert crests.lookup("E0", "Arsenal") == (
        "https://crests.football-data.org/57.png")


def test_scottish_crest_sync_uses_the_display_only_fallback(tmp_path, monkeypatch):
    from proofodds import crests, fixtures

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"teams": [{"idTeam": "133647", "strTeam": "Celtic",
                               "strSport": "Soccer", "strCountry": "Scotland",
                               "strBadge": "https://r2.thesportsdb.com/images/media/team/badge/celtic.png"}]}

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(data, "known_teams", lambda league: frozenset({"Celtic"}))
    monkeypatch.setattr(fixtures.requests, "get", lambda *args, **kwargs: Response())
    assert fixtures.sync_crests("SC0") == {"SC0": 1}
    assert crests.lookup("SC0", "Celtic") == (
        "https://r2.thesportsdb.com/images/media/team/badge/celtic.png")


def test_scottish_crest_sync_uses_the_sportsdb_full_name_alias(tmp_path, monkeypatch):
    from proofodds import crests, fixtures
    from types import SimpleNamespace
    queries = []

    def get(_url, params=None, **_kwargs):
        queries.append(params["t"])
        return SimpleNamespace(status_code=200, json=lambda: {"teams": [{
            "idTeam": "133643", "strTeam": "Heart of Midlothian",
            "strSport": "Soccer", "strCountry": "Scotland",
            "strBadge": "https://r2.thesportsdb.com/images/media/team/badge/hearts.png"}]})

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(data, "known_teams", lambda league: frozenset({"Hearts"}))
    monkeypatch.setattr(fixtures.requests, "get", get)
    assert fixtures.sync_crests("SC0") == {"SC0": 1}
    assert queries == ["Heart of Midlothian"]
    assert crests.lookup("SC0", "Hearts").endswith("/hearts.png")


@pytest.mark.parametrize("url", [
    "http://crests.football-data.org/57.png",
    "https://crests.football-data.org.evil.test/57.png",
    "https://user@crests.football-data.org/57.png",
    "https://crests.football-data.org:444/57.png",
    "data:image/svg+xml,<svg/>",
    None,
])
def test_crest_cache_rejects_every_unapproved_origin(url):
    from proofodds import crests
    assert crests.safe_url(url) is None


def test_a_local_licensed_crest_wins_over_the_provider_cache(tmp_path, monkeypatch):
    from proofodds import crests, render
    static = tmp_path / "static"
    (static / "clubs").mkdir(parents=True)
    (static / "clubs" / "arsenal.svg").write_text("<svg/>")
    data_dir = tmp_path / "data"
    monkeypatch.setattr(config, "STATIC_DIR", static)
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    crests.update("E0", [{
        "club": "Arsenal", "id": 57, "raw_name": "Arsenal FC",
        "url": "https://crests.football-data.org/57.png",
    }])

    assert render.club_mark("Arsenal", "E0")["src"] == "/clubs/arsenal.svg"


@pytest.mark.needs_data
def test_market_probabilities_sum_to_one():
    matches = data.add_market_probabilities(data.load_matches("E0"))
    priced = matches[matches["has_odds"]]
    total = priced[["mkt_H", "mkt_D", "mkt_A"]].sum(axis=1)
    assert abs(total - 1).max() < 1e-9
    assert (priced["overround"] > 0).all()      # a book always takes a cut


@pytest.mark.needs_data
def test_matches_without_odds_are_kept_not_dropped():
    matches = data.add_market_probabilities(data.load_matches("E0"))
    assert (~matches["has_odds"]).sum() > 0
    assert matches["FTR"].notna().all()


def test_log_loss_reference_points():
    import numpy as np
    uniform = np.full((50, 3), 1 / 3)
    assert abs(data.log_loss(uniform, ["H"] * 50) - config.UNIFORM_LOG_LOSS) < 1e-12


def _odds_frame(**overrides):
    """One-row frame with everything add_market_probabilities needs."""
    import pandas as pd
    row = {"FTHG": 1, "FTAG": 0, "FTR": "H",
           "AvgCH": 2.10, "AvgCD": 3.40, "AvgCA": 3.60,
           "AvgC>2.5": 1.90, "AvgC<2.5": 1.95}
    row.update(overrides)
    return pd.DataFrame([row])


def test_zero_odds_are_a_placeholder_not_a_price():
    """
    football-data files occasionally carry 0.0 in an odds column. notna() does
    not catch it, and 1/0 turns that row into probabilities of [0, 0, nan] and
    a log loss of ~34 for one match — enough to poison a whole week's number.
    Both market paths must refuse any odds <= 1.
    """
    out = data.add_market_probabilities(_odds_frame(AvgCA=0.0))
    assert not out["has_odds"].iloc[0]
    assert out[["mkt_H", "mkt_D", "mkt_A"]].isna().all(axis=None)

    out = data.add_market_probabilities(_odds_frame(**{"AvgC<2.5": 0.0}))
    assert not out["has_ou_odds"].iloc[0]

    # and a real price on every column still grades
    out = data.add_market_probabilities(_odds_frame())
    assert out["has_odds"].iloc[0] and out["has_ou_odds"].iloc[0]


def test_benchmark_is_the_market_average_not_pinnacle():
    """
    The site grades against AvgC* (football-data dropped Pinnacle's closing
    columns in January 2026). A row with Pinnacle prices and no average must
    NOT be gradeable — silently mixing benchmarks is the failure mode this
    pins down.
    """
    assert data.ODDS_COLS == ["AvgCH", "AvgCD", "AvgCA"]
    assert data.OU_COLS == ["AvgC>2.5", "AvgC<2.5"]

    import numpy as np
    only_pinnacle = _odds_frame(AvgCH=np.nan, AvgCD=np.nan, AvgCA=np.nan,
                                PSCH=2.05, PSCD=3.45, PSCA=3.70)
    out = data.add_market_probabilities(only_pinnacle)
    assert not out["has_odds"].iloc[0]


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


# --------------------------------------------------------------------------- #
#  Seven divisions in one chain
# --------------------------------------------------------------------------- #
class _StubModel:
    """Enough of a fitted model to build an entry without reading any CSVs."""
    gamma = 1.3
    rho = -0.03
    league_mean = 1.35

    def outcome_probs(self, h, a):
        return [0.5, 0.25, 0.25]

    def expected_goals(self, h, a):
        return 1.6, 1.1

    def totals_probs(self, h, a, line=2.5):
        import numpy as _np
        return _np.array([0.54, 0.46])


def _stub_past(teams):
    import pandas as pd
    return pd.DataFrame({
        "Date": pd.to_datetime(["2026-08-01"] * 200),
        "HomeTeam": [teams[0]] * 200,
        "AwayTeam": [teams[1]] * 200,
    })


@pytest.fixture
def stub_models(monkeypatch):
    """Fit nothing; the point of these tests is the bookkeeping around the fit."""
    from proofodds import ledger as ledger_module

    def fake(now, league, extra_teams=()):
        if league == "BROKEN":
            raise RuntimeError("not enough history to fit a model")
        teams = list(extra_teams) or ["A", "B"]
        return _StubModel(), teams, _stub_past(teams)

    monkeypatch.setattr(ledger_module, "_model_for", fake)
    return ledger_module


def test_one_entry_covers_every_division_and_names_them(ledger_in, stub_models,
                                                        tmp_path):
    """
    All divisions share a single chain. Splitting the ledger per league would
    give seven chains to check instead of one, and the whole argument rests on
    a stranger being able to check it in a minute.
    """
    from proofodds.fixtures import Fixture
    now = dt.datetime(2026, 8, 26, 6, 0, tzinfo=dt.timezone.utc)
    kick = now + dt.timedelta(days=2)
    stub_models.publish([
        Fixture(kick, "Arsenal", "Chelsea", league="E0"),
        Fixture(kick, "Real Madrid", "Sevilla", league="SP1"),
        Fixture(kick, "Porto", "Benfica", league="P1"),
    ], now=now)

    entry = json.loads(sorted(tmp_path.glob("*.json"))[0].read_text())
    assert entry["version"] == ledger_in.SCHEMA_VERSION
    assert entry["leagues"] == ["E0", "P1", "SP1"]
    assert set(entry["models"]) == {"E0", "P1", "SP1"}
    assert {r["league"] for r in entry["predictions"]} == {"E0", "P1", "SP1"}
    assert stub_models.verify_chain()["ok"]


def test_every_new_entry_identifies_the_generator(
        ledger_in, stub_models, tmp_path, monkeypatch):
    """
    A parameter list is not a code version.  The commit is the readable link
    back to the repository; the source digest still identifies the exact bytes
    if the working tree was modified when the model ran.
    """
    from proofodds.fixtures import Fixture
    identity = {
        "commit": "a" * 40,
        "dirty": False,
        "source_sha256": "b" * 64,
    }
    monkeypatch.setattr(ledger_in, "generator_identity", lambda: identity)

    now = dt.datetime(2026, 8, 26, 6, 0, tzinfo=dt.timezone.utc)
    stub_models.publish([
        Fixture(now + dt.timedelta(days=2), "Arsenal", "Chelsea", league="E0"),
    ], now=now)

    entry = json.loads(sorted(tmp_path.glob("*.json"))[0].read_text())
    assert entry["version"] == 4
    assert entry["generator"] == identity
    assert stub_models.verify_chain()["ok"]


def test_generator_source_hash_changes_with_the_source_bytes(ledger_in, tmp_path):
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_text("XI = 0.002\n")
    second.write_text("PRIOR_SD = 0.6\n")

    before = ledger_in.generator_source_hash([first, second])
    second.write_text("PRIOR_SD = 0.7\n")
    after = ledger_in.generator_source_hash([first, second])

    assert before != after
    assert ledger_in.generator_source_hash([second, first]) == after


def test_a_division_that_cannot_be_fitted_is_recorded_not_hidden(
        ledger_in, stub_models, tmp_path):
    """
    Six divisions published and one recorded as skipped beats seven published
    or nothing published. What it must never be is six published and the
    seventh missing without a word — an entry has to say what it does not
    contain, or "complete" means nothing.
    """
    from proofodds.fixtures import Fixture
    now = dt.datetime(2026, 8, 26, 6, 0, tzinfo=dt.timezone.utc)
    kick = now + dt.timedelta(days=2)
    stub_models.publish([
        Fixture(kick, "Arsenal", "Chelsea", league="E0"),
        Fixture(kick, "X", "Y", league="BROKEN"),
    ], now=now)

    entry = json.loads(sorted(tmp_path.glob("*.json"))[0].read_text())
    assert entry["leagues"] == ["E0"]
    assert entry["skipped"] == [{"league": "BROKEN", "n": 1,
                                 "reason": "not enough history to fit a model"}]


def test_the_feed_spelling_is_sealed_alongside_the_graded_one(
        ledger_in, stub_models, tmp_path):
    """
    Sealing the raw name is what makes a naming mistake recoverable. Without
    it, a club we could not place in August is a prediction nobody can ever
    score; with it, one line in OVERRIDES grades every one of them, and no
    ledger file is touched.
    """
    from proofodds.fixtures import Fixture
    now = dt.datetime(2026, 8, 26, 6, 0, tzinfo=dt.timezone.utc)
    stub_models.publish([
        Fixture(now + dt.timedelta(days=2), "Newtown", "Arsenal", league="E0",
                home_raw="Newtown Rovers FC", away_raw="Arsenal",
                resolved=False),
    ], now=now)

    row = json.loads(sorted(tmp_path.glob("*.json"))[0].read_text())["predictions"][0]
    assert row["home_raw"] == "Newtown Rovers FC"
    assert row["name_provisional"] is True
    # Stored only when it differs — an entry should carry information, not noise
    assert "away_raw" not in row


def test_the_same_pairing_in_two_divisions_is_not_deduplicated(
        ledger_in, stub_models, tmp_path):
    """
    The de-duplication key gained the division for a reason. Without it, a
    coincidence of names across two countries would silently delete a
    prediction from the record.
    """
    from proofodds.fixtures import Fixture
    now = dt.datetime(2026, 8, 26, 6, 0, tzinfo=dt.timezone.utc)
    kick = now + dt.timedelta(days=2)
    stub_models.publish([
        Fixture(kick, "Valencia", "Sporting", league="SP1"),
        Fixture(kick, "Valencia", "Sporting", league="P1"),
    ], now=now)

    rows = stub_models.all_predictions()
    assert len(rows) == 2
    assert {r["league"] for r in rows} == {"SP1", "P1"}


def test_the_same_match_sealed_under_two_spellings_is_one_prediction(
        ledger_in, tmp_path, real_names):
    """
    The duplicate that reached production.

    Coventry went up to the Premier League for 2026/27. On the 26th the club
    had no line in the results file, so the fixture was sealed under the feed's
    spelling, "Coventry City FC". By the 27th their first result had been
    published, the resolver found them, and the same fixture was sealed again
    as "Coventry". Keying de-duplication on the raw strings let both through:
    two cards on the front page for one match, showing 42% and 18% for a home
    win — and, far worse than the eyesore, the same match counted twice in the
    log loss.

    So the key is the name as it will be GRADED, and the earlier entry wins.
    Not because it is better — it is worse, priced with no history at all —
    but because it was published first, and a later, better-informed
    prediction must never be allowed to quietly replace one already sealed.
    """
    # The results file has caught up by now — which is exactly the moment the
    # duplicate becomes visible, and therefore the moment it must be merged.
    # (KNOWN is pinned to 2025/26; the live E0 set spans eleven seasons, so it
    # already holds Hull from 2016/17 and gains Coventry with their first
    # 2026/27 result.)
    data._known_cache["E0"] = (
        data._cache_key("E0"),
        frozenset(set(league_names.KNOWN["E0"]) | {"Coventry", "Hull"}))

    kickoff = "2026-08-29T14:00:00Z"
    for day, home, away, p_h, raw in [
            ("2026-08-26", "Coventry City FC", "Hull City AFC", 0.423784, None),
            ("2026-08-27", "Coventry", "Hull", 0.179277, "Coventry City FC")]:
        row = {"league": "E0", "kickoff": kickoff, "home": home, "away": away,
               "p_H": p_h, "p_D": 0.27, "p_A": round(1 - p_h - 0.27, 6)}
        if raw:
            row["home_raw"], row["away_raw"] = raw, "Hull City AFC"
        entry = {"version": 2, "leagues": ["E0"],
                 "published_at": f"{day}T00:09:00Z",
                 "prev_hash": ledger_in.GENESIS, "models": {}, "predictions": [row]}
        entry["hash"] = ledger_in.compute_hash(entry)
        (tmp_path / f"{day}.json").write_text(json.dumps(entry))

    rows = ledger_in.all_predictions()
    assert len(rows) == 1
    assert rows[0]["published_at"].startswith("2026-08-26")
    assert rows[0]["p_H"] == 0.423784


def test_a_kickoff_the_feed_has_not_fixed_is_not_printed_as_a_time(
        ledger_in, stub_models, tmp_path):
    """
    football-data.org sets TIMED once a kickoff is fixed. Before that a match
    sits at SCHEDULED, carrying a rough date and a placeholder time — nine of
    eighteen Primeira Liga fixtures on the day this was written.

    Publishing them is right: the eight-day window seals them days before any
    plausible kickoff, so the promise holds. Printing the placeholder as though
    it were a kickoff time is not. A site that insists on precision does not
    get to invent the one number it was handed as a guess.
    """
    from proofodds.fixtures import Fixture
    now = dt.datetime(2026, 8, 26, 6, 0, tzinfo=dt.timezone.utc)
    kick = now + dt.timedelta(days=4)
    stub_models.publish([
        Fixture(kick, "Porto", "Benfica", league="P1", time_confirmed=False),
        Fixture(kick, "Arsenal", "Chelsea", league="E0"),
    ], now=now)

    rows = {r["home"]: r for r in
            json.loads(sorted(tmp_path.glob("*.json"))[0].read_text())["predictions"]}
    assert rows["Porto"]["kickoff_tbc"] is True
    # and absent, not False, where the time is known — an entry says what is
    # true of it and stays silent about the rest
    assert "kickoff_tbc" not in rows["Arsenal"]


def test_a_schema_1_entry_is_still_read_exactly_as_sealed(ledger_in, tmp_path):
    """
    The first entries name the division once, at entry level, because there was
    only one. They must keep working untouched — rewriting them to the new
    shape would break their hashes, which is precisely the thing that must
    never happen.
    """
    entry = {
        "version": 1, "league": "E0",
        "published_at": "2026-08-24T06:00:00Z",
        "prev_hash": ledger_in.GENESIS,
        "model": {"name": "dixon-coles", "rho": -0.0738},
        "predictions": [{"kickoff": "2026-08-26T19:00:00Z",
                         "home": "Arsenal", "away": "Chelsea",
                         "p_H": 0.5, "p_D": 0.25, "p_A": 0.25}],
    }
    entry["hash"] = ledger_in.compute_hash(entry)
    (tmp_path / "2026-08-24.json").write_text(json.dumps(entry))

    assert ledger_in.verify_chain()["ok"]
    rows = ledger_in.all_predictions()
    assert len(rows) == 1 and rows[0]["league"] == "E0"
    assert rows[0]["model_rho"] == -0.0738


def test_the_scorecard_splits_by_division(real_names):
    from proofodds import grade
    rows = grade.by_league(_fake_week())
    assert [r["league"] for r in rows] == ["E0", "SP1"]
    assert rows[0]["n"] == 2 and rows[1]["n"] == 1
    assert rows[0]["name"] == "Premier League"


# --------------------------------------------------------------------------- #
#  The second market
# --------------------------------------------------------------------------- #
def _fitted():
    import datetime as _dt
    from proofodds import ledger as _l
    model, teams, _ = _l._model_for(
        _dt.datetime(2026, 5, 1, tzinfo=_dt.timezone.utc), "E0")
    return model, {t: i for i, t in enumerate(teams)}


@pytest.mark.needs_data
def test_totals_come_out_of_the_same_grid_as_the_result():
    """
    No second model. The scoreline grid already holds the whole distribution,
    so the total is a different sum of the same numbers — which is why both
    markets carry identical assumptions, the low-score correction included.
    """
    import numpy as np
    model, idx = _fitted()
    h, a = idx["Liverpool"], idx["Everton"]

    grid = model.score_matrix(h, a)
    goals = np.arange(grid.shape[0])
    by_hand = float(grid[(goals[:, None] + goals[None, :]) > 2].sum())

    over, under = model.totals_probs(h, a, 2.5)
    assert abs(over - by_hand) < 1e-12
    assert abs(over + under - 1.0) < 1e-12       # a half-goal line cannot push


@pytest.mark.needs_data
def test_a_stronger_attack_pushes_the_total_up():
    model, idx = _fitted()
    big = model.totals_probs(idx["Liverpool"], idx["Everton"])[0]
    small = model.totals_probs(idx["Everton"], idx["Burnley"])[0]
    assert big > small


def test_both_markets_are_sealed_and_each_sums_to_exactly_one(
        ledger_in, stub_models, tmp_path):
    """
    Same rule as the 1X2 probabilities: a ledger that invites people to check
    its arithmetic must not ship numbers that fail the first check anyone runs.
    """
    from proofodds.fixtures import Fixture
    now = dt.datetime(2026, 8, 26, 6, 0, tzinfo=dt.timezone.utc)
    stub_models.publish(
        [Fixture(now + dt.timedelta(days=2), "Arsenal", "Chelsea", league="E0")],
        now=now)
    row = json.loads(sorted(tmp_path.glob("*.json"))[0].read_text())["predictions"][0]
    assert row["p_H"] + row["p_D"] + row["p_A"] == 1.0
    assert row["p_over25"] + row["p_under25"] == 1.0


def test_the_two_markets_are_graded_on_their_own_matches():
    """
    A match can be scoreable on the result and not on the total: a closing
    total is not published for every match, and entries sealed before this
    market was published carry no probability for it at all. Neither gap may
    borrow matches from the other.
    """
    from proofodds import grade
    week = _fake_week().copy()
    week.loc[week.index[-1], ["ou_graded", "ou_model_loss", "ou_market_loss"]] = \
        [False, float("nan"), float("nan")]

    s, t = grade.scorecard(week), grade.totals_scorecard(week)
    assert s["n"] == 3 and t["n"] == 2
    assert t["uniform_log_loss"] == config.UNIFORM_LOG_LOSS_BINARY
    assert s["uniform_log_loss"] == config.UNIFORM_LOG_LOSS
    assert t["uniform_log_loss"] != s["uniform_log_loss"]


def test_the_totals_market_reports_share_of_available_not_just_the_gap():
    """
    The trap this guards. About 0.150 nats of knowledge sit in a 1X2 closing
    line and about 0.020 in a closing total, so a model lands closer to the
    market on goals almost regardless of how good it is. Two gaps of equal size
    mean very different things, and only the normalised figure compares them.
    """
    from proofodds import grade
    t = grade.totals_scorecard(_fake_week())
    assert t["share_of_available"] is not None
    expected = ((config.UNIFORM_LOG_LOSS_BINARY - t["model_log_loss"])
                / (config.UNIFORM_LOG_LOSS_BINARY - t["market_log_loss"]))
    assert abs(t["share_of_available"] - expected) < 1e-12


def test_the_weekly_email_reports_the_second_market_separately():
    from proofodds import newsletter
    s = newsletter.summarise(_fake_week(), dt.date(2026, 8, 24), dt.date(2026, 8, 30))
    assert s["totals"]["n"] == 3
    text = newsletter.render_text(s)
    assert "Over/under 2.5 goals" in text
    assert "not comparable" in text
    assert newsletter.render_html(s)


def test_the_verifier_needs_nothing_installed():
    """
    `python -m proofodds.verify` is the command the whole claim rests on, and a
    stranger runs it on a bare clone. It imported .ledger, which imports numpy,
    which pulls in pandas and requests — so the answer to "check it yourself"
    was really "install a scientific Python stack first".

    This asserts the file imports nothing outside the standard library.
    """
    import ast
    src = (Path(__file__).resolve().parent.parent
           / "proofodds" / "verify.py").read_text()
    imported = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            # a relative import is a sibling module, which is the thing to avoid
            assert node.level == 0, "verify.py must not import from the package"
            imported.add((node.module or "").split(".")[0])
    assert imported <= {"__future__", "hashlib", "json", "sys", "pathlib"}, \
        f"verify.py grew a dependency: {imported}"


def test_the_two_hash_implementations_agree(ledger_in, stub_models, tmp_path):
    """
    verify.py rewrites the hashing instead of calling ledger.compute_hash, so
    that our code is not the only witness to our code. The cost of a second
    implementation is that it can drift from the first. This is the guard.
    """
    from proofodds import verify as v
    from proofodds.fixtures import Fixture
    now = dt.datetime(2026, 8, 26, 6, 0, tzinfo=dt.timezone.utc)
    stub_models.publish([
        Fixture(now + dt.timedelta(days=2), "Arsenal", "Chelsea", league="E0"),
        Fixture(now + dt.timedelta(days=2), "Porto", "Benfica", league="P1"),
    ], now=now)

    entry = json.loads(sorted(tmp_path.glob("*.json"))[0].read_text())
    assert v.entry_hash(entry) == ledger_in.compute_hash(entry) == entry["hash"]

    ok, problems, stats = v.verify(tmp_path)
    assert ok and not problems and stats["sealed"] == 2

    # and it must notice a tampered entry on its own terms
    path = sorted(tmp_path.glob("*.json"))[0]
    bad = json.loads(path.read_text())
    bad["predictions"][0]["p_H"] = 0.99
    path.write_text(json.dumps(bad))
    ok, problems, _ = v.verify(tmp_path)
    assert not ok and "content hash mismatch" in problems[0]


@pytest.mark.parametrize("probs", [
    [0.725108, 0.147726, 0.127166],       # 73 + 15 + 13 = 101, live on the site
    [1 / 3, 1 / 3, 1 / 3],                # 33 + 33 + 33 = 99
    [0.5, 0.25, 0.25],
    [0.985, 0.010, 0.005],
    [0.49, 0.51],
])
def test_displayed_percentages_always_sum_to_one_hundred(probs):
    """
    Three probabilities rounded separately do not add up. Bayern v Stuttgart
    went out as 73/15/13 — a hundred and one per cent, two sections above a
    paragraph insisting our numbers sum to exactly one. It is the first
    arithmetic a sceptical reader checks, and it was wrong.
    """
    from proofodds.render import percent_split
    out = percent_split(probs)
    assert sum(out) == 100
    assert all(abs(o - p * 100) < 1 for o, p in zip(out, probs))


def test_matchday_calendar_always_runs_from_today_through_today_plus_eight():
    """Empty dates stay selectable and anything outside the horizon stays out."""
    from proofodds import render

    today = dt.date(2026, 8, 30)

    def row(offset, home, league="E0", *, past=False):
        kickoff = dt.datetime.combine(
            today + dt.timedelta(days=offset), dt.time(15, 0),
            tzinfo=dt.timezone.utc)
        return {
            "kickoff_dt": kickoff, "is_past": past, "league": league,
            "home": home, "league_name": config.LEAGUES[league]["name"],
            "league_short": config.LEAGUES[league]["short"],
            "league_country": config.LEAGUES[league]["country"],
            "league_flag": config.LEAGUES[league]["flag"],
        }

    days = render.upcoming_view([
        row(0, "Today"), row(3, "Three days", "SP1"),
        row(8, "Last day"), row(9, "Too late"),
        row(0, "Already started", past=True),
    ], today=today, days_ahead=8)

    assert len(days) == 9
    assert days[0]["date"] == "2026-08-30" and days[0]["picker_label"] == "Sun"
    assert days[1]["date"] == "2026-08-31" and days[1]["picker_label"] == "Mon"
    assert all(len(day["picker_label"]) == 3 for day in days)
    assert days[-1]["date"] == "2026-09-07"
    assert [len(day["matches"]) for day in days] == [1, 0, 0, 1, 0, 0, 0, 0, 1]
    assert days[3]["leagues"][0]["code"] == "SP1"
    assert all(match["home"] != "Too late"
               for day in days for match in day["matches"])


def test_matchday_calendar_rejects_a_backwards_horizon():
    from proofodds import render
    with pytest.raises(ValueError, match="negative"):
        render.upcoming_view([], today=dt.date(2026, 8, 30), days_ahead=-1)


@pytest.mark.needs_data
def test_the_stylesheet_url_changes_when_the_stylesheet_does(tmp_path, monkeypatch):
    """
    The CSS is cached for an hour and the HTML for five minutes, so after a
    deploy a returning visitor gets new markup against an old stylesheet. That
    is worse than either alone: the theme toggle showed both icons at once and
    the mobile header collapsed into three overlapping rows, on a phone whose
    only crime was having visited before.

    Stamping the URL with the file's own hash makes that pair impossible.
    """
    import re
    from proofodds import render

    def version(out):
        render.build(out)
        return re.search(r"style\.css\?v=(\w+)",
                         (out / "index.html").read_text()).group(1)

    before = version(tmp_path / "a")
    css = config.STATIC_DIR / "style.css"
    original = css.read_bytes()
    try:
        css.write_bytes(original + b"\n/* changed */\n")
        after = version(tmp_path / "b")
    finally:
        css.write_bytes(original)

    assert before and after and before != after
    assert version(tmp_path / "c") == before      # and back again, deterministic


@pytest.mark.needs_data
def test_the_page_renders_a_filter_and_a_theme_toggle(tmp_path):
    """
    Both are progressive enhancement: without JavaScript every match is still
    in the HTML and visible, which is the right default for a page whose whole
    argument is that nothing is hidden.
    """
    import re
    from proofodds import render
    render.build(tmp_path)
    html = (tmp_path / "index.html").read_text()
    assert 'class="theme-toggle"' in html
    assert "proofodds-theme" in html          # remembered across visits
    assert 'id="fixtures"' in html            # what the filter narrows
    dates = re.findall(r'data-date-choice="(\d{4}-\d{2}-\d{2})"', html)
    assert len(dates) == 9 and len(set(dates)) == 9
    assert (dt.date.fromisoformat(dates[-1])
            - dt.date.fromisoformat(dates[0])).days == 8
    assert 'class="date-step date-step--prev"' in html
    assert 'class="date-step date-step--next"' in html
    assert '<span class="tl">Over 2.5</span>' in html
    assert '<span class="td">Under 2.5</span>' in html
    weekdays = re.findall(r'class="date-weekday">([^<]+)</span>', html)
    assert len(weekdays) == 9 and all(len(day) == 3 for day in weekdays)
    assert "Today" not in weekdays and "Tomorrow" not in weekdays
    assert "URLSearchParams(window.location.search)" in html
    assert 'url.searchParams.set("date", date)' in html
    assert "Boolean(requested && !requestedExists)" in html
    assert 'chip.hidden = code !== "all" && !available.has(code)' in html
    assert "chip.disabled" not in html


def test_mobile_css_does_not_create_an_offscreen_canvas():
    """Hidden controls and edge-to-edge filters must not widen Android Chrome."""
    css = (config.STATIC_DIR / "style.css").read_text()
    assert "left: -9999px" not in css
    assert "overflow-x: clip" not in css
    assert css.count("overflow-x: hidden") >= 2
    assert "grid-template-columns: repeat(4, minmax(0, 1fr))" in css
    assert "overscroll-behavior-x: none" in css
    assert "grid-template-columns: repeat(9, 70px)" in css
    assert "max-width: 980px" in css
    assert "overscroll-behavior-inline: contain" in css
    assert ".chip[hidden] { display: none; }" in css
    assert "margin-left: -15px" not in css


def test_club_crest_tiles_use_a_neutral_white_background():
    """Real crests and monogram fallbacks sit on the same quiet white tile."""
    import re
    css = (config.STATIC_DIR / "style.css").read_text()
    block = re.search(r"\.club-mark \{([^}]+)\}", css, re.DOTALL)
    assert block and "background: #FFFFFF;" in block.group(1)
    for old_colour in ("#E5EEFF", "#E6F6EF", "#FFF0E6",
                       "#F1EAFF", "#FFF5D9", "#E6F4F7"):
        assert old_colour not in css


def test_only_the_approved_crest_hosts_are_allowed_by_the_deploy_csp():
    for relative in ("deploy/nginx-security-headers.conf", "deploy/Caddyfile"):
        content = (config.ROOT / relative).read_text()
        assert ("img-src 'self' data: https://crests.football-data.org"
                in content)
        assert "https://r2.thesportsdb.com" in content


def test_the_po_mark_is_used_consistently(tmp_path, monkeypatch):
    """Header, footer, browser tab and installed app share one source mark."""
    import pandas as pd
    import re
    from proofodds import render

    monkeypatch.setattr(render.grade, "graded_frame", lambda: pd.DataFrame())
    render.build(tmp_path)
    html = (tmp_path / "index.html").read_text()
    manifest = json.loads((tmp_path / "site.webmanifest").read_text())
    logo = (tmp_path / "logo.svg").read_text()

    assert html.count('class="brand-mark"') == 2
    versions = set(re.findall(
        r'/(?:logo\.svg|favicon\.svg|apple-touch-icon\.png|site\.webmanifest)\?v=([a-f0-9]+)',
        html))
    assert html.count('/logo.svg?v=') == 2
    assert len(versions) == 1
    assert (tmp_path / "favicon.svg").read_bytes() == (
        config.STATIC_DIR / "logo.svg").read_bytes()
    assert (tmp_path / "apple-touch-icon.png").read_bytes().startswith(b"\x89PNG")
    assert "<text" not in logo       # identical letterforms on every platform
    assert "#0b1628" in logo.lower()
    assert '<meta name="theme-color" content="#0B1628">' in html
    assert manifest["name"] == "ProofOdds"
    assert manifest["theme_color"] == "#0B1628"
    assert manifest["icons"][0]["src"] == "/logo.svg"


def test_scoreline_view_mirrors_the_low_score_correction():
    from proofodds import render

    independent = render.top_scorelines(1.0, 1.0, 0.0, limit=121)
    corrected = render.top_scorelines(1.0, 1.0, -0.1, limit=121)
    p0 = {row["label"]: row["p"] for row in independent}
    p1 = {row["label"]: row["p"] for row in corrected}

    assert len(p1) == 121 and abs(sum(p1.values()) - 1.0) < 1e-12
    assert p1["0\u20130"] > p0["0\u20130"]
    assert p1["1\u20131"] > p0["1\u20131"]
    assert p1["1\u20130"] < p0["1\u20130"]
    assert p1["0\u20131"] < p0["0\u20131"]


def test_the_build_creates_a_permanent_page_for_each_match(
        tmp_path, monkeypatch):
    """
    Match cards are no longer a dead end: their URL is generated from sealed
    identity fields, included in the sitemap and backed by the raw entry.

    This test deliberately uses no results CSVs. Rendering a sealed forecast
    and its evidence must not depend on being able to grade it yet.
    """
    import pandas as pd
    from proofodds import ledger, render

    kickoff = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=2)) \
        .replace(hour=15, minute=0, second=0, microsecond=0)
    kickoff_s = kickoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    predictions = tmp_path / "predictions"
    predictions.mkdir()
    entry = {
        "version": 4,
        "leagues": ["E0"],
        "published_at": "2026-08-28T06:00:00Z",
        "prev_hash": ledger.GENESIS,
        "generator": {},
        "models": {"E0": {"name": "dixon-coles", "rho": -0.08}},
        "predictions": [{
            "league": "E0", "kickoff": kickoff_s,
            "home": "Arsenal", "away": "Chelsea",
            "p_H": 0.5, "p_D": 0.25, "p_A": 0.25,
            "p_over25": 0.54, "p_under25": 0.46,
            "xg_home": 1.61, "xg_away": 1.08,
        }],
    }
    entry["hash"] = ledger.compute_hash(entry)
    (predictions / "2026-08-28.json").write_text(json.dumps(entry))

    monkeypatch.setattr(config, "PREDICTIONS_DIR", predictions)
    monkeypatch.setattr(config, "TIMESTAMPS_DIR", tmp_path / "timestamps")
    monkeypatch.setattr(render.grade, "graded_frame", lambda: pd.DataFrame())

    out = tmp_path / "site"
    render.build(out)
    route = f"/matches/{kickoff.date()}/e0-arsenal-v-chelsea/"
    page = out / route.lstrip("/") / "index.html"

    assert page.exists()
    html = page.read_text()
    assert "Arsenal vs Chelsea" in html
    assert "/predictions/2026-08-28.json" in html
    assert entry["hash"][:16] in html
    assert "1.85 fair odds" in html
    assert "2.17 fair odds" in html
    assert "Top 3 correct scores" in html
    assert "Indicative only" in html
    assert "not part of the scorecard" in html
    assert route in (out / "sitemap.xml").read_text()

    home = (out / "index.html").read_text()
    assert f'href="{route}"' in home
    assert ">Premier League<" in home
    assert "/flags/england.svg" in home
    assert (out / "flags" / "england.svg").exists()


def test_match_urls_use_the_sealed_names_not_later_display_aliases():
    """A future name-resolution improvement must not move a shared page."""
    from proofodds import render

    row = {
        "league": "E0", "kickoff": "2030-09-01T15:00:00Z",
        "home": "Coventry City FC", "away": "Hull City AFC",
        "home_raw": "Coventry City FC", "away_raw": "Hull City AFC",
        "p_H": 0.4, "p_D": 0.3, "p_A": 0.3,
        "xg_home": 1.4, "xg_away": 1.1,
        "published_at": "2026-08-28T06:00:00Z",
        "entry_hash": "a" * 64, "entry_file": "2026-08-28.json",
    }
    view = render.prediction_view(row)
    assert view["match_url"] == (
        "/matches/2030-09-01/e0-coventry-city-fc-v-hull-city-afc/")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))


# --------------------------------------------------------------------------- #
#  The newsletter
# --------------------------------------------------------------------------- #
def _fake_week():
    import pandas as pd
    rows = []
    for i, (lg, h, a, ftr, p, m) in enumerate([
        ("E0", "Arsenal", "Chelsea", "H", 0.55, 0.50),
        ("E0", "Leeds", "Everton", "A", 0.20, 0.30),
        ("SP1", "Real Madrid", "Sevilla", "H", 0.60, 0.58),
    ]):
        rows.append({
            "date": pd.Timestamp("2026-08-28") + pd.Timedelta(days=i),
            "league": lg,
            "home": h, "away": a, "FTR": ftr, "FTHG": 2, "FTAG": 1,
            "p_H": p, "p_D": 0.25, "p_A": 1 - p - 0.25,
            "mkt_H": m, "mkt_D": 0.25, "mkt_A": 1 - m - 0.25,
            "graded": True,
            "model_loss": -__import__("math").log(p),
            "market_loss": -__import__("math").log(m),
            "p_over25": 0.54, "p_under25": 0.46,
            "mkt_over25": 0.55, "mkt_under25": 0.45,
            "over25": True, "ou_graded": True,
            "ou_model_loss": -__import__("math").log(0.54),
            "ou_market_loss": -__import__("math").log(0.55),
        })
    return pd.DataFrame(rows)


def test_a_week_with_no_graded_matches_sends_nothing():
    """
    Silence is the correct output for an empty week.

    An email that says "0 matches this week" trains people to ignore the next
    one, and the next one is the point.
    """
    from proofodds import newsletter
    import pandas as pd
    assert newsletter.summarise(pd.DataFrame(), dt.date(2026, 8, 24),
                                dt.date(2026, 8, 30)) is None


def test_weekly_summary_reports_the_gap_both_ways():
    from proofodds import newsletter
    s = newsletter.summarise(_fake_week(), dt.date(2026, 8, 24), dt.date(2026, 8, 30))
    assert s is not None and s["n"] == 3
    # gap is model minus market: positive means we are behind
    assert abs(s["gap"] - (s["model"] - s["market"])) < 1e-12
    assert s["gap"] > 0
    text = newsletter.render_text(s)
    assert "behind the closing line" in text
    assert "Arsenal" in text and "Leeds" in text
    assert newsletter.subject_line(s).startswith("Week of 2026-08-24")


def test_weekly_send_is_not_repeated(tmp_path, monkeypatch):
    """A week already sent must never go out twice."""
    from proofodds import newsletter
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    assert not newsletter.already_sent("2026-08-24")
    newsletter.mark_sent("2026-08-24", 12345)
    assert newsletter.already_sent("2026-08-24")
    newsletter.mark_sent("2026-08-24", 12345)          # idempotent
    data = json.loads((tmp_path / newsletter.SENT_FILE).read_text())
    assert data["weeks"] == ["2026-08-24"]


def test_sending_without_a_key_raises_rather_than_pretending():
    from proofodds import newsletter
    import pytest as _pytest
    if config.KIT_API_KEY:
        _pytest.skip("a real key is configured")
    with _pytest.raises(RuntimeError, match="KIT_API_KEY"):
        newsletter.send_broadcast("s", "<p>x</p>", "p")


def test_week_bounds_is_the_week_that_just_finished():
    from proofodds import newsletter
    start, end = newsletter.week_bounds(dt.date(2026, 8, 31))   # a Monday
    assert start == dt.date(2026, 8, 24) and end == dt.date(2026, 8, 30)
    assert start.weekday() == 0 and end.weekday() == 6


def test_backup_and_editor_leftovers_are_never_published():
    """static/ is gitignored for these names, so only the build can catch them."""
    from proofodds import render
    for name in ("style.css.bak", "style.css.bak2", "style.css.bak.20260826",
                 ".style.css.swp", "match.html.orig", "patch.rej", "x.tmp",
                 "notes~", ".DS_Store", "Thumbs.db"):
        assert render.is_junk(name), name
    for name in ("style.css", "flags", "logo.svg", "robots.txt",
                 "backup.css", "template.css"):
        assert not render.is_junk(name), name


def test_leftovers_are_skipped_at_every_level_of_static(tmp_path, monkeypatch):
    """The real build copier: top level and nested asset directories."""
    from proofodds import render
    static = tmp_path / "static"
    (static / "flags").mkdir(parents=True)
    (static / "style.css").write_text("body{}", encoding="utf-8")
    (static / "style.css.bak").write_text("old", encoding="utf-8")
    (static / "flags" / "england.svg").write_text("<svg/>", encoding="utf-8")
    (static / "flags" / "england.svg.bak").write_text("old", encoding="utf-8")
    monkeypatch.setattr(config, "STATIC_DIR", static)

    out = tmp_path / "out"
    out.mkdir()
    render.copy_static(out)

    assert (out / "style.css").read_text() == "body{}"
    assert (out / "flags" / "england.svg").exists()
    assert not (out / "style.css.bak").exists()
    assert not (out / "flags" / "england.svg.bak").exists()


# --------------------------------------------------------------------------- #
#  Guest ledger
# --------------------------------------------------------------------------- #
def _seal_guest(tmp_path, monkeypatch, **overrides):
    from proofodds import guest, guest_data
    monkeypatch.setattr(config, "GUESTS_DIR", tmp_path / "guests")
    monkeypatch.setattr(guest_data, "resolve", lambda name, code: (name, "exact"))
    kwargs = dict(guest_name="Test Guest", league="E0", home="Arsenal",
                  away="Chelsea", kickoff="2099-01-01T15:00Z", market="1X2",
                  selection="H", odds=2.05, stamp=False)
    kwargs.update(overrides)
    return guest.seal(**kwargs)


def test_guest_entry_is_sealed_chained_and_verifiable(tmp_path, monkeypatch):
    """
    A guest chain must be checkable by the SAME standalone verifier as the
    main ledger — a guest record that needed special tooling to audit would
    not deserve the page it is printed on.
    """
    from proofodds import guest, verify
    from proofodds.ledger import compute_hash

    first = _seal_guest(tmp_path, monkeypatch)
    entry = json.loads(first.read_text(encoding="utf-8"))
    assert entry["prev_hash"] == "0" * 64
    assert entry["hash"] == compute_hash(entry)
    assert verify.entry_hash(entry) == entry["hash"]

    second = _seal_guest(tmp_path, monkeypatch, selection="D", odds=3.4,
                         now=dt.datetime(2098, 1, 1, tzinfo=dt.timezone.utc))
    linked = json.loads(second.read_text(encoding="utf-8"))
    assert linked["prev_hash"] == entry["hash"]
    assert verify.entry_hash(linked) == linked["hash"]


def test_guest_seal_refuses_what_would_need_fixing_later(tmp_path, monkeypatch):
    import pytest as _pytest
    from proofodds import guest

    with _pytest.raises(ValueError):   # started match
        _seal_guest(tmp_path, monkeypatch, kickoff="2020-01-01T15:00Z")
    with _pytest.raises(ValueError):   # placeholder price
        _seal_guest(tmp_path, monkeypatch, odds=1.0)
    with _pytest.raises(ValueError):   # unknown market
        _seal_guest(tmp_path, monkeypatch, market="BTTS")
    with _pytest.raises(ValueError):   # selection/market mismatch
        _seal_guest(tmp_path, monkeypatch, market="OU2.5", selection="H")
    with _pytest.raises(ValueError):   # unknown division
        _seal_guest(tmp_path, monkeypatch, league="XX")


def test_creator_coverage_is_complete_and_source_limited():
    """All published feeds are accepted, but only with markets they carry."""
    europe = {code for code, meta in config.GUEST_COMPETITIONS.items()
              if meta["source"] == "season"}
    extra = {code for code, meta in config.GUEST_COMPETITIONS.items()
             if meta["source"] == "extra"}

    assert len(config.GUEST_COMPETITIONS) == 38
    assert {"E0", "E2", "EC", "SC3", "D2", "I2", "SP2", "F2",
            "B1", "T1", "G1"} <= europe
    assert extra == {"ARG", "AUT", "BRA", "CHN", "DNK", "FIN", "IRL",
                     "JPN", "MEX", "NOR", "POL", "ROU", "RUS", "SWE",
                     "SWZ", "USA"}
    assert all(config.GUEST_COMPETITIONS[code]["markets"]
               == ("1X2", "OU2.5", "AH") for code in europe)
    assert all(config.GUEST_COMPETITIONS[code]["markets"] == ("1X2",)
               for code in extra)
    assert not any("BTTS" in meta["markets"]
                   for meta in config.GUEST_COMPETITIONS.values())


def test_extra_creator_csv_is_normalised(tmp_path, monkeypatch):
    import pandas as pd
    from proofodds import guest_data

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    (tmp_path / "guest_BRA.csv").write_text(
        "Country,League,Season,Date,Time,Home,Away,HG,AG,Res,AvgCH,AvgCD,AvgCA\n"
        "Brazil,Serie A,2026,31/08/2026,21:00,Flamengo,Palmeiras,2,1,H,2.1,3.4,3.5\n"
        "Brazil,Serie A,2026,08/09/2026,20:00,Gremio,Santos,,,,2.0,3.3,3.8\n",
        encoding="utf-8")
    guest_data._extra_cache.clear()

    frame = guest_data.load_matches("BRA")

    assert list(frame[["HomeTeam", "AwayTeam", "FTR"]].iloc[0]) == [
        "Flamengo", "Palmeiras", "H"]
    assert len(frame) == 1                         # future/unplayed row excluded
    assert pd.api.types.is_datetime64_any_dtype(frame["Date"])
    assert frame.iloc[0]["AvgCH"] == 2.1


def test_guest_market_and_handicap_line_validation(tmp_path, monkeypatch):
    import pytest as _pytest

    with _pytest.raises(ValueError, match="no published closing benchmark"):
        _seal_guest(tmp_path, monkeypatch, league="BRA", market="OU2.5",
                    selection="over")
    with _pytest.raises(ValueError, match="requires --line"):
        _seal_guest(tmp_path, monkeypatch, market="AH", selection="H")
    with _pytest.raises(ValueError, match="quarter-goal"):
        _seal_guest(tmp_path, monkeypatch, market="AH", selection="H",
                    line=-0.3)
    with _pytest.raises(ValueError, match="only valid for the AH"):
        _seal_guest(tmp_path, monkeypatch, line=0.0)

    path = _seal_guest(tmp_path, monkeypatch, market="AH", selection="A",
                       line=0.25)
    assert json.loads(path.read_text())["line"] == 0.25


@pytest.mark.parametrize("difference,line,odds,result,pnl", [
    (1, -1.0, 2.0, "push", 0.0),
    (1, -0.75, 2.0, "half won", 0.5),
    (0, 0.25, 2.0, "half won", 0.5),
    (0, -0.25, 2.0, "half lost", -0.5),
    (-1, 1.0, 2.0, "push", 0.0),
    (-2, 1.5, 2.0, "lost", -1.0),
])
def test_asian_handicap_quarter_line_settlement(
        difference, line, odds, result, pnl):
    from proofodds import guest

    assert guest._asian_settlement(difference, line, odds) == (result, pnl)


def _write_guest_entry(tmp_path, monkeypatch, entry):
    from proofodds import guest
    from proofodds.ledger import compute_hash
    monkeypatch.setattr(config, "GUESTS_DIR", tmp_path / "guests")
    directory = config.GUESTS_DIR / "test-guest"
    directory.mkdir(parents=True)
    entry = dict(entry, schema="guest-2", guest="test-guest",
                 guest_name="Test Guest", sealed_at="2026-08-31T10:00:00Z",
                 book="", note="", prev_hash="0" * 64)
    entry["hash"] = compute_hash(entry)
    (directory / "test-guest--2026-08-31T100000Z.json").write_text(
        json.dumps(entry), encoding="utf-8")


def test_asian_clv_requires_the_same_closing_line(tmp_path, monkeypatch):
    import pandas as pd
    from proofodds import guest, guest_data

    matches = pd.DataFrame([{
        "Date": pd.Timestamp("2026-09-01"), "HomeTeam": "Arsenal",
        "AwayTeam": "Chelsea", "FTHG": 2, "FTAG": 1, "FTR": "H",
        "AHCh": -0.5, "AvgCAHH": 1.90, "AvgCAHA": 2.00,
    }])
    monkeypatch.setattr(guest_data, "load_matches", lambda code: matches)
    _write_guest_entry(tmp_path, monkeypatch, {
        "kickoff": "2026-09-01T15:00:00Z", "league": "E0",
        "home": "Arsenal", "away": "Chelsea", "home_raw": "Arsenal",
        "away_raw": "Chelsea", "market": "AH", "selection": "H",
        "line": -0.5, "odds_taken": 2.0,
    })

    row = guest.grade_guest("test-guest")["entries"][0]
    assert row["status"] == "graded"
    assert row["result"] == "won" and row["pnl"] == 1.0
    assert row["close_line"] == -0.5
    assert row["clv"] == pytest.approx(2.0 / 1.9 - 1)


def test_asian_different_line_is_settled_but_not_fake_clv(
        tmp_path, monkeypatch):
    import pandas as pd
    from proofodds import guest, guest_data

    matches = pd.DataFrame([{
        # One-day difference proves the UTC/local-date join tolerance too.
        "Date": pd.Timestamp("2026-08-31"), "HomeTeam": "Flamengo",
        "AwayTeam": "Palmeiras", "FTHG": 1, "FTAG": 1, "FTR": "D",
        "AHCh": -0.5, "AvgCAHH": 1.95, "AvgCAHA": 1.95,
    }])
    monkeypatch.setattr(guest_data, "load_matches", lambda code: matches)
    _write_guest_entry(tmp_path, monkeypatch, {
        "kickoff": "2026-09-01T00:30:00Z", "league": "E0",
        "home": "Flamengo", "away": "Palmeiras", "home_raw": "Flamengo",
        "away_raw": "Palmeiras", "market": "AH", "selection": "H",
        "line": 0.0, "odds_taken": 1.91,
    })

    record = guest.grade_guest("test-guest")
    row = record["entries"][0]
    assert row["status"] == "line_changed"
    assert row["result"] == "push" and row["pnl"] == 0.0
    assert row["close_line"] == -0.5 and row["line_advantage"] == 0.5
    assert row["clv"] is None and row["beat_close"] is None
    assert record["n_graded"] == 0 and record["n_settled"] == 1


@pytest.mark.needs_data
def test_guest_clv_grades_against_the_average_close(tmp_path, monkeypatch):
    """
    CLV is odds-taken over the closing price for the same selection. A draw
    taken at 3.6 against a close of 3.47 must read +3.7%, won, +2.6 units —
    and an unplayed match must stay pending, not vanish.
    """
    from proofodds import data, guest
    from proofodds.ledger import compute_hash
    monkeypatch.setattr(config, "GUESTS_DIR", tmp_path / "guests")

    matches = data.add_market_probabilities(data.load_matches("E0"))
    played = matches[matches["graded"] if "graded" in matches else
                     matches["has_odds"] & matches["FTR"].notna()].iloc[0]

    gdir = tmp_path / "guests" / "test-guest"
    gdir.mkdir(parents=True)
    entry = {
        "schema": "guest-1", "guest": "test-guest", "guest_name": "Test Guest",
        "sealed_at": "2020-01-01T10:00:00Z",
        "kickoff": played["Date"].strftime("%Y-%m-%dT%H:%M:%SZ"),
        "league": "E0", "home": played["HomeTeam"], "away": played["AwayTeam"],
        "home_raw": played["HomeTeam"], "away_raw": played["AwayTeam"],
        "market": "1X2", "selection": str(played["FTR"]),
        "odds_taken": float(played[{"H": "AvgCH", "D": "AvgCD",
                                    "A": "AvgCA"}[played["FTR"]]]) * 1.05,
        "book": "", "note": "", "prev_hash": "0" * 64,
    }
    entry["hash"] = compute_hash(entry)
    (gdir / "test-guest--2020-01-01T100000Z.json").write_text(
        json.dumps(entry), encoding="utf-8")

    record = guest.grade_guest("test-guest")
    assert record["n_graded"] == 1
    row = record["entries"][0]
    assert row["status"] == "graded"
    assert row["won"] is True
    assert abs(row["clv"] - 0.05) < 1e-9      # priced 5% over the close
    assert row["beat_close"] is True
# --------------------------------------------------------------------------- #
# ProofOdds 1.2 markets and Corners Lab
def test_btts_and_asian_handicap_are_coherent():
    from proofodds.dixon_coles import DixonColes
    import numpy as np
    model = DixonColes(["H", "A"], np.zeros(2), np.zeros(2), .2, .1, -.05,
                       .002, .6)
    assert np.isclose(model.btts_probs(0, 1).sum(), 1)
    for line in (-.75, -.25, 0, .25, .75):
        row = model.asian_handicap(0, 1, line)
        assert np.isclose(row["p_home"] + row["p_away"], 1)
        assert row["fair_home"] > 1 and row["fair_away"] > 1


def test_new_competitions_and_corner_source_boundaries():
    from proofodds import config
    assert config.LEAGUES["B1"] == {
        "name": "Jupiler Pro League", "short": "JPL", "country": "Belgium",
        "flag": "belgium", "fdorg": None, "tier": 1,
        "source": "season", "fixtures": "fdco",
    }
    assert config.LEAGUES["SC0"]["source"] == "season"
    assert config.LEAGUES["SC0"]["fixtures"] == "fdco"
    assert config.LEAGUES["BRA"]["source"] == "extra"
    assert "SC0" in config.GUEST_COMPETITIONS
    assert config.GUEST_COMPETITIONS["BRA"]["markets"] == ("1X2",)
    assert data.OVERRIDES["BRA"]["ca mineiro"] == "Atletico-MG"
    assert data.OVERRIDES["BRA"]["cr flamengo"] == "Flamengo RJ"
    assert data.OVERRIDES["BRA"]["botafogo fr"] == "Botafogo RJ"


def test_legacy_latin1_results_file_is_read(tmp_path):
    path = tmp_path / "SC0_1819.csv"
    path.write_bytes(b"HomeTeam,AwayTeam,Price\nA,B,8\xa0\n")
    frame = data.read_csv(path)
    assert frame.loc[0, "HomeTeam"] == "A"


def test_name_audit_uses_the_configured_fixture_source(monkeypatch):
    from proofodds import fixtures
    called = []
    monkeypatch.setattr(fixtures, "from_football_data_co_uk",
                        lambda days, leagues: called.append((days, leagues)) or [])
    monkeypatch.setattr(fixtures, "from_football_data_org",
                        lambda league, days: (_ for _ in ()).throw(AssertionError("wrong source")))
    assert fixtures.for_name_audit("SC0", 14) == []
    assert called == [(14, ["SC0"])]


def test_fdco_fixture_reader_strips_utf8_bom(monkeypatch):
    from proofodds import fixtures
    from types import SimpleNamespace
    tomorrow = dt.date.today() + dt.timedelta(days=1)
    payload = ("Div,Date,Time,HomeTeam,AwayTeam\n"
               f"SC0,{tomorrow:%d/%m/%Y},15:00,Dundee,St Mirren\n")
    response = SimpleNamespace(status_code=200,
                               content=b"\xef\xbb\xbf" + payload.encode("utf-8"))
    monkeypatch.setattr(fixtures.requests, "get", lambda *a, **k: response)
    monkeypatch.setattr(fixtures, "_name", lambda raw, league, unresolved: (raw, True))
    got = fixtures.from_football_data_co_uk(14, ["SC0"])
    assert len(got) == 1
    assert got[0].league == "SC0"
    assert got[0].home_raw == "Dundee"


def test_corner_model_produces_normalised_total_distribution():
    from proofodds import corners
    import numpy as np, pandas as pd
    rng = np.random.default_rng(4); n = 120
    frame = pd.DataFrame({"HomeTeam": np.where(np.arange(n)%2, "A", "B"),
                          "AwayTeam": np.where(np.arange(n)%2, "B", "A"),
                          "HC": rng.poisson(5.5, n), "AC": rng.poisson(4.5, n)})
    model = corners.fit_from_frame(frame, ["A", "B"])
    pmf = model.total_pmf(0, 1)
    assert np.isclose(pmf.sum(), 1) and (pmf >= 0).all()
    assert model.expected(0, 1)[0] > 0


def test_corner_cards_fill_the_second_summary_column():
    from proofodds import render
    row = {"league": "SC0", "kickoff": "2030-09-02T18:45:00Z",
           "home": "Celtic", "away": "Aberdeen",
           "p_H": .6, "p_D": .2, "p_A": .2,
           "published_at": "2026-09-02T00:08:00Z",
           "corners": {"x_home": 7.5, "x_away": 3.2, "x_total": 10.6,
                       "totals": [{"line": 9.5, "p_over": .58, "p_under": .42},
                                  {"line": 10.5, "p_over": .48, "p_under": .52}]}}
    view = render.prediction_view(row)
    assert view["corner_main"]["line"] == 10.5


# --------------------------------------------------------------------------- #
#  Scored vs forecast-only markets
# --------------------------------------------------------------------------- #
def test_the_scored_market_registry_matches_what_the_sources_carry():
    """
    The season files publish a closing 1X2, over/under 2.5 and one Asian
    handicap; the "new leagues" files publish a closing 1X2 and nothing else.
    Calling a market scored where no closing price exists is the exact failure
    this registry prevents, so it is pinned rather than trusted.
    """
    assert config.scored_markets("E0") == ("1X2", "OU2.5", "AH")
    assert config.scored_markets("BRA") == ("1X2",)
    assert config.is_scored("BRA", "1X2")
    assert not config.is_scored("BRA", "OU2.5")
    assert not config.is_scored("BRA", "AH")
    # every division the model publishes can at least be scored on the result
    for code in config.LEAGUES:
        assert "1X2" in config.scored_markets(code), code
    # and nothing claims to score a market with no benchmark anywhere
    for market in config.FORECAST_MARKETS:
        for code in config.LEAGUES:
            assert market not in config.scored_markets(code), (code, market)


@pytest.mark.needs_data
def test_a_division_without_closing_totals_never_grades_them():
    """
    Structural backstop to the labelling: Brazil's source has no closing total
    or handicap column, so those markets must be impossible to grade there —
    not merely untagged.
    """
    from proofodds import data
    try:
        matches = data.add_market_probabilities(data.load_matches("BRA"))
    except FileNotFoundError:
        pytest.skip("BRA creator CSV not synced in this checkout")
    assert not matches["has_ou_odds"].any()
    if "has_ah_odds" in matches:
        assert not matches["has_ah_odds"].any()


def test_every_published_market_is_tagged_on_the_match_card():
    """
    A number on a card without its tag is the failure mode the whole two-bucket
    scheme exists to prevent, so the template is checked for one tag per market.
    """
    card = (config.TEMPLATE_DIR / "match.html").read_text(encoding="utf-8")
    assert card.count("mkt-tag") >= 5      # 1X2, O/U, BTTS, AH, corners
    assert "match.ou_scored" in card and "match.ah_scored" in card
    assert "mkt-legend" in card


# --------------------------------------------------------------------------- #
#  TheStatsAPI client
# --------------------------------------------------------------------------- #
def _odds_payload(book="Pinnacle", **markets):
    base = {"match_odds": {"home": {"opening": "2.05", "last_seen": "2.10"},
                           "draw": {"opening": "3.45", "last_seen": "3.50"},
                           "away": {"opening": "3.80", "last_seen": "3.70"}}}
    base.update(markets)
    return {"data": {"match_id": "mt_1", "bookmakers": [
        {"bookmaker": book, "markets": base}]}}


def test_a_price_that_is_still_moving_is_never_a_closing_price(monkeypatch):
    """
    The single safety property of the whole module. There is no `closing`
    field — prices are {opening, last_seen} — and on a match that has not
    finished, last_seen is simply the latest number. Grading against it would
    score the model against a moving target, silently.
    """
    import pytest as _pytest
    from proofodds import statsapi
    monkeypatch.setattr(statsapi, "match_odds",
                        lambda *a, **k: _odds_payload())

    for status in ("scheduled", "live", "postponed", ""):
        with _pytest.raises(statsapi.StatsAPIError) as caught:
            statsapi.closing_odds({"id": "mt_1", "status": status})
        assert "not finished" in str(caught.value)

    prices = statsapi.closing_odds({"id": "mt_1", "status": "finished"})
    assert prices["1X2"] == {"H": 2.10, "D": 3.50, "A": 3.70}


def test_placeholder_prices_are_dropped_not_inverted(monkeypatch):
    """0 and 1 are placeholders. 1/0 is what turns one row into 34 nats."""
    from proofodds import statsapi
    monkeypatch.setattr(statsapi, "match_odds", lambda *a, **k: _odds_payload(
        match_odds={"home": {"last_seen": "2.05"},
                    "draw": {"last_seen": "0"},
                    "away": {"last_seen": "3.60"}},
        btts={"yes": {"last_seen": "1.80"}, "no": {"last_seen": "1.95"}}))
    prices = statsapi.closing_odds({"id": "mt_1", "status": "finished"})
    assert "1X2" not in prices          # incomplete -> absent, never partial
    assert prices["BTTS"] == {"yes": 1.80, "no": 1.95}


def test_closing_odds_reads_lines_and_handicaps(monkeypatch):
    from proofodds import statsapi
    monkeypatch.setattr(statsapi, "match_odds", lambda *a, **k: _odds_payload(
        total_goals={"2.5": {"over": {"last_seen": "1.90"},
                             "under": {"last_seen": "1.95"}},
                     "3.5": {"over": {"last_seen": "3.10"},
                             "under": {"last_seen": "1.36"}}},
        asian_handicap={"home": {"-0.5": {"last_seen": "2.10"}},
                        "away": {"+0.5": {"last_seen": "1.80"}}}))
    prices = statsapi.closing_odds({"id": "mt_1", "status": "finished"})
    assert prices["OU"]["2.5"] == {"over": 1.90, "under": 1.95}
    assert prices["OU"]["3.5"]["under"] == 1.36
    assert prices["AH"]["-0.5"]["home"] == 2.10
    assert prices["AH"]["+0.5"]["away"] == 1.80


def test_a_book_we_did_not_ask_for_is_not_substituted(monkeypatch):
    """
    Pinnacle does not price every market here; soft books do. Falling back to
    whoever is present would quietly grade a sharp benchmark against a soft
    one, and the number would look better for no reason.
    """
    from proofodds import statsapi
    monkeypatch.setattr(statsapi, "match_odds",
                        lambda *a, **k: _odds_payload(book="Bet365"))
    assert statsapi.closing_odds({"id": "mt_1", "status": "finished"}) == {}


def test_overround_separates_a_sharp_book_from_an_average():
    from proofodds import statsapi
    # a real Pinnacle close runs ~2.5-3% over; an average or soft book 4-5%
    sharp = statsapi.overround({"H": 2.10, "D": 3.50, "A": 3.70})
    average = statsapi.overround({"H": 2.05, "D": 3.45, "A": 3.65})
    assert 0.015 < sharp < 0.038          # inside the band the gate accepts
    assert average > 0.038                # outside it, which is the point
    assert average > sharp


def test_the_monthly_quota_stops_rather_than_warns(tmp_path):
    import pytest as _pytest
    from proofodds import statsapi
    budget = statsapi.Budget(path=tmp_path / "b.json", per_min=99, monthly=3)
    for _ in range(3):
        budget.check()
        budget.spend()
    assert budget.remaining() == 0
    with _pytest.raises(statsapi.QuotaExhausted):
        budget.check()
    # and it survives a restart, because the counter is on disk
    assert statsapi.Budget(path=tmp_path / "b.json", monthly=3).used() == 3


def test_the_client_refuses_to_call_without_a_key(monkeypatch, tmp_path):
    import pytest as _pytest
    from proofodds import statsapi
    monkeypatch.setattr(config, "STATSAPI_KEY", "")
    monkeypatch.setattr(config, "STATSAPI_DIR", tmp_path)
    with _pytest.raises(statsapi.StatsAPIError) as caught:
        statsapi.get("football/competitions", {"page": 1})
    assert "never be committed" in str(caught.value)


def test_opening_and_closing_are_read_separately(monkeypatch):
    """
    The payload carries no timestamp, so the only evidence that `last_seen` is
    a close rather than a late price is that its margin is thinner than the
    opening one. Reading both is what makes that check possible.
    """
    from proofodds import statsapi
    monkeypatch.setattr(statsapi, "match_odds", lambda *a, **k: _odds_payload())
    pair = statsapi.opening_and_closing_1x2({"id": "mt_1"})
    assert pair["opening"] == {"H": 2.05, "D": 3.45, "A": 3.80}
    assert pair["last_seen"] == {"H": 2.10, "D": 3.50, "A": 3.70}
    # and the fixture behaves like a real market: the close is tighter
    assert statsapi.overround(pair["last_seen"]) < statsapi.overround(pair["opening"])


def test_being_throttled_is_not_the_same_as_having_no_price(monkeypatch, tmp_path):
    """
    The first Ligue 1 sweep recorded eight matches as having no Pinnacle price
    when they had simply been refused. Coverage is the number the benchmark
    decision rests on, so a refusal has to be a different type from an
    absence — otherwise our own rate limiter quietly argues against migrating.
    """
    import pytest as _pytest
    from proofodds import statsapi

    class _Response:
        status_code = 429
        headers = {"retry-after": "0"}
        text = "rate limited"

    monkeypatch.setattr(config, "STATSAPI_KEY", "test-key")
    monkeypatch.setattr(config, "STATSAPI_DIR", tmp_path)
    monkeypatch.setattr(statsapi.requests, "get", lambda *a, **k: _Response())
    monkeypatch.setattr(statsapi.time, "sleep", lambda *_: None)

    # Bound to tmp_path: the module-level _budget resolves its path at import
    # from the real STATSAPI_DIR, so a test that let it through would spend
    # the live monthly quota on every CI run.
    budget = statsapi.Budget(path=tmp_path / "b.json", per_min=99)

    with _pytest.raises(statsapi.RateLimited):
        statsapi.get("football/matches/mt_1/odds", cache=False, budget=budget)
    assert issubclass(statsapi.RateLimited, statsapi.StatsAPIError)
    assert statsapi._budget.path != budget.path


def test_requests_are_spaced_rather_than_bursted(monkeypatch):
    """
    Ten requests in two seconds is a burst to a token bucket however
    comfortably it fits inside a per-minute figure. Even spacing is the same
    throughput without the 429s.
    """
    from proofodds import statsapi
    slept: list[float] = []
    monkeypatch.setattr(statsapi.time, "sleep", lambda s: slept.append(s))
    clock = {"t": 0.0}
    monkeypatch.setattr(statsapi.time, "monotonic", lambda: clock["t"])

    budget = statsapi.Budget(per_min=10)
    budget.throttle()                 # first call is free
    assert not slept
    budget.throttle()                 # second must wait a full 6s gap
    assert slept and abs(slept[-1] - 6.0) < 1e-9
