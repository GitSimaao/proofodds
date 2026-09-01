"""Results and closing prices used only by the creator/guest ledger.

The model data path and the creator data path are intentionally separate.
`data.py` owns the eight divisions whose matches train a Dixon-Coles model.
This module owns the wider set of competitions in which somebody else may
seal a pick.  Supporting a competition here makes no claim that ProofOdds has
fitted or validated a model for it.

football-data.co.uk publishes two schemas:

* season files (``mmz4281/<season>/<code>.csv``): 22 European divisions,
  with closing 1X2, O/U 2.5 and a main Asian-handicap line;
* country files (``new/<code>.csv``): 16 further top-flight feeds, with a
  closing 1X2 only.

The market registry in ``config.GUEST_COMPETITIONS`` mirrors that distinction.
"""

from __future__ import annotations

import difflib
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from . import config, data

log = logging.getLogger(__name__)

EXTRA_BASE = "https://www.football-data.co.uk/new"
EXTRA_CORE = ["Date", "Home", "Away", "HG", "AG", "Res"]
EXTRA_ODDS = ["AvgCH", "AvgCD", "AvgCA"]


def competition(code: str) -> dict:
    code = code.upper()
    try:
        return config.GUEST_COMPETITIONS[code]
    except KeyError as exc:
        raise ValueError(
            f"unknown creator competition {code!r} — use `python -m "
            "proofodds.guest coverage` for the complete list") from exc


def extra_path(code: str) -> Path:
    """Local cache path for one all-history country feed."""
    return config.DATA_DIR / f"guest_{code.upper()}.csv"


def _looks_like_extra(raw: bytes) -> bool:
    head = raw[:2000].lstrip().lstrip(b"\xef\xbb\xbf")
    if head[:1] in (b"<", b"{"):
        return False
    first = head.split(b"\n", 1)[0]
    return all(name in first for name in (b"Home", b"Away", b"Res"))


def download_extra(code: str, *, force: bool = False) -> bool:
    """Refresh one country feed without letting a bad response replace it."""
    meta = competition(code)
    if meta["source"] != "extra":
        raise ValueError(f"{code} is a season-file competition, not an extra feed")
    path = extra_path(code)
    if path.exists() and path.stat().st_size and not force:
        return False

    url = f"{EXTRA_BASE}/{code}.csv"
    response = requests.get(url, timeout=45)
    if response.status_code != 200:
        raise RuntimeError(f"{url} returned {response.status_code}")
    if not _looks_like_extra(response.content):
        raise RuntimeError(f"{url} did not return a results CSV — cache preserved")

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".csv.part")
    tmp.write_bytes(response.content)
    tmp.replace(path)
    return True


def refresh(code: str) -> None:
    """Refresh the files needed to resolve and grade one competition."""
    code = code.upper()
    meta = competition(code)
    if meta["source"] == "extra":
        download_extra(code, force=True)
        return

    # Creator entries concern current fixtures, not model training.  The last
    # two season files cover the summer boundary without downloading twelve
    # seasons for every lower division somebody might try once.
    successes = 0
    for season in config.SEASONS[-2:]:
        try:
            data.download_season(code, season, force=True)
            successes += 1
        except Exception as exc:
            log.info("creator data %s %s not refreshed: %s", code, season, exc)
    if not successes and not any(data.is_cached(data.season_path(code, season))
                                 for season in config.SEASONS):
        raise RuntimeError(f"no usable results file is available for {code}")


def refresh_many(codes=None) -> None:
    codes = list(codes or config.GUEST_COMPETITIONS)
    failures = []
    for code in codes:
        try:
            refresh(code)
        except Exception as exc:
            failures.append((code, str(exc)))
            log.warning("could not refresh creator competition %s: %s", code, exc)
    if failures:
        joined = "; ".join(f"{code}: {reason}" for code, reason in failures)
        raise RuntimeError(f"creator-data refresh incomplete — {joined}")


_extra_cache: dict[str, tuple[int, pd.DataFrame]] = {}


def _load_extra(code: str) -> pd.DataFrame:
    path = extra_path(code)
    if not path.exists():
        raise FileNotFoundError(
            f"No creator CSV for {code}. Run `python -m proofodds.guest sync "
            f"--leagues {code}` first.")
    key = path.stat().st_mtime_ns
    cached = _extra_cache.get(code)
    if cached and cached[0] == key:
        return cached[1].copy()

    raw = pd.read_csv(path, encoding="utf-8-sig")
    missing = [col for col in EXTRA_CORE if col not in raw.columns]
    if missing:
        raise ValueError(f"{path.name} is missing {missing}")

    renamed = raw.rename(columns={
        "Home": "HomeTeam", "Away": "AwayTeam",
        "HG": "FTHG", "AG": "FTAG", "Res": "FTR",
        "League": "Competition",
    })
    keep = ["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR",
            "Competition", "Season", *EXTRA_ODDS]
    frame = renamed[[col for col in keep if col in renamed.columns]].copy()
    for col in EXTRA_ODDS:
        if col not in frame:
            frame[col] = np.nan
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    for col in ("FTHG", "FTAG"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame["Date"] = pd.to_datetime(frame["Date"], dayfirst=True,
                                    format="mixed", errors="coerce")
    frame = frame.dropna(subset=["Date", "HomeTeam", "AwayTeam",
                                 "FTHG", "FTAG", "FTR"])
    frame["FTHG"] = frame["FTHG"].astype(int)
    frame["FTAG"] = frame["FTAG"].astype(int)
    frame["FTR"] = frame["FTR"].astype(str).str.strip().str.upper()
    frame = frame[frame["FTR"].isin(["H", "D", "A"])]
    for col in ("HomeTeam", "AwayTeam"):
        frame[col] = frame[col].astype(str).str.strip()
    if "Competition" in frame:
        frame["Competition"] = frame["Competition"].astype(str).str.strip()
    frame["League"] = code
    frame = frame.sort_values(["Date", "HomeTeam"]).reset_index(drop=True)
    _extra_cache[code] = (key, frame)
    return frame.copy()


def load_matches(code: str) -> pd.DataFrame:
    """Played matches in either source schema, normalised to model columns."""
    code = code.upper()
    meta = competition(code)
    if meta["source"] == "season":
        return data.load_matches(code)
    return _load_extra(code)


def known_teams(code: str) -> frozenset[str]:
    code = code.upper()
    if competition(code)["source"] == "season":
        return data.known_teams(code)
    frame = load_matches(code)
    return frozenset(set(frame["HomeTeam"]) | set(frame["AwayTeam"]))


def resolve(name: str, code: str) -> tuple[str | None, str]:
    """Resolve a submitted club name without ever guessing between two clubs."""
    code = code.upper()
    if competition(code)["source"] == "season":
        return data.resolve(name, code)

    name = (name or "").strip()
    if not name:
        return None, "empty"
    try:
        known = known_teams(code)
    except FileNotFoundError:
        return None, "no data for competition — run guest sync first"
    if name in known:
        return name, "exact"

    probes = (data.fold(name), " ".join(data.tokens(name)))
    for probe in probes:
        hits = {club for club in known
                if probe in (data.fold(club), " ".join(data.tokens(club)))}
        if len(hits) == 1:
            return hits.pop(), "folded"
        if len(hits) > 1:
            return None, f"ambiguous between {sorted(hits)}"

    target = "".join(data.tokens(name))
    scored = sorted(
        ((difflib.SequenceMatcher(None, target,
                                  "".join(data.tokens(club))).ratio(), club)
         for club in known), reverse=True)
    if not scored:
        return None, "no teams in data"
    best = scored[0]
    runner = scored[1] if len(scored) > 1 else (0.0, None)
    if best[0] >= 0.88 and best[0] - runner[0] >= 0.08:
        return best[1], f"fuzzy {best[0]:.2f}"
    return None, (f"unmatched (closest {best[1]!r} at {best[0]:.2f})"
                  if best[0] > 0.5 else "unmatched")


def markets(code: str) -> tuple[str, ...]:
    return tuple(competition(code)["markets"])
