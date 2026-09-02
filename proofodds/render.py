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
def percent_split(values) -> list[int]:
    """
    Whole percentages that always sum to 100, by largest remainder.

    Rounding three probabilities separately does not: 0.7251, 0.1477 and
    0.1272 become 73, 15 and 13, which is 101. That was on the front page,
    two sections above a paragraph insisting our numbers sum to exactly one.

    Giving the leftover to the largest remainder is the same rule the ledger
    uses when it seals, so the card and the sealed file round the same way.
    Decimals were the other option and they do not fix it — 33.3 three times
    is 99.9 — and a tenth of a percent claims a precision a goals model with
    no lineups does not have. The fair odds beside each figure carry the
    precision for anyone who wants it.
    """
    scaled = [float(v) * 100 for v in values]
    out = [int(x) for x in scaled]
    for i in sorted(range(len(scaled)), key=lambda i: scaled[i] - out[i],
                    reverse=True)[:100 - sum(out)]:
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


def top_scorelines(xg_home, xg_away, rho=0.0, *, limit: int = 3,
                   max_goals: int = 10) -> list[dict]:
    """Reconstruct the most likely scores from the sealed model inputs.

    This is a display-only view, not a third graded market.  The calculation
    deliberately mirrors the Dixon-Coles score grid used when the prediction
    was produced: two Poisson distributions plus its four-cell low-score
    correction.  Entries seal xG and the division's rho, so the view remains
    deterministic without changing a byte of the public record.
    """
    try:
        lam, mu = float(xg_home), float(xg_away)
        rho = 0.0 if rho is None else float(rho)
    except (TypeError, ValueError):
        return []
    if not all(math.isfinite(value) for value in (lam, mu, rho)) \
            or lam < 0 or mu < 0 or limit <= 0 or max_goals < 0:
        return []

    grid = dixon_coles.score_matrix_from_xg(lam, mu, rho, max_goals)
    cells = [(float(grid[home_goals, away_goals]), home_goals, away_goals)
             for home_goals in range(max_goals + 1)
             for away_goals in range(max_goals + 1)]
    ranked = sorted(cells, key=lambda cell: (-cell[0], cell[1], cell[2]))[:limit]
    return [{"home_goals": home_goals,
             "away_goals": away_goals,
             "label": f"{home_goals}\u2013{away_goals}",
             "p": probability}
            for probability, home_goals, away_goals in ranked]


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
        "asian_handicap": handicaps,
        "main_ah": main_ah,
        "p_btts_yes": row.get("p_btts_yes"),
        "p_btts_no": row.get("p_btts_no"),
        "pct_btts_yes": pct_btts[0] if pct_btts else None,
        "pct_btts_no": pct_btts[1] if pct_btts else None,
        "corners": corner_data,
        "corner_main": corner_main,
        "top_scorelines": top_scorelines(
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
                   + (config.STATIC_DIR / "site.webmanifest").read_bytes())
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
        "uniform_log_loss": config.UNIFORM_LOG_LOSS,
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

    corner_matches = [m for m in matches if m.get("corners") and not m["is_past"]]
    write("corners/index.html", env.get_template("corners.html").render(
        page="corners", canonical="/corners/", matches=corner_matches,
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

    write("ledger/index.html", env.get_template("ledger.html").render(
        page="ledger", canonical="/ledger/",
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
        # chain reachable by URL alone.
        (raw_dir / "index.json").write_text(json.dumps(
            {"guest": record["slug"],
             "files": [p.name for p in guest.entry_files(record["slug"])]},
            indent=2), encoding="utf-8")

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
    public_pages = ["/", "/corners/", "/scorecard/", "/ledger/", "/method/", "/privacy/",
                    "/log/", "/log/first-post/"]
    public_pages.extend(f"/guests/{r['slug']}/" for r in guest_records)
    public_pages.extend(match["match_url"] for match in matches)
    (out_dir / "sitemap.xml").write_text(
        sitemap(public_pages), encoding="utf-8")

    # the ledger itself, served raw so anyone can recompute the hashes
    raw = out_dir / "predictions"
    raw.mkdir(exist_ok=True)
    for path in ledger.ledger_files():
        shutil.copy2(path, raw / path.name)
    (raw / "index.json").write_text(
        json.dumps({"entries": entries, "chain": chain,
                    "external_timestamps": anchors}, indent=2),
        encoding="utf-8")

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
