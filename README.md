# ProofOdds

Premier League match probabilities, **published before kickoff and scored afterwards**
against Pinnacle's closing line.

The model is a Dixon-Coles goals model. It does not beat the closing line — a
walk-forward backtest over nine seasons puts it about 0.017 nats per match behind —
and the site says so on the front page. That is the product: not a prediction service,
a measurement one. Anyone can publish probabilities; almost nobody publishes the score.

---

## What makes the record checkable

Three properties, each enforced by code rather than by promise:

**Sealed before kickoff.** Every run writes one JSON file into `predictions/`. A
prediction is never issued for a match that has already started, and a file for a
date that already exists is never modified.

**Chained.** Each entry carries the SHA-256 of the entry before it. Alter any past
prediction and every entry after it fails verification — visibly, on the public
ledger page. The subtle version of the attack (edit a prediction *and* recompute its
own hash) is caught by the link check, and there is a test for exactly that.

**Independently timestamped.** The ledger is committed and pushed to a public git
repository on every run. Commit timestamps are a witness we do not control.

```bash
git clone https://github.com/yourname/proofodds && cd proofodds
python -m proofodds.verify
```

Set that up with `sudo bash scripts/setup-git.sh git@github.com:USER/proofodds.git`.
It refuses to commit anything if `.env` is not ignored — a token pushed to a
public repository stays in the history for ever, and rotating it afterwards does
not remove it.

---

## Running it

```bash
pip install -r requirements.txt

python scripts/daily.py            # refresh results, seal, grade, rebuild
python scripts/daily.py --no-git   # same, without committing the ledger
python scripts/daily.py --build-only
python -m pytest tests -q          # 26 tests
python -m proofodds.verify         # recompute the whole chain
```

`scripts/replay.py` runs the same pipeline over historical matchdays into a
throwaway directory, so the machinery can be proved end to end before there is a
live record. **Its output is a backtest, not a track record** — those entries were
generated after the fact, and the script says so every time it runs. It writes to
`_replay/` and `site-preview/`, both git-ignored, and never touches `predictions/`.

---

## Deploying to a VPS

Tested on Debian 12 / Ubuntu 24.04 on Hetzner.

```bash
ssh root@your-server
git clone https://github.com/yourname/proofodds /opt/proofodds
sudo bash /opt/proofodds/scripts/bootstrap.sh
```

The script picks a web server instead of assuming one, which matters on a box
that already serves something:

| What it finds | What it does |
|---|---|
| nginx running | Adds **one** server block for proofodds.com. Every other vhost is untouched, and `nginx -t` runs before any reload — a bad config disables the new block rather than breaking live sites. |
| Nothing on :80 | Installs Caddy, which handles TLS by itself. |
| Something else on :80 | Stops and tells you, rather than fighting for the port. |

On the nginx path, finish with the certificate:

```bash
apt-get install -y certbot python3-certbot-nginx   # if not already there
certbot --nginx -d proofodds.com -d www.proofodds.com
```

Then point DNS at the server (`A` and `AAAA` on the apex, `CNAME www` to the
apex), put a free [football-data.org](https://www.football-data.org/) token in
`/opt/proofodds/.env`, and add a git remote with a deploy key so the ledger gets
pushed.

```bash
systemctl list-timers proofodds.timer
journalctl -u proofodds.service -f
```

Publishing is idempotent, so running the job often is safe: the frequency exists
for grading and rebuilding after matches finish, not for publishing.

---

## Cost

Phase 0 runs on the VPS plus a domain. Nothing else is required:

| | |
|---|---|
| Results and closing odds | [football-data.co.uk](https://www.football-data.co.uk/englandm.php) — free |
| Upcoming fixtures | football-data.org free tier, or `data/fixtures.csv` |
| Live pre-match odds | optional, not needed for grading |

---

## Layout

```
proofodds/
  config.py        settings, league list, tuned hyperparameters, the backtest prior
  data.py          download + cache football-data.co.uk, team-name normalisation
  dixon_coles.py   the model: tau, weighted likelihood with analytic gradient, fitting
  fixtures.py      upcoming fixtures (football-data.org, or a CSV fallback)
  ledger.py        seal predictions, hash chain, publication rules
  grade.py         join predictions to results, log loss vs the closing line
  charts.py        inline SVG, themed through CSS custom properties
  render.py        Jinja2 -> static site
  verify.py        `python -m proofodds.verify`
templates/         base, index, scorecard, ledger, method
static/style.css   one stylesheet, light and dark
scripts/           daily.py, replay.py, bootstrap.sh, setup-git.sh
deploy/            nginx server block, Caddyfile, systemd unit + timer, .env.example
predictions/       the ledger — committed, never rewritten
tests/             the chain, the publication rules, data handling
```

---

## The model, briefly

Each team carries an attack and a defence rating; there is one league-wide home
advantage. Expected goals are `α_home × β_away × γ × league_mean` and
`α_away × β_home × league_mean`, fed into two Poisson distributions and multiplied
into a grid of scorelines. Dixon and Coles (1997) add a correction for the four
low-scoring cells — 0-0 and 1-1 happen more than independence predicts, 1-0 and 0-1
less — and an exponential time decay, here ξ = 0.002/day, a half-life of 347 days.

Ratings are estimated by maximum penalised likelihood. The likelihood supplies an
analytic gradient, which is what makes refitting on every match date cheap enough to
do for real. A Gaussian prior on the ratings pins the attack/defence shift degeneracy
and gives promoted teams a sensible starting point at league average.

The decay rate and prior width were tuned on 2017/18–2020/21 and then frozen. Full
research repository, including the walk-forward backtest and its 3,250-match record:
see `config.BACKTEST["repo"]`.

## Limitations

Goals only — no shots, no expected goals, no lineups. The model has never heard of a
suspension, which is most of why the closing line beats it. Promoted teams start at
league average and their first weeks are the model's worst. De-vigging is
proportional, the simplest method, which slightly overstates longshots and if
anything flatters the model. Matches without closing odds stay out of the score
rather than being quietly counted.

## Not betting advice

Statistical forecasts, published for information. 18+.
If gambling stops being fun, [BeGambleAware](https://www.begambleaware.org/).
