"""Greedy hill climb of the Scout's ranking rules on the backtest.

Run: .venv/bin/python agents/eval/hillclimb.py [--ba all] [--start pb-v2] [--write] [--dry-run] [--log trials.json]

Starts from the Scout's defaults (pb-v1), or from a saved version with --start, and tries one rule
change at a time on top of the best version so far. A change is kept only if it raises lift at 50 on
the TRAIN streets (lift at 200 breaks ties). Held-out lift is computed for every trial but never used
to decide, so it stays an honest score. Lift at 50 rests on 5 to 12 hits, so every trial also prints
lift at 200 and candidate precision, the steadier measures. Each kept version gets the next free
number (pb-v3, pb-v4, ...; existing version files and eval_runs versions are never overwritten), its
rules go to agents/eval/versions/<version>.json, ready to become a playbook candidate, and --write
puts it in eval_runs.
"""
import argparse
import glob
import json
import os
import re
from collections import defaultdict

import httpx

import backtest as bt

VERSIONS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "versions")

# Today's question first: score each shop against its nearest same-use shops on the street as well as
# its segment (a block the VOA priced higher is not out of line). Then the earlier one-change ideas,
# re-tried on top of whatever is best by then.
NEIGHBOUR_PROPOSALS = [
    ("neighbours: local z >= 1 (4 nearest)", {"min_local_z": 1.0}),
    ("neighbours: local z >= 1.5", {"min_local_z": 1.5}),
    ("neighbours: local z >= 2", {"min_local_z": 2.0}),
    ("neighbours: 2 nearest", {"neighbour_k": 2}),
    ("neighbours: 6 nearest", {"neighbour_k": 6}),
    *[(f"neighbours: local z >= {z:g}, {k} nearest", {"min_local_z": z, "neighbour_k": k})
      for k in (2, 6) for z in (1.0, 1.5, 2.0)],
    ("rank by saving against the local median", {"rank_by": "saving_local"}),
    ("rank by local z", {"rank_by": "local_z"}),
    *[(f"local z >= {z:g} + rank by local saving", {"min_local_z": z, "rank_by": "saving_local"})
      for z in (1.0, 1.5, 2.0)],
    ("neighbours instead of the run penalty: local z >= 1, runs score in full",
     {"min_local_z": 1.0, "cluster_factor": 1.0}),
    ("neighbours instead of the run penalty: local z >= 1.5, runs score in full",
     {"min_local_z": 1.5, "cluster_factor": 1.0}),
    ("neighbours and runs at half: local z >= 1, runs x0.5", {"min_local_z": 1.0, "cluster_factor": 0.5}),
    ("neighbours: 4 nearest", {"neighbour_k": 4}),
]

EARLIER_PROPOSALS = [
    ("rank by z instead of saving (stop favouring the biggest rents)", {"rank_by": "z"}),
    ("rank by % above segment median", {"rank_by": "excess_pct"}),
    ("rank by sqrt(saving) x z", {"rank_by": "saving_x_z"}),
    ("drop priced runs to the bottom", {"cluster_factor": 0.0}),
    ("priced runs score in full", {"cluster_factor": 1.0}),
    ("segments need 8+ shops", {"min_segment_n": 8}),
    ("segments need 3+ shops", {"min_segment_n": 3}),
    ("z >= 3", {"min_z": 3.0}),
    ("z >= 1.5", {"min_z": 1.5}),
    ("spread floor 10% of median", {"spread_floor": 0.10}),
    ("spread floor 2% of median", {"spread_floor": 0.02}),
    ("no minimum saving", {"min_saving": 0}),
    ("minimum saving £5k", {"min_saving": 5000}),
]

PROPOSALS = NEIGHBOUR_PROPOSALS + EARLIER_PROPOSALS


def evaluate(by_split, hits, rules):
    return {split: bt.metrics(bt.score(by_split[split], rules), hits) for split in ("train", "heldout")}


def better(new, old):
    return (new["lift_at_50"] or 0, new["lift_at_200"] or 0) > (old["lift_at_50"] or 0, old["lift_at_200"] or 0)


def uses_neighbours(rules):
    return rules["min_local_z"] is not None or rules["rank_by"] in ("local_z", "saving_local")


def saved_versions():
    """{N: path} of agents/eval/versions/pb-vN.json."""
    out = {}
    for path in glob.glob(os.path.join(VERSIONS, "pb-v*.json")):
        m = re.fullmatch(r"pb-v(\d+)\.json", os.path.basename(path))
        if m:
            out[int(m.group(1))] = path
    return out


def remote_versions():
    """Numbers N of the pb-vN versions already in Supabase eval_runs (a read), so --write never reuses one."""
    rest, auth = bt.supabase()
    if not rest:
        return set()
    r = httpx.get(f"{rest}/eval_runs?select=version&version=like.pb-v*", headers=auth, timeout=10)
    r.raise_for_status()
    return {int(m.group(1)) for row in r.json() if (m := re.fullmatch(r"pb-v(\d+)", row["version"]))}


def lift(m, k):
    v = m[f"lift_at_{k}"]
    return f"{v:.2f} ({m[f'top{k}_hits']:>2})" if v is not None else f"  -- ({m[f'top{k}_hits']:>2})"


def row(tag, kept, label, res):
    t, h = res["train"], res["heldout"]
    return (f"{tag:7} {kept:4} {lift(t, 50):>9} {lift(t, 200):>9} {lift(h, 50):>9} {lift(h, 200):>9} "
            f"{t['candidate_precision'] or 0:>7.2%} {h['candidate_precision'] or 0:>7.2%} "
            f"{t['candidates']:>6} {h['candidates']:>6}  {label}")


def check_reproduces(name, saved, best):
    """A saved version must still score exactly as recorded, or the climb would start from a different place."""
    for split in ("train", "heldout"):
        drift = {k: (v, best[split].get(k)) for k, v in saved[split].items() if best[split].get(k) != v}
        if drift:
            raise SystemExit(f"{name} {split} no longer reproduces its saved metrics (saved, now): {drift}")
    print(f"{name} reproduces every saved metric on both splits")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ba", default="all")
    ap.add_argument("--start", default="", help="start from a saved version, e.g. pb-v2 (default: Scout defaults, pb-v1)")
    ap.add_argument("--write", action="store_true", help="write each kept version's eval_runs rows to Supabase")
    ap.add_argument("--dry-run", action="store_true", help="print the trials only: no version files, no eval_runs")
    ap.add_argument("--log", help="also write every trial (rules and metrics, no shop rows) to this JSON file")
    args = ap.parse_args()
    if args.dry_run and args.write:
        ap.error("--dry-run and --write together make no sense")
    bt.load_env()

    if args.start:
        path = os.path.join(VERSIONS, f"{args.start}.json")
        start = json.load(open(path))
        base_name, base_label = args.start, start.get("change", "start")
        rules = {**bt.DEFAULT_RULES, **bt.load_rules(path)}
    else:
        start, base_name, base_label, rules = None, "pb-v1", "Scout defaults", dict(bt.DEFAULT_RULES)

    shops, hits = bt.load(args.ba)
    by_split = defaultdict(list)
    for s in shops:
        by_split[bt.split_of(s["street_key"])].append(s)

    best = evaluate(by_split, hits, rules)
    if start and args.ba == "all":  # saved versions were scored nationally
        check_reproduces(base_name, start, best)
    trials = [{"version": base_name, "kept": "base", "change": base_label, "rules": rules, **best}]
    print(f"{'version':7} {'kept':4} {'train@50':>9} {'train@200':>9} {'held@50':>9} {'held@200':>9} "
          f"{'prec tr':>7} {'prec ho':>7} {'cand tr':>6} {'cand ho':>6}  change   (lift (hits))")
    print(row(base_name, "base", base_label, best))

    taken = set(saved_versions()) | (remote_versions() if args.write else set())
    next_n = max(taken, default=1) + 1
    current, current_label, kept_versions = base_name, base_label, []
    if not args.start and 1 not in taken and not args.dry_run:
        kept_versions.append(("pb-v1", base_label, None, rules, best))  # first run: save the defaults too
    tried = {json.dumps(rules, sort_keys=True): base_name}
    for label, change in PROPOSALS:
        trial = {**rules, **change}
        if trial == rules:
            print(f"{'-':7} {'same':4} {'':>49}  {label} (already in force, skipped)")
            continue
        if set(change) == {"neighbour_k"} and not uses_neighbours(trial):
            print(f"{'-':7} {'same':4} {'':>49}  {label} (no neighbour rule in force, so k changes nothing; skipped)")
            continue
        key = json.dumps(trial, sort_keys=True)
        if key in tried:  # same rules give the same scores, and the best only rises, so it cannot be kept now
            print(f"{'-':7} {'same':4} {'':>49}  {label} (same rules as trial '{tried[key]}', skipped)")
            continue
        tried[key] = label
        res = evaluate(by_split, hits, trial)
        kept = better(res["train"], best["train"])
        tag = f"pb-v{next_n}" if kept else "-"
        print(row(tag, "yes" if kept else "no", label, res))
        trials.append({"version": tag, "kept": "yes" if kept else "no", "change": label, "rules": trial, **res})
        if kept:
            kept_versions.append((tag, label, current, trial, res))
            next_n += 1
            rules, best, current, current_label = trial, res, tag, label

    to_write = [] if args.dry_run else kept_versions
    for version, *_ in to_write:
        path = os.path.join(VERSIONS, f"{version}.json")
        if os.path.exists(path):
            raise SystemExit(f"{path} already exists; version files are never overwritten (nothing written)")
    for version, label, based_on, vrules, res in to_write:
        path = os.path.join(VERSIONS, f"{version}.json")
        json.dump({"version": version, "change": label, "based_on": based_on, "rules": vrules,
                   "train": res["train"], "heldout": res["heldout"]}, open(path, "w"), indent=2)
        print(f"wrote {os.path.relpath(path)}")
        if args.write:
            for split in ("train", "heldout"):
                bt.write_eval_run(version, split, {**res[split], "change": label, "based_on": based_on},
                                  args.ba, vrules)

    if args.log:
        json.dump({"ba": args.ba, "start": base_name, "trials": trials}, open(args.log, "w"), indent=2)

    first = trials[0]
    print(f"\nbest: {current} ({current_label})")
    for split in ("train", "heldout"):
        a, b = first[split], best[split]
        print(f"  {split:7} lift@50 {a['lift_at_50']} -> {b['lift_at_50']}, lift@200 {a['lift_at_200']} -> "
              f"{b['lift_at_200']}, candidate precision {a['candidate_precision']} -> {b['candidate_precision']} "
              f"({a['candidates']} -> {b['candidates']} candidates)")
    print(json.dumps(rules))


if __name__ == "__main__":
    main()
