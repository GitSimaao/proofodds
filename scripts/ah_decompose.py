#!/usr/bin/env python3
"""
Why the Asian handicap line on the scorecard reads the way it does. Rerun it;
don't take our word.

    python scripts/ah_decompose.py

The handicap is largely the result market re-sliced — on the completed
2025/26 Premier League file 71.6% of main lines sit at 0.75 or closer, and at
0.00 the handicap IS draw-no-bet. So it is worth explaining rather than
shrugging at that the model scores WORSE than a coin flip on it while
capturing most of what the closing line knows on the result.

This script decomposes the live graded handicap sample by line size, by
division, and by what it actually rests on: the AHCh histogram as graded, how
many fixtures never reached the handicap score and why, how many legs were
dropped as pushes, and the effective weighted sample behind the published
match count. It then tests the two mechanical explanations against each other
— no room in the benchmark, versus push exclusion selecting the sample — and
measures how far the three scored scorecard lines are three separate
measurements at all.

It READS. It writes nothing, changes no model, no ladder, no grading, no tag,
and never touches predictions/.
"""
from __future__ import annotations
import collections, math, sys
from pathlib import Path
import numpy as np, pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from proofodds import grade, config
from proofodds.grade import paired_interval

LN2 = math.log(2)
Z = grade.Z95


def per_unit(sub):
    w = sub["ah_weight"].to_numpy(float)
    d = np.divide((sub["ah_model_loss"] - sub["ah_market_loss"]).to_numpy(float),
                  np.where(w > 0, w, np.nan))
    keep = np.isfinite(d)
    return d[keep], w[keep]


def wmean_se(sub):
    d, w = per_unit(sub)
    iv = paired_interval(d, weights=w)
    return iv["mean"], iv["se"], int(len(d)), float(w.sum())


pd.set_option("display.width", 200)


def fmt(x, nd=4):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.{nd}f}"


def legs_for(line: float):
    return [line] if int(round(line * 4)) % 2 == 0 else [np.floor(line * 2) / 2,
                                                         np.ceil(line * 2) / 2]


def bucket_stats(sub: pd.DataFrame) -> dict:
    """Stake-weighted log losses and the paired interval, exactly as ah_scorecard does."""
    w = sub["ah_weight"].to_numpy(float)
    active = float(w.sum())
    if active <= 0:
        return {"n": len(sub), "weight": 0.0}
    model = float(sub["ah_model_loss"].sum() / active)
    market = float(sub["ah_market_loss"].sum() / active)
    per_unit = np.divide((sub["ah_model_loss"] - sub["ah_market_loss"]).to_numpy(float),
                         np.where(w > 0, w, np.nan))
    iv = paired_interval(per_unit, weights=w[np.isfinite(per_unit)])
    return {"n": len(sub), "weight": active, "model": model, "market": market,
            "gap": model - market, "se": iv["se"], "ci_low": iv["ci_low"],
            "ci_high": iv["ci_high"], "separated": iv["separated"],
            "available": LN2 - market, "eff_rows": int(np.isfinite(per_unit).sum())}


def print_table(title, rows, label_w=22):
    print(f"\n{title}")
    head = (f"{'':<{label_w}} {'matches':>8} {'weight':>8} {'model':>8} {'market':>8} "
            f"{'gap':>9} {'±95%':>9} {'ln2-market':>11} {'sep':>5}")
    print(head)
    print("-" * len(head))
    for label, s in rows:
        if not s.get("weight"):
            print(f"{label:<{label_w}} {s['n']:>8} {0.0:>8.2f} {'—':>8} {'—':>8} {'—':>9} {'—':>9} {'—':>11} {'—':>5}")
            continue
        half = (s["ci_high"] - s["ci_low"]) / 2 if s["ci_low"] is not None else None
        print(f"{label:<{label_w}} {s['n']:>8} {s['weight']:>8.2f} {s['model']:>8.4f} "
              f"{s['market']:>8.4f} {s['gap']:>+9.4f} {('±'+fmt(half)) if half else '—':>9} "
              f"{s['available']:>+11.4f} {('yes' if s['separated'] else 'no'):>5}")


def main():
    graded = grade.graded_frame()
    ah = graded[graded["ah_graded"]].copy()
    ah["abs_line"] = ah["AHCh"].abs()

    print("=" * 78)
    print("TASK 1 — ASIAN HANDICAP DECOMPOSITION  (measurement only, nothing changed)")
    print("=" * 78)
    print(f"ln 2 = {LN2:.6f}   (a coin flip; the AH benchmark)")

    # ---------------------------------------------------------------- whole set
    whole = bucket_stats(ah)
    print_table("WHOLE GRADED AH SET", [("all lines", whole)])
    print(f"\n  rows with ah_graded = True : {len(ah)}")
    print(f"  effective weighted sample  : {whole['weight']:.2f} settled unit stakes")
    print(f"  rows contributing zero wgt : {int((ah['ah_weight'] <= 0).sum())} (every leg pushed)")
    print(f"  published scorecard says   : ah_scorecard n = {grade.ah_scorecard(graded)['n']}, "
          f"stake_equiv = {grade.ah_scorecard(graded)['stake_equiv']:.2f}")

    # ------------------------------------------------------------- line buckets
    def bucket_of(x):
        if x <= 0.25 + 1e-9: return "|line| <= 0.25"
        if x <= 0.75 + 1e-9: return "0.50 to 0.75"
        return "|line| >= 1.00"
    ah["bucket"] = ah["abs_line"].map(bucket_of)
    order = ["|line| <= 0.25", "0.50 to 0.75", "|line| >= 1.00"]
    print_table("BY LINE SIZE", [(b, bucket_stats(ah[ah["bucket"] == b])) for b in order])

    # Share of the total loss each bucket contributes, in nats of stake.
    print("\nWHERE THE LOSS SITS  (total excess nats = sum(model - market) over the bucket)")
    print(f"{'':<22} {'excess nats':>12} {'share of total':>15} {'weight share':>13}")
    tot_excess = float((ah["ah_model_loss"] - ah["ah_market_loss"]).sum())
    tot_w = float(ah["ah_weight"].sum())
    for b in order:
        s = ah[ah["bucket"] == b]
        ex = float((s["ah_model_loss"] - s["ah_market_loss"]).sum())
        print(f"{b:<22} {ex:>+12.4f} {(ex/tot_excess*100 if tot_excess else float('nan')):>14.1f}% "
              f"{(float(s['ah_weight'].sum())/tot_w*100):>12.1f}%")
    print(f"{'TOTAL':<22} {tot_excess:>+12.4f} {100.0:>14.1f}% {100.0:>12.1f}%")

    # -------------------------------------------------------- finer line detail
    print("\nBY EXACT LINE  (signed AHCh, as graded)")
    head = f"{'line':>7} {'matches':>8} {'weight':>8} {'model':>8} {'market':>8} {'gap':>9} {'ln2-market':>11}"
    print(head); print("-" * len(head))
    for line in sorted(ah["AHCh"].unique()):
        s = bucket_stats(ah[ah["AHCh"] == line])
        if not s.get("weight"):
            print(f"{line:>+7.2f} {s['n']:>8} {0.0:>8.2f}")
            continue
        print(f"{line:>+7.2f} {s['n']:>8} {s['weight']:>8.2f} {s['model']:>8.4f} "
              f"{s['market']:>8.4f} {s['gap']:>+9.4f} {s['available']:>+11.4f}")

    # ---------------------------------------------------------------- divisions
    print_table("BY DIVISION (all lines)",
                [(f"{lg} ({config.LEAGUES.get(lg, {}).get('name', lg)})"
                  if hasattr(config, "LEAGUES") else lg, bucket_stats(ah[ah["league"] == lg]))
                 for lg in sorted(ah["league"].unique())], label_w=34)

    print("\nBY DIVISION x LINE BUCKET")
    head = (f"{'league':<8} {'bucket':<18} {'matches':>8} {'weight':>8} {'model':>8} "
            f"{'market':>8} {'gap':>9} {'ln2-market':>11}")
    print(head); print("-" * len(head))
    for lg in sorted(ah["league"].unique()):
        for b in order:
            s = bucket_stats(ah[(ah["league"] == lg) & (ah["bucket"] == b)])
            if not s.get("weight"):
                print(f"{lg:<8} {b:<18} {s['n']:>8} {0.0:>8.2f}")
                continue
            print(f"{lg:<8} {b:<18} {s['n']:>8} {s['weight']:>8.2f} {s['model']:>8.4f} "
                  f"{s['market']:>8.4f} {s['gap']:>+9.4f} {s['available']:>+11.4f}")

    # ---------------------------------------------------------------- histogram
    print("\nAHCh HISTOGRAM — THE GRADED SAMPLE AS ACTUALLY GRADED")
    head = f"{'line':>7} {'matches':>8} {'share':>8} {'weight':>8} {'legs':>6} {'pushed legs':>12}"
    print(head); print("-" * len(head))
    pushed_by_line = collections.Counter(); legs_by_line = collections.Counter()
    pushed_total = legs_total = 0
    for _, row in ah.iterrows():
        line = float(row["AHCh"]); legs = legs_for(line)
        legs_total += len(legs); legs_by_line[line] += len(legs)
        for leg in legs:
            if abs(float(row["FTHG"] - row["FTAG"] + leg)) < 1e-9:
                pushed_by_line[line] += 1; pushed_total += 1
    for line in sorted(ah["AHCh"].unique()):
        n = int((ah["AHCh"] == line).sum())
        print(f"{line:>+7.2f} {n:>8} {n/len(ah)*100:>7.1f}% "
              f"{float(ah.loc[ah['AHCh']==line,'ah_weight'].sum()):>8.2f} "
              f"{legs_by_line[line]:>6} {pushed_by_line[line]:>12}")
    print(f"{'TOTAL':>7} {len(ah):>8} {100.0:>7.1f}% {tot_w:>8.2f} {legs_total:>6} {pushed_total:>12}")

    print("\n|AHCh| concentration, graded sample:")
    for thr in (0.25, 0.75, 1.0):
        print(f"  |line| <= {thr:<5} : {(ah['abs_line'] <= thr + 1e-9).sum():>4} / {len(ah)} "
              f"= {(ah['abs_line'] <= thr + 1e-9).mean()*100:5.1f}% of graded matches")

    # --------------------------------------------------------- pushes in detail
    print(f"\nPUSHES: {pushed_total} of {legs_total} legs dropped "
          f"({pushed_total/legs_total*100:.1f}% of legs)")
    print(f"  whole-line matches that pushed entirely (weight 0): "
          f"{int((ah['ah_weight'] <= 0).sum())}")
    print(f"  quarter-line matches that lost one leg (weight 0.5): "
          f"{int(((ah['ah_weight'] > 0.49) & (ah['ah_weight'] < 0.51)).sum())}")
    zero = ah[ah["ah_weight"] <= 0]
    if len(zero):
        print("  every-leg-pushed matches (line, score):")
        for _, r in zero.iterrows():
            print(f"    {r['league']} {r['date'].date()} {r['home']} v {r['away']}  "
                  f"line {float(r['AHCh']):+.2f}  {int(r['FTHG'])}-{int(r['FTAG'])}")
    draws = ah[ah["FTHG"] == ah["FTAG"]]
    print(f"\n  draws in the graded AH sample: {len(draws)} / {len(ah)} = {len(draws)/len(ah)*100:.1f}%")
    print(f"  draws carrying full weight   : {int((draws['ah_weight'] >= 0.99).sum())}")
    print(f"  draws carrying zero weight   : {int((draws['ah_weight'] <= 0).sum())}")

    # ------------------------------------------------- fixtures NOT graded: why
    print("\nFIXTURES NOT GRADED ON AH")
    played = graded[graded["played"]]
    has_ah = played[played["has_ah_odds"].fillna(False)]
    no_ladder_match = has_ah[has_ah["ah_model_home"].isna()]
    print(f"  played sealed predictions                    : {len(played)}")
    print(f"  ... of those with a closing AH price (AHCh)  : {len(has_ah)}")
    print(f"  ... of those graded                          : {len(ah)}")
    print(f"  ... NOT graded: ladder had no exact AHCh     : {len(no_ladder_match)}")
    if len(no_ladder_match):
        print("\n  the AHCh values the sealed 25-line ladder could not match:")
        head2 = f"{'AHCh':>8} {'matches':>8}   nearest ladder line / reason"
        print("  " + head2); print("  " + "-" * len(head2))
        for line, n in sorted(collections.Counter(
                no_ladder_match["AHCh"].dropna().astype(float)).items()):
            sample = no_ladder_match[no_ladder_match["AHCh"] == line].iloc[0]
            lad = sample.get("asian_handicap")
            if isinstance(lad, list) and lad:
                lines = sorted(float(x["line"]) for x in lad)
                nearest = min(lines, key=lambda x: abs(x - line))
                why = (f"nearest {nearest:+.2f} (ladder {min(lines):+.2f}..{max(lines):+.2f}, "
                       f"{len(lines)} rungs)")
            else:
                why = "no ladder sealed in that entry (market added later)"
            print(f"  {line:>+8.2f} {n:>8}   {why}")
        nan_line = int(no_ladder_match["AHCh"].isna().sum())
        if nan_line:
            print(f"  {'(no AHCh)':>8} {nan_line:>8}   closing main line absent")
        no_lad = no_ladder_match[[not isinstance(x, list) for x in no_ladder_match["asian_handicap"]]]
        print(f"\n  of the {len(no_ladder_match)} ungraded: {len(no_lad)} carry no sealed ladder at all "
              f"(sealed before the market existed), {len(no_ladder_match)-len(no_lad)} carry a ladder "
              f"whose rungs miss the closing line.")

    # --------------------------------- the same matches, graded on the 1X2
    both = ah[ah["graded"]]
    print("\nTHE SAME MATCHES, GRADED ON THE 1X2  (how much room each market leaves)")
    head = (f"{'':<22} {'n':>5} {'model':>8} {'market':>8} {'gap':>9} "
            f"{'benchmark':>10} {'available':>10}")
    print(head); print("-" * len(head))
    for label, sub in [("all graded AH matches", both)] + \
                      [(b, both[both["bucket"] == b]) for b in order]:
        if sub.empty:
            print(f"{label:<22} {0:>5}"); continue
        m = float(sub["model_loss"].mean()); k = float(sub["market_loss"].mean())
        print(f"{label:<22} {len(sub):>5} {m:>8.4f} {k:>8.4f} {m-k:>+9.4f} "
              f"{config.UNIFORM_LOG_LOSS:>10.4f} {config.UNIFORM_LOG_LOSS-k:>+10.4f}")
    print("  (1X2 benchmark is uniform 1/3-1/3-1/3 = ln 3; AH benchmark is ln 2.)")

    # ------------------------------- section 5's PL line distribution, checked
    print("\n" + "=" * 78)
    print("SECTION 5's PREMIER LEAGUE LINE DISTRIBUTION, CHECKED")
    print("=" * 78)
    from proofodds.data import load_all_matches
    pl = load_all_matches(["E0"])
    pl2526 = pl[pl["Season"].astype(str).str.contains("2025")]
    for name, frame in [("E0 2025/26 completed file (the quoted source)", pl2526),
                        ("E0 rows in the GRADED AH sample", ah[ah["league"] == "E0"])]:
        col = frame["AHCh"].dropna().astype(float)
        print(f"\n{name}: {len(frame)} rows, {len(col)} with AHCh")
        if not len(col): continue
        for line, n in sorted(collections.Counter(col).items(), key=lambda kv: -kv[1])[:8]:
            print(f"    {line:>+6.2f}  {n:>4}  {n/len(col)*100:5.1f}%")
        print(f"    |line| <= 0.75 : {(col.abs() <= 0.75 + 1e-9).mean()*100:5.1f}%")


    graded = grade.graded_frame()
    ah = graded[graded["ah_graded"]].copy()
    ah["abs_line"] = ah["AHCh"].abs()
    near = ah[ah["abs_line"] <= 0.25 + 1e-9]
    mid  = ah[(ah["abs_line"] > 0.25 + 1e-9) & (ah["abs_line"] <= 0.75 + 1e-9)]
    far  = ah[ah["abs_line"] >= 1.0 - 1e-9]

    print("=" * 78)
    print("CAN THIS SAMPLE TELL THE BUCKETS APART?")
    print("=" * 78)
    print("The buckets are disjoint sets of matches, so the difference of two")
    print("bucket gaps has se = sqrt(se1^2 + se2^2).\n")
    head = f"{'comparison':<34} {'difference':>11} {'se':>9} {'95% interval':>22} {'separated':>10}"
    print(head); print("-" * len(head))
    labels = {"near (<=0.25)": near, "mid (0.50-0.75)": mid, "far (>=1.00)": far}
    stats = {k: wmean_se(v) for k, v in labels.items()}
    for a, b in [("near (<=0.25)", "far (>=1.00)"),
                 ("near (<=0.25)", "mid (0.50-0.75)"),
                 ("far (>=1.00)", "mid (0.50-0.75)")]:
        ma, sa, _, _ = stats[a]; mb, sb, _, _ = stats[b]
        d = ma - mb; se = math.sqrt(sa**2 + sb**2)
        lo, hi = d - Z*se, d + Z*se
        print(f"{a+' - '+b:<34} {d:>+11.4f} {se:>9.4f} "
              f"{f'[{lo:+.4f}, {hi:+.4f}]':>22} {('yes' if lo*hi>0 else 'no'):>10}")

    print("\nROOM AVAILABLE IN EACH BUCKET (ln 2 - closing-line log loss)")
    head = f"{'bucket':<20} {'market':>9} {'ln2-market':>11} {'model gap':>11} {'gap / room':>12}"
    print(head); print("-" * len(head))
    for k, v in labels.items():
        w = v["ah_weight"].sum()
        mk = float(v["ah_market_loss"].sum()/w); mo = float(v["ah_model_loss"].sum()/w)
        room = LN2 - mk
        print(f"{k:<20} {mk:>9.4f} {room:>+11.4f} {mo-mk:>+11.4f} "
              f"{(mo-mk)/room if abs(room)>1e-9 else float('nan'):>12.1f}x")
    w = ah["ah_weight"].sum()
    mk = float(ah["ah_market_loss"].sum()/w); mo = float(ah["ah_model_loss"].sum()/w)
    print(f"{'ALL':<20} {mk:>9.4f} {LN2-mk:>+11.4f} {mo-mk:>+11.4f} {(mo-mk)/(LN2-mk):>12.1f}x")
    print("\n  For reference, the same ratio on the 1X2, whole graded set:")
    g = graded[graded["graded"]]
    m1 = float(g["model_loss"].mean()); k1 = float(g["market_loss"].mean())
    print(f"    room = ln3 - market = {config.UNIFORM_LOG_LOSS - k1:+.4f}, "
          f"gap = {m1-k1:+.4f}, gap/room = {(m1-k1)/(config.UNIFORM_LOG_LOSS-k1):.2f}x")

    # ---------------------------------------------------- hypothesis (b) tested
    print("\n" + "=" * 78)
    print("HYPOTHESIS (b): DOES PUSH EXCLUSION SELECT AGAINST THE MODEL?")
    print("=" * 78)
    print("A push has no outcome, so it cannot be scored on the AH. But every one")
    print("of these matches IS scored on the 1X2, where nothing is dropped. So ask")
    print("the 1X2 whether the matches the AH threw away were ones the model was")
    print("good at. If they were, exclusion selects against the model.\n")

    pushed_any, full_weight = [], []
    for idx, row in ah.iterrows():
        line = float(row["AHCh"])
        p = any(abs(float(row["FTHG"]-row["FTAG"]+leg)) < 1e-9 for leg in legs_for(line))
        (pushed_any if p else full_weight).append(idx)

    head = f"{'1X2 on...':<38} {'n':>5} {'model':>8} {'market':>8} {'gap':>9} {'±95%':>9}"
    print(head); print("-" * len(head))
    for label, idxs in [("AH matches with NO pushed leg", full_weight),
                        ("AH matches with >=1 pushed leg", pushed_any),
                        ("AH matches where EVERY leg pushed", list(ah[ah["ah_weight"] <= 0].index))]:
        sub = ah.loc[idxs]; sub = sub[sub["graded"]]
        if sub.empty: print(f"{label:<38} {0:>5}"); continue
        iv = paired_interval((sub["model_loss"]-sub["market_loss"]).to_numpy(float))
        half = f"±{(iv['ci_high']-iv['ci_low'])/2:.4f}" if iv["ci_low"] is not None else "—"
        print(f"{label:<38} {len(sub):>5} {sub['model_loss'].mean():>8.4f} "
              f"{sub['market_loss'].mean():>8.4f} {iv['mean']:>+9.4f} {half:>9}")

    print("\nDRAWS INSIDE THE GRADED AH SAMPLE")
    ah["is_draw"] = ah["FTHG"] == ah["FTAG"]
    head = f"{'':<30} {'matches':>8} {'weight':>8} {'AH gap':>9} {'1X2 gap':>9}"
    print(head); print("-" * len(head))
    for label, sub in [("draws", ah[ah["is_draw"]]), ("non-draws", ah[~ah["is_draw"]])]:
        m, se, n, wt = wmean_se(sub)
        g2 = sub[sub["graded"]]
        gap2 = float((g2["model_loss"]-g2["market_loss"]).mean()) if len(g2) else float("nan")
        print(f"{label:<30} {len(sub):>8} {wt:>8.2f} "
              f"{(m if m is not None else float('nan')):>+9.4f} {gap2:>+9.4f}")
    print(f"\n  draws as a share of matches : {ah['is_draw'].mean()*100:.1f}%")
    print(f"  draws as a share of weight  : "
          f"{ah.loc[ah['is_draw'],'ah_weight'].sum()/ah['ah_weight'].sum()*100:.1f}%")
    print("  -> push exclusion shrinks the draw region but does not empty it.")

    # -------------------------------------------------- TASK 2: market overlap
    print("\n" + "=" * 78)
    print("HOW MUCH DO THE THREE SCORECARD LINES OVERLAP? (input to TASK 2)")
    print("=" * 78)
    n_all = len(graded)
    sets = {"1X2": graded["graded"], "OU 2.5": graded["ou_graded"], "AH": graded["ah_graded"]}
    print("\nMatch overlap (sealed predictions that are graded on each pair):")
    head = f"{'':<10} " + " ".join(f"{k:>10}" for k in sets)
    print(head); print("-" * len(head))
    for a, sa in sets.items():
        print(f"{a:<10} " + " ".join(f"{int((sa & sb).sum()):>10}" for sb in sets.values()))
    for a, sa in sets.items():
        for b, sb in sets.items():
            if a < b:
                both = int((sa & sb).sum())
                print(f"  {b} matches also graded on {a}: {both}/{int(sb.sum())} "
                      f"= {both/max(int(sb.sum()),1)*100:.1f}%")

    print("\nCorrelation of the PER-MATCH paired difference (model loss - market loss)")
    print("on the matches graded by both. This is the quantity each scorecard line")
    print("is a mean of, so its correlation is how far the two lines are one fact.\n")
    diffs = {
        "1X2": (graded["graded"], graded["model_loss"] - graded["market_loss"]),
        "OU 2.5": (graded["ou_graded"], graded["ou_model_loss"] - graded["ou_market_loss"]),
        "AH": (graded["ah_graded"], np.divide(
            (graded["ah_model_loss"] - graded["ah_market_loss"]).to_numpy(float),
            np.where(graded["ah_weight"].to_numpy(float) > 0,
                     graded["ah_weight"].to_numpy(float), np.nan))),
    }
    head = f"{'pair':<16} {'n':>5} {'pearson r':>11} {'spearman':>10} {'r^2':>8}"
    print(head); print("-" * len(head))
    names = list(diffs)
    for i in range(len(names)):
        for j in range(i+1, len(names)):
            a, b = names[i], names[j]
            ma, da = diffs[a]; mb, db = diffs[b]
            da = pd.Series(np.asarray(da, dtype=float), index=graded.index)
            db = pd.Series(np.asarray(db, dtype=float), index=graded.index)
            keep = ma.to_numpy() & mb.to_numpy() & np.isfinite(da) & np.isfinite(db)
            x, y = da[keep], db[keep]
            if len(x) < 3:
                print(f"{a+' vs '+b:<16} {len(x):>5}"); continue
            r = float(np.corrcoef(x, y)[0, 1])
            rs = float(pd.Series(x).corr(pd.Series(y), method="spearman"))
            print(f"{a+' vs '+b:<16} {len(x):>5} {r:>+11.3f} {rs:>+10.3f} {r*r:>8.3f}")

    print("\nSame correlation for the AH split by line size (does a big handicap")
    print("test something the 1X2 does not?):")
    head = f"{'AH bucket':<20} {'n':>5} {'pearson r':>11} {'r^2':>8}"
    print(head); print("-" * len(head))
    d_ah = pd.Series(np.asarray(diffs["AH"][1], dtype=float), index=graded.index)
    d_1x2 = graded["model_loss"] - graded["market_loss"]
    absl = graded["AHCh"].abs()
    for label, mask in [("|line| <= 0.25", absl <= 0.25 + 1e-9),
                        ("0.50 to 0.75", (absl > 0.25+1e-9) & (absl <= 0.75+1e-9)),
                        ("|line| >= 1.00", absl >= 1.0 - 1e-9)]:
        keep = (graded["ah_graded"] & graded["graded"] & mask).to_numpy() \
               & np.isfinite(d_ah) & np.isfinite(d_1x2)
        x, y = d_ah[keep], d_1x2[keep]
        if len(x) < 3: print(f"{label:<20} {len(x):>5}"); continue
        r = float(np.corrcoef(x, y)[0, 1])
        print(f"{label:<20} {len(x):>5} {r:>+11.3f} {r*r:>8.3f}")


    print("\n" + "=" * 78)
    print("NOTHING WAS WRITTEN. predictions/, the model, the ladder and the")
    print("grading code are untouched by this script.")
    print("=" * 78)


if __name__ == "__main__":
    main()
