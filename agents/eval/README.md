# eval agent

Owner: person 1. Scores each Scout / playbook version on real outcomes first. Layer 1 (the backtest) is code: `backtest.py`.

## Layer 1: outcome backtest

```
.venv/bin/python data/build.py 2023a                       # once: the 2023 list as compiled on 30 Mar 2023
.venv/bin/python agents/eval/backtest.py --ba all --show 10  # or --ba 5360 for Hackney
.venv/bin/python agents/eval/backtest.py --ba all --rules rules.json --version pb-v2 --write
```

- Inputs: only `shops_2023a` (2023 list, epoch 0001, compiled 30 Mar 2023), so nothing after April 2023 leaks in.
- Answer key: the RV was later cut in the 2023 list (epoch 0023), floor area within 2%, new entry effective 1 Apr 2023 (a correction, not a physical change). 7,023 of 466,856 shops nationally (1.5%); 45 in Hackney.
- Ranking: the Scout's rules (`DEFAULT_RULES`, overridden by a playbook's rules JSON), segment tone = street + sector + VOA scheme.
- Score: lift at 50 = top-50 hit rate / hit rate of all shops on the same streets. Streets split in half by hash: hill-climb on `train`, report `heldout`.
- `--write` upserts one `eval_runs` row per split (unique on version, dataset_snapshot, split).

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
