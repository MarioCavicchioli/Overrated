"""Greedy hill climb of the Scout's ranking rules on the backtest.

Run: .venv/bin/python agents/eval/hillclimb.py [--ba all] [--write]

Starts from the Scout's defaults (pb-v1) and tries one rule change at a time on top of the best
version so far. A change is kept only if it raises lift at 50 on the TRAIN streets (lift at 200
breaks ties, since 50 picks is noisy). Held-out lift is computed for every version but never used
to decide, so it stays an honest score. Each kept version is written to eval_runs (--write) and
its rules to agents/eval/versions/<version>.json, ready to become a playbook candidate.
"""
import argparse
import json
import os
from collections import defaultdict

import backtest as bt

# One idea per step, tried in this order on top of whatever is best so far.
PROPOSALS = [
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


def evaluate(by_split, hits, rules):
    return {split: bt.metrics(bt.score(by_split[split], rules), hits) for split in ("train", "heldout")}


def better(new, old):
    return (new["lift_at_50"], new["lift_at_200"] or 0) > (old["lift_at_50"], old["lift_at_200"] or 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ba", default="all")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    bt.load_env()

    shops, hits = bt.load(args.ba)
    by_split = defaultdict(list)
    for s in shops:
        by_split[bt.split_of(s["street_key"])].append(s)

    out_dir = os.path.join(os.path.dirname(__file__), "versions")
    os.makedirs(out_dir, exist_ok=True)
    rules = dict(bt.DEFAULT_RULES)
    best = evaluate(by_split, hits, rules)
    history = [("pb-v1", "Scout defaults", rules, best, True)]
    print(f"{'version':8} {'kept':5} {'train@50':>9} {'train@200':>9} {'heldout@50':>10}  change")
    print(f"{'pb-v1':8} {'base':5} {best['train']['lift_at_50']:>9} {best['train']['lift_at_200']:>9} "
          f"{best['heldout']['lift_at_50']:>10}  Scout defaults")

    n = 1
    for label, change in PROPOSALS:
        trial = {**rules, **change}
        if trial == rules:
            continue
        res = evaluate(by_split, hits, trial)
        kept = better(res["train"], best["train"])
        tag = f"pb-v{n + 1}" if kept else "-"
        print(f"{tag:8} {'yes' if kept else 'no':5} {res['train']['lift_at_50']:>9} "
              f"{res['train']['lift_at_200']:>9} {res['heldout']['lift_at_50']:>10}  {label}")
        if kept:
            n += 1
            rules, best = trial, res
            history.append((tag, label, rules, res, True))

    for version, label, vrules, res, _ in history:
        json.dump({"version": version, "change": label, "rules": vrules,
                   "train": res["train"], "heldout": res["heldout"]},
                  open(os.path.join(out_dir, f"{version}.json"), "w"), indent=2)
        if args.write:
            for split in ("train", "heldout"):
                bt.write_eval_run(version, split, {**res[split], "change": label}, args.ba, vrules)

    v, label, vrules, res, _ = history[-1]
    print(f"\nbest: {v} ({label}); held-out lift@50 {res['heldout']['lift_at_50']} "
          f"vs pb-v1 {history[0][3]['heldout']['lift_at_50']}")
    print(json.dumps(vrules))


if __name__ == "__main__":
    main()
