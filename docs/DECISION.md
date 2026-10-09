# ProofOdds: the business decision of 9 October 2026

Written before the rebuild, so the reasoning is on record. Every number in
"What was measured" was read from this server or from Kit on 9 October 2026.
Everything under "Assumption" is a guess and is labelled as one.

## 1. What was measured

### Traffic

nginx keeps 14 daily rotations (`/etc/logrotate.d/nginx`, `rotate 14`). The
logs therefore start on **25 September**. The launch on 8 September and the
sixteen days after it are gone and cannot be recovered. The numbers below
are for 25 September to 9 October only (15 days, 12,523 requests).

How bots were excluded: a visitor counts as a person only if (a) the user
agent is a browser and matches none of about 70 crawler, library and scanner
signatures, (b) the same IP also fetched at least one stylesheet, image or
font, which crawlers that only read HTML do not, and (c) the IP was not
mostly producing 404s and made fewer than 300 page requests. This is an
**upper bound**: headless browsers that load assets pass it, and so does
Simão.

| Measure | Value |
|---|---|
| Distinct IPs, all traffic | 2,582 |
| IPs with a browser user agent | 1,362 |
| IPs passing all three tests ("people") | 246 |
| People per day | 5 to 20, typically 10 to 15 |
| People per ISO week | wk 39 (3 days): 40 · wk 40: 75 · wk 41 (5 days): 43 |
| Seen on two or more days | 36 |
| Page views by people | 762, of which 350 are match cards (54 people) and 180 the home page (96 people) |
| `/method/` | 20 people |
| `/scorecard/` | 16 people |
| `/ledger/` | 16 people |
| `/nations-league/` | 15 people |
| `/referee/` | 12 people |
| `/data/` | 5 people |
| Referrers | 122 people with none; Google 5; one each from Yandex, ChatGPT and two others |
| Newsletter form submissions through the site | 0 hits on `/subscribed/` or `/confirmed/` |

Roughly 90% of requests are crawlers: SEO bots (Semrush, Ahrefs,
DataForSeo, SERanking), AI crawlers (GPTBot, ChatGPT-User), and scrapers
with faked browser agents.

### Newsletter (Kit, free plan)

| Measure | Value |
|---|---|
| Active subscribers | 3 |
| When they joined | 2 on 26 August (before launch), 1 on 13 September |
| Unsubscribed, bounced, complained | 0 |
| Broadcasts sent | 5 (7, 14, 21, 28 September; 5 October) |
| Opens per send | 2, 2, 1, 1, 1 |
| Clicks | 3 on each of two sends, from one recipient |

### Costs

| Item | Cost |
|---|---|
| Hetzner cloud server, 2 vCPU / 4 GB / 40 GB, Nuremberg | About €4 to €5 a month for this size. Not read from an invoice: Hetzner's price page did not render a figure. Check the invoice. |
| The same server also runs | a CTFd instance (4 containers), packetlab.blog, a Discord bot. None is ProofOdds. |
| Kit | Free plan |
| football-data.org | Free tier token |
| TheStatsAPI | **Active.** The key in `.env` returned data on 9 October: 702 requests used this month, all from the Nations League page. The 7-day trial began about 2 September, so this is either a free tier or a plan that is being billed. Only the account page can say which. |
| Odds provider | `PROOFODDS_ODDS_PROVIDER=none`, no key |
| Domain | Not visible from this machine |

Disk is at 90% (32 of 38 GB), mostly not ProofOdds: `/root/.cache` is 3 GB.

### Used and unused

Used: the ledger, the anchor, the grader, the site build, the weekly email.

Unused: `/referee/` (no guest, ever), `deploy/Caddyfile` (nginx serves the site),
fourteen `.bak` files and three `.bak` directories, `site-preview/`,
`_replay/`, `/opt/proofodds-refresh` and `/opt/pl-dixon-coles` (older copies
outside the repository).

Found during the audit, and different from the brief:

- The Nations League page (`proofodds/nations.py`, `templates/nations.html`,
  303 changed lines in six tracked files) has been **live since 24 September
  from uncommitted code**. The site says every build is reproducible from
  the public repository. For two weeks that was not true.
- `proofodds/statsapi.py` is **not** dead. That page imports it and calls
  TheStatsAPI on every run.

## 2. The decision

**No candidate business is supported by these numbers.** Ten to fifteen
visitors a day, three subscribers of whom two predate the launch, and no
user of the one feature aimed at other people: nothing here shows demand
for anything.

The honest version of "choose the business" is therefore "choose the one
test worth running". That test is **verification for people who publish
picks or forecasts**, run for 60 days, with demand created by direct
outreach rather than by waiting for traffic.

### Why this one

It is the only candidate whose customers can be named and contacted one by
one. Tipsters and model-builders are public by definition: they have
Telegram channels, X accounts, Substacks, Discord servers. A solo founder
can put the offer in front of 150 of them in 60 days. None of the other
candidates has that property: they all wait on an audience that does not
exist yet.

It also uses the one thing here that is rare. The model is not rare, and it
loses. A sealed, timestamped record that the publisher cannot edit is rare
in this industry.

### Who pays, and why not the free alternative

The buyer is a person who sells picks and whose prospects do not believe
their record. The free alternatives are Blogabet and Tipstrr. Both verify
records, and both are marketplaces: the record lives in their database, the
audience is theirs, and they take a share of the tipster's sales.

What is different here:

1. The record can be checked by a stranger without trusting ProofOdds:
   hash chain, Bitcoin timestamp, a verifier anyone can run.
2. The tipster keeps their own audience and their own payments. ProofOdds
   is a record, not a marketplace.
3. Picks can be sealed now and revealed at kickoff, so a paid pick is not
   leaked to non-payers. Built in this rebuild; it did not exist before.
4. In the UK the advertising code requires a tipster to hold documentary
   evidence for any profit claim, and ASA rulings have turned on whether
   tips were "proofed" by a robust system. A sealed record is that evidence.

Points 1 and 2 are real but may not matter to buyers. That is what the test
is for.

### Price

- **Free:** a public sealed record, unlimited entries, graded against the
  closing line. Records opened during the test stay free permanently.
- **Planned, not charged today:** €12 a month for commercial sellers
  (badge and embed for their own channels, reveal-at-kickoff, a page without
  ProofOdds' own forecasts on it).

Nothing is charged until a payment route exists (see section 5) and until at
least one user has said they would pay.

### Revenue arithmetic

| Step | Number | Basis |
|---|---|---|
| Inbound interest | 12 people visited `/referee/` in 15 days, about 24 a month | Measured |
| Inbound sign-ups | 0 in a month | Measured |
| Outbound contacts in 60 days | 150 | Assumption: 4 per working day |
| Reply rate | 10%, so 15 conversations | Assumption, unmeasured |
| Start sealing | 3% of contacts, so about 5 records | Assumption, unmeasured |
| Would pay €12 | 30% of those, so 1 or 2 | Assumption, unmeasured |
| Revenue after 60 days | €12 to €24 a month | Follows from the above |
| To cover a €100 budget | 9 paying users | Arithmetic |
| To pay one salary (€2,000) | about 170 paying users | Arithmetic |

On these assumptions the test produces pocket money, and a living needs
either thousands of contacts or an inbound channel that does not exist. This
is not a plan that reaches a salary within a year. It is a cheap test of
whether anyone wants the product at all, at a running cost of about €5 a
month plus Simão's time.

### What proves it wrong by 8 December 2026

Stop if either is true on 8 December:

- fewer than **5 people outside the project** have each sealed 10 or more
  entries; or
- none of them, asked directly, says they would pay €12 a month.

If it stops: freeze the ledger, keep the site as a static archive with the
scorecard and every sealed entry intact, switch the timers off. Cost falls
to the server alone. The code remains a public, working reference for
sealed forecasting, which has value to Simão as a portfolio piece and none
as a business.

## 3. The alternatives, and why not

### A model that beats the close

Buyer: bettors. They would pay only for forecasts better than the closing
line, which is free to anyone who removes the margin.

The current model is 0.0374 nats behind after 599 matches. Detecting a gap
needs a sample that grows with the inverse square of the gap: the present
interval (±0.015 at 599 matches) implies about 1,400 matches to confirm an
edge of 0.01 nats and about 15,000 to confirm 0.003. A real edge for a
public-data model is more likely the second size than the first. At about
600 graded matches a month that is two years of sealing before a claim
could honestly be made.

Selling picks is also the most regulated version of this: it is what the UK
advertising rules on tipsters are written for, and the closest to Stripe's
prohibited category.

Not chosen as the business. Not forbidden as research: the ledger keeps
running, so a better model can be dropped in and will be measured the same
way.

### A newsletter or content audience

Buyer: advertisers or affiliates. Three subscribers after a month, one of
whom opens. In betting, the money per subscriber comes from operator
affiliate deals, which in Portugal are limited to SRIJ-licensed operators
and which would put a bookmaker's logo beside a site whose point is that it
sells nothing. Not chosen.

### Data or editorial for media

Buyer: publishers. Five people visited `/data/` in 15 days. Media buy match
data from Opta and similar. A model that loses to the market is not an
editorial asset. Not chosen.

## 4. Regulation and practical limits

This is a summary for a lawyer to correct, not legal advice.

| Topic | Position | Needs a lawyer |
|---|---|---|
| Portugal, SRIJ (Decreto-Lei 66/2015) | Licensing applies to operators that take bets. A record-keeping service takes none. | Yes: confirm that publishing other people's picks is not promotion of gambling under the Código da Publicidade, art. 21. |
| UK Gambling Commission | Licenses operators. Tipsters and their tools are outside it. | No |
| UK advertising (CAP Code, ASA) | Applies to the tipster's marketing, and to ours. Profit claims need documentary evidence; no implying profit is likely; no cherry-picked periods. The record pages show closing-line value and flat-stake results over the whole chain, which is consistent with that. | Yes, once: review the wording of the record page and badge. |
| Stripe | Stripe's Portugal list prohibits "sports forecasting or odds-making with a monetary or material prize". A record-keeping subscription offers no prize, but the category is adjacent and accounts in adjacent categories get closed. | Ask Stripe in writing before taking a payment. |
| VAT | Selling a €12 digital subscription to consumers across the EU and UK means VAT in each buyer's country. A merchant of record (Paddle, Lemon Squeezy) handles that; each has its own restricted list. | Accountant |
| GDPR | A tipster's name, handle and email are personal data. Records are public and permanent by design, which conflicts with the right to erasure unless the user agreed to it clearly beforehand. | Yes: this is the most important one. The terms must say, before the first entry, that entries cannot be deleted, and what can be done instead (the display name can be removed; the hashes cannot). |
| Age and responsible gambling | The footer already carries 18+ and a BeGambleAware link. No operator is advertised and there are no affiliate links. | No |

## 5. Data sources

The fixture feed that went stale is `football-data.co.uk/fixtures.csv`, the
only fixture source for 14 of 23 divisions. Prices below were read on 9
October 2026.

| Source | Price a month | Coverage | Limits | Odds | Licence terms seen |
|---|---|---|---|---|---|
| football-data.org Free (in use) | €0 | 12 competitions, 9 of our 23 divisions | 10 calls/min | None | Not stated on the pricing page |
| football-data.org Standard | €49 | 30 competitions; includes League One, League Two, Segunda, Serie B, 2. Bundesliga, Ligue 2, Belgium, Turkey, Greece, Scottish Premiership. The Scottish Championship, League One and League Two are **not** on the coverage page. | 60 calls/min | Add-on €15: pre-match 1X2 only | Not stated |
| Sportmonks Starter / Growth | €29 / €99 | 5 / 30 leagues of your choice | 2,000 to 2,500 calls per entity per hour | Add-on €15; "premium" feed from €129 | Not on the pricing page |
| The Odds API 20K | $30 | Major soccer leagues; lower divisions not confirmed | 20,000 credits | 1X2, totals, spreads; Pinnacle listed; historical snapshots listed | Not on the pricing page |
| API-Football | Not verified: the pricing page refused automated access. Third-party pages disagree (from $19 or €19). | Claimed: all leagues on every plan | Claimed: 7,500 calls/day on the first paid plan | Claimed: included | Unknown |
| TheStatsAPI (in use for internationals) | Not verified; see the account | International matches in use; club coverage not evaluated | Observed: 10,000 requests a month, 12 a minute | Has an odds endpoint; closing-price coverage not evaluated | Unknown |

**Recommendation: buy nothing now.**

- The product being tested, sealing other people's picks, does not use a
  fixture feed. It needs results and closing prices, which come from
  football-data.co.uk's season files. Those are current; only
  `fixtures.csv` is stale.
- The fixture feed matters only to the house model, which loses to the
  market in every cohort. €49 a month to keep fourteen losing divisions
  sealing on time is not a good use of a €50 to €150 budget.
- If the test passes on 8 December, the purchase to make is
  **football-data.org Standard at €49** (variable
  `PROOFODDS_FDORG_TOKEN`, already wired: the same code path, a wider plan),
  because it needs no new integration. Check API-Football's real price by
  hand first; if it is near €19 with every league, it is the better buy and
  needs a new fetcher.
- No source above is confirmed to supply **closing** odds for Asian handicap
  or totals in lower divisions. football-data.co.uk remains the only
  benchmark for those, free, and a single point of failure. That risk is
  accepted for the test and is now alerted on.

## 6. What the rebuild does

See `docs/REBUILD.md` for the list of what was built, removed and replaced.
