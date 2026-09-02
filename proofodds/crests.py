"""Display-only club crest metadata from trusted football providers.

The public prediction ledger deliberately contains no image URL.  Crest URLs
can change and have no bearing on a forecast, so they live in the ignored
``data/club_crests.json`` cache and may be refreshed without rewriting history.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from . import config

log = logging.getLogger(__name__)

CREST_HOSTS = frozenset({"crests.football-data.org", "r2.thesportsdb.com"})
MAP_FILENAME = "club_crests.json"


def map_path() -> Path:
    return config.DATA_DIR / MAP_FILENAME


def safe_url(value: object) -> str | None:
    """Return a provider crest URL only when its origin is exactly allowed."""
    if not isinstance(value, str):
        return None
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError:
        return None
    if (parsed.scheme != "https" or parsed.hostname not in CREST_HOSTS
            or port not in (None, 443) or parsed.username or parsed.password
            or not parsed.path):
        return None
    return value


@lru_cache(maxsize=16)
def _read_cached(path_string: str, mtime_ns: int) -> dict:
    path = Path(path_string)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        log.warning("cannot read club crest cache %s: %s", path, exc)
        return {"version": 1, "provider": "football-data.org", "clubs": {}}
    if not isinstance(payload, dict) or not isinstance(payload.get("clubs"), dict):
        log.warning("ignoring malformed club crest cache %s", path)
        return {"version": 1, "provider": "football-data.org", "clubs": {}}
    return payload


def load() -> dict:
    path = map_path()
    if not path.is_file():
        return {"version": 1, "provider": "football-data.org", "clubs": {}}
    return _read_cached(str(path), path.stat().st_mtime_ns)


def lookup(league: str, club: str) -> str | None:
    entry = load().get("clubs", {}).get(league, {}).get(club)
    if not isinstance(entry, dict):
        return None
    return safe_url(entry.get("url"))


def update(league: str, rows: list[dict]) -> int:
    """Merge trusted provider rows into the ignored cache, atomically."""
    clean: dict[str, dict] = {}
    for row in rows:
        club = row.get("club")
        url = safe_url(row.get("url"))
        if not isinstance(club, str) or not club.strip() or not url:
            continue
        entry = {"url": url}
        team_id = row.get("id")
        if isinstance(team_id, int):
            entry["id"] = team_id
        raw_name = row.get("raw_name")
        if isinstance(raw_name, str) and raw_name:
            entry["raw_name"] = raw_name
        clean[club] = entry

    if not clean:
        return 0

    payload = load()
    payload = {
        "version": 1,
        "provider": "football-data.org",
        "clubs": dict(payload.get("clubs", {})),
    }
    division = dict(payload["clubs"].get(league, {}))
    changed = sum(division.get(club) != entry for club, entry in clean.items())
    if not changed:
        return 0
    division.update(clean)
    payload["clubs"][league] = division

    path = map_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    temporary.replace(path)
    _read_cached.cache_clear()
    return changed
