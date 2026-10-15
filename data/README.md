# Data service

Owner: person 1. DuckDB + Parquet over VOA files. Tools: search_properties, get_valuation, comparables, street_tone, historic_values (eval only), plus scan (Scout helper).

Raw files go in data/raw/ (gitignored). Parquet goes in data/parquet/ (gitignored). Tables: `*_2026` (2026 list, epoch 0004), `*_2023` (2023 list now, epoch 0023), `*_2023a` (2023 list as compiled on 30 Mar 2023, epoch 0001; eval backtest input only).

## Run

```
python3 -m venv .venv && .venv/bin/pip install -r data/requirements.txt
.venv/bin/python data/build.py                      # ~2 min, builds data/parquet/
.venv/bin/uvicorn data.service:app --port 8000
ngrok http 8000                                     # public URL -> DATA_SERVICE_URL
```

Every request except /health needs header `X-API-Key: $DATA_SERVICE_KEY` (VOA data must not be public).

## Endpoints (all GET)

- `/search_properties?q=high street wimbledon&postcode=SW19&limit=20`
- `/get_valuation/{uarn}`: list entry, summary valuation lines, Zone A price, street tone, z_score, flags, proposal (proposed_rv, annual_saving, est_saving), local_median, local_n, local_z, neighbours, latlng
- `/comparables/{uarn}?n=6`: same segment first, then same street, then same postcode sector (same SCAT); each has uarn, zone_a_price, distance_m, use
- `/street_tone?uarn=`, `?segment_key=HIGH STREET|SW19 5#682317` or `?street_key=HIGH STREET|SW19 5` (every segment on the street); `&scat_code=249&year=2026`
- `/historic_values/{uarn}`: 2023 entry, historic entries, 2023 Zone A and tone (eval only)
- `/scan?ba_code=5360&flagged_only=true` or `/scan?area=SW19`: every shop in a borough or postcode district/sector scored against its segment and its nearest neighbours (local_median, local_n, local_z, neighbours), flagged first, with lat/long, use, floor areas and source_snapshot (Hackney is 5360); `year=2023` works the same

## Definitions

- street_key = normalised street name + postcode sector, e.g. `HIGH STREET|SW19 5`
- segment_key = street_key + `#` + VOA scheme ref, e.g. `SHOREDITCH HIGH STREET|E1 6#694626`. Tone is always per segment: the VOA prices stretches of one street under different schemes (Shoreditch High St: £1,140 and £1,550). `street_n` / `street_median` / `street_spread` in responses describe the segment.
- zone_a_price = price on the largest "Retail Zone A" summary valuation line
- itza = zoned area in Zone A terms (A x1, B x0.5, C x0.25, remainder x0.125)
- spread = max(1.4826 x MAD, 5% of median); z_score = (zone_a_price - median) / spread
- flags: zone_a_above_tone (z > 2, street n >= 5), just_over_51k, just_over_500k, matches_neighbours (below). No EPC floor-area flag yet.
- house number = first integer in the address part before the first comma, ignoring a leading `(...)` clause and floor ordinals (1ST, 3RD): `225-227, OLD STREET` -> 225, `(INCL 1-9 HILLS PL) BST-3RD FLRS 215-219, ...` -> 215. No number there = no house number.
- neighbours (in `/scan` rows and at the top level of `/get_valuation`): the 4 nearest other shops with a Zone A price, the same SCAT code and the same street_key (any VOA scheme). Nearest = smallest house-number gap, then same postcode first, then uarn; shops with no house number at the same postcode fill in after numbered ones. A shop with no house number takes the shops at its own postcode (numbered first). Each is `{uarn (string), address, zone_a_price}`.
- local_n = number of neighbours (0-4); local_median = their median Zone A price; local_z = (zone_a_price - local_median) / the segment spread (the same spread as z_score). local_median and local_z are null when local_n < 2. Flag `matches_neighbours` when |zone_a_price - local_median| <= 1 segment spread: the VOA probably priced that block deliberately (e.g. 225-227 Old Street: z 2.0 against its segment, local_z 0). It describes a shop, so it never counts towards `/scan`'s `flagged`, `flagged_only` or its sort.
- proposal: Zone A cut to the street median over the ITZA, RV rounded down to £50, saving uses 2026 multipliers and years left to 31 Mar 2029
- distance_m: between postcode centroids from postcodes.io (0 = same postcode)
