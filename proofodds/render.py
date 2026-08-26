"""
Static site build.

Jinja2 templates in, plain HTML out. No JavaScript framework, no build step
beyond this file, nothing to break at 03:00 when the cron runs. The only
client-side script on the site converts kickoff times to the reader's timezone.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import math
import shutil

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import charts, config, grade, ledger

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
def upcoming_view() -> list[dict]:
    """
    Group unplayed, already-published predictions by day for the front page.

    Only the ledger is read — the site can never show a probability that was
    not sealed first. That is the whole point, so it is worth the constraint.
    """
    now = dt.datetime.now(dt.timezone.utc)
    rows = []
    for row in ledger.all_predictions():
        kickoff = dt.datetime.strptime(row["kickoff"], "%Y-%m-%dT%H:%M:%SZ") \
                             .replace(tzinfo=dt.timezone.utc)
        if kickoff <= now:
            continue
        rows.append({**row,
                     "kickoff_dt": kickoff,
                     "kickoff_label": kickoff.strftime("%a %d %b, %H:%M UTC"),
                     "bar": charts.outcome_bar(row["p_H"], row["p_D"], row["p_A"])})

    rows.sort(key=lambda r: (r["kickoff_dt"], r["home"]))

    days, current = [], None
    for row in rows:
        label = row["kickoff_dt"].strftime("%A %d %B")
        if current is None or current["label"] != label:
            current = {"label": label, "matches": []}
            days.append(current)
        current["matches"].append(row)
    return days


def ledger_view() -> list[dict]:
    out = []
    for path in ledger.ledger_files():
        entry = ledger.read(path)
        out.append({
            "file": path.name,
            "published_at": entry["published_at"],
            "n": len(entry["predictions"]),
            "hash": entry["hash"],
            "prev_hash": entry["prev_hash"],
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
    weeks = grade.by_week(graded)
    calib = grade.calibration(graded)
    chain = ledger.verify_chain()
    days = upcoming_view()

    common = {
        "site_name": config.SITE_NAME,
        "site_url": config.SITE_URL,
        "tagline": config.SITE_TAGLINE,
        "repo_url": config.REPO_URL,
        "built_at": dt.datetime.now(dt.timezone.utc).strftime("%d %b %Y"),
        "uniform_log_loss": config.UNIFORM_LOG_LOSS,
        "backtest": config.BACKTEST,
        "score": score,
        "genesis": ledger.GENESIS,
    }

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    def write(rel: str, html: str):
        path = out_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")

    write("index.html", env.get_template("index.html").render(
        page="index", canonical="/",
        fixture_days=days,
        n_upcoming=sum(len(d["matches"]) for d in days),
        lookahead_days=config.LOOKAHEAD_DAYS,
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
        entries=ledger_view(), chain=chain, **common))

    write("method/index.html", env.get_template("method.html").render(
        page="method", canonical="/method/",
        xi=config.XI,
        half_life=int(round(math.log(2) / config.XI)),
        **common))

    # static assets
    for item in config.STATIC_DIR.glob("*"):
        shutil.copy2(item, out_dir / item.name)
    (out_dir / "favicon.svg").write_text(FAVICON, encoding="utf-8")
    (out_dir / "robots.txt").write_text(
        ROBOTS.format(site_url=config.SITE_URL), encoding="utf-8")
    (out_dir / "sitemap.xml").write_text(
        sitemap(["/", "/scorecard/", "/ledger/", "/method/"]), encoding="utf-8")

    # the ledger itself, served raw so anyone can recompute the hashes
    raw = out_dir / "predictions"
    raw.mkdir(exist_ok=True)
    for path in ledger.ledger_files():
        shutil.copy2(path, raw / path.name)
    (raw / "index.json").write_text(
        json.dumps({"entries": ledger_view(), "chain": chain}, indent=2),
        encoding="utf-8")

    log.info("built %d pages into %s", 4, out_dir)
