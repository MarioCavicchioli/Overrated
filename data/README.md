# Data service

Owner: person 1. DuckDB + Parquet over VOA files. Tools: search_properties, get_valuation, comparables, street_tone, historic_values (eval only), plus scan (Scout helper).

Raw files go in data/raw/ (gitignored). Parquet goes in data/parquet/ (gitignored).

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
- `/get_valuation/{uarn}`: list entry, summary valuation lines, Zone A price, street tone, z_score, flags, proposal (proposed_rv, annual_saving, est_saving), latlng
- `/comparables/{uarn}?n=6`: same segment first, then same street, then same postcode sector (same SCAT); each has uarn, zone_a_price, distance_m, use
- `/street_tone?uarn=`, `?segment_key=HIGH STREET|SW19 5#682317` or `?street_key=HIGH STREET|SW19 5` (every segment on the street); `&scat_code=249&year=2026`
- `/historic_values/{uarn}`: 2023 entry, historic entries, 2023 Zone A and tone (eval only)
- `/scan?ba_code=5360&flagged_only=true` or `/scan?area=SW19`: every shop in a borough or postcode district/sector scored against its segment, flagged first, with lat/long, use, floor areas and source_snapshot (Hackney is 5360)

## Definitions

- street_key = normalised street name + postcode sector, e.g. `HIGH STREET|SW19 5`
- segment_key = street_key + `#` + VOA scheme ref, e.g. `SHOREDITCH HIGH STREET|E1 6#694626`. Tone is always per segment: the VOA prices stretches of one street under different schemes (Shoreditch High St: £1,140 and £1,550). `street_n` / `street_median` / `street_spread` in responses describe the segment.
- zone_a_price = price on the largest "Retail Zone A" summary valuation line
- itza = zoned area in Zone A terms (A x1, B x0.5, C x0.25, remainder x0.125)
- spread = max(1.4826 x MAD, 5% of median); z_score = (zone_a_price - median) / spread
- flags: zone_a_above_tone (z > 2, street n >= 5), just_over_51k, just_over_500k. No EPC floor-area flag yet.
- proposal: Zone A cut to the street median over the ITZA, RV rounded down to £50, saving uses 2026 multipliers and years left to 31 Mar 2029
- distance_m: between postcode centroids from postcodes.io (0 = same postcode)
