"""
National-team forecasts, and the Nations League page they exist for.

What this module is
-------------------
The same Dixon-Coles model the divisions use, fitted on international
matches instead of club ones. `dixon_coles.fit` never knew what a club was --
its own docstring says "nothing here knows anything about football" -- so
Portugal and Hungary enter it as two integers exactly as Arsenal and Chelsea
do. Nothing in the model needed changing.

What this module is NOT
-----------------------
Sealed, and therefore not scored. Nothing here writes to `predictions/`,
nothing here reaches `grade.py`, and no figure produced here appears on the
scorecard or in any pooled log loss. That is a deliberate separation, not an
omission waiting to be filled: the site's whole claim is that a published
number was committed before kickoff and measured afterwards, and a forecast
that skips both halves must never be able to borrow the credibility of one
that did not. The page says so where a reader will see it.

Three modelling decisions worth stating, because a reader can check them
------------------------------------------------------------------------
**Neutral venues are excluded from the fit.** The model carries one global
home advantage; it has no way to say "this match had none". A tournament final
on neutral ground, fitted as though the nominal home side had an advantage it
did not have, biases that parameter for every other match. The source flags
`is_neutral`, so those matches are dropped rather than mismodelled. It costs
the European Championship and World Cup finals -- roughly a tenth of the pool
-- and keeps the parameter meaning what it says.

**The pool is wider than the competition.** Fitting on the Nations League
alone would be a mistake that looks like a simplification. Its groups are
stratified by ability into Leagues A to D, so within one edition a team meets
only opponents of its own level, and the attack and defence ratings of a
League A side and a League D side are then barely identified against each
other: a weak team that beats its weak peers looks strong. Qualifying
campaigns and friendlies mix the levels heavily, which is what connects the
graph, so they are fitted too.

**The time decay is not the club one.** `config.XI` gives a 347-day half-life,
tuned for clubs playing 38-plus matches a season. A national team plays about
ten a year, so that decay leaves almost nothing behind it. `NATIONS_XI` is
fitted separately by the holdout in `scripts/tune_nations.py` and is a much
slower decay, which is the honest consequence of a thinner calendar rather
than a preference.

Cost
----
Results are cached on disk and refreshed on a timer, because
`statsapi.matches` is deliberately uncached -- statuses and scores move, so it
must be -- and re-reading six competitions on every site build would spend the
monthly quota on history that cannot change.
"""

from __future__ import annotations

import datetime as dt
import logging

import numpy as np
import pandas as pd

from . import config, dixon_coles, statsapi

log = logging.getLogger(__name__)

# The competition whose fixtures this page forecasts.
NATIONS_LEAGUE = "comp_574977"

# Everything fitted, mapped to a readable name for the provenance block.
# The Nations League is in here twice over: once as the thing being predicted,
# once as part of its own training data.
POOL = {
    "comp_574977": "UEFA Nations League",
    "comp_3759":   "EURO qualification",
    "comp_2954":   "World Cup qualification (UEFA)",
    "comp_2949":   "European Championship",
    "comp_6107":   "FIFA World Cup",
    "comp_29967":  "International friendlies",
}

RESULTS_CACHE = config.DATA_DIR / "nations_results.csv"
FIXTURES_CACHE = config.DATA_DIR / "nations_fixtures.csv"

COLUMNS = ["match_id", "Date", "competition", "HomeTeam", "AwayTeam",
           "FTHG", "FTAG", "neutral"]


def _score(match: dict) -> tuple[int, int] | None:
    """
    The 90-minute score, which is the only one the model is about.

    A knockout tie that went to extra time or penalties has a `score.home`
    that includes them. Feeding that to a Poisson model trained on league
    football would teach it that internationals score more goals than they do,
    and the inflation lands entirely on the knockout rounds -- which is to say
    on exactly the matches between the strongest teams.
    """
    score = match.get("score") or {}
    regulation = score.get("regulation") or {}
    home, away = regulation.get("home"), regulation.get("away")
    if home is None or away is None:
        home, away = score.get("home"), score.get("away")
    if home is None or away is None:
        return None
    return int(home), int(away)


def _rows(matches: list[dict], competition: str) -> list[dict]:
    out = []
    for match in matches:
        if str(match.get("status", "")).lower() != "finished":
            continue
        goals = _score(match)
        if goals is None:
            continue
        home, away = match.get("home_team") or {}, match.get("away_team") or {}
        if not home.get("name") or not away.get("name"):
            continue
        out.append({
            "match_id": match.get("id"),
            "Date": (match.get("utc_date") or "")[:10],
            "competition": competition,
            "HomeTeam": home["name"],
            "AwayTeam": away["name"],
            "FTHG": goals[0],
            "FTAG": goals[1],
            # None means the source did not say. Treated as "not neutral",
            # which is what it is for every league-format match in the pool,
            # and stated on the page rather than assumed silently.
            "neutral": bool(match.get("is_neutral")),
        })
    return out


def _age_hours(path) -> float:
    if not path.exists():
        return float("inf")
    age = dt.datetime.now().timestamp() - path.stat().st_mtime
    return age / 3600.0


def refresh(max_age_hours: float = 20.0, force: bool = False) -> bool:
    """
    Rebuild the pooled results cache and the Nations League fixture list.

    Returns True if the API was called. The two caches age at different rates
    on purpose: a finished match never changes, so the training pool is a
    daily job, while the fixture list carries kick-off times that move and is
    refreshed on the shorter clock in `refresh_fixtures`.
    """
    if not force and _age_hours(RESULTS_CACHE) < max_age_hours:
        return False
    if not config.STATSAPI_KEY:
        log.warning("nations: PROOFODDS_STATSAPI_KEY is not set — keeping the "
                    "cached results, if any")
        return False

    frames = []
    for code, name in POOL.items():
        try:
            rows = _rows(statsapi.matches(competition_id=code, per_page=100), name)
        except Exception as exc:                 # never take the build down
            log.warning("nations: %s unavailable (%s) — using what is cached", name, exc)
            continue
        log.info("nations: %s — %d finished matches", name, len(rows))
        frames.append(pd.DataFrame(rows, columns=COLUMNS))

    if not frames:
        return False
    pooled = pd.concat(frames, ignore_index=True)
    pooled = pooled.drop_duplicates(subset="match_id").sort_values("Date")
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    pooled.to_csv(RESULTS_CACHE, index=False)
    log.info("nations: cached %d international results from %d competitions",
             len(pooled), len(frames))
    return True


def refresh_fixtures(max_age_hours: float = 3.0, force: bool = False) -> bool:
    """The Nations League fixture list, on a shorter clock than the results."""
    if not force and _age_hours(FIXTURES_CACHE) < max_age_hours:
        return False
    if not config.STATSAPI_KEY:
        return False
    try:
        matches = statsapi.matches(competition_id=NATIONS_LEAGUE, per_page=100)
    except Exception as exc:
        log.warning("nations: fixture list unavailable (%s)", exc)
        return False

    rows = []
    for match in matches:
        if str(match.get("status", "")).lower() == "finished":
            continue
        home, away = match.get("home_team") or {}, match.get("away_team") or {}
        if not home.get("name") or not away.get("name"):
            continue
        rows.append({
            "match_id": match.get("id"),
            "kickoff": match.get("utc_date") or "",
            "HomeTeam": home["name"],
            "AwayTeam": away["name"],
            "matchday": match.get("matchday"),
            "neutral": bool(match.get("is_neutral")),
        })
    frame = pd.DataFrame(rows, columns=["match_id", "kickoff", "HomeTeam",
                                        "AwayTeam", "matchday", "neutral"])
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    frame.sort_values("kickoff").to_csv(FIXTURES_CACHE, index=False)
    return True


def load_results(drop_neutral: bool = True) -> pd.DataFrame:
    """The pooled international results, indexed for the fitter."""
    if not RESULTS_CACHE.exists():
        return pd.DataFrame(columns=COLUMNS)
    frame = pd.read_csv(RESULTS_CACHE)
    if frame.empty:
        return frame
    frame["Date"] = pd.to_datetime(frame["Date"])
    if drop_neutral:
        frame = frame[~frame["neutral"].astype(bool)]
    frame = frame.dropna(subset=["FTHG", "FTAG"]).sort_values("Date")
    teams = sorted(set(frame["HomeTeam"]) | set(frame["AwayTeam"]))
    lookup = {name: i for i, name in enumerate(teams)}
    frame = frame.copy()
    frame["home_id"] = frame["HomeTeam"].map(lookup)
    frame["away_id"] = frame["AwayTeam"].map(lookup)
    frame.attrs["teams"] = teams
    return frame


def load_fixtures() -> pd.DataFrame:
    if not FIXTURES_CACHE.exists():
        return pd.DataFrame(columns=["match_id", "kickoff", "HomeTeam",
                                     "AwayTeam", "matchday", "neutral"])
    return pd.read_csv(FIXTURES_CACHE)


def fit(frame: pd.DataFrame | None = None, ref_date=None,
        xi: float | None = None, prior_sd: float | None = None):
    """One fit over the whole international pool."""
    frame = load_results() if frame is None else frame
    if frame.empty:
        return None
    teams = frame.attrs.get("teams") or sorted(
        set(frame["HomeTeam"]) | set(frame["AwayTeam"]))
    ref_date = ref_date or frame["Date"].max()
    return dixon_coles.fit_from_frame(
        frame, teams, ref_date=ref_date,
        xi=config.NATIONS_XI if xi is None else xi,
        prior_sd=config.NATIONS_PRIOR_SD if prior_sd is None else prior_sd)


def appearances(frame: pd.DataFrame, ref_date, xi: float) -> dict[str, float]:
    """
    Time-weighted matches behind each team, which is what "cold start" means
    for a national side.

    A club that has played six matches this season has six recent matches. A
    national team that has played six has them spread over two years, and the
    decay leaves it with far less than six. The page flags a team whose
    effective count is thin rather than printing a confident number over it.
    """
    weights = dixon_coles.time_weights(frame["Date"].to_numpy(), ref_date, xi)
    out: dict[str, float] = {}
    for home, away, weight in zip(frame["HomeTeam"], frame["AwayTeam"], weights):
        out[home] = out.get(home, 0.0) + float(weight)
        out[away] = out.get(away, 0.0) + float(weight)
    return out


def forecast(days_ahead: int | None = None, now: dt.datetime | None = None
             ) -> dict:
    """
    Every upcoming Nations League match we can price, with the model's
    probabilities and everything the page needs to qualify them.
    """
    days_ahead = config.NATIONS_LOOKAHEAD_DAYS if days_ahead is None else days_ahead
    now = now or dt.datetime.now(dt.timezone.utc)

    results = load_results()
    fixtures = load_fixtures()
    if results.empty or fixtures.empty:
        return {"available": False, "matches": [], "reason":
                "no international results are cached yet"}

    model = fit(results)
    if model is None or not model.converged:
        return {"available": False, "matches": [], "reason":
                "the model did not converge on the cached pool"}

    teams = results.attrs["teams"]
    index = {name: i for i, name in enumerate(teams)}
    ref_date = results["Date"].max()
    weighted = appearances(results, ref_date, config.NATIONS_XI)

    horizon = now + dt.timedelta(days=days_ahead)
    rows = []
    unpriceable = []
    for fixture in fixtures.itertuples():
        kickoff = str(fixture.kickoff)
        if not kickoff:
            continue
        when = dt.datetime.fromisoformat(kickoff.replace("Z", "+00:00"))
        if when < now or when > horizon:
            continue
        home, away = fixture.HomeTeam, fixture.AwayTeam
        if home not in index or away not in index:
            # A team with no international result in the pool cannot be
            # priced at all. Saying so beats printing league average and
            # calling it a forecast.
            unpriceable.append(f"{home} v {away}")
            continue
        hid, aid = index[home], index[away]
        probs = model.outcome_probs(hid, aid)
        xg_home, xg_away = model.expected_goals(hid, aid)
        over, under = model.totals_probs(hid, aid, line=config.TOTALS_LINE)
        btts_yes, btts_no = model.btts_probs(hid, aid)
        thin = [name for name in (home, away)
                if weighted.get(name, 0.0) < config.NATIONS_COLD_START]
        rows.append({
            "match_id": fixture.match_id,
            "kickoff": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "kickoff_label": when.strftime("%a %d %b · %H:%M"),
            "date_label": when.strftime("%A %d %B"),
            "matchday": fixture.matchday,
            "neutral": bool(fixture.neutral),
            "home": home,
            "away": away,
            "p_H": float(probs[0]), "p_D": float(probs[1]), "p_A": float(probs[2]),
            "pct_H": round(float(probs[0]) * 100),
            "pct_D": round(float(probs[1]) * 100),
            "pct_A": round(float(probs[2]) * 100),
            "p_over": float(over), "p_under": float(under),
            "pct_over": round(float(over) * 100),
            "pct_under": round(float(under) * 100),
            "p_btts_yes": float(btts_yes), "p_btts_no": float(btts_no),
            "pct_btts_yes": round(float(btts_yes) * 100),
            "pct_btts_no": round(float(btts_no) * 100),
            "xg_home": float(xg_home), "xg_away": float(xg_away),
            "favourite": "HDA"[int(np.argmax(probs))],
            "thin": thin,
        })

    rows.sort(key=lambda r: (r["kickoff"], r["home"]))
    days: list[dict] = []
    for row in rows:
        if not days or days[-1]["label"] != row["date_label"]:
            days.append({"label": row["date_label"], "matches": []})
        days[-1]["matches"].append(row)

    by_competition = (results.groupby("competition").size().sort_values(
        ascending=False).to_dict())

    return {
        "available": True,
        "matches": rows,
        "days": days,
        "unpriceable": unpriceable,
        "n_teams": len(teams),
        "n_train": len(results),
        "effective_n": round(float(model.effective_n), 1),
        "trained_through": ref_date.date().isoformat(),
        "home_advantage": round(float(model.gamma), 4),
        "rho": round(float(model.rho), 4),
        "league_mean_goals": round(float(model.league_mean), 4),
        "xi": config.NATIONS_XI,
        "half_life": int(round(np.log(2) / config.NATIONS_XI)),
        "prior_sd": config.NATIONS_PRIOR_SD,
        "pool": by_competition,
        "n_neutral_dropped": int(_neutral_count()),
        "lookahead_days": days_ahead,
    }


def _neutral_count() -> int:
    if not RESULTS_CACHE.exists():
        return 0
    frame = pd.read_csv(RESULTS_CACHE)
    return int(frame["neutral"].astype(bool).sum()) if not frame.empty else 0


def ratings(limit: int = 20) -> list[dict]:
    """The strongest sides by the fitted ratings — a sanity check, in public."""
    frame = load_results()
    if frame.empty:
        return []
    model = fit(frame)
    if model is None:
        return []
    table = model.ratings_table()
    # `ratings_table` returns multipliers, not log parameters: attack above 1
    # scores more than average, defence below 1 concedes less. Strength is
    # therefore the ratio, not the difference.
    rows = [{"team": team, "attack": float(attack), "defence": float(defence),
             "net": float(attack) / float(defence)}
            for team, attack, defence in zip(table["team"], table["attack"],
                                             table["defence"])]
    rows.sort(key=lambda r: r["net"], reverse=True)
    return rows[:limit]
