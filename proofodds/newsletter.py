"""
The weekly email: one number, honestly, every Monday.

What this sends is the scorecard, not a preview of the week ahead. That is the
whole point of the list — someone who subscribes is asking to be told how we
did, and the answer arrives whether it flatters us or not. A newsletter you can
only send in a good week is a newsletter you will eventually fake.

Delivery is Kit (kit.com), because its free tier covers 10,000 subscribers with
unlimited broadcasts and it has a real API. Sending from our own server is not
worth the deliverability fight.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import logging

import pandas as pd
import requests

from . import config, grade, ledger

log = logging.getLogger(__name__)

KIT_API = "https://api.kit.com/v4"
SENT_FILE = "newsletter_sent.json"


# --------------------------------------------------------------------------- #
#  What happened this week
# --------------------------------------------------------------------------- #
def week_bounds(today: dt.date | None = None) -> tuple[dt.date, dt.date]:
    """The Monday-to-Sunday week that just finished."""
    today = today or dt.date.today()
    this_monday = today - dt.timedelta(days=today.weekday())
    last_monday = this_monday - dt.timedelta(days=7)
    return last_monday, this_monday - dt.timedelta(days=1)


def summarise(graded: pd.DataFrame, start: dt.date, end: dt.date) -> dict | None:
    """
    The week's numbers, plus the running total since the first prediction.

    Returns None when nothing was graded — a week with no matches produces no
    email rather than an empty one.
    """
    if graded.empty or "graded" not in graded.columns:
        return None

    done = graded[graded["graded"]]
    if done.empty:
        return None

    mask = (done["date"].dt.date >= start) & (done["date"].dt.date <= end)
    week = done[mask]
    if week.empty:
        return None

    model_w = float(week["model_loss"].mean())
    market_w = float(week["market_loss"].mean())
    model_all = float(done["model_loss"].mean())
    market_all = float(done["market_loss"].mean())

    order = {code: i for i, code in enumerate(config.LEAGUE_ORDER)}
    week = week.copy()
    week["_order"] = week["league"].map(lambda c: order.get(c, 99))

    rows = []
    for r in week.sort_values(["_order", "date"]).itertuples():
        rows.append({
            "date": r.date.date().isoformat(),
            "league": r.league,
            "league_name": config.league_name(r.league),
            "home": r.home, "away": r.away,
            "score": f"{int(r.FTHG)}-{int(r.FTAG)}",
            "said": {"H": r.p_H, "D": r.p_D, "A": r.p_A}[r.FTR],
            "market_said": {"H": r.mkt_H, "D": r.mkt_D, "A": r.mkt_A}[r.FTR],
        })

    # Per-division rows for the week. Small samples, and the email says so —
    # but a reader who only follows one league should not have to take a
    # seven-league average on faith.
    per_league = []
    for code, block in week.groupby("league"):
        per_league.append({
            "league": code,
            "name": config.league_name(code),
            "n": int(len(block)),
            "model": float(block["model_loss"].mean()),
            "market": float(block["market_loss"].mean()),
            "gap": float((block["model_loss"] - block["market_loss"]).mean()),
        })
    per_league.sort(key=lambda r: order.get(r["league"], 99))

    return {
        "leagues": per_league,
        "start": start.isoformat(), "end": end.isoformat(),
        "n": int(len(week)),
        "model": model_w, "market": market_w, "gap": model_w - market_w,
        "n_all": int(len(done)),
        "model_all": model_all, "market_all": market_all,
        "gap_all": model_all - market_all,
        "matches": rows,
        "head": ledger.verify_chain()["head"],
    }


# --------------------------------------------------------------------------- #
#  The email itself
# --------------------------------------------------------------------------- #
def _verdict(gap: float) -> str:
    if gap < 0:
        return "ahead of the closing line"
    return "behind the closing line"


def subject_line(s: dict) -> str:
    return (f"Week of {s['start']}: {s['gap']:+.4f} against the closing line "
            f"({s['n']} matches)")


def render_text(s: dict) -> str:
    """Plain text, because plain text is what this newsletter is."""
    lines = [
        f"ProofOdds — week of {s['start']} to {s['end']}",
        "",
        f"{s['n']} matches graded this week.",
        "",
        f"  ProofOdds     {s['model']:.4f}",
        f"  Closing line  {s['market']:.4f}",
        f"  Gap           {s['gap']:+.4f}   ({_verdict(s['gap'])})",
        "",
        f"Since the first sealed prediction, over {s['n_all']} graded matches:",
        f"  ProofOdds     {s['model_all']:.4f}",
        f"  Closing line  {s['market_all']:.4f}",
        f"  Gap           {s['gap_all']:+.4f}",
        "",
    ]
    if len(s.get("leagues", [])) > 1:
        lines += ["By division this week (small samples — the numbers above are",
                  "the ones with enough matches behind them):", ""]
        for l in s["leagues"]:
            lines.append(f"  {l['name']:<16} {l['n']:>3} matches   "
                         f"{l['model']:.4f} vs {l['market']:.4f}   {l['gap']:+.4f}")
        lines.append("")
    lines += [
        "Log loss, lower is better. Predicting 1/3-1/3-1/3 every week",
        f"scores {config.UNIFORM_LOG_LOSS:.4f}, so the distance between that and the",
        "closing line is everything anyone knows about football.",
        "",
        "This week, match by match — the probability we gave to what actually",
        "happened, next to the market's:",
        "",
    ]
    current = None
    for m in s["matches"]:
        if m["league_name"] != current:
            current = m["league_name"]
            lines += ["", f"  {current}"]
        lines.append(f"    {m['date']}  {m['home']} {m['score']} {m['away']}"
                     f"   us {m['said']:.0%} / market {m['market_said']:.0%}")
    lines += [
        "",
        f"Full record: {config.SITE_URL}/scorecard/",
        f"Ledger head: {s['head'][:16]}…  ({config.SITE_URL}/ledger/)",
        "",
        "Every prediction above was sealed and hashed before kickoff. You can",
        "recompute the whole chain yourself from the public repository.",
        "",
        "— ProofOdds",
    ]
    return "\n".join(lines)


def render_html(s: dict) -> str:
    """A deliberately plain HTML version. Email clients punish cleverness."""
    e = html.escape
    parts, current = [], None
    for m in s["matches"]:
        if m["league_name"] != current:
            current = m["league_name"]
            parts.append(
                f'<tr><td colspan="3" style="padding:14px 0 4px;font-size:12px;'
                f'letter-spacing:.06em;text-transform:uppercase;color:#6b7280">'
                f'{e(current)}</td></tr>')
        parts.append(
            f'<tr>'
            f'<td style="padding:4px 10px 4px 0;color:#6b7280;white-space:nowrap">{e(m["date"])}</td>'
            f'<td style="padding:4px 10px 4px 0">{e(m["home"])} '
            f'<strong>{e(m["score"])}</strong> {e(m["away"])}</td>'
            f'<td style="padding:4px 0;text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums">'
            f'{m["said"]:.0%} <span style="color:#9ca3af">/ {m["market_said"]:.0%}</span></td>'
            f'</tr>')
    rows = "".join(parts)

    def block(label, model, market, gap):
        return (
            f'<table role="presentation" cellpadding="0" cellspacing="0" '
            f'style="margin:0 0 18px;font-variant-numeric:tabular-nums">'
            f'<tr><td style="padding:2px 16px 2px 0;color:#6b7280">{label}</td>'
            f'<td style="padding:2px 0"></td></tr>'
            f'<tr><td style="padding:2px 16px 2px 0">ProofOdds</td>'
            f'<td style="padding:2px 0"><strong>{model:.4f}</strong></td></tr>'
            f'<tr><td style="padding:2px 16px 2px 0">Closing line</td>'
            f'<td style="padding:2px 0">{market:.4f}</td></tr>'
            f'<tr><td style="padding:2px 16px 2px 0">Gap</td>'
            f'<td style="padding:2px 0;color:{"#0f7a52" if gap < 0 else "#9a6410"}">'
            f'<strong>{gap:+.4f}</strong> &nbsp;<span style="color:#6b7280">'
            f'{_verdict(gap)}</span></td></tr>'
            f'</table>')

    return f"""<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
font-size:15px;line-height:1.55;color:#10151a;max-width:600px">
<p style="margin:0 0 6px;font-size:13px;color:#6b7280;letter-spacing:.06em;text-transform:uppercase">
Week of {e(s['start'])} – {e(s['end'])}</p>
<p style="margin:0 0 20px;font-size:20px;font-weight:700">{s['n']} matches graded.</p>
{block("This week", s['model'], s['market'], s['gap'])}
{block(f"Since the start &mdash; {s['n_all']} matches", s['model_all'], s['market_all'], s['gap_all'])}
<p style="margin:0 0 18px;color:#4e5760">Log loss, lower is better. Predicting
1/3-1/3-1/3 every week scores {config.UNIFORM_LOG_LOSS:.4f}, so the distance between
that and the closing line is everything anyone knows about football.</p>
<p style="margin:0 0 8px;color:#4e5760">Match by match — the probability we gave to
what actually happened, next to the market's:</p>
<table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;font-size:14px;
border-top:1px solid #e5e7eb;border-bottom:1px solid #e5e7eb;margin:0 0 20px">{rows}</table>
<p style="margin:0 0 6px"><a href="{config.SITE_URL}/scorecard/" style="color:#1c5cab">Full record</a>
&nbsp;·&nbsp; <a href="{config.SITE_URL}/ledger/" style="color:#1c5cab">Ledger</a>
&nbsp;·&nbsp; <a href="{config.REPO_URL}" style="color:#1c5cab">Source</a></p>
<p style="margin:0 0 18px;font-size:13px;color:#6b7280">Ledger head
<code>{e(s['head'][:16])}…</code> — every prediction above was sealed and hashed before
kickoff, and you can recompute the chain yourself.</p>
</div>"""


# --------------------------------------------------------------------------- #
#  Kit
# --------------------------------------------------------------------------- #
def send_broadcast(subject: str, html_body: str, preview: str) -> dict:
    """Create the broadcast in Kit and schedule it to go out immediately."""
    if not config.KIT_API_KEY:
        raise RuntimeError("PROOFODDS_KIT_API_KEY is not set")

    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    payload = {
        "subject": subject,
        "description": f"ProofOdds weekly scorecard, {now.date()}",
        "preview_text": preview,
        "content": html_body,
        "public": False,
        "published_at": now.isoformat(),
        # A near-future send_at rather than "now" leaves a couple of minutes to
        # cancel in Kit if something looks wrong once it is queued.
        "send_at": (now + dt.timedelta(minutes=config.NEWSLETTER_DELAY_MIN)).isoformat(),
        "subscriber_filter": [],
    }
    resp = requests.post(f"{KIT_API}/broadcasts", json=payload,
                         headers={"X-Kit-Api-Key": config.KIT_API_KEY,
                                  "Content-Type": "application/json"},
                         timeout=30)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Kit returned {resp.status_code}: {resp.text[:400]}")
    return resp.json()


# --------------------------------------------------------------------------- #
#  Idempotency
# --------------------------------------------------------------------------- #
def _sent_path():
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return config.OUTPUT_DIR / SENT_FILE


def already_sent(week_start: str) -> bool:
    path = _sent_path()
    if not path.exists():
        return False
    return week_start in json.loads(path.read_text()).get("weeks", [])


def mark_sent(week_start: str, broadcast_id) -> None:
    path = _sent_path()
    data = json.loads(path.read_text()) if path.exists() else {"weeks": [], "log": []}
    if week_start not in data["weeks"]:
        data["weeks"].append(week_start)
    data["log"].append({"week": week_start, "broadcast_id": broadcast_id,
                        "at": dt.datetime.now(dt.timezone.utc)
                                .strftime("%Y-%m-%dT%H:%M:%SZ")})
    path.write_text(json.dumps(data, indent=2))


def weekly(dry_run: bool = True, today: dt.date | None = None) -> dict:
    """Build and (optionally) send this week's email."""
    start, end = week_bounds(today)
    summary = summarise(grade.graded_frame(), start, end)

    if summary is None:
        log.info("no matches graded between %s and %s — nothing to send", start, end)
        return {"sent": False, "reason": "no graded matches"}

    if already_sent(summary["start"]):
        log.info("week of %s already sent", summary["start"])
        return {"sent": False, "reason": "already sent"}

    subject = subject_line(summary)
    text = render_text(summary)

    if dry_run:
        print(text)
        return {"sent": False, "reason": "dry run", "subject": subject,
                "summary": summary}

    result = send_broadcast(subject, render_html(summary),
                            preview=f"{summary['n']} matches graded. "
                                    f"Gap {summary['gap']:+.4f}.")
    broadcast_id = result.get("broadcast", {}).get("id") or result.get("id")
    mark_sent(summary["start"], broadcast_id)
    log.info("broadcast %s scheduled: %s", broadcast_id, subject)
    return {"sent": True, "broadcast_id": broadcast_id, "subject": subject}
