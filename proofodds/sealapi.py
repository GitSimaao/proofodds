"""
The sealing service: what /seal/ posts to.

A guest with an invite token seals one pick and gets a receipt. Everything
that makes an entry trustworthy lives in guest.seal(); this file only checks
who is asking, turns a form into arguments, and reports what happened.

    python -m proofodds.sealapi            # listens on 127.0.0.1:8377

It listens on localhost only. nginx proxies /api/ to it and rate-limits the
route (deploy/nginx-proofodds.conf). There is no open sign-up: a token is
issued by the operator with `python -m proofodds.guest invite`, so the only
people who can write to this box are people the operator has spoken to.

The token travels in the request body, never in the URL, so it does not end
up in the access log.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from . import config, guest

log = logging.getLogger("sealapi")

HOST = "127.0.0.1"
PORT = int(os.environ.get("PROOFODDS_SEAL_PORT", "8377"))
MAX_BODY = 4096
# Per token. A real tipster posts a handful of picks a day; this is a ceiling
# against a leaked token or a runaway script, not a plan limit.
MAX_SEALS_PER_DAY = int(os.environ.get("PROOFODDS_SEAL_DAILY_LIMIT", "60"))

# One seal at a time: each entry links to the one before it.
_chain_lock = threading.Lock()
_recent: dict[str, list[float]] = {}

FIELDS = ("league", "home", "away", "kickoff", "market", "selection", "odds",
          "line", "book", "reveal")


def _over_limit(slug: str) -> bool:
    now = time.time()
    stamps = [t for t in _recent.get(slug, []) if now - t < 86400]
    _recent[slug] = stamps
    return len(stamps) >= MAX_SEALS_PER_DAY


def handle_seal(form: dict) -> tuple[int, dict]:
    """Seal one entry from form fields. Returns (HTTP status, payload)."""
    who = guest.guest_for_token(form.get("token", ""))
    if who is None:
        # Same answer for a wrong token and a revoked one.
        return 403, {"error": "That token is not valid."}
    if _over_limit(who["slug"]):
        return 429, {"error": f"Daily limit of {MAX_SEALS_PER_DAY} entries "
                              "reached for this record."}

    values = {name: (form.get(name) or "").strip() for name in FIELDS}
    missing = [name for name in ("league", "home", "away", "kickoff", "market",
                                 "selection", "odds") if not values[name]]
    if missing:
        return 400, {"error": "Missing: " + ", ".join(missing) + "."}
    if any(len(value) > 80 for value in values.values()):
        return 400, {"error": "A field is too long."}
    try:
        odds = float(values["odds"].replace(",", "."))
        line = (float(values["line"].replace(",", "."))
                if values["line"] else None)
    except ValueError:
        return 400, {"error": "Odds and line must be numbers."}

    with _chain_lock:
        kwargs = dict(
            guest_name=who["name"], slug=who["slug"],
            league=values["league"], home=values["home"],
            away=values["away"], kickoff=values["kickoff"],
            market=values["market"], selection=values["selection"],
            odds=odds, line=line, book=values["book"],
            reveal=values["reveal"])
        try:
            try:
                path = guest.seal(**kwargs)
            except FileExistsError:
                # Entries are named to the second. Two in one second is a
                # person being quick, not an error worth showing them.
                time.sleep(1.1)
                path = guest.seal(**kwargs)
        except (ValueError, FileExistsError, FileNotFoundError) as exc:
            # guest.seal's refusals are written to be read by the guest.
            return 400, {"error": str(exc)}
        except Exception:
            log.exception("sealing failed for %s", who["slug"])
            return 500, {"error": "Sealing failed on our side. Nothing was "
                                  "recorded. Try again, and tell us if it "
                                  "repeats."}
        _recent.setdefault(who["slug"], []).append(time.time())
        entry = json.loads(path.read_text(encoding="utf-8"))
        held = path.parent.parent == guest.EMBARGO_DIR
        try:
            guest.publish()
        except Exception:
            # The entry is sealed on disk. The five-minute timer commits it.
            log.exception("publish after seal failed — the timer will retry")

    return 200, {
        "sealed_at": entry["sealed_at"], "hash": entry["hash"],
        "prev_hash": entry["prev_hash"], "guest": who["slug"],
        "match": f"{entry['home']} v {entry['away']}",
        "kickoff": entry["kickoff"], "market": entry["market"],
        "selection": entry["selection"], "odds_taken": entry["odds_taken"],
        "line": entry.get("line"),
        "public": not held,
        "reveal": ("at kickoff" if entry.get("reveal") == "kickoff"
                   else "now" if not held
                   else "when the entries sealed before it are public"),
        "anchored": (config.TIMESTAMPS_DIR / f"{path.name}.ots").exists(),
        "record": f"{config.SITE_URL.rstrip('/')}/guests/{who['slug']}/",
    }


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>{title} — ProofOdds</title>
<link rel="stylesheet" href="/style.css"></head>
<body><main class="shell" style="max-width:44rem;padding:3rem 1rem">
<h1>{title}</h1>{body}
<p><a href="/seal/">Seal another</a> · <a href="/referee/">How this works</a></p>
</main></body></html>"""


def render_page(status: int, payload: dict) -> str:
    esc = html.escape
    if status != 200:
        return PAGE.format(title="Not sealed", body=(
            f"<p><strong>{esc(payload['error'])}</strong></p>"
            "<p>Nothing was recorded. Go back, correct it and send it again.</p>"))
    line = (f" (line {payload['line']:+g})" if payload["line"] is not None else "")
    rows = [
        ("Match", payload["match"]),
        ("Kickoff (as you stated it)", payload["kickoff"]),
        ("Pick", f"{payload['market']} {payload['selection']}{line} "
                 f"@ {payload['odds_taken']}"),
        ("Sealed at", payload["sealed_at"]),
        ("Entry hash", payload["hash"]),
        ("Published", payload["reveal"]),
        ("Bitcoin timestamp", "submitted; confirms within a few hours"
         if payload["anchored"] else
         "NOT submitted — the calendar servers did not answer. The entry is "
         "still chained."),
    ]
    table = "".join(f"<tr><th style='text-align:left;padding:.3rem 1rem .3rem 0'>"
                    f"{esc(k)}</th><td style='word-break:break-all'>"
                    f"{esc(str(v))}</td></tr>" for k, v in rows)
    return PAGE.format(title="Sealed", body=(
        f"<table>{table}</table>"
        "<p>Keep the entry hash. It is your receipt: this exact pick, at this "
        "exact time, and it cannot be changed or removed, by you or by us.</p>"
        f"<p>Your record: <a href=\"{esc(payload['record'])}\">"
        f"{esc(payload['record'])}</a>. The page is rebuilt every three "
        "hours, so a new entry takes up to that long to appear.</p>"))


class Handler(BaseHTTPRequestHandler):
    server_version = "ProofOddsSeal/1"

    def log_message(self, fmt, *args):  # journal, not stderr noise
        log.info("%s %s", self.address_string(), fmt % args)

    def _send(self, status: int, body: str, kind: str) -> None:
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", f"{kind}; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/api/health":
            return self._send(200, json.dumps({"ok": True}), "application/json")
        self._send(404, json.dumps({"error": "not found"}), "application/json")

    def do_POST(self):
        if self.path != "/api/seal":
            return self._send(404, json.dumps({"error": "not found"}),
                              "application/json")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if not 0 < length <= MAX_BODY:
            return self._send(413, json.dumps({"error": "bad request size"}),
                              "application/json")
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        as_json = "application/json" in self.headers.get("Content-Type", "")
        try:
            form = (json.loads(raw) if as_json
                    else {k: v[0] for k, v in parse_qs(raw).items()})
            if not isinstance(form, dict):
                raise ValueError
            form = {str(k): str(v) for k, v in form.items() if v is not None}
        except ValueError:
            return self._send(400, json.dumps({"error": "unreadable body"}),
                              "application/json")
        status, payload = handle_seal(form)
        if as_json:
            return self._send(status, json.dumps(payload), "application/json")
        self._send(status, render_page(status, payload), "text/html")


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)-7s %(name)s: %(message)s")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    log.info("listening on %s:%d", HOST, PORT)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
