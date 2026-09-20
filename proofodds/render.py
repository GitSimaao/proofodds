"""
Static site build.

Jinja2 templates in, plain HTML out. No JavaScript framework, no build step
beyond this file, nothing to break at 03:00 when the cron runs. The only
client-side script on the site converts kickoff times to the reader's timezone.
"""

from __future__ import annotations

import datetime as dt
import fnmatch
import hashlib
import json
import logging
import math
import re
import shutil
import subprocess
import unicodedata

import numpy as np
from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import anchor, charts, config, crests, dixon_coles, grade, guest, ledger
from . import data
from .data import sealed_name

log = logging.getLogger(__name__)

ROBOTS = """User-agent: *
Allow: /

Sitemap: {site_url}/sitemap.xml
"""

# static/ is a working directory, so an editor swap file or a hand-made
# style.css.bak lands there easily — and .gitignore hides exactly those names,
# so `git status` stays clean while the build copies them out to a public URL.
# An old stylesheet is not a secret, but nothing here is meant to publish a
# file nobody is watching. The globs deliberately echo the .gitignore ones.
JUNK_GLOBS = ("*.bak*", "*.swp", "*.swo", "*.orig", "*.rej", "*.tmp",
              "*~", ".DS_Store", "Thumbs.db")


def is_junk(name: str) -> bool:
    """True for editor leftovers and hand-made backups that must not ship."""
    return any(fnmatch.fnmatch(name, pattern) for pattern in JUNK_GLOBS)


def ignore_junk(_directory, names):
    """copytree hook, so a leftover inside static/flags/ is skipped too."""
    return {name for name in names if is_junk(name)}


def copy_static(out_dir) -> None:
    """Copy static/ into the build, minus the leftovers.

    Static assets may be grouped into directories (country flags today,
    deliberately licensed club crests later). Keep their public paths intact.
    """
    for item in config.STATIC_DIR.glob("*"):
        if is_junk(item.name):
            log.info("not publishing %s — editor or backup leftover", item.name)
            continue
        target = out_dir / item.name
        if item.is_dir():
            shutil.copytree(item, target, ignore=ignore_junk)
        else:
            shutil.copy2(item, target)
def environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(config.TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return env


# --------------------------------------------------------------------------- #
def percent_split(values, total: int = 100) -> list[int]:
    """
    Whole units that always sum to `total`, by largest remainder.

    Rounding three probabilities separately does not: 0.7251, 0.1477 and
    0.1272 become 73, 15 and 13, which is 101. That was on the front page,
    two sections above a paragraph insisting our numbers sum to exactly one.

    Giving the leftover to the largest remainder is the same rule the ledger
    uses when it seals, so the card and the sealed file round the same way.
    Decimals were the other option and they do not fix it — 33.3 three times
    is 99.9 — and a tenth of a percent claims a precision a goals model with
    no lineups does not have. The fair odds beside each figure carry the
    precision for anyone who wants it.

    `total` is the number of units the values are split into, and 100 — whole
    percentages — is only the common case. The correct-score matrix passes
    1000, because integer percentages would print half of its 39 cells as 0%
    and rounding each cell on its own would stop them adding to their own
    total. Tenths of a percent, allocated by the same rule, do neither.
    """
    scaled = [float(v) * total for v in values]
    out = [int(x) for x in scaled]
    for i in sorted(range(len(scaled)), key=lambda i: scaled[i] - out[i],
                    reverse=True)[:total - sum(out)]:
        out[i] += 1
    return out


def slugify(value: str) -> str:
    """A small, dependency-free slug for durable match URLs and crest files."""
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "club"


def club_mark(name: str, league: str = "") -> dict:
    """
    Resolve a local or provider crest, with a deterministic monogram fallback.

    A deliberately licensed local file always wins.  Otherwise the ignored,
    display-only football-data.org cache supplies the URL.  Neither source is
    written to the immutable prediction ledger.
    """
    slug = slugify(name)
    source = None
    for suffix in (".svg", ".png", ".webp"):
        candidate = config.STATIC_DIR / "clubs" / f"{slug}{suffix}"
        if candidate.is_file():
            source = f"/clubs/{candidate.name}"
            break
    if source is None and league:
        source = crests.lookup(league, name)

    words = [word for word in re.findall(r"[A-Za-z0-9]+", unicodedata.normalize(
        "NFKD", name).encode("ascii", "ignore").decode())
        if word.lower() not in {"fc", "afc", "cf", "ac", "sc", "cd"}]
    if len(words) >= 2:
        initials = (words[0][0] + words[-1][0]).upper()
    elif words:
        initials = words[0][:2].upper()
    else:
        initials = "FC"
    tone = int(hashlib.sha256(name.encode("utf-8")).hexdigest()[:2], 16) % 6
    return {"src": source, "initials": initials, "tone": tone}


def _tenths_text(tenths: int) -> str:
    """A tenth-of-a-percent figure, or the honest floor for one below it.

    A cell that rounds to nothing is not a cell that cannot happen. Printing
    0.0% for 1-in-3000 says the model ruled it out, which it did not, so the
    floor is stated as a floor. It is also why the printed numbers do not add
    to 100.0 and the caption does not claim they do — the tenths underneath
    them do.
    """
    return f"{tenths / 10:.1f}%" if tenths else "<0.1%"


def score_matrix(xg_home, xg_away, rho,
                 max_goals: int = config.SCORE_GRID_MAX) -> dict | None:
    """
    The whole sealed scoreline distribution, as a printable partition.

    The card used to show three correct scores. Three scores say where the
    mass peaks and nothing about where the rest of it is, which on a 3-1
    forecast is most of it. So the grid is printed whole: every exact score
    from 0-0 to `max_goals`-`max_goals`, plus three buckets that catch each
    way the match can leave the grid. The 36 cells and 3 buckets are a
    partition of the same distribution the result, the totals, BTTS and the
    handicap on this card are all sums of — they add to one by construction,
    with nothing dropped and no "any other score" left over.

    Reconstructed from the sealed `xg_home`, `xg_away` and the division's
    `model_rho`, exactly as the entry sealed them. Nothing is fetched and
    nothing sealed moves. An entry that predates one of those three fields
    returns None: rho is the low-score correction, and pretending it was zero
    would print 36 numbers the sealed model never produced.
    """
    if xg_home is None or xg_away is None or rho is None:
        return None
    try:
        lam, mu, rho = float(xg_home), float(xg_away), float(rho)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (lam, mu, rho)) \
            or lam < 0 or mu < 0 or max_goals < 1:
        return None

    grid = dixon_coles.score_matrix_from_xg(lam, mu, rho)
    size = max_goals + 1
    exact = [[float(grid[home_goals, away_goals]) for away_goals in range(size)]
             for home_goals in range(size)]
    tails = [float(grid[size:, :size].sum()),      # home 6+, away inside
             float(grid[:size, size:].sum()),      # away 6+, home inside
             float(grid[size:, size:].sum())]      # both 6+
    parts = [p for row in exact for p in row] + tails

    # A negative cell is not a small display problem. tau() multiplies four
    # cells of the grid and a rho outside its sane range drives one of them
    # below zero, at which point the "distribution" is not one and no amount
    # of rounding makes the table honest. Refuse to render it.
    if min(parts) < 0:
        raise ValueError(
            f"score matrix has negative mass for xg={lam},{mu} rho={rho}: "
            f"min cell {min(parts)!r}")
    if abs(sum(parts) - 1.0) > 1e-9:
        raise ValueError(
            f"score matrix partition sums to {sum(parts)!r}, not 1")

    # One rounding pass over all 39 values at once, in tenths. Rounding them
    # separately would leave the printed table not adding to its own total.
    tenths = percent_split(parts, 1000)
    peak = max(parts[:size * size])
    rows = []
    for home_goals in range(size):
        cells = []
        for away_goals in range(size):
            p = exact[home_goals][away_goals]
            cells.append({
                "home_goals": home_goals,
                "away_goals": away_goals,
                "label": f"{home_goals}\u2013{away_goals}",
                "p": p,
                "tenths": tenths[home_goals * size + away_goals],
                "text": _tenths_text(tenths[home_goals * size + away_goals]),
                # Square-rooted: the raw ratio leaves everything but the
                # favourite white, and a 2% scoreline is a real result.
                "heat": round(math.sqrt(p / peak), 3) if peak > 0 else 0.0,
                "is_peak": p == peak,
            })
        rows.append({"label": str(home_goals), "cells": cells})

    bucket_labels = (("home", f"{size}+", f"0\u2013{max_goals}"),
                     ("away", f"0\u2013{max_goals}", f"{size}+"),
                     ("both", f"{size}+", f"{size}+"))
    buckets = [{"key": key, "home": home, "away": away, "p": p,
                "tenths": t, "text": _tenths_text(t)}
               for (key, home, away), p, t
               in zip(bucket_labels, tails, tenths[size * size:])]

    return {
        "max_goals": max_goals,
        "labels": [str(n) for n in range(size)],
        "rows": rows,
        "n_cells": size * size,
        "buckets": buckets,
        "tenths_total": sum(tenths),
    }


# --------------------------------------------------------------------------- #
#  Partitions
#
#  Every market on a match card is a sum over the same fitted scoreline
#  distribution, so every one of them can be printed as a partition: a set of
#  outcomes that is exhaustive and mutually exclusive, adding to one by
#  construction with nothing left in an "any other result" line. `_partition`
#  is the single place that checks that claim before the card makes it, and
#  the single place that rounds — one pass over the whole set in tenths, so
#  the printed figures add to their own total instead of to 99.9 or 100.1.
# --------------------------------------------------------------------------- #
def _partition(entries: list[dict]) -> dict:
    """
    Round a set of outcomes that should add to one, and say whether it printed.

    `entries` are dicts carrying at least `p`. The returned `exact` flag is
    what the card's footnote is driven by: when every outcome is worth at
    least a tenth of a percent the printed figures really do add to 100.0 and
    the card says so, and when one of them is below that it is shown as the
    honest floor `<0.1%`, the printed figures no longer add to 100.0, and the
    card says that instead. Claiming the first while printing the second is
    the kind of small lie this site cannot afford.
    """
    parts = [float(e["p"]) for e in entries]
    if min(parts) < 0:
        raise ValueError(f"partition has negative mass: min {min(parts)!r}")
    if abs(sum(parts) - 1.0) > 1e-9:
        raise ValueError(f"partition sums to {sum(parts)!r}, not 1")
    tenths = percent_split(parts, 1000)
    # "outcomes", never "items": a Jinja template that says `part.items` gets
    # the dict method, not the list, and renders nothing while raising nothing
    # that looks like a template bug.
    outcomes = [{**entry, "p": part, "tenths": tenth, "text": _tenths_text(tenth)}
                for entry, part, tenth in zip(entries, parts, tenths)]
    return {"outcomes": outcomes, "exact": all(t > 0 for t in tenths),
            "tenths_total": sum(tenths), "n": len(outcomes)}


def _yes_no(label: str, p_yes: float) -> dict:
    return _partition([{"label": label, "p": p_yes},
                       {"label": "No", "p": 1.0 - p_yes}])


def goal_ladder(row: dict, league: str) -> list[dict] | None:
    """
    The whole sealed 0.5-5.5 ladder, read from the entry and tagged line by line.

    All six lines have been sealed in every prediction since 2 SEPTEMBER 2026
    and only the 2.5 line was ever shown. That date is worth getting right:
    `goal_totals` first appears in the entry of 2 September, not 26 August, and
    the 630 predictions sealed between 28 August and 1 September carry
    `p_over25` alone. Dropping the standalone over/under block in favour of
    this ladder would have taken the 2.5 line off all 630 of those cards, so an
    entry with no ladder falls back to the one line it did seal and the card's
    sparse-markets note says the other five were never sealed.

    Nothing here is recomputed either way: every probability comes straight out
    of the sealed row. An entry that sealed neither returns None rather than a
    ladder reconstructed after the fact.

    The tags are per line and they are not decoration. football-data.co.uk
    publishes a closing price for the 2.5 line and for no other, so 2.5 is
    scored against the market where the division's source carries it and every
    other line is sealed and scored by nothing. One block, two tags — because
    letting the block inherit the 2.5 line's tag would be the site claiming a
    benchmark for five lines it cannot check, in the place a reader skims.
    """
    lines = row.get("goal_totals") or []
    if not lines and row.get("p_over25") is not None \
            and row.get("p_under25") is not None:
        lines = [{"line": config.TOTALS_LINE, "p_over": row["p_over25"],
                  "p_under": row["p_under25"]}]
    if not lines:
        return None
    main_is_scored = config.is_scored(league, "OU2.5")
    ladder = []
    for item in lines:
        try:
            line = float(item["line"])
            p_over, p_under = float(item["p_over"]), float(item["p_under"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (p_over > 0 and p_under > 0):
            continue
        is_main = abs(line - config.TOTALS_LINE) < 1e-9
        scored = is_main and main_is_scored
        pct = percent_split([p_over, p_under])
        ladder.append({
            "line": line,
            "label": f"{line:.1f}",
            "p_over": p_over, "p_under": p_under,
            "pct_over": pct[0], "pct_under": pct[1],
            "fair_over": 1.0 / p_over, "fair_under": 1.0 / p_under,
            "is_main": is_main,
            "scored": scored,
            "tag": "scored vs close" if scored else "sealed, not scored",
            "tag_class": "is-scored" if scored else "is-forecast",
        })
    return ladder or None


def derived_markets(xg_home, xg_away, rho, home: str, away: str) -> dict | None:
    """
    Every market that is a different sum of the SAME fitted scoreline grid.

    Clean sheets, win to nil, odd/even, exact total goals, goals by team and
    the winning margin are not a second model and not a second fit. They are
    the distribution `score_matrix_from_xg` builds from the sealed `xg_home`,
    `xg_away` and the division's sealed `model_rho`, summed a different way
    each time — which is also true of the result, the ladder, BTTS and the
    handicap, the difference being only that those four were sealed as numbers
    and these are recomputed here.

    Two things this must not do, and both have a way of going wrong quietly:

    * It must use the FULL grid at `MAX_GOALS`, never `render.score_matrix`.
      That one is a display object truncated at SCORE_GRID_MAX with three tail
      buckets; deriving a total or a margin from it would put the tail in the
      wrong place and nothing on the page would look wrong.
    * Every market must be its own exhaustive partition. `_partition` refuses
      one that is not, rather than printing a set of numbers that quietly adds
      to 0.98.

    Returns None for an entry that predates any of the three sealed inputs,
    for the same reason `score_matrix` does: rho is the low-score correction
    and pretending it was zero would print a distribution the sealed model
    never produced.
    """
    if xg_home is None or xg_away is None or rho is None:
        return None
    try:
        lam, mu, rho = float(xg_home), float(xg_away), float(rho)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (lam, mu, rho)) or lam < 0 or mu < 0:
        return None

    grid = dixon_coles.score_matrix_from_xg(lam, mu, rho)
    if float(grid.min()) < 0:
        raise ValueError(
            f"derived markets have negative mass for xg={lam},{mu} rho={rho}: "
            f"min cell {float(grid.min())!r}")
    size = grid.shape[0]
    goals = np.arange(size)
    home_goals = grid.sum(axis=1)
    away_goals = grid.sum(axis=0)
    totals = np.zeros(2 * size - 1)
    np.add.at(totals, np.add.outer(goals, goals).ravel(), grid.ravel())
    margin = np.zeros(2 * size - 1)          # index 0 == away by `size-1`
    np.add.at(margin, (np.subtract.outer(goals, goals) + size - 1).ravel(),
              grid.ravel())
    centre = size - 1

    total_cut = config.TOTAL_GOALS_MAX
    team_cut = config.TEAM_GOALS_MAX

    def _exact_total() -> dict:
        entries = [{"label": str(t), "goals": t, "p": float(totals[t])}
                   for t in range(total_cut + 1)]
        entries.append({"label": f"{total_cut + 1} or more", "goals": None,
                        "p": float(totals[total_cut + 1:].sum())})
        return _partition(entries)

    def _team(side: np.ndarray) -> dict:
        entries = [{"label": str(g), "goals": g, "p": float(side[g])}
                   for g in range(team_cut + 1)]
        entries.append({"label": f"{team_cut + 1} or more", "goals": None,
                        "p": float(side[team_cut + 1:].sum())})
        return _partition(entries)

    return {
        "max_goals": size - 1,
        "total_cut": total_cut,
        "team_cut": team_cut,
        # A clean sheet is the OPPONENT failing to score, which is the column
        # (or row) of the grid at nil — not the side's own goals.
        "clean_sheet": {
            "home": _yes_no("Yes", float(grid[:, 0].sum())),
            "away": _yes_no("Yes", float(grid[0, :].sum())),
        },
        "win_to_nil": {
            "home": _yes_no("Yes", float(grid[1:, 0].sum())),
            "away": _yes_no("Yes", float(grid[0, 1:].sum())),
        },
        # 0-0 is an even total, and the partition says so rather than leaving a
        # reader to guess which way nil-nil falls.
        "odd_even": _partition([
            {"label": "Even", "p": float(totals[0::2].sum())},
            {"label": "Odd", "p": float(totals[1::2].sum())},
        ]),
        "exact_total": _exact_total(),
        "team_goals": {"home": _team(home_goals), "away": _team(away_goals)},
        "margin": _partition([
            {"label": f"{home} by 3+", "side": "home",
             "p": float(margin[centre + 3:].sum())},
            {"label": f"{home} by 2", "side": "home",
             "p": float(margin[centre + 2])},
            {"label": f"{home} by 1", "side": "home",
             "p": float(margin[centre + 1])},
            {"label": "Draw", "side": "draw", "p": float(margin[centre])},
            {"label": f"{away} by 1", "side": "away",
             "p": float(margin[centre - 1])},
            {"label": f"{away} by 2", "side": "away",
             "p": float(margin[centre - 2])},
            {"label": f"{away} by 3+", "side": "away",
             "p": float(margin[:centre - 2].sum())},
        ]),
    }


def corner_view(row: dict) -> dict | None:
    """
    The sealed corner distribution, shown only where it is worth showing.

    Two gates, and they are different gates. The DIVISION gate lives in
    `ledger._corner_gate` and decides whether a corner model is fitted and
    sealed at all. This one is about the individual entry: a corner block
    sealed before 18 September 2026 came from a fit with no time weighting,
    where a corner count from 2015/16 voted as loudly as one from last week.
    Those blocks are in the ledger for good and are never rewritten, so the
    card reads them and declines to print them. `xi` is the marker only the
    weighted fit writes, which is why its absence is the test rather than a
    date comparison.
    """
    block = row.get("corners")
    if not isinstance(block, dict) or not block.get("totals"):
        return None
    if block.get("xi") is None:
        return None
    lines = []
    for item in block["totals"]:
        try:
            line = float(item["line"])
            p_over, p_under = float(item["p_over"]), float(item["p_under"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (p_over > 0 and p_under > 0):
            continue
        pct = percent_split([p_over, p_under])
        lines.append({"line": line, "label": f"{line:.1f}",
                      "p_over": p_over, "p_under": p_under,
                      "pct_over": pct[0], "pct_under": pct[1],
                      "fair_over": 1.0 / p_over, "fair_under": 1.0 / p_under})
    if not lines:
        return None
    return {"x_home": block.get("x_home"), "x_away": block.get("x_away"),
            "x_total": block.get("x_total"),
            "dispersion": block.get("dispersion"),
            "xi": block.get("xi"), "lines": lines}


def prediction_view(row: dict, now: dt.datetime | None = None) -> dict:
    """Turn one immutable ledger row into the site's richer display model."""
    now = now or dt.datetime.now(dt.timezone.utc)
    kickoff = dt.datetime.strptime(row["kickoff"], "%Y-%m-%dT%H:%M:%SZ") \
                         .replace(tzinfo=dt.timezone.utc)
    league = row.get("league", "E0")
    meta = config.LEAGUES.get(league, {})
    home = sealed_name(row["home"], league, row.get("home_raw", ""))
    away = sealed_name(row["away"], league, row.get("away_raw", ""))
    pct = percent_split([row["p_H"], row["p_D"], row["p_A"]])
    pct_ou = (percent_split([row["p_over25"], row["p_under25"]])
              if row.get("p_over25") is not None else None)
    pct_btts = (percent_split([row["p_btts_yes"], row["p_btts_no"]])
                if row.get("p_btts_yes") is not None else None)
    handicaps = row.get("asian_handicap") or []
    main_ah = min(handicaps, key=lambda x: abs(float(x["p_home"]) - .5)) if handicaps else None
    # Read, sealed, and shown as SEALED, NOT SCORED — there is no free closing
    # price for corners anywhere, so this is never an edge claim. `corner_view`
    # also refuses a block sealed by the old unweighted fit; see its docstring.
    corner_data = row.get("corners")
    corner_totals = corner_data.get("totals", []) if isinstance(corner_data, dict) else []
    corner_main = (min(corner_totals, key=lambda x: abs(float(x["p_over"]) - .5))
                   if corner_totals else None)
    corners_shown = corner_view(row)
    favourite = ("H", "D", "A")[max(
        range(3), key=lambda i: (row["p_H"], row["p_D"], row["p_A"])[i])]
    match_slug = (f"{league.lower()}-{slugify(row['home'])}-v-"
                  f"{slugify(row['away'])}")
    match_url = f"/matches/{row['kickoff'][:10]}/{match_slug}/"
    tbc = bool(row.get("kickoff_tbc"))
    entry_file = row.get("entry_file", f"{row['published_at'][:10]}.json")

    ladder = goal_ladder(row, league)
    derived = derived_markets(row.get("xg_home"), row.get("xg_away"),
                              row.get("model_rho"), home, away)
    matrix = score_matrix(row.get("xg_home"), row.get("xg_away"),
                          row.get("model_rho"))

    # One registry, read by BOTH the market nav and the sections themselves.
    # The nav exists so a reader can reach any market on this card in one tap;
    # a nav item that scrolls to nothing, or a section with no way to reach it,
    # is worse than no nav at all, and two lists of conditions in two files is
    # exactly how that happens. The template asks this list what to render.
    sections = [("result", "Result")]
    if ladder or row.get("xg_home") is not None or row.get("p_over25") is not None:
        sections.append(("goals", "Goals"))
    if row.get("p_btts_yes") is not None or derived:
        sections.append(("btts", "Both teams"))
    if handicaps:
        sections.append(("handicap", "Handicap"))
    if derived:
        sections.append(("margin", "Margin"))
        sections.append(("teams", "By team"))
    if matrix:
        sections.append(("score", "Correct score"))
    if corners_shown:
        sections.append(("corners", "Corners"))

    return {
        **row,
        "league": league,
        "league_name": meta.get("name", league),
        "league_short": meta.get("short", league),
        "league_country": meta.get("country", ""),
        "league_flag": meta.get("flag"),
        "home": home,
        "away": away,
        "xg_home": row.get("xg_home"),
        "xg_away": row.get("xg_away"),
        "p_over25": row.get("p_over25"),
        "p_under25": row.get("p_under25"),
        "goal_totals": row.get("goal_totals", []),
        # Whether THIS division has a closing benchmark for each market. The
        # Brasileirao source publishes a closing 1X2 and nothing else, so its
        # over/under and handicap fall to the THIRD tier, not the second: the
        # card used to tag them "forecast only", which says they are measured
        # against guessing, and nothing measures them at all. `grade.py` needs
        # a closing price to set `ou_graded` / `ah_graded`, so a Brazilian
        # over/under reaches no scorecard on this site and the tag now says so.
        # A tag is only worth keeping while it is the truth.
        "result_scored": config.is_scored(league, "1X2"),
        "ou_scored": config.is_scored(league, "OU2.5"),
        "ah_scored": config.is_scored(league, "AH"),
        # A fixture first published before a market existed carries only what
        # was sealed that day. First publication wins, so the missing markets
        # cannot be added later — and the card says why, rather than leaving a
        # reader to wonder where the markets the method page promises are.
        "sparse_markets": [
            # Bare noun phrases: the sentence on the card is "this card
            # carries no ...", and "no the Asian handicap" was live prose.
            label for key, label in (("p_over25", "over/under 2.5"),
                                     ("p_btts_yes", "both teams to score"),
                                     ("goal_totals",
                                      "goal-total lines other than 2.5"),
                                     ("asian_handicap", "Asian handicap"))
            if not row.get(key)]
        # The corner section is missing for two different reasons and they are
        # not the same sentence. A division that never sealed one has no corner
        # history worth modelling; an entry sealed before 18 September 2026 has
        # a block that was fitted with no time decay and is deliberately not
        # printed. Both are "sealed before this existed" from the reader's
        # side, which is what this list says.
        + (["corner model"]
           if corners_shown is None and not row.get("corners") else [])
        + (["time-weighted corner model"]
           if corners_shown is None and row.get("corners") else []),
        "asian_handicap": handicaps,
        "main_ah": main_ah,
        "p_btts_yes": row.get("p_btts_yes"),
        "p_btts_no": row.get("p_btts_no"),
        "pct_btts_yes": pct_btts[0] if pct_btts else None,
        "pct_btts_no": pct_btts[1] if pct_btts else None,
        "corners": corner_data,
        "corner_main": corner_main,
        "corner_view": corners_shown,
        "goal_ladder": ladder,
        "score_matrix": matrix,
        "sections": [{"id": i, "label": l} for i, l in sections],
        "section_ids": {i for i, _ in sections},
        # Recomputed from the sealed xg and rho, exactly as the correct-score
        # view is. Nothing here is read out of the entry and nothing here is
        # added to it: the entry is already 625 KB for 88 predictions, and a
        # deterministic function of three sealed numbers does not need sealing
        # twice. The card says which is which.
        "derived": derived,
        "home_mark": club_mark(home, league),
        "away_mark": club_mark(away, league),
        "cold_start": [sealed_name(n, league) for n in row.get("cold_start", [])],
        "pct_H": pct[0], "pct_D": pct[1], "pct_A": pct[2],
        "pct_over": pct_ou[0] if pct_ou else None,
        "pct_under": pct_ou[1] if pct_ou else None,
        "favourite": favourite,
        "kickoff_dt": kickoff,
        "kickoff_tbc": tbc,
        # Present only when the feed moved an unfixed kickoff after we sealed
        # it. The card shows both, so the correction is part of the record
        # rather than something a reader has to diff two JSON files to find.
        "kickoff_sealed": row.get("kickoff_sealed"),
        "kickoff_label": (kickoff.strftime("%a %d %b") + ", time TBC"
                          if tbc else kickoff.strftime("%a %d %b, %H:%M UTC")),
        "kickoff_date_label": kickoff.strftime("%A, %d %B %Y"),
        "bar": charts.outcome_bar(row["p_H"], row["p_D"], row["p_A"]),
        "match_url": match_url,
        "entry_file": entry_file,
        "entry_url": f"/predictions/{entry_file}",
        "is_past": kickoff <= now,
    }


def match_views(now: dt.datetime | None = None) -> list[dict]:
    """Every unique sealed fixture, including past ones, for durable pages."""
    now = now or dt.datetime.now(dt.timezone.utc)
    rows = [prediction_view(row, now=now) for row in ledger.all_predictions()]
    order = {code: i for i, code in enumerate(config.LEAGUE_ORDER)}
    rows.sort(key=lambda r: (r["kickoff_dt"], order.get(r["league"], 99), r["home"]))
    return rows


def upcoming_view(rows: list[dict] | None = None, *,
                  today: dt.date | None = None,
                  days_ahead: int | None = None) -> list[dict]:
    """
    Build the selectable calendar from today through today + days_ahead.

    Only the ledger is read — the site can never show a probability that was
    not sealed first. That is the whole point, so it is worth the constraint.
    Empty dates are kept: a date picker with holes is not a calendar, and an
    honest zero is more useful than making the reader wonder whether a day is
    missing because the feed failed.
    """
    rows = rows if rows is not None else match_views()
    today = today or dt.datetime.now(dt.timezone.utc).date()
    days_ahead = config.LOOKAHEAD_DAYS if days_ahead is None else days_ahead
    if days_ahead < 0:
        raise ValueError("days_ahead cannot be negative")
    end = today + dt.timedelta(days=days_ahead)
    rows = [row for row in rows
            if not row["is_past"] and today <= row["kickoff_dt"].date() <= end]

    order = {code: i for i, code in enumerate(config.LEAGUE_ORDER)}
    rows.sort(key=lambda r: (r["kickoff_dt"], order.get(r["league"], 99), r["home"]))

    days = []
    by_date = {}
    for offset in range(days_ahead + 1):
        date = today + dt.timedelta(days=offset)
        day = {
            "date": date.isoformat(),
            "label": f"{date.strftime('%A')} {date.day} {date.strftime('%B')}",
            "picker_label": date.strftime("%a"),
            "number": str(date.day),
            "month": date.strftime("%b"),
            "is_today": offset == 0,
            "matches": [],
            "leagues": [],
        }
        days.append(day)
        by_date[date] = day

    # Group by division inside each selected day. Many leagues shown at once
    # are a wall; the reader chooses the date first, then optionally narrows it.
    for row in rows:
        current = by_date[row["kickoff_dt"].date()]
        current["matches"].append(row)
        league = next((item for item in current["leagues"]
                       if item["code"] == row["league"]), None)
        if league is None:
            league = {"code": row["league"],
                      "name": row["league_name"],
                      "short": row["league_short"],
                      "country": row["league_country"],
                      "flag": row["league_flag"],
                      "matches": []}
            current["leagues"].append(league)
        league["matches"].append(row)
    return days


def ledger_view(anchor_report=None) -> list[dict]:
    anchor_report = anchor_report or anchor.report()
    by_entry = {row["entry"]: row for row in anchor_report["entries"]}
    out = []
    for path in ledger.ledger_files():
        entry = ledger.read(path)
        generator = entry.get("generator", {})
        out.append({
            "file": path.name,
            "published_at": entry["published_at"],
            "n": len(entry["predictions"]),
            "hash": entry["hash"],
            "prev_hash": entry["prev_hash"],
            "generator_commit": generator.get("commit"),
            "generator_dirty": generator.get("dirty"),
            "generator_source": generator.get("source_sha256"),
            # Schema 5 and later. Absent on older entries, which is itself the
            # honest answer: they cannot say what they did not cover.
            "coverage": entry.get("coverage"),
            **_coverage_ratio(entry),
            "anchor": by_entry.get(path.name, {
                "status": "none", "blocks": [], "proof": None}),
        })
    return list(reversed(out))


def _coverage_ratio(entry: dict) -> dict:
    """
    What fraction of the divisions asked for actually got sealed, and why not.

    The numerator is `entry["leagues"]` — the divisions that have a prediction
    IN THIS FILE — and never a count derived from the fixture feed. The
    template used to compute `returned|length - missing|length`, which counts a
    division as covered whenever the feed answered for it. A division held by
    the unresolved-name guard DID return fixtures and sealed none of them, so
    it landed in the numerator while contributing nothing to the entry: the
    page would have claimed coverage it does not have, in precisely the
    situation the guard exists to handle. Counting what was sealed cannot drift
    from the file, because it is read out of the file.

    `not_covered` groups the divisions by identical reason. Fourteen divisions
    failing for one stale file is one fact, and printing it fourteen times
    reads as fourteen.
    """
    coverage = entry.get("coverage") or {}
    requested = list(coverage.get("requested") or [])
    if not requested:
        return {"covered": None, "requested_n": None, "not_covered": []}

    sealed = set(entry.get("leagues") or [])
    reasons = {m["league"]: m["reason"]
               for m in coverage.get("missing") or [] if m.get("league")}
    for s_ in entry.get("skipped") or []:
        # A skipped division returned fixtures, so it is absent from
        # `coverage.missing` and its only account of itself is here.
        n = s_.get("n")
        reasons[s_["league"]] = (
            f"returned {n} fixture(s), sealed none: {s_.get('reason', '')}"
            if n else s_.get("reason", ""))

    grouped: list[dict] = []
    for code in requested:
        if code in sealed:
            continue
        reason = reasons.get(code) or "no reason recorded"
        for g in grouped:
            if g["reason"] == reason:
                g["leagues"].append(code)
                break
        else:
            grouped.append({"leagues": [code], "reason": reason})

    return {"covered": sum(1 for c in requested if c in sealed),
            "requested_n": len(requested),
            "not_covered": grouped}


def corner_eligibility() -> list[dict]:
    """
    The corner rule, measured rather than asserted, for the method page.

    The page that explains the rule prints the table the rule produces, from
    the same data the ledger gates on — so a division that falls out of the
    corner model falls out of the method page's list in the same build, and
    nobody has to remember to retype a list of leagues. A hand-typed list is
    exactly the thing that rots silently here.

    Never fatal. A division whose CSVs are missing is reported as unmeasurable
    rather than taking the whole site down with it.
    """
    today = np.datetime64(dt.date.today())
    table = []
    for code in config.ENABLED_LEAGUES:
        meta = config.LEAGUES.get(code, {})
        entry = {"code": code, "name": meta.get("name", code),
                 "raw": None, "effective": None, "median": None, "bar": None,
                 "clubs": None, "newest": None, "passes": False, "error": None}
        try:
            matches = data.load_matches(code)
            past = matches[matches["Date"].to_numpy(dtype="datetime64[D]") < today]
            gate = ledger._corner_gate(past, today)
            if {"HC", "AC"}.issubset(past.columns):
                rows = past.dropna(subset=["HC", "AC"])
            else:
                rows = past.iloc[0:0]
            entry.update(
                raw=int(len(rows)),
                effective=round(gate["n_effective"], 1),
                median=round(gate["median"], 1),
                bar=gate["bar"], clubs=gate["n_clubs"],
                newest=(str(rows["Season"].iloc[-1]) if len(rows) else None),
                passes=bool(gate["passes"]))
        except Exception as exc:                      # noqa: BLE001
            log.warning("corner eligibility: %s could not be measured (%s)",
                        code, exc)
            entry["error"] = str(exc)[:120]
        table.append(entry)
    return table


def sitemap(pages: list[str]) -> str:
    today = dt.date.today().isoformat()
    urls = "".join(
        f"<url><loc>{config.SITE_URL}{p}</loc><lastmod>{today}</lastmod></url>"
        for p in pages)
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"{urls}</urlset>")


# --------------------------------------------------------------------------- #
def build_provenance() -> dict:
    """
    Which commit built this page, and whether a reader can obtain that commit.

    /ledger/ tells a reader to clone the repository and recompute the chain,
    and /method/ makes the same offer. Those instructions are only true while
    the code serving the page is code the reader can actually get. Twice in two
    days it was not: the site was deployed from commits that had not been
    pushed, so somebody cloning got a repository that could not build the page
    they were reading.

    Nothing caught it either time, because the divergence is invisible from
    both ends — the server looks fine, the repository looks fine, and only
    comparing the two shows it. Nobody compares the two. So the build records
    which commit it came from and whether that commit is on the remote, and the
    page prints it. Deploying ahead of the repository is now wrong in public
    instead of undetectable, which is the same move 081ba02 made for a stale
    feed and the stale-build note already makes for an old build: the fix for
    an invisible state is to make it a recorded one.

    `published` is judged against the local `origin/main` ref, because a build
    must not make a network call to decide what to render. It therefore reports
    what this machine last saw, and is None — unknown, and the page says
    nothing — when git cannot answer at all.

    This is deliberately NOT sealed into the ledger entry. It describes the
    machine that rendered a page, which is not a fact about the predictions,
    and entries are append-only: `ledger.generator_identity` stays as it is.
    """
    def _git(*args: str) -> subprocess.CompletedProcess | None:
        try:
            return subprocess.run(["git", *args], cwd=str(config.ROOT),
                                  capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            return None

    unknown = {"commit": None, "dirty": None, "published": None}
    if not (config.ROOT / ".git").exists():
        return unknown

    head = _git("rev-parse", "HEAD")
    if head is None or head.returncode != 0 or not head.stdout.strip():
        return unknown
    commit = head.stdout.strip()

    # Tracked files only. An untracked file cannot change what the templates
    # render, and `outputs/` would otherwise report every build as modified.
    status = _git("status", "--porcelain", "--untracked-files=no")
    dirty = (bool(status.stdout.strip())
             if status is not None and status.returncode == 0 else None)

    # Exit 0 = HEAD is an ancestor of origin/main, so a clone gets it. Exit 1 =
    # it is not. Anything else (no remote ref at all, for instance) is unknown
    # rather than a claim in either direction.
    ancestor = _git("merge-base", "--is-ancestor", commit, "origin/main")
    published = None
    if ancestor is not None and ancestor.returncode in (0, 1):
        published = ancestor.returncode == 0

    return {"commit": commit, "dirty": dirty, "published": published}


def build(out_dir=None) -> None:
    out_dir = out_dir or config.SITE_DIR
    env = environment()

    graded = grade.graded_frame()
    score = grade.scorecard(graded)
    totals = grade.totals_scorecard(graded)
    btts = grade.btts_scorecard(graded)
    asian = grade.ah_scorecard(graded)
    # How far the three scored markets are three separate measurements.
    # Only /method/ uses it, beside the boxes that name them.
    overlap = grade.market_overlap(graded)
    weeks = grade.by_week(graded)
    leagues = grade.by_league(graded)
    cohorts = grade.by_cohort(graded)
    calib = grade.calibration(graded)
    # What fraction of the matches that were actually PLAYED we sealed a
    # prediction for. Every other table on the site starts from the ledger and
    # asks what happened; this one starts from what happened and asks whether
    # the ledger has it, which is the only direction that can see a match we
    # never sealed at all.
    coverage_rows = grade.coverage_by_league()
    coverage_stats = grade.coverage_summary(coverage_rows)
    chain = ledger.verify_chain()
    anchors = anchor.report()
    entries = ledger_view(anchors)
    build_now = dt.datetime.now(dt.timezone.utc)
    matches = match_views(now=build_now)
    days = upcoming_view(matches, today=build_now.date(),
                         days_ahead=config.LOOKAHEAD_DAYS)

    # A version stamp taken from the stylesheet's own contents.
    #
    # The CSS is cached for an hour and the HTML for five minutes, so after a
    # deploy a returning visitor gets the new markup against the old
    # stylesheet — which is worse than either alone. The theme toggle showed
    # both its icons at once and the mobile header collapsed into three
    # overlapping rows, on a phone whose only crime was having visited before.
    #
    # Versioning the URL makes the stale pair impossible: change the file and
    # the address changes with it, so a browser either has both halves old or
    # both new.
    css = (config.STATIC_DIR / "style.css").read_bytes()
    logo = (config.STATIC_DIR / "logo.svg").read_bytes()
    brand_files = (logo
                   + (config.STATIC_DIR / "apple-touch-icon.png").read_bytes()
                   + (config.STATIC_DIR / "site.webmanifest").read_bytes()
                   + (config.STATIC_DIR / "og.png").read_bytes())
    asset_v = hashlib.sha256(css).hexdigest()[:10]
    brand_v = hashlib.sha256(brand_files).hexdigest()[:10]

    # Which commit this build came from, and whether a reader can clone it.
    # Printed in the footer of every page, so a site running ahead of its own
    # repository says so instead of quietly breaking the clone instruction on
    # /ledger/.
    provenance = build_provenance()

    common = {
        "asset_v": asset_v,
        "brand_v": brand_v,
        "site_name": config.SITE_NAME,
        "site_url": config.SITE_URL,
        "tagline": config.SITE_TAGLINE,
        "repo_url": config.REPO_URL,
        "built_at": build_now.strftime("%d %b %Y"),
        # Machine-readable, so the page can compare its own age with the
        # reader's clock and say when it has gone stale.
        "built_iso": build_now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "build_commit": provenance["commit"],
        "build_commit_short": (provenance["commit"] or "")[:10] or None,
        "build_dirty": provenance["dirty"],
        # True / False / None, and None means "git could not say", in which
        # case the page claims nothing either way — hence the explicit
        # `is False` rather than a falsy test, which would read "unknown" as
        # "unpublished" and print an accusation the build cannot support.
        "build_published": provenance["published"],
        "build_unpublished": provenance["published"] is False,
        "uniform_log_loss": config.UNIFORM_LOG_LOSS,
        "min_league_rows": config.MIN_LEAGUE_ROWS,
        "backtest": config.BACKTEST,
        "score": score,
        "totals": totals,
        "btts": btts,
        "asian": asian,
        "totals_line": config.TOTALS_LINE,
        "genesis": ledger.GENESIS,
        "leagues": leagues,
        "cohorts": cohorts,
        "coverage_rows": coverage_rows,
        "coverage_played": [r for r in coverage_rows if r["played"]],
        "coverage_waiting": [r for r in coverage_rows if not r["played"]],
        "coverage_stats": coverage_stats,
        # Three different counts, because the copy needs three different
        # facts and used to have only one. Anything describing what the site
        # HAS PUBLISHED reads n_published, which comes out of the sealed
        # ledger; anything describing the SCORECARD reads n_scored, which
        # comes out of the table actually on that page; and n_configured is
        # only for sentences that genuinely mean "set up to run".
        #
        # `n_leagues` is deliberately gone rather than redefined. Every use of
        # it in a template was a claim about what is published, and leaving the
        # name in place would let the next one be written without a decision
        # being made. A template that asks for it now fails the build.
        "n_published": len(ledger.published_leagues()),
        "n_scored": len([row for row in leagues if row["n"]]),
        "n_configured": len(config.ENABLED_LEAGUES),
        # Names for every configured division, so a page can spell out a
        # cohort's membership without knowing which of them are live.
        "league_names": {code: config.league_name(code) for code in config.LEAGUES},
        "signup_action": config.SIGNUP_ACTION,
        "contact_email": config.CONTACT_EMAIL,
        "data_controller": config.DATA_CONTROLLER,
    }

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    # Counted at the point of writing, not reconstructed at the end. A
    # hardcoded tally drifted once already — two log pages arrived and the
    # message kept saying 194 — and on a site whose whole argument is "the
    # numbers reconcile", even a log line is not allowed to lie.
    pages_written = 0

    def write(rel: str, html: str):
        nonlocal pages_written
        path = out_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
        pages_written += 1

    # Filter only on divisions actually present in the sealed upcoming view.
    # An international break or different season end can empty one without
    # changing the configured league list.
    shown = [c for c in config.LEAGUE_ORDER
             if any(lg["code"] == c for d in days for lg in d["leagues"])]

    write("index.html", env.get_template("index.html").render(
        page="index", canonical="/",
        fixture_days=days,
        shown_codes=shown,
        n_shown=len(shown) or len(ledger.published_leagues()),
        league_meta={c: config.LEAGUES[c] for c in config.LEAGUES},
        n_upcoming=sum(len(d["matches"]) for d in days),
        lookahead_days=config.LOOKAHEAD_DAYS,
        picker_end=days[-1]["label"] if days else "",
        **common))

    for match in matches:
        write(match["match_url"].lstrip("/") + "index.html",
              env.get_template("match.html").render(
                  page="match", canonical=match["match_url"], match=match,
                  **common))

    write("scorecard/index.html", env.get_template("scorecard.html").render(
        page="scorecard", canonical="/scorecard/",
        weeks=weeks,
        weeks_shown=list(reversed(weeks[-20:])),
        curve_chart=charts.cumulative_gap(score.get("curve", [])),
        calibration_chart=charts.calibration(calib),
        **common))

    # The date the coverage block actually starts, read off the entries rather
    # than typed into the page. Entries sealed before it exists cannot say what
    # they did not cover, and the page has to be able to say that too.
    with_coverage = [e for e in reversed(entries) if e.get("coverage")]
    write("ledger/index.html", env.get_template("ledger.html").render(
        page="ledger", canonical="/ledger/",
        coverage_from=(with_coverage[0]["published_at"][:10]
                       if with_coverage else None),
        entries=entries, chain=chain, anchors=anchors, **common))

    write("method/index.html", env.get_template("method.html").render(
        page="method", canonical="/method/",
        xi=config.XI,
        half_life=int(round(math.log(2) / config.XI)),
        corner_table=corner_eligibility(),
        overlap=overlap,
        total_goals_max=config.TOTAL_GOALS_MAX,
        team_goals_max=config.TEAM_GOALS_MAX,
        score_grid_max=config.SCORE_GRID_MAX,
        max_goals=config.MAX_GOALS,
        goal_total_lines=config.GOAL_TOTAL_LINES,
        **common))

    # The log: dated notes with a stable home on our own domain, so links from
    # elsewhere (HN, Reddit) point at a page we control rather than a post a
    # moderator can delete. The launch note fills its numbers from the live
    # scorecard at build time — a page whose argument is verifiability must
    # never show a stale figure.
    log_posts = [{
        "url": "/log/first-post/",
        "title": "Nobody publishes the score",
        # The date a page went live, not the date of a plan. This built and
        # published on 1 September; announcing it elsewhere a week later does
        # not move when it was published, and a launch note dated in the
        # future would be a strange first exhibit for a site about honest
        # timestamps.
        "date": "1 September 2026",
        "summary": ("Why this site exists: probabilities sealed before kickoff, "
                    "scored against the market-average close, record public "
                    "either way."),
    }]
    write("log/index.html", env.get_template("log.html").render(
        page="log", canonical="/log/", posts=log_posts, **common))
    write("log/first-post/index.html",
          env.get_template("log_first_post.html").render(
              page="log", canonical="/log/first-post/",
              post_date=log_posts[0]["date"], **common))

    # Guest records — other people's predictions sealed under our rules,
    # measured by closing line value. Pages exist only once a guest does; the
    # raw chain files are served beside each page so the record is auditable
    # without trusting the table that summarises it.
    guest_records = guest.all_guests()
    for record in guest_records:
        write(f"guests/{record['slug']}/index.html",
              env.get_template("guest.html").render(
                  page="guest", canonical=f"/guests/{record['slug']}/",
                  guest=record, **common))
        raw_dir = out_dir / "guests" / record["slug"] / "entries"
        raw_dir.mkdir(parents=True, exist_ok=True)
        for path in guest.entry_files(record["slug"]):
            shutil.copy2(path, raw_dir / path.name)
        # Directory listings are off on the server; a JSON index makes the raw
        # chain reachable by URL alone. Named with a leading underscore for the
        # same reason as /predictions/_chain.json: a guest chain is verified by
        # pointing the standalone verifier at the directory, and a listing file
        # sitting among the entries used to read as a broken chain.
        (raw_dir / "_files.json").write_text(json.dumps(
            {"guest": record["slug"],
             "files": [p.name for p in guest.entry_files(record["slug"])]},
            indent=2), encoding="utf-8")

    # The offer, not just the demonstration.
    #
    # Every piece of machinery for measuring somebody else's record was built
    # and tested, and the site said nothing about it anywhere — no nav item, no
    # footer link, /guests/ a 404. A visitor who would have wanted their record
    # sealed had no way to find out it was possible. The page exists before the
    # first guest does, on purpose: the offer is what produces the guest.
    write("referee/index.html", env.get_template("referee.html").render(
        page="referee", canonical="/referee/", guests=guest_records, **common))

    # The third thing a visitor might want, after the weekly report and the
    # offer to be measured: the numbers themselves. They were already public —
    # /predictions/ is served raw and cross-origin — but nothing on the site
    # said so, or said what they are worth, so the only people who found them
    # were the ones already reading the ledger.
    write("data/index.html", env.get_template("data.html").render(
        page="data", canonical="/data/",
        latest_entry=entries[0]["file"].removesuffix(".json") if entries else "",
        latest_count=entries[0]["n"] if entries else 0,
        sealed_total=sum(entry["n"] for entry in entries),
        **common))

    write("privacy/index.html", env.get_template("privacy.html").render(
        page="privacy", canonical="/privacy/", **common))

    # Where Kit sends people after the form, and after the confirmation click.
    # Landing them back here rather than on a Kit page keeps the whole flow on
    # a site that has just promised to be straight with them.
    write("subscribed/index.html", env.get_template("subscribed.html").render(
        page="subscribed", canonical="/subscribed/", **common))
    write("confirmed/index.html", env.get_template("confirmed.html").render(
        page="confirmed", canonical="/confirmed/", **common))

    copy_static(out_dir)
    # Browsers still request /favicon.svg by convention. Keep it byte-for-byte
    # identical to the mark used in the header so the identity cannot drift.
    (out_dir / "favicon.svg").write_bytes(logo)
    (out_dir / "robots.txt").write_text(
        ROBOTS.format(site_url=config.SITE_URL), encoding="utf-8")
    public_pages = ["/", "/scorecard/", "/ledger/", "/method/", "/referee/",
                    "/data/", "/privacy/", "/log/", "/log/first-post/",
                    "/predictions/"]
    public_pages.extend(f"/guests/{r['slug']}/" for r in guest_records)
    public_pages.extend(match["match_url"] for match in matches)
    (out_dir / "sitemap.xml").write_text(
        sitemap(public_pages), encoding="utf-8")

    # the ledger itself, served raw so anyone can recompute the hashes
    raw = out_dir / "predictions"
    raw.mkdir(exist_ok=True)
    for path in ledger.ledger_files():
        shutil.copy2(path, raw / path.name)
    # The build summary is NOT named index.json.
    #
    # `verify.py` reads every *.json in the directory it is pointed at, and the
    # obvious way to audit this site is to download /predictions/ and run the
    # verifier on it. With a summary file sitting in there, that produced
    # "CHAIN BROKEN — index.json: content hash mismatch" against a chain that
    # was perfectly intact — the single worst possible false alarm for this
    # project to ship. The verifier now skips non-entry files as well, so this
    # is the belt to that pair of braces.
    (raw / "_chain.json").write_text(
        json.dumps({"entries": entries, "chain": chain,
                    "external_timestamps": anchors}, indent=2),
        encoding="utf-8")

    # /predictions/ and /timestamps/ are linked from the footer of every page as
    # the raw evidence, and both answered 403: directory listings are off on the
    # server and neither had an index. The single most inviting link on the site
    # — "Raw JSON" — was a Forbidden page. These two write a real index instead
    # of turning listings on, so the evidence is browsable and each file is
    # named beside its hash rather than dumped by the web server.
    write("predictions/index.html", env.get_template("raw_index.html").render(
        page="ledger", canonical="/predictions/",
        heading="The sealed ledger, raw",
        blurb=("One JSON file per publication day, each carrying the SHA-256 of "
               "the one before it. These are the files the scorecard is computed "
               "from and the files the verifier reads."),
        columns=["Matches", "Entry hash"],
        files=[{"name": e["file"], "url": f"/predictions/{e['file']}",
                "cells": [e["n"], e["hash"][:16] + "…"]} for e in entries],
        extra_url="/predictions/_chain.json", **common))

    write("timestamps/index.html", env.get_template("raw_index.html").render(
        page="ledger", canonical="/timestamps/",
        heading="Detached OpenTimestamps proofs",
        blurb=("One proof per sealed entry, from the first entry submitted "
               "onward. Download a proof and its matching JSON into the same "
               "directory and the standard client verifies it against Bitcoin "
               "without involving this server."),
        columns=["Status", "Block"],
        files=[{"name": row["proof"],
                "url": f"/timestamps/{row['proof']}",
                "cells": [row["status"],
                          row["blocks"][0] if row["blocks"] else "—"]}
               for row in reversed(anchors["entries"]) if row["proof"]],
        extra_url=None, **common))

    # Detached OpenTimestamps proofs.  They live outside /predictions/ so that
    # the raw ledger remains JSON-only and its web-server content type stays
    # truthful.  Download a JSON and its matching .ots into the same directory
    # to verify it with the standard client.
    if config.TIMESTAMPS_DIR.exists():
        proofs = out_dir / "timestamps"
        proofs.mkdir(exist_ok=True)
        for path in config.TIMESTAMPS_DIR.glob("*.ots"):
            shutil.copy2(path, proofs / path.name)

    log.info("built %d pages into %s", pages_written, out_dir)


def publish_site(keep_backups: int = 2) -> None:
    """
    Build into a staging directory, then swap it in with two renames.

    `build()` starts by deleting its output directory and then writes three
    hundred pages into it one at a time. Pointed straight at the directory
    nginx is serving — which is what the daily job did — that is several
    seconds during which the site is a 404, and then a stretch during which it
    is half a site, every three hours. Nobody noticed because nobody was
    looking; a few hundred people arriving from one link would.

    So the build goes somewhere else and the swap is two renames, which take no
    measurable time and leave a complete site on either side of them. The
    previous build is kept for a couple of rounds as a rollback that needs no
    tooling: `mv site-backup-<stamp> site` and it is back.
    """
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    staging = config.ROOT / f"staging-{stamp}"
    backup = config.ROOT / f"site-backup-{stamp}"
    live = config.SITE_DIR

    build(staging)

    # Refuse to swap in something obviously broken. A staging directory with no
    # front page is a build that failed halfway, and replacing a working site
    # with it would turn a bad build into an outage.
    for required in ("index.html", "scorecard/index.html", "ledger/index.html",
                     "predictions/index.html"):
        if not (staging / required).is_file():
            shutil.rmtree(staging, ignore_errors=True)
            raise RuntimeError(
                f"refusing to publish: staging build has no {required}")

    had_live = live.exists()
    if had_live:
        live.rename(backup)
    try:
        staging.rename(live)
    except OSError:
        if had_live:
            backup.rename(live)          # put the working site back
        shutil.rmtree(staging, ignore_errors=True)
        raise

    log.info("published %s -> %s", staging.name, live.name)

    old_backups = sorted(config.ROOT.glob("site-backup-*"))
    for stale in old_backups[:-keep_backups] if keep_backups else old_backups:
        shutil.rmtree(stale, ignore_errors=True)
        log.info("removed old build %s", stale.name)
