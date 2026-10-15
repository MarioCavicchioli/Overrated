"""Eval layer 1: outcome backtest of the Scout's ranking on the 2023 list.

Run: .venv/bin/python agents/eval/backtest.py [--ba 5360 | --ba all] [--rules rules.json]
                                              [--version default] [--write]

Inputs are only the 2023 list as compiled on 30 Mar 2023 (shops_2023a, epoch 0001), so no later
value leaks in. The answer key comes from the current 2023 list (epoch 0023): a shop is a hit if
its RV was later cut, its floor area is unchanged (within 2%), and the new entry is effective from
1 Apr 2023 (a correction backdated to the list start, not a physical change).

Score: lift at 50 = hit rate of the top 50 / hit rate of all shops on the same streets as the top 50
(the expected hit rate of 50 random shops from those streets). Streets are split in half by a hash
of street_key: hill-climb on `train`, report `heldout`.
"""
import argparse
import hashlib
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone

import duckdb
import httpx

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PQ = os.path.join(ROOT, "data", "parquet")
SNAPSHOT = "voa-2023-compiled-epoch-0001 vs epoch-0023"

# The Scout's defaults (agents/scout/agent.md); a playbook version overrides any of them.
DEFAULT_RULES = {
    "min_z": 2.0,            # Zone A this many spreads above its segment's median
    "min_segment_n": 5,      # segments smaller than this are not scored
    "min_saving": 1000,      # est_saving floor for tone candidates, GBP
    "threshold_min_z": 1.0,  # just over £51k / £500k only counts at this z or above
    "cluster_size": 3,       # this many shops at one high price in a segment = a priced run
    "cluster_factor": 0.5,   # score multiplier for priced runs
    "spread_floor": 0.05,    # spread is at least this share of the segment median
}


def load_env():
    path = os.path.join(ROOT, ".env")
    if os.path.exists(path):
        for line in open(path):
            k, _, v = line.strip().partition("=")
            if k and not k.startswith("#") and v:
                os.environ.setdefault(k, v)


def split_of(street_key):
    return "train" if int(hashlib.md5(street_key.encode()).hexdigest(), 16) % 2 == 0 else "heldout"


def multiplier(rv):
    """2023/24 multipliers (small 49.9p under £51k, standard 51.2p). Only the ranking uses them."""
    return 0.499 if rv < 51000 else 0.512


def load(ba, rules):
    """April 2023 shops in scope, scored against every shop in their segment (which can cross a borough line)."""
    con = duckdb.connect()
    scope = "" if ba == "all" else f"and ba_code = '{ba}'"
    con.execute(f"""
        create view base as select *, street_key || '#' || coalesce(scheme_ref, '') segment_key
        from '{PQ}/shops_2023a.parquet' where scat_code = '249' and zone_a_price is not null;
        create view scoped as select * from base where true {scope};
        create view seg as
          select segment_key, count(*) n, median(zone_a_price) med from base
          where segment_key in (select segment_key from scoped) group by 1;
        create view seg_mad as
          select b.segment_key, median(abs(b.zone_a_price - s.med)) mad
          from base b join seg s using (segment_key) group by 1;
    """)
    rows = con.execute(f"""
        select s.uarn, s.address, s.street_key, s.segment_key, s.rv, s.total_area, s.zone_a_price, s.itza,
               g.n seg_n, g.med seg_median, greatest(1.4826 * m.mad, {rules['spread_floor']} * g.med) seg_spread
        from scoped s join seg g using (segment_key) join seg_mad m using (segment_key)
    """).fetchall()
    cols = ["uarn", "address", "street_key", "segment_key", "rv", "total_area", "zone_a_price", "itza",
            "seg_n", "seg_median", "seg_spread"]
    shops = [dict(zip(cols, r)) for r in rows]

    # Answer key: later cut, same floor area, effective from the list start.
    hits = {r[0] for r in con.execute(f"""
        select b.uarn from scoped b
        join '{PQ}/list_2023.parquet' l using (uarn)
        join '{PQ}/sv_2023.parquet' v on v.uarn = l.uarn and v.assessment_ref = l.assessment_ref
        where l.rv < b.rv and abs(v.total_area - b.total_area) <= 0.02 * b.total_area
          and l.effective_date = date '2023-04-01'
    """).fetchall()}
    return shops, hits


def score(shops, rules):
    """Apply the Scout's rules; returns shops ranked best first, with score and flags."""
    for s in shops:
        s["z"] = (s["zone_a_price"] - s["seg_median"]) / s["seg_spread"] if s["seg_spread"] else 0
        drop = max(s["zone_a_price"] - s["seg_median"], 0) * (s["itza"] or 0)
        proposed = max(s["rv"] - drop, 0)
        s["est_saving"] = s["rv"] * multiplier(s["rv"]) - proposed * multiplier(proposed)
        s["flags"] = []
        if s["seg_n"] >= rules["min_segment_n"] and s["z"] >= rules["min_z"] and s["est_saving"] >= rules["min_saving"]:
            s["flags"].append("zone_a_above_tone")
        if (51000 <= s["rv"] < 54000 or 500000 <= s["rv"] < 525000) and s["z"] >= rules["threshold_min_z"]:
            s["flags"].append("just_over_threshold")
    # priced runs: several above-tone shops in one segment at the same price
    runs = defaultdict(Counter)
    for s in shops:
        if "zone_a_above_tone" in s["flags"]:
            runs[s["segment_key"]][s["zone_a_price"]] += 1
    for s in shops:
        if "zone_a_above_tone" in s["flags"] and runs[s["segment_key"]][s["zone_a_price"]] >= rules["cluster_size"]:
            s["flags"].append("segment_cluster")
        s["score"] = (s["est_saving"] if "zone_a_above_tone" in s["flags"] else 0) * (
            rules["cluster_factor"] if "segment_cluster" in s["flags"] else 1)
    # candidates first (by score), then everything else by z, so there are always 50 picks
    return sorted(shops, key=lambda s: (not s["flags"], -s["score"], -s["z"]))


def metrics(ranked, hits, k=50):
    top = ranked[:k]
    top_hits = sum(1 for s in top if s["uarn"] in hits)
    streets = {s["street_key"] for s in top}
    same_street = [s for s in ranked if s["street_key"] in streets]
    same_rate = sum(1 for s in same_street if s["uarn"] in hits) / len(same_street)
    cands = [s for s in ranked if s["flags"]]
    cand_hits = sum(1 for s in cands if s["uarn"] in hits)
    return {
        "shops": len(ranked), "hits": sum(1 for s in ranked if s["uarn"] in hits),
        "base_rate": round(sum(1 for s in ranked if s["uarn"] in hits) / len(ranked), 4),
        f"top{k}_hits": top_hits, f"top{k}_rate": round(top_hits / k, 4),
        "same_street_shops": len(same_street), "same_street_rate": round(same_rate, 4),
        f"lift_at_{k}": round((top_hits / k) / same_rate, 3) if same_rate else None,
        "candidates": len(cands), "candidate_hits": cand_hits,
        "candidate_precision": round(cand_hits / len(cands), 4) if cands else None,
        f"top{k}_candidates": sum(1 for s in top if s["flags"]),
    }


def write_eval_run(version, split, m, ba, rules):
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not (url and key):
        print("  (Supabase not configured: eval_runs row not written)")
        return
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json",
               "Prefer": "resolution=merge-duplicates,return=minimal"}
    now = datetime.now(timezone.utc).isoformat()
    r = httpx.post(f"{url.rstrip('/')}/rest/v1/eval_runs?on_conflict=version,dataset_snapshot,split",
                   headers=headers, timeout=10, json={
                       "version": version, "scout_version": "scout-v3",
                       "dataset_snapshot": f"{SNAPSHOT} ba{ba}", "split": split,
                       "lift_at_50": m["lift_at_50"], "metrics": {**m, "rules": rules},
                       "status": "completed", "finished_at": now})
    r.raise_for_status()
    print(f"  eval_runs row written: {version} / {split}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ba", default="5360", help="billing authority code, or 'all' for England and Wales")
    ap.add_argument("--rules", help="JSON file of rule overrides (a playbook version's rules)")
    ap.add_argument("--version", default="default", help="playbook version label for eval_runs")
    ap.add_argument("--write", action="store_true", help="write eval_runs rows to Supabase")
    ap.add_argument("--show", type=int, default=0, help="print the top N held-out picks")
    args = ap.parse_args()

    load_env()
    rules = {**DEFAULT_RULES, **(json.load(open(args.rules)) if args.rules else {})}
    shops, hits = load(args.ba, rules)
    by_split = defaultdict(list)
    for s in shops:
        by_split[split_of(s["street_key"])].append(s)

    print(f"backtest ba={args.ba} version={args.version} ({SNAPSHOT})")
    for split in ("train", "heldout"):
        ranked = score(by_split[split], rules)
        m = metrics(ranked, hits)
        print(f"[{split}] lift@50 {m['lift_at_50']}  top50 {m['top50_hits']}/50 hits "
              f"vs {m['same_street_rate']:.1%} on the same streets (base {m['base_rate']:.1%}, "
              f"{m['shops']} shops, {m['hits']} hits); candidates {m['candidates']} "
              f"at {m['candidate_precision'] or 0:.1%} precision, {m['top50_candidates']} in the top 50")
        if split == "heldout" and args.show:
            for s in ranked[:args.show]:
                print(f"   {'HIT ' if s['uarn'] in hits else '    '}{s['uarn']:>12} {s['address'][:40]:40} "
                      f"{s['zone_a_price']:>8} vs {s['seg_median']:>7} z={s['z']:.2f} {s['flags']}")
        if args.write:
            write_eval_run(args.version, split, m, args.ba, rules)


if __name__ == "__main__":
    main()
