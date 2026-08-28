"""
Static site build.

Jinja2 templates in, plain HTML out. No JavaScript framework, no build step
beyond this file, nothing to break at 03:00 when the cron runs. The only
client-side script on the site converts kickoff times to the reader's timezone.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import math
import re
import shutil
import unicodedata

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import anchor, charts, config, dixon_coles, grade, ledger
from .data import sealed_name

log = logging.getLogger(__name__)

FAVICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
<rect width="32" height="32" rx="5" fill="#2A78D6"/>
<path d="M7 17l6 6 12-14" fill="none" stroke="#fff" stroke-width="3.6"
      stroke-linecap="round" stroke-linejoin="round"/>
</svg>"""

ROBOTS = """User-agent: *
Allow: /

Sitemap: {site_url}/sitemap.xml
"""


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


def club_mark(name: str) -> dict:
    """
    Resolve an optional self-hosted crest, with a deterministic monogram fallback.

    Official crests are trademarks and remote image URLs are mutable.  The site
    therefore only uses files deliberately placed in static/clubs/; until one
    exists the fallback still gives every club a compact visual identity.
    """
    slug = slugify(name)
    source = None
    for suffix in (".svg", ".png", ".webp"):
        candidate = config.STATIC_DIR / "clubs" / f"{slug}{suffix}"
        if candidate.is_file():
            source = f"/clubs/{candidate.name}"
            break

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
        "top_scorelines": top_scorelines(
            row.get("xg_home"), row.get("xg_away"), row.get("model_rho")),
        "home_mark": club_mark(home),
        "away_mark": club_mark(away),
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


def upcoming_view(rows: list[dict] | None = None) -> list[dict]:
    """
    Group unplayed, already-published predictions by day for the front page.

    Only the ledger is read — the site can never show a probability that was
    not sealed first. That is the whole point, so it is worth the constraint.
    """
    rows = rows if rows is not None else match_views()
    rows = [row for row in rows if not row["is_past"]]

    order = {code: i for i, code in enumerate(config.LEAGUE_ORDER)}
    rows.sort(key=lambda r: (r["kickoff_dt"], order.get(r["league"], 99), r["home"]))

    # Grouped by day, then by division inside the day. Seven leagues on one
    # page is a wall unless something separates them, and the day is what a
    # reader is actually looking for first.
    days, current = [], None
    for row in rows:
        label = row["kickoff_dt"].strftime("%A %d %B")
        if current is None or current["label"] != label:
            current = {"label": label, "matches": [], "leagues": []}
            days.append(current)
        current["matches"].append(row)
        if not current["leagues"] or current["leagues"][-1]["code"] != row["league"]:
            current["leagues"].append({"code": row["league"],
                                       "name": row["league_name"],
                                       "short": row["league_short"],
                                       "country": row["league_country"],
                                       "flag": row["league_flag"],
                                       "matches": []})
        current["leagues"][-1]["matches"].append(row)
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
    weeks = grade.by_week(graded)
    leagues = grade.by_league(graded)
    calib = grade.calibration(graded)
    chain = ledger.verify_chain()
    anchors = anchor.report()
    entries = ledger_view(anchors)
    matches = match_views()
    days = upcoming_view(matches)

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
    asset_v = hashlib.sha256(css).hexdigest()[:10]

    common = {
        "asset_v": asset_v,
        "site_name": config.SITE_NAME,
        "site_url": config.SITE_URL,
        "tagline": config.SITE_TAGLINE,
        "repo_url": config.REPO_URL,
        "built_at": dt.datetime.now(dt.timezone.utc).strftime("%d %b %Y"),
        "uniform_log_loss": config.UNIFORM_LOG_LOSS,
        "backtest": config.BACKTEST,
        "score": score,
        "totals": totals,
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

    def write(rel: str, html: str):
        path = out_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")

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

    write("privacy/index.html", env.get_template("privacy.html").render(
        page="privacy", canonical="/privacy/", **common))

    # Where Kit sends people after the form, and after the confirmation click.
    # Landing them back here rather than on a Kit page keeps the whole flow on
    # a site that has just promised to be straight with them.
    write("subscribed/index.html", env.get_template("subscribed.html").render(
        page="subscribed", canonical="/subscribed/", **common))
    write("confirmed/index.html", env.get_template("confirmed.html").render(
        page="confirmed", canonical="/confirmed/", **common))

    # Static assets may be grouped into directories (country flags today,
    # deliberately licensed club crests later). Keep their public paths intact.
    for item in config.STATIC_DIR.glob("*"):
        target = out_dir / item.name
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)
    (out_dir / "favicon.svg").write_text(FAVICON, encoding="utf-8")
    (out_dir / "robots.txt").write_text(
        ROBOTS.format(site_url=config.SITE_URL), encoding="utf-8")
    public_pages = ["/", "/scorecard/", "/ledger/", "/method/", "/privacy/"]
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

    log.info("built %d pages into %s", 7 + len(matches), out_dir)
