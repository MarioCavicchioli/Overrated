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

Nearest-neighbour tone: the VOA prices blocks of a long street differently, so a shop can sit two
spreads above its segment yet match its neighbours exactly. Same definition as the data service:
- house number: first integer before the first comma, ignoring a leading (...) clause and floor
  ordinals (1ST, 3RD): '225-227, OLD STREET' -> 225, '(INCL 1-9 HILLS PL) BST-3RD FLRS 215-219, ...' -> 215
- neighbours: the k nearest OTHER shops with a Zone A price, the same scat_code and the same street_key
  (any VOA scheme): by |house number gap|, then same postcode, then lower uarn, with unnumbered shops at
  the same postcode filling in after the numbered ones. A shop with no number takes the shops at its
  own postcode (numbered first, by number).
- local_median = their median Zone A, local_n = how many, local_z = (Zone A - local_median) / segment
  spread; local_n < 2 -> local_median and local_z are None, which fails any min_local_z rule.
Rules `neighbour_k`, `min_local_z`, `threshold_min_local_z` and rank_by `local_z` / `saving_local` use
it; the defaults leave the ranking exactly as it was (pb-v1 and pb-v2 reproduce).
"""
import argparse
import hashlib
import json
import os
import re
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timezone
from statistics import median

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
    "rank_by": "saving",     # saving | z | excess_pct | saving_x_z | local_z | saving_local
    "neighbour_k": 4,        # nearest same-use shops on the street that make the local median
    "min_local_z": None,     # tone candidates also need local_z at or above this (None = off)
    "threshold_min_local_z": None,  # just-over-threshold candidates also need this local_z (None = off)
}
NEIGHBOUR_K_MAX = 8          # neighbours kept per shop; neighbour_k can be at most this


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


def load_rules(path):
    """Rule overrides from a flat rules JSON or a saved version file (versions/pb-vN.json keeps them under "rules")."""
    if not path:
        return {}
    data = json.load(open(path))
    return data["rules"] if isinstance(data.get("rules"), dict) else data


_LEADING_CLAUSE = re.compile(r"^\s*(?:\([^)]*\)\s*)+")
# an integer that is not part of a longer one and not a floor ordinal (1ST, 2ND, 3RD, 4TH)
_HOUSE_NUMBER = re.compile(r"(?<!\d)\d+(?!\d)(?!(?:ST|ND|RD|TH)\b)", re.IGNORECASE)


def house_number(address):
    """First integer before the first comma, ignoring a leading (...) clause and floor ordinals.

    '225-227, OLD STREET' -> 225; 'BST & GRD FLR 223, SHOREDITCH HIGH STREET' -> 223;
    'UNIT 3B AT HORNBEAM APARTMENTS, SEVEN SISTERS ROAD' -> 3;
    '(INCL 1-9 HILLS PL) BST-3RD FLRS 215-219, OXFORD STREET' -> 215; no number there -> None.
    """
    head = _LEADING_CLAUSE.sub("", address or "", count=1).split(",", 1)[0]
    m = _HOUSE_NUMBER.search(head)
    return int(m.group()) if m else None


def find_neighbours(pool, kmax=NEIGHBOUR_K_MAX):
    """uarn -> [(uarn, zone_a_price), ...], nearest first, at most kmax, for every shop in `pool`.

    pool rows: (uarn, address, postcode, street_key, scat_code, zone_a_price); rows with no Zone A price
    are left out. Neighbours are the other shops with the same street_key and scat_code (any scheme),
    in the data service's order (data/service.py neighbours_cte):
    - a numbered shop: numbered shops by |house number gap|, then same postcode first, then lower uarn;
      shops with no number at its own postcode fill in after those (lower uarn first)
    - a shop with no number: the shops at its own postcode, numbered first by number, then lower uarn
    One sorted pass per street, so the cost grows with the biggest street, not with n^2.
    """
    groups = defaultdict(list)
    for uarn, address, postcode, street_key, scat_code, price in pool:
        if price is not None:
            groups[(street_key, scat_code)].append((house_number(address), postcode, uarn, price))
    out = {}
    for members in groups.values():
        numbered = sorted((m for m in members if m[0] is not None), key=lambda m: (m[0], m[2]))
        unnumbered = sorted((m for m in members if m[0] is None), key=lambda m: m[2])
        nums = [m[0] for m in numbered]
        at_postcode, unnumbered_at = defaultdict(list), defaultdict(list)
        for m in numbered + unnumbered:
            at_postcode[m[1]].append(m)
        for m in unnumbered:
            unnumbered_at[m[1]].append(m)
        for i, (num, pc, uarn, _) in enumerate(numbered):
            # the kmax smallest gaps lie within kmax places either side in number order; then take
            # every shop within the kmax-th gap, so ties on the gap go by postcode and uarn, not position
            gaps = sorted(abs(nums[j] - num) for j in range(max(0, i - kmax), min(len(nums), i + kmax + 1)) if j != i)
            near = []
            if gaps:
                d = gaps[min(kmax, len(gaps)) - 1]
                window = numbered[bisect_left(nums, num - d):bisect_right(nums, num + d)]
                near = sorted((m for m in window if m[2] != uarn), key=lambda m: (abs(m[0] - num), m[1] != pc, m[2]))[:kmax]
            near += unnumbered_at[pc][:kmax - len(near)]
            out[uarn] = [(m[2], m[3]) for m in near]
        for num, pc, uarn, _ in unnumbered:
            out[uarn] = [(m[2], m[3]) for m in at_postcode[pc] if m[2] != uarn][:kmax]
    return out


def load(ba):
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
               g.n seg_n, g.med seg_median, m.mad seg_mad
        from scoped s join seg g using (segment_key) join seg_mad m using (segment_key)
        order by s.uarn
    """).fetchall()
    cols = ["uarn", "address", "street_key", "segment_key", "rv", "total_area", "zone_a_price", "itza",
            "seg_n", "seg_median", "seg_mad"]
    shops = [dict(zip(cols, r)) for r in rows]

    # Nearest same-use neighbours on the same street (any scheme, any borough), from the same April 2023 rows.
    nb = find_neighbours(con.execute("""
        select uarn, address, postcode, street_key, scat_code, zone_a_price from base
        where street_key in (select street_key from scoped)
    """).fetchall())
    for s in shops:
        s["neighbours"] = nb.get(s["uarn"], [])

    # Answer key: later cut, same floor area, effective from the list start.
    hits = {r[0] for r in con.execute(f"""
        select b.uarn from scoped b
        join '{PQ}/list_2023.parquet' l using (uarn)
        join '{PQ}/sv_2023.parquet' v on v.uarn = l.uarn and v.assessment_ref = l.assessment_ref
        where l.rv < b.rv and abs(v.total_area - b.total_area) <= 0.02 * b.total_area
          and l.effective_date = date '2023-04-01'
    """).fetchall()}
    return shops, hits


def saving_against(s, tone):
    """Annual saving (2023/24 multipliers) if the shop's Zone A came down to `tone` on its zoned area."""
    drop = max(s["zone_a_price"] - tone, 0) * (s["itza"] or 0)
    proposed = max(s["rv"] - drop, 0)
    return s["rv"] * multiplier(s["rv"]) - proposed * multiplier(proposed)


def local_tone(s, k):
    """(local_n, local_median) over the shop's k nearest neighbours, cached per k. As in the data
    service, the local median needs at least 2 neighbours (None below that)."""
    cache = s.setdefault("_local", {})
    if k not in cache:
        prices = [p for _, p in s.get("neighbours", ())[:k]]
        cache[k] = (len(prices), median(prices) if len(prices) >= 2 else None)
    return cache[k]


def score(shops, rules):
    """Apply the Scout's rules; returns shops ranked best first, with score and flags."""
    k, min_local_z, threshold_min_local_z = rules["neighbour_k"], rules["min_local_z"], rules["threshold_min_local_z"]
    if not 1 <= k <= NEIGHBOUR_K_MAX:
        raise ValueError(f"neighbour_k must be 1..{NEIGHBOUR_K_MAX}")
    for s in shops:
        s["seg_spread"] = max(1.4826 * (s["seg_mad"] or 0), rules["spread_floor"] * s["seg_median"])
        s["z"] = (s["zone_a_price"] - s["seg_median"]) / s["seg_spread"] if s["seg_spread"] else 0
        s["est_saving"] = saving_against(s, s["seg_median"])
        # local tone needs 2+ neighbours; below that local_z is None, which fails any min_local_z
        # rule and scores 0 under the local rankings
        s["local_n"], s["local_median"] = local_tone(s, k)
        s["local_z"] = ((s["zone_a_price"] - s["local_median"]) / s["seg_spread"]
                        if s["local_median"] is not None and s["seg_spread"] else None)
        s["est_saving_local"] = saving_against(s, s["local_median"]) if s["local_z"] is not None else None
        s["flags"] = []
        if (s["seg_n"] >= rules["min_segment_n"] and s["z"] >= rules["min_z"] and s["est_saving"] >= rules["min_saving"]
                and (min_local_z is None or (s["local_z"] is not None and s["local_z"] >= min_local_z))):
            s["flags"].append("zone_a_above_tone")
        if ((51000 <= s["rv"] < 54000 or 500000 <= s["rv"] < 525000) and s["z"] >= rules["threshold_min_z"]
                and (threshold_min_local_z is None
                     or (s["local_z"] is not None and s["local_z"] >= threshold_min_local_z))):
            s["flags"].append("just_over_threshold")
    # priced runs: several above-tone shops in one segment at the same price
    runs = defaultdict(Counter)
    for s in shops:
        if "zone_a_above_tone" in s["flags"]:
            runs[s["segment_key"]][s["zone_a_price"]] += 1
    for s in shops:
        if "zone_a_above_tone" in s["flags"] and runs[s["segment_key"]][s["zone_a_price"]] >= rules["cluster_size"]:
            s["flags"].append("segment_cluster")
        base = {
            "saving": s["est_saving"],
            "z": s["z"],
            "excess_pct": s["zone_a_price"] / s["seg_median"] - 1 if s["seg_median"] else 0,
            "saving_x_z": max(s["est_saving"], 0) ** 0.5 * max(s["z"], 0),
            "local_z": s["local_z"] or 0,
            "saving_local": s["est_saving_local"] or 0,
        }[rules["rank_by"]]
        s["score"] = (base if "zone_a_above_tone" in s["flags"] else 0) * (
            rules["cluster_factor"] if "segment_cluster" in s["flags"] else 1)
    # candidates first (by score), then everything else by z, so there are always 50 picks; uarn
    # settles exact ties so the ranking never depends on the order rows came out of DuckDB
    return sorted(shops, key=lambda s: (not s["flags"], -s["score"], -s["z"], s["uarn"]))


def metrics(ranked, hits, k=50):
    m = _metrics(ranked, hits, k)
    m200 = _metrics(ranked, hits, 200)
    m["lift_at_200"] = m200["lift_at_200"]
    m["top200_hits"] = m200["top200_hits"]
    m["same_street_rate_at_200"] = m200["same_street_rate"]
    return m


def _metrics(ranked, hits, k):
    top = ranked[:k]
    top_hits = sum(1 for s in top if s["uarn"] in hits)
    streets = {s["street_key"] for s in top}
    same_street = [s for s in ranked if s["street_key"] in streets]
    same_rate = sum(1 for s in same_street if s["uarn"] in hits) / len(same_street)
    cands = [s for s in ranked if s["flags"]]
    cand_hits = sum(1 for s in cands if s["uarn"] in hits)
    tone = [s for s in cands if "zone_a_above_tone" in s["flags"]]
    tone_hits = sum(1 for s in tone if s["uarn"] in hits)
    return {
        "shops": len(ranked), "hits": sum(1 for s in ranked if s["uarn"] in hits),
        "base_rate": round(sum(1 for s in ranked if s["uarn"] in hits) / len(ranked), 4),
        f"top{k}_hits": top_hits, f"top{k}_rate": round(top_hits / k, 4),
        "same_street_shops": len(same_street), "same_street_rate": round(same_rate, 4),
        f"lift_at_{k}": round((top_hits / k) / same_rate, 3) if same_rate else None,
        "candidates": len(cands), "candidate_hits": cand_hits,
        "candidate_precision": round(cand_hits / len(cands), 4) if cands else None,
        f"top{k}_candidates": sum(1 for s in top if s["flags"]),
        # the above-tone flag alone (the threshold flag ignores the neighbour rules)
        "tone_candidates": len(tone), "tone_hits": tone_hits,
        "tone_precision": round(tone_hits / len(tone), 4) if tone else None,
    }


def supabase():
    """(rest base url, headers) from .env, or (None, None) when Supabase is not configured."""
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not (url and key):
        return None, None
    base = url.rstrip("/").removesuffix("/rest/v1")
    return f"{base}/rest/v1", {"apikey": key, "Authorization": f"Bearer {key}"}


def write_eval_run(version, split, m, ba, rules):
    rest, auth = supabase()
    if not rest:
        print("  (Supabase not configured: eval_runs row not written)")
        return
    headers = {**auth, "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=minimal"}
    now = datetime.now(timezone.utc).isoformat()
    r = httpx.post(f"{rest}/eval_runs?on_conflict=version,dataset_snapshot,split",
                   headers=headers, timeout=10, json={
                       "version": version, "scout_version": "scout-v3",
                       "dataset_snapshot": f"{SNAPSHOT} ba{ba}", "split": split,
                       "lift_at_50": m["lift_at_50"], "metrics": {**m, "rules": rules},
                       "status": "completed", "finished_at": now})
    r.raise_for_status()
    print(f"  eval_runs row written: {version} / {split}")


def describe(s, hits):
    """One line per shop: hit or not, address, Zone A vs segment and vs its nearest neighbours."""
    local = (f"local {s['local_median']:>7g} (n={s['local_n']}) lz={s['local_z']:.2f}" if s["local_z"] is not None
             else f"local -- (n={s['local_n']})")
    prices = ", ".join(f"{p:g}" for _, p in s.get("neighbours", ())[:s["local_n"]])
    return (f"   {'HIT ' if s['uarn'] in hits else '    '}{s['uarn']:>12} {s['address'][:44]:44} "
            f"{s['zone_a_price']:>7g} vs seg {s['seg_median']:>7g} z={s['z']:.2f} | {local} [{prices}] {s['flags']}")


def tone_flags(by_split, rules):
    """{uarn: shop} of the shops the rules flag above tone, scored split by split as in the backtest."""
    for split in ("train", "heldout"):
        score(by_split[split], rules)
    return {s["uarn"]: s for shops in by_split.values() for s in shops if "zone_a_above_tone" in s["flags"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ba", default="5360", help="billing authority code, or 'all' for England and Wales")
    ap.add_argument("--rules", help="JSON of rule overrides: a flat rules file or a versions/pb-vN.json")
    ap.add_argument("--version", default="default", help="playbook version label for eval_runs")
    ap.add_argument("--write", action="store_true", help="write eval_runs rows to Supabase")
    ap.add_argument("--show", type=int, default=0, help="print the top N held-out picks")
    ap.add_argument("--compare", help="other rules (e.g. agents/eval/versions/pb-v2.json): list the shops they "
                                      "flag above tone and --rules does not, and the other way round")
    args = ap.parse_args()

    load_env()
    rules = {**DEFAULT_RULES, **load_rules(args.rules)}
    shops, hits = load(args.ba)
    by_split = defaultdict(list)
    for s in shops:
        by_split[split_of(s["street_key"])].append(s)

    print(f"backtest ba={args.ba} version={args.version} ({SNAPSHOT})")
    for split in ("train", "heldout"):
        ranked = score(by_split[split], rules)
        m = metrics(ranked, hits)
        print(f"[{split}] lift@50 {m['lift_at_50']}  top50 {m['top50_hits']}/50 hits "
              f"vs {m['same_street_rate']:.1%} on the same streets (base {m['base_rate']:.1%}, "
              f"{m['shops']} shops, {m['hits']} hits); lift@200 {m['lift_at_200']} ({m['top200_hits']}/200); "
              f"candidates {m['candidates']} at {m['candidate_precision'] or 0:.1%} precision, "
              f"{m['top50_candidates']} in the top 50; above tone {m['tone_candidates']} "
              f"at {m['tone_precision'] or 0:.1%}")
        if split == "heldout" and args.show:
            for s in ranked[:args.show]:
                print(describe(s, hits))
        if args.write:
            write_eval_run(args.version, split, m, args.ba, rules)

    if args.compare:
        other = {**DEFAULT_RULES, **load_rules(args.compare)}
        theirs = set(tone_flags(by_split, other))
        mine = tone_flags(by_split, rules)  # last, so each shop's local fields are under --rules
        dropped = [s for s in shops if s["uarn"] in theirs and s["uarn"] not in mine]
        added = [s for s in shops if s["uarn"] in mine and s["uarn"] not in theirs]
        for label, rows in ((f"flagged above tone by {args.compare}, not by --rules", dropped),
                            (f"flagged above tone by --rules, not by {args.compare}", added)):
            print(f"\n{len(rows)} {label} ({sum(1 for s in rows if s['uarn'] in hits)} later cut); "
                  f"local figures under --rules, neighbours' Zone A in brackets")
            for s in sorted(rows, key=lambda s: -s["est_saving"]):
                print(describe(s, hits))


if __name__ == "__main__":
    main()
