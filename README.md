# ProofOdds

Football match probabilities for eight European divisions, **published before kickoff
and scored afterwards** against Pinnacle's closing line.

| | | |
|---|---|---|
| `E0` Premier League | `SP1` La Liga | `D1` Bundesliga |
| `E1` Championship | `I1` Serie A | `F1` Ligue 1 |
| `N1` Eredivisie | | `P1` Primeira Liga |

Two markets are published for every match — the result, and whether it goes over 2.5
goals — both out of one fitted model and both graded against Pinnacle's closing price.
Individual match pages also reconstruct the three most likely scorelines from the
sealed expected goals and league low-score correction. They are labelled as indicative
and excluded from the scorecard: without a closing-line comparison, they are context,
not a third benchmarked claim.

One Dixon-Coles model is fitted per division, on that division's matches only. It does
not beat the closing line — a walk-forward backtest over nine Premier League seasons
puts it about 0.017 nats per match behind — and the site says so on the front page.
That is the product: not a prediction service, a measurement one. Anyone can publish
probabilities; almost nobody publishes the score.

Eight divisions rather than one is a statistical decision before it is a product one.
A season of the Premier League is 380 matches; the margin of error on the model-minus-
market gap at that sample is wider than the gap itself, so a single-league scorecard
cannot say anything for years. Eight divisions is roughly 2,700 matches a season, which
brings the answer inside one.

---

## What makes the record checkable

Layers of evidence, each with a deliberately limited claim:

**Sealed before kickoff.** Every run writes one JSON file into `predictions/`. A
prediction is never issued for a match that has already started, and a file for a
date that already exists is never modified.

**Chained.** Each entry carries the SHA-256 of the entry before it, so altering one
prediction without rebuilding every later hash fails verification — visibly, on the
public ledger page. The subtle version of the attack (edit a prediction *and* recompute
its own hash) is caught by the link check, and there is a test for exactly that.

**Publicly observable.** Every run commits and pushes the ledger to a public
repository. Rebuilding the chain to hide a change therefore means rewriting every later
file and force-pushing, which anyone who cloned it earlier can see.

**Generator identified.** New entries carry the git commit of the code that produced
their probabilities, a dirty-working-tree flag, and a SHA-256 digest of the exact source
files used by the generator. The commit is the readable reference; the source digest
still identifies the bytes if the deployed tree had local changes.

**Externally timestamped, from the first submitted entry onward.** The daily job submits
each new entry to OpenTimestamps and keeps upgrading its detached proof until it has a
Bitcoin block attestation. A failed submission remains a visible gap. The ledger page
distinguishes older chain-only entries, pending submissions, matching proofs that contain
a block attestation, and any mismatch. It never stamps an old entry later and presents
that as contemporaneous evidence.

The distinction matters. The chain proves order and internal consistency. Git makes the
history observable, but its dates are settings and its owner can rewrite it. An
independently verified `.ots` file proves that the exact JSON bytes existed before the
Bitcoin block named in the proof. A pending file is shown only as submitted. The site can
inspect and match an attestation to its JSON; full independent Bitcoin verification
requires a Bitcoin node.

```bash
git clone https://github.com/GitSimaao/proofodds && cd proofodds
python -m proofodds.verify
```

That command needs nothing installed — `proofodds/verify.py` imports only the
standard library, and rewrites the hashing rather than calling the code that
wrote it. Our code checking our code could agree with itself while both were
wrong; a separate implementation, short enough to read in one sitting, cannot.
A test asserts the two agree on every sealed entry, and another asserts the
file never grows a dependency.

Set that up with `sudo bash scripts/setup-git.sh git@github.com:USER/proofodds.git`.
It refuses to commit anything if `.env` is not ignored — a token pushed to a
public repository stays in the history for ever, and rotating it afterwards does
not remove it.

---

## Running it

```bash
pip install -r requirements.txt

# The results CSVs are a download, not part of this repo — they are
# football-data.co.uk's data, not ours. Tests marked needs_data skip with
# instructions until you run this; the rest of the suite still runs.
python -c "from proofodds import data; data.refresh('E0')"

python scripts/daily.py            # refresh results, seal, grade, rebuild
python scripts/daily.py --no-git   # same, without committing the ledger
python scripts/daily.py --build-only
python scripts/weekly.py           # DRY RUN of the Monday email — prints, sends nothing
python scripts/weekly.py --send    # actually schedules the broadcast
python -m proofodds.anchor         # retry/upgrade timestamp proofs, print their status
python -m pytest tests -q          # data-marked tests skip until the CSVs are downloaded
python scripts/check_names.py      # audit club names before adding a division
python -m proofodds.verify         # recompute the whole chain (no deps)
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

## Adding a division

Two feeds, two spellings, one join. Results come from football-data.co.uk, which
writes `Ein Frankfurt` and `M'gladbach` and `Ath Madrid`. Fixtures come from
football-data.org, which writes `Eintracht Frankfurt` and `Borussia Mönchengladbach`
and `Club Atlético de Madrid`. Every prediction is keyed on a club name, and a name
that does not join is a prediction that is published, hashed, and then never scored.

With one division that risk was a hand-written list of twenty clubs. With seven it is
about a hundred and forty, changing every summer with promotion — a list nobody
maintains. So there is no list. The canonical set of names for a division is whatever
appears in that division's own results files, and a feed name is resolved onto it in
stages: exact, then an explicit override, then the same name with the club-type words
folded away, then an abbreviation rule (`Ein` opens `Eintracht`, `Ath` opens
`Atlético`), then similarity. When two candidates are plausible — `Milan` opens
`Milano`, so Internazionale looks like AC Milan — it returns nothing rather than
picking one.

Two things make a mistake survivable. Unresolved names still get published, under a
readable form of the feed's own spelling, **with the raw feed name sealed alongside**;
grading tries both, so one line in `data.OVERRIDES` grades every affected prediction
retroactively, without touching a single ledger file. And each entry records the
divisions it could not fit, so an entry always says what it does not contain.

Before turning a division on:

```bash
python scripts/check_names.py --leagues P1
```

It prints every club the fixture feed will send for the next 90 days, the spelling the
results file uses, and which rule connected them — then exits non-zero if anything is
unresolved. Add the division to `PROOFODDS_LEAGUES` once it is clean.

```bash
PROOFODDS_LEAGUES=E0,E1,SP1,I1,D1,F1,P1,N1
```

The 157 club names of the 2025/26 season are checked in `tests/league_names.py` and
asserted on every test run, so a change to the resolver cannot quietly break a
division that used to work.

---

## The weekly email

The list exists to turn a one-day traffic spike into an audience. What it sends
is the scorecard — matches graded, our log loss, the closing line's, and the gap
— every Monday, whatever the gap says. A newsletter you could only send in a
good week is a newsletter you would eventually fake, so this one has no good and
bad weeks, only weeks.

Delivery is [Kit](https://kit.com) (free to 10,000 subscribers, unlimited
broadcasts, real API). Two environment variables turn it on:

```bash
PROOFODDS_SIGNUP_ACTION=https://app.kit.com/forms/<id>/subscriptions
PROOFODDS_KIT_API_KEY=<v4 api key>
```

Without `SIGNUP_ACTION` the signup box does not render at all — better no form
than one that swallows addresses into nothing. Without `KIT_API_KEY` the weekly
timer stays disabled rather than failing every Monday.

Three guards, because an email cannot be unsent: `weekly.py` is a **dry run by
default**, a week already sent is never sent twice, and a week with no graded
matches produces silence instead of an empty email. The broadcast is scheduled
15 minutes out, so there is still time to kill it in Kit.

```bash
systemctl enable --now proofodds-weekly.timer   # Mondays 09:00 UTC
```

Turn on double opt-in in the Kit form settings. It is not strictly required by
the GDPR but it protects the list from forged signups and it materially improves
deliverability.

---

## Cost

Phase 0 runs on the VPS plus a domain. Nothing else is required:

| | |
|---|---|
| Results and closing odds | [football-data.co.uk](https://www.football-data.co.uk/) — currently used for all eight divisions |
| Upcoming fixtures | football-data.org — currently used for all eight divisions |
| External timestamps | OpenTimestamps — free, no account or API key |
| Live pre-match odds | optional, not needed for grading |

The current data setup is for the pre-launch project. Before charging for any
product built on it, confirm the commercial-use terms or paid plan for each
feed; neither this table nor the fact that an endpoint is accessible grants a
commercial licence.

---

## Layout

```
proofodds/
  config.py        settings, league list, tuned hyperparameters, the backtest prior
  data.py          download + cache football-data.co.uk, club-name resolution
  crests.py        validated, display-only football-data.org crest URL cache
  dixon_coles.py   the model: tau, weighted likelihood with analytic gradient, fitting
  fixtures.py      upcoming fixtures (football-data.org, or a CSV fallback)
  ledger.py        seal predictions, hash chain, publication rules
  anchor.py        submit, upgrade and report detached OpenTimestamps proofs
  grade.py         join predictions to results, log loss vs the closing line
  charts.py        inline SVG, themed through CSS custom properties
  render.py        Jinja2 -> static site
  verify.py        `python -m proofodds.verify`
  newsletter.py    the weekly scorecard email and the Kit client
templates/         base, index, per-match pages, scorecard, ledger, method, privacy
static/style.css   one stylesheet, light and dark
static/logo.svg    the PO mark used by the header, footer, favicon and web app
static/flags/      self-hosted country flags used by division filters
scripts/           daily.py, weekly.py, sync_crests.py, replay.py, bootstrap.sh, setup-git.sh
deploy/            nginx server block, Caddyfile, systemd unit + timer, .env.example
predictions/       the ledger — committed, never rewritten
timestamps/        detached .ots proofs — pending, attested and mismatched stay distinct
tests/             the chain, the publication rules, data handling
```

### Club crests

Cards and match pages use the crest URL football-data.org returns with each
team. The URLs are cached in ignored `data/club_crests.json`: they are display
metadata, never part of a sealed prediction, so a provider image changing
cannot change the ledger. Populate every enabled division without publishing
anything:

```bash
python scripts/sync_crests.py
```

The normal fixture fetch also refreshes the clubs it sees, so promoted teams
pick up a mark automatically. Only HTTPS URLs on
`crests.football-data.org` are accepted. A missing or rejected URL falls back
to the deterministic two-letter mark rather than breaking a card.

A deliberately self-hosted crest at `static/clubs/<canonical-slug>.svg` (then
`.png` or `.webp`) always takes precedence over the provider URL. This is useful
when the project later obtains an explicitly licensed asset pack.

Club crests remain protected marks. API access and image availability do not by
themselves grant redistribution or commercial rights; confirm the chosen
football-data.org plan and the clubs' mark rules before monetising this display.

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

The two markets are not comparable by their gaps. A 1X2 closing line holds about
0.150 nats of knowledge over guessing; a closing total holds about 0.020. There is
roughly seven times less to know about goals, so any model sits closer to the market
there — the scorecard reports the share of what was available in each, which is the
only honest comparison. The closing total is also published only from 2019/20, four
seasons less history than the result market.

Goals only — no shots, no expected goals, no lineups. The model has never heard of a
suspension, which is most of why the closing line beats it. Promoted teams start at
league average and their first weeks are the model's worst. De-vigging is
proportional, the simplest method, which slightly overstates longshots and if
anything flatters the model. Matches without closing odds stay out of the score
rather than being quietly counted.

## Not betting advice

Statistical forecasts, published for information. 18+.
If gambling stops being fun, [BeGambleAware](https://www.begambleaware.org/).
