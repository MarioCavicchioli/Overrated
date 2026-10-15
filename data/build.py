"""Build Parquet tables from the raw VOA files in data/raw.

Run: .venv/bin/python data/build.py [2026 2023 2023a]
Writes data/parquet/*.parquet (gitignored). Takes a few minutes.

Raw files are asterisk-delimited with no header. Summary valuations mix record
types: an 01 row per property followed by its 02 (area line) rows, so we tag
each 02 row with the UARN of the 01 row above it before loading.
"""
import glob
import os
import subprocess
import sys

import duckdb

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw")
WORK = os.path.join(RAW, "_work")
OUT = os.path.join(HERE, "parquet")

# (table suffix, list year, epoch). 2023a is the 2023 list as compiled on 30 Mar 2023, before any
# later change: the eval backtest scores it and checks the answers against 2023 (epoch 0023).
DATASETS = [("2026", "2026", "0004"), ("2023", "2023", "0023"), ("2023a", "2023", "0001")]

LIST_COLS = [
    "entry_no", "ba_code", "community_code", "ba_ref", "desc_code", "desc_text",
    "uarn", "full_address", "firm_name", "number_name", "street", "town",
    "postal_district", "county", "postcode", "effective_date", "composite", "rv",
    "settlement_code", "assessment_ref", "alteration_date", "scat",
    "sub_street_3", "sub_street_2", "sub_street_1", "case_no", "from_date",
    "to_date", "_end",
]
HIST_COLS = [
    "ba_code", "community_code", "ba_ref", "desc_code", "desc_text", "uarn",
    "effective_date", "composite", "rv", "settlement_code", "assessment_ref",
    "alteration_date", "scat", "case_no", "from_date", "to_date", "_end",
]
SV_COLS = [
    "rec", "assessment_ref", "uarn", "ba_code", "firm_name", "number_name",
    "sub_street_3", "sub_street_2", "sub_street_1", "street", "postal_district",
    "town", "county", "postcode", "scheme_ref", "primary_desc", "total_area",
    "subtotal", "total_value", "adopted_rv", "list_year", "ba_name", "ba_ref",
    "vo_ref", "from_date", "to_date", "scat", "unit", "unadjusted_price",
]
LINE_COLS = ["uarn", "assessment_ref", "rec", "line_no", "floor", "description", "area", "price", "value"]


def one(pattern):
    hits = glob.glob(os.path.join(RAW, pattern))
    if len(hits) != 1:
        raise SystemExit(f"expected one file for {pattern}, found {hits}")
    return hits[0]


def split_sv(src, year):
    """Split a summary valuation file into 01 rows and 02 rows tagged with uarn."""
    heads = os.path.join(WORK, f"sv{year}_01.csv")
    lines = os.path.join(WORK, f"sv{year}_02.csv")
    if not (os.path.exists(heads) and os.path.exists(lines)):
        awk = (
            '$1=="01"{u=$3; a=$2; print > H} '
            '$1=="02"{print u "*" a "*" $0 > L}'
        )
        subprocess.run(
            ["awk", "-F*", "-v", f"H={heads}", "-v", f"L={lines}", awk, src],
            check=True, env={**os.environ, "LC_ALL": "C"},
        )
    return heads, lines


def read_csv(path, cols):
    names = ", ".join(f"'{c}': 'VARCHAR'" for c in cols)
    return (
        f"read_csv('{path}', delim='*', header=false, quote='', escape='', "
        f"columns={{{names}}}, null_padding=true, ignore_errors=true)"
    )


def main():
    os.makedirs(WORK, exist_ok=True)
    os.makedirs(OUT, exist_ok=True)
    con = duckdb.connect()

    # helpers: street key = normalised street + postcode sector ("SW1A 1")
    con.execute("""
        create macro norm(s) as upper(trim(regexp_replace(coalesce(s, ''), '\\s+', ' ', 'g')));
        create macro sector(pc) as nullif(trim(left(norm(pc), length(norm(pc)) - 2)), '');
        create macro street_key(st, pc) as norm(st) || '|' || coalesce(sector(pc), '');
        create macro num(s) as try_cast(nullif(trim(s), '') as double);
    """)

    only = set(sys.argv[1:])  # e.g. `build.py 2023a` rebuilds just that dataset
    for year, list_year, epoch in DATASETS:
        if only and year not in only:
            continue
        le = one(f"*{list_year}-listentries-compiled-epoch-{epoch}-baseline-csv/*baseline-csv.csv")
        hist = glob.glob(os.path.join(RAW, f"*{list_year}-listentries-compiled-epoch-{epoch}-baseline-csv/*historic-csv.csv"))
        sv = one(f"*{list_year}-summaryvaluations-compiled-epoch-{epoch}-baseline-csv/*.csv")
        heads, lines = split_sv(sv, year)
        print(f"[{year}] loading list entries")

        con.execute(f"""
            copy (
              select ba_code, ba_ref, desc_code, desc_text, uarn::bigint as uarn,
                     full_address, firm_name, number_name, street, town, postal_district,
                     county, norm(postcode) as postcode, sector(postcode) as postcode_sector,
                     street_key(street, postcode) as street_key,
                     try_strptime(effective_date, '%d-%b-%Y')::date as effective_date,
                     composite, num(rv)::bigint as rv, settlement_code, assessment_ref,
                     left(scat, 3) as scat_code, scat,
                     try_strptime(from_date, '%d-%b-%Y')::date as from_date
              from {read_csv(le, LIST_COLS)}
              where try_cast(uarn as bigint) is not null
            ) to '{OUT}/list_{year}.parquet' (format parquet)
        """)

        if hist:  # epoch 0001 of a list has no historic entries yet
          con.execute(f"""
            copy (
              select ba_code, ba_ref, desc_code, desc_text, uarn::bigint as uarn,
                     try_strptime(effective_date, '%d-%b-%Y')::date as effective_date,
                     composite, num(rv)::bigint as rv, assessment_ref,
                     left(scat, 3) as scat_code,
                     try_strptime(from_date, '%d-%b-%Y')::date as from_date,
                     try_strptime(to_date, '%d-%b-%Y')::date as to_date
              from {read_csv(hist[0], HIST_COLS)}
              where try_cast(uarn as bigint) is not null
            ) to '{OUT}/historic_{year}.parquet' (format parquet)
        """)

        print(f"[{year}] loading summary valuations")
        con.execute(f"""
            copy (
              select assessment_ref, uarn::bigint as uarn, ba_code, ba_name, scheme_ref,
                     primary_desc, num(total_area) as total_area, num(total_value) as total_value,
                     num(adopted_rv)::bigint as adopted_rv, unit, num(unadjusted_price) as unadjusted_price,
                     left(scat, 3) as scat_code,
                     try_strptime(from_date, '%d-%b-%Y')::date as from_date
              from {read_csv(heads, SV_COLS)}
              where try_cast(uarn as bigint) is not null
            ) to '{OUT}/sv_{year}.parquet' (format parquet)
        """)
        con.execute(f"""
            copy (
              select uarn::bigint as uarn, assessment_ref, line_no::int as line_no, floor,
                     description, num(area) as area, num(price) as price, num(value) as value
              from {read_csv(lines, LINE_COLS)}
              where try_cast(uarn as bigint) is not null
            ) to '{OUT}/sv_lines_{year}.parquet' (format parquet)
        """)

        # One row per property with a retail zoning: Zone A price from the
        # biggest Zone A line, plus the zoned area in "Zone A terms" (ITZA).
        print(f"[{year}] building shops table")
        con.execute(f"""
            copy (
              with za as (
                select uarn, assessment_ref,
                       arg_max(price, area) as zone_a_price,
                       sum(area) as zone_a_area
                from '{OUT}/sv_lines_{year}.parquet'
                where description = 'Retail Zone A' and price > 0
                group by all
              ),
              zones as (
                select uarn, assessment_ref,
                       sum(area * case description
                         when 'Retail Zone A' then 1 when 'Retail Zone B' then 0.5
                         when 'Retail Zone C' then 0.25 when 'Remaining Retail Zone' then 0.125
                         when 'Retail Zone D' then 0.125 else 0 end) as itza
                from '{OUT}/sv_lines_{year}.parquet'
                group by all
              )
              select l.uarn, l.full_address as address, l.postcode, l.postcode_sector,
                     l.street_key, l.street, l.town, l.ba_code, l.scat_code, l.desc_text,
                     l.rv, s.total_area, s.adopted_rv, s.scheme_ref,
                     za.zone_a_price, za.zone_a_area, zones.itza
              from '{OUT}/list_{year}.parquet' l
              join '{OUT}/sv_{year}.parquet' s using (uarn, assessment_ref)
              join za using (uarn, assessment_ref)
              left join zones using (uarn, assessment_ref)
            ) to '{OUT}/shops_{year}.parquet' (format parquet)
        """)

    for f in sorted(glob.glob(os.path.join(OUT, "*.parquet"))):
        n = con.execute(f"select count(*) from '{f}'").fetchone()[0]
        print(f"{os.path.basename(f):28s} {n:>10,d} rows")


if __name__ == "__main__":
    main()
