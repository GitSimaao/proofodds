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
import unicodedata

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import anchor, charts, config, crests, dixon_coles, grade, guest, ledger
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
    # Still read, still sealed, deliberately not rendered. Corners are the one
    # market this site publishes no score for, so it publishes no forecast for
    # them either — see the note in config.FORECAST_MARKETS. The entries keep
    # accumulating so the record exists on the day the scoring does.
    corner_data = row.get("corners")
    corner_totals = corner_data.get("totals", []) if isinstance(corner_data, dict) else []
    corner_main = (min(corner_totals, key=lambda x: abs(float(x["p_over"]) - .5))
                   if corner_totals else None)
    favourite = ("H", "D", "A")[max(
        range(3), key=lambda i: (row["p_H"], row["p_D"], row["p_A"])[i])]
    match_slug = (f"{league.lower()}-{slugify(row['home'])}-v-"
                  f"{slugify(row['away'])}")
    match_url = f"/matches/{row['kickoff'][:10]}/{match_slug}/"
    tbc = bool(row.get("kickoff_tbc"))
    entry_file = row.get("entry_file", f"{row['published_at'][:10]}.json")

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
        # over/under and handicap are forecasts like BTTS is — the card has to
        # say which is which, on the card, not in a footnote.
        "ou_scored": config.is_scored(league, "OU2.5"),
        "ah_scored": config.is_scored(league, "AH"),
        # A fixture first published before a market existed carries only what
        # was sealed that day. First publication wins, so the missing markets
        # cannot be added later — and the card says why, rather than leaving a
        # reader to wonder where the markets the method page promises are.
        "sparse_markets": [
            label for key, label in (("p_over25", "over/under 2.5"),
                                     ("p_btts_yes", "both teams to score"),
                                     ("asian_handicap", "the Asian handicap"))
            if not row.get(key)],
        "asian_handicap": handicaps,
        "main_ah": main_ah,
        "p_btts_yes": row.get("p_btts_yes"),
        "p_btts_no": row.get("p_btts_no"),
        "pct_btts_yes": pct_btts[0] if pct_btts else None,
        "pct_btts_no": pct_btts[1] if pct_btts else None,
        "corners": corner_data,
        "corner_main": corner_main,
        "score_matrix": score_matrix(
            row.get("xg_home"), row.get("xg_away"), row.get("model_rho")),
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
            "anchor": by_entry.get(path.name, {
                "status": "none", "blocks": [], "proof": None}),
        })
    return list(reversed(out))


def sitemap(pages: list[str]) -> str:
    today = dt.date.today().isoformat()
    urls = "".join(
        f"<url><loc>{config.SITE_URL}{p}</loc><lastmod>{today}</lastmod></url>"
        for p in pages)
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"{urls}</urlset>")


# --------------------------------------------------------------------------- #
def build(out_dir=None) -> None:
    out_dir = out_dir or config.SITE_DIR
    env = environment()

    graded = grade.graded_frame()
    score = grade.scorecard(graded)
    totals = grade.totals_scorecard(graded)
    btts = grade.btts_scorecard(graded)
    asian = grade.ah_scorecard(graded)
    weeks = grade.by_week(graded)
    leagues = grade.by_league(graded)
    calib = grade.calibration(graded)
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
        "n_leagues": len(config.ENABLED_LEAGUES),
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
        n_shown=len(shown) or len(config.ENABLED_LEAGUES),
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
