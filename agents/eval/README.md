# eval agent

Owner: person 1. Scores each Scout / playbook version on real outcomes first. Layer 1 (the backtest) is code: `backtest.py`.

## Layer 1: outcome backtest

```
.venv/bin/python data/build.py 2023a                       # once: the 2023 list as compiled on 30 Mar 2023
.venv/bin/python agents/eval/backtest.py --ba all --show 10  # or --ba 5360 for Hackney
.venv/bin/python agents/eval/backtest.py --ba all --rules agents/eval/versions/pb-v3.json --version pb-v3 --write
.venv/bin/python agents/eval/backtest.py --ba 5360 --rules agents/eval/versions/pb-v7.json --compare agents/eval/versions/pb-v2.json
.venv/bin/python agents/eval/hillclimb.py --ba all --start pb-v2 --dry-run   # --write to save kept versions
```

- Inputs: only `shops_2023a` (2023 list, epoch 0001, compiled 30 Mar 2023), so nothing after April 2023 leaks in.
- Answer key: the RV was later cut in the 2023 list (epoch 0023), floor area within 2%, new entry effective 1 Apr 2023 (a correction, not a physical change). 6,033 of 394,148 shops nationally (1.5%); 40 of 2,570 in Hackney.
- Ranking: the Scout's rules (`DEFAULT_RULES`, overridden by a flat rules JSON or a `versions/pb-vN.json`), segment tone = street + sector + VOA scheme, local tone = nearest neighbours (below).
- Score: lift at 50 = top-50 hit rate / hit rate of all shops on the same streets. Streets split in half by hash: hill-climb on `train`, report `heldout`.
- `--write` upserts one `eval_runs` row per split (unique on version, dataset_snapshot, split). `--compare` lists the shops one rule set flags above tone and the other does not, with their neighbours' prices.

## First results (version `default`, 15 Oct)

| Scope | Split | Lift at 50 | Top 50 hits | Same-street rate | Candidate precision vs base |
|---|---|---|---|---|---|
| England and Wales | train | 2.47 | 9/50 | 7.3% | 4.9% vs 1.6% |
| England and Wales | heldout | 1.57 | 5/50 | 6.4% | 4.2% vs 1.5% |
| Hackney | train | 1.91 | 1/50 | 1.1% | 0/12 |
| Hackney | heldout | 0.32 | 1/50 | 6.3% | 1/62 |

Hackney has 40 hits in scope, too few to measure; use the national score for the eval and the hill climb. Nationally the top 50 is all Bond Street and Oxford Street, because the score is est_saving and it favours the biggest RVs.

Layers 2 (pack checks in code) and 3 (advocate accuracy on tribunal cases) are not built yet.

## Hill climb (15 Oct)

`hillclimb.py` tries one rule change at a time on top of the best version so far, keeps it only if train lift at 50 rises (lift at 200 breaks ties), and reports held-out without using it. Kept versions go to `eval_runs` and `versions/<version>.json`.

| Version | Change | Train lift@50 | Held-out lift@50 |
|---|---|---|---|
| pb-v1 | Scout defaults | 2.47 | 1.57 |
| pb-v2 | priced runs (segment_cluster) drop to the bottom | 3.31 | 1.59 |

Rejected on train: ranking by z (train 0.0: the most extreme outliers are data quirks, never cut), by % above median (1.02), by sqrt(saving) x z (1.90), segment size 3 or 8, z >= 1.5 or 3, spread floor 2% or 10%, saving floors £0 or £5k. Ranking by saving wins, likely because big rents are the ones owners appeal.

Caveat: 50 picks means 5 to 12 hits, so one or two hits move lift by 0.2 to 0.4, and held-out moved between 0.5 and 2.5 across the trials. Gate future playbooks on lift at 200 or candidate precision as well, not lift at 50 alone.

## Nearest neighbours (15 Oct)

The VOA prices blocks of a long street differently, so a shop can sit 2 spreads above its segment and still match the shops either side: 225-227 Old Street is £790 against a segment median of £595 (z 2.0), and its four nearest neighbours are all £790. Each shop now also gets a local tone from its nearest same-use shops, defined exactly as in the data service (`data/README.md`). On all 394,148 shops of the 2023 list, the backtest's neighbour lists match the service's SQL one for one.

- House number: the first integer before the first comma, ignoring a leading `(...)` clause and floor ordinals.
- Neighbours: the k nearest other shops with the same SCAT code on the same street_key (any scheme), by house-number gap, then same postcode, then uarn.
- local_z = (Zone A - the neighbours' median) / segment spread. It is null below 2 neighbours.
- New rules: `neighbour_k` (default 4), `min_local_z` and `threshold_min_local_z` (default off), and `rank_by` `local_z` or `saving_local`. With the defaults, pb-v1 and pb-v2 reproduce every saved metric exactly.

`hillclimb.py --ba all --start pb-v2 --write` first checks that pb-v2 still reproduces. It then tries the neighbour rules (local z 1, 1.5 or 2; 2, 4 or 6 nearest; ranking by local saving or local z; combinations), then the earlier proposals again on top. Kept versions, lift (hits):

| Version | Change | Train lift@50 | Train lift@200 | Held-out lift@50 | Held-out lift@200 | Candidate precision train / held-out |
|---|---|---|---|---|---|---|
| pb-v1 | Scout defaults | 2.47 (9) | 3.62 (30) | 1.57 (5) | 1.92 (22) | 4.9% / 4.2% |
| pb-v2 | priced runs drop to the bottom | 3.31 (8) | 3.44 (28) | 1.59 (6) | 1.87 (21) | 4.9% / 4.2% |
| pb-v3 | above tone also needs local z >= 1.5 (4 nearest) | 4.24 (12) | 4.44 (36) | 4.20 (9) | 2.79 (27) | 6.0% / 5.0% |
| pb-v4 | local z >= 2 over the 6 nearest | 4.39 (12) | 4.01 (37) | 3.98 (8) | 2.59 (26) | 5.8% / 4.9% |
| pb-v5 | rank by saving against the local median | 4.50 (14) | 3.13 (29) | 4.25 (8) | 2.71 (27) | 5.8% / 4.9% |
| pb-v6 | segments need 8+ shops | 4.72 (15) | 3.60 (34) | 3.33 (5) | 2.36 (23) | 6.0% / 4.6% |
| pb-v7 | spread floor 2% of median | 5.57 (14) | 3.32 (33) | 4.45 (6) | 2.73 (25) | 6.7% / 5.7% |

Rejected on train lift@50: the other k and local z combinations; ranking by local z or by z (0 hits in the top 50); letting priced runs back in once neighbours filter them; ranking by % above median or by sqrt(saving) x z; segments of 3+; z >= 1.5 or 3; a 10% spread floor; dropping the £1k saving floor; a £5k saving floor.

**Does the neighbour rule help on held-out?** Yes, by the steadier measures too, not just lift@50. The one change from pb-v2 to pb-v3 raises held-out lift@200 from 1.87 to 2.79 (21 to 27 hits in the top 200) and held-out candidate precision from 4.2% to 5.0%. Train moves the same way (lift@200 3.44 to 4.44). All nine filters tried on top of pb-v2 (2, 4 or 6 nearest, local z >= 1, 1.5 or 2) raise held-out lift@200 (to between 2.15 and 3.32) and held-out precision (to between 4.65% and 5.03%). Three limits:

- The gain is at the top of the list. The 71 shops it pushes out of pb-v2's held-out top 200 were later cut at 8.5%; the 71 that replace them were cut at 16.9% (train: 5.8% against 17.4%). Deeper down it costs hits: the top 1,000 holds 65 hits instead of 72 on held-out, and 77 instead of 102 on train.
- Most of the precision gain comes from priced runs that pb-v2 already ranked last. On held-out they are 2,457 of the 2,956 above-tone flags it drops, cut at 3.5%. Outside those runs, the shops it drops were cut about as often as the ones it keeps (held-out 4.2% against 4.7%; train 6.5% against 5.6%).
- After pb-v3, the climb kept changes on train lift@50 (12 to 15 hits) while train lift@200 fell from 4.44 to 3.32, and no later version beats pb-v3's held-out lift@200. pb-v7 is the climb's formal best, but on held-out it is level with pb-v3 (lift@200 2.73 against 2.79, precision 5.7% against 5.0%), so its four extra changes are unproven. pb-v3 is the safer playbook candidate.

**The deployed Scout v4 scores below pb-v3.** Its rule is local z >= 2 over the 4 nearest, with priced runs no longer ranked last, and threshold flags also needing local z >= 1 (that part is modelled by `threshold_min_local_z`). To reproduce:

```
.venv/bin/python agents/eval/backtest.py --ba all --rules <(echo '{"cluster_factor": 1, "min_local_z": 2, "threshold_min_local_z": 1}')
```

It scores train lift@50 3.26 (10) and lift@200 3.69 (32), held-out lift@50 4.05 (7) and lift@200 2.39 (19), with precision 5.7% / 4.6%. That beats pb-v2 on held-out lift@200 and precision, but with fewer held-out hits in the top 200 (19 against 21). Two changes bring it to pb-v3's level:

- Rank priced runs last again: held-out top-200 hits go from 19 to 25.
- Leave threshold flags out of the neighbour test. Threshold flags are cut about twice as often as above-tone flags (10 to 12%), and the ones the test removes were cut at 11 to 12%, no less often than the ones it keeps (8 to 11%).

Lowering local z to 1.5 adds 2 more held-out hits (27), which is within noise.

**Hackney (`--ba 5360`) is too small to score.** It has 40 hits in scope and 1 or 2 in any top 50 or 200.

| Version | Train lift@50 | Train lift@200 | Held-out lift@50 | Held-out lift@200 | Candidates later cut, train / held-out |
|---|---|---|---|---|---|
| pb-v2 | 1.91 (1) | 1.35 (2) | 3.33 (1) | 0.35 (2) | 0/12 / 1/62 |
| pb-v7 | 2.05 (1) | 1.35 (2) | 0.28 (1) | 0.36 (2) | 0/7 / 0/9 |

pb-v7 drops 62 of pb-v2's 72 above-tone flags in Hackney and adds 2 (pb-v3 drops 66). 60 of the 62 were priced runs that pb-v2 already ranked last, and 61 fail local z >= 2. Typical drops (Zone A against segment median; 6 nearest same-use shops in brackets), all with local z 0:

- 225-227 Old Street: £790 against £595 (790, 790, 790, 790, 595, 790)
- 222-224 Stamford Hill: £540 against £441 (540, 540, 540, 480, 540, 480)
- 574-576 Kingsland Road: £595 against £485 (595, 595, 532, 595, 595, 560)

The one dropped flag the VOA later cut is 75-77 Stoke Newington Road: £660 against £525, with all six neighbours at £660. It was cut to £525 from 1 Apr 2023 while its neighbours stayed at £660, so the VOA corrected one shop, not the block. Both pb-v3 and pb-v7 lose it.
