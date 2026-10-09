# What the rebuild of 9 October 2026 changed

The decision behind this is in `docs/DECISION.md`: test verification for
people who publish picks, for 60 days, and keep the house ledger running as
the working example.

## Built

| What | Where | Why |
|---|---|---|
| Self-serve sealing | `proofodds/sealapi.py`, `templates/seal.html`, `/seal/`, `deploy/proofodds-seal.service` | A guest could only seal by messaging the operator. Nobody would do that twice. |
| Invite tokens | `python -m proofodds.guest invite / revoke` | One record per real person, and no open write access to the server. |
| Reveal at kickoff | `guest.seal(reveal="kickoff")`, `guest.release_due`, `deploy/proofodds-release.timer` | Someone who sells picks cannot publish them before the match. Without this the product excluded its own buyers. |
| Late-seal check | `guest._sealed_late`, `guest_data.kickoff_utc` | The kickoff in an entry is typed by the guest. Before this, an entry sealed at half-time with a false kickoff would have been graded as genuine. |
| Record badge | `render.guest_badge`, `/guests/<slug>/badge.svg` | The thing a guest puts on their own channel, which is also how other people find ProofOdds. Shows no CLV figure below 30 graded entries. |
| Alerts | `proofodds/alert.py`, `PROOFODDS_ALERT_URL` | A failed run or a dead fixture feed now sends a push notification. Before, both wrote to files on the server. |
| Privacy terms for records | `templates/privacy.html` | A permanent public record needs to say so before the first entry. |

## Fixed

| What | Detail |
|---|---|
| The verifier skipped every guest entry | `verify.is_entry` required a `predictions` field, which guest entries do not have. `python proofodds/verify.py guests/<slug>` reported an empty chain as sound. It now counts and checks them. |
| The failure alarm was never installed | `proofodds-failed@.service` was in `deploy/` but not in `/etc/systemd/system/`, and the installed `proofodds.service` had no `OnFailure=` line. A failed run alerted nobody and logged nothing. Both are installed now. |
| The site ran on uncommitted code | The Nations League page had been live since 24 September from files that were not in the repository. Committed. |

## Removed

| What | Why |
|---|---|
| 14 `.bak` files, `deploy.bak/`, `scripts.bak/`, `site-preview/`, `_replay/` | Untracked leftovers from August. Nothing read them. |
| "No charge and no plan to introduce one" on `/referee/` | No longer true. Replaced with what is true: records opened in 2026 stay free, a paid plan is planned, nothing is charged today. |
| "Deliberately manual" on `/referee/` | No longer true. |

## Kept, deliberately

| What | Why |
|---|---|
| The house model and its ledger, all 23 divisions | It costs nothing to run, it is the public example of a record that shows its losses, and stopping it would end the only out-of-sample evidence there is. It is not the product. |
| The scorecard, the method page, the three tags, the cohort split | They report results already published. Nothing was removed or reworded. |
| The weekly email | Three subscribers signed up for the scorecard and keep getting exactly that. |
| `proofodds/statsapi.py` | The brief called it dead. It is not: the Nations League page calls it on every run. |
| `deploy/Caddyfile` | `scripts/bootstrap.sh` installs from it. |
| Python, Jinja, nginx, systemd | Nothing about the test needs a different stack. |
| Open source | The offer is "check it yourself". |

## Not done

- No new model. See `docs/DECISION.md` section 3.
- No paid data source. See section 5.
- No payment integration. It waits on Stripe's answer and a lawyer.
- No end-to-end seal on the live server. Sealing a made-up guest would put a
  fabricated record in the public repository. The path was exercised in
  tests and in a scratch build; the first real token is the first live run.
- Nothing outside `/opt/proofodds`, apart from the nginx site file and the
  systemd units. `/opt/proofodds-refresh` and `/opt/pl-dixon-coles` are old
  copies and were left alone.
