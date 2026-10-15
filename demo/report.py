"""Printable evidence-pack report (A4) for one case: GET /report/{case_id}.

Every figure comes from Supabase (the pack, verdict and case rows) or the data service (list entry,
summary valuation, street tone). Every value is HTML-escaped.
"""
import html

from demo import pipeline as P

STAGE_WORDS = {
    "waiting_owner": "Waiting for the owner's approval to file the Check",
    "check_draft": "Check being drafted",
    "check_submitted": "Check filed (mock); waiting for the VOA's decision",
    "waiting_check_decision": "Check filed (mock); waiting for the VOA's decision",
    "challenge_draft": "Check decided with no change; drafting the Challenge",
    "challenge_submitted": "Challenge filed (mock)",
    "waiting_challenge_decision": "Challenge filed (mock); waiting for the VOA's decision",
    "resolved": "Resolved",
    "closed": "Closed",
    "needs_human": "Needs a person to review",
}


def e(x):
    return html.escape("" if x is None else str(x), quote=True)


def pct(a, b):
    d = P.pct_diff(a, b)
    return "n/a" if d is None else (f"+{d}%" if d > 0 else f"−{-d}%" if d < 0 else "0%")


def signed_price(x):
    if x is None:
        return "n/a"
    s = P.price(abs(x))
    return s if abs(x) < 0.005 else ("+" if x > 0 else "−") + s


def m(x):
    return P.m2(x)


def table(head, rows, numeric=(), cls=""):
    th = "".join(f'<th class="{"n" if i in numeric else ""}" scope="col">{e(h)}</th>' for i, h in enumerate(head))
    body = []
    for r in rows:
        row_cls, cells = (r[0], r[1]) if isinstance(r, tuple) else ("", r)
        tds = "".join(f'<td class="{"n" if i in numeric else ""}">{c}</td>' for i, c in enumerate(cells))
        body.append(f'<tr class="{row_cls}">{tds}</tr>')
    return (f'<div class="table-wrap"><table class="{cls}"><thead><tr>{th}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


def render(case_id):
    c = P.case_view(case_id)
    pack, verdict = c["pack"], c["verdict"]
    v = P.get_valuation(c["uarn"])
    entry, shop, summary = v["entry"], v.get("shop") or {}, v.get("summary") or {}
    comps = pack["comparables"]
    comp_uarns = {str(x["uarn"]) for x in comps}
    seg = P.segment_view(shop, c["uarn"], comp_uarns) if shop else None
    prop = pack.get("proposal") or v.get("proposal") or {}
    calc = pack.get("calc") or {}
    ref = pack.get("case_ref") or "case"
    za, med, spread = calc.get("zone_a_price"), calc.get("street_median"), calc.get("street_spread")
    itza = shop.get("itza")
    current, proposed, annual = pack["current_rv"], pack["proposed_rv"], pack["annual_saving"]
    retail = entry.get("scat_code") in P.RETAIL_SCAT
    mult_now, mult_after = P.multiplier(current, retail), P.multiplier(proposed, retail)
    street = P.street_name(shop.get("street_key") or entry.get("street_key"))
    created = pack.get("created_at")
    addr_by_uarn = {str(x["uarn"]): x.get("address") for x in comps if x.get("address")}
    for s in (seg or {}).get("shops", []):
        addr_by_uarn.setdefault(s["uarn"], s["address"])

    def record_address(uarn):
        uarn = str(uarn)
        if uarn not in addr_by_uarn:
            try:
                addr_by_uarn[uarn] = P.get_valuation(uarn)["entry"]["full_address"]
            except P.DemoError:
                addr_by_uarn[uarn] = None
        return addr_by_uarn[uarn]

    # status line
    if c["stage"]:
        status = STAGE_WORDS.get(c["stage"], c["stage"])
        if c["stage"] == "resolved" and c.get("outcome"):
            status += f": {c['outcome']}"
    elif verdict and verdict["verdict"] != "survive":
        status = "Not filed: the VOA advocate killed the case"
    else:
        status = "Under review"
    verdict_badge = ""
    if verdict:
        word = "Survives" if verdict["verdict"] == "survive" else "Killed" if verdict["verdict"] == "kill" else "Needs evidence"
        verdict_badge = f'<span class="badge {e(verdict["verdict"])}">{e(word)}</span>'

    sections = []

    # 1 summary
    n = seg["n"] if seg else None
    lead = (f"The Zone A price of {e(P.price(za))} is {e(P.pct_diff(za, med))}% above the {e(P.price(med))} median "
            f"for the {e(n)} shops on this stretch of {e(street)} (VOA scheme {e(shop.get('scheme_ref'))}). "
            f"Brought into line with the tone of the list, the rateable value falls from {e(P.gbp(current))} "
            f"to {e(P.gbp(proposed))}.")
    sections.append(("Summary", f"""
      <div class="figures">
        <div><span class="l">Current rateable value</span><span class="v">{e(P.gbp(current))}</span></div>
        <div><span class="l">Proposed rateable value</span><span class="v">{e(P.gbp(proposed))}</span></div>
        <div><span class="l">Saving per year</span><span class="v pos">{e(P.gbp(annual))}</span></div>
        <div><span class="l">Saving to 31 Mar 2029</span><span class="v pos">{e(P.gbp(prop.get('est_saving')))}</span></div>
      </div>
      <p class="lead">{lead}</p>
      <p class="status"><span class="k">Independent review</span> {verdict_badge or 'Pending'}
         <span class="sep"></span><span class="k">Status</span> {e(status)}</p>"""))

    # 2 property
    kv = [
        ("Address", entry.get("full_address")), ("Postcode", entry.get("postcode")),
        ("Description", entry.get("desc_text")), ("Billing authority", summary.get("ba_name")),
        ("UARN", entry.get("uarn")), ("Assessment reference", entry.get("assessment_ref")),
        ("VOA scheme", summary.get("scheme_ref")), ("In the list from", P.fmt_date(entry.get("from_date"))),
        ("Total area", f"{m(summary.get('total_area'))} ({summary.get('unit') or 'NIA'})"),
        ("Rateable value", P.gbp(entry.get("rv"))),
        ("Estimated bill before reliefs", f"£{current * mult_now:,.2f} a year at {mult_now * 100:.1f}p "
                                          f"({P.band_label(current, retail)})"),
    ]
    sections.append(("Property", '<dl class="kv">' + "".join(
        f"<dt>{e(k)}</dt><dd>{e(val)}</dd>" for k, val in kv) + "</dl>"))

    # 3 zoning
    lines = v.get("lines") or []
    rows = [[e(ln.get("floor")), e(ln.get("description")), e(m(ln.get("area")) if ln.get("area") else "–"),
             e(P.price(ln.get("price"))), e(P.gbp(ln.get("value")))] for ln in lines]
    total = sum(ln.get("value") or 0 for ln in lines)
    rows.append(("total", ["", "Total of the valuation lines", "", "", e(P.gbp(total))]))
    rows.append(("total", ["", "Adopted rateable value (rounded)", "", "", e(P.gbp(summary.get("adopted_rv")))]))
    zl = [ln for ln in lines if ln.get("description") in P.ZONE_FACTORS and ln.get("area")]
    itza_terms = " + ".join(
        f"{m(ln['area'])}" + ("" if P.ZONE_FACTORS[ln["description"]] == 1
                              else f" ÷ {int(1 / P.ZONE_FACTORS[ln['description']])}") for ln in zl)
    sections.append(("Zoning breakdown", table(["Floor", "Description", "Area", "Price", "Value"], rows,
                                               numeric=(2, 3, 4)) +
                     f'<p class="note">Zone A is the first 6.1 m from the shop window, at the full price per m²; '
                     f'Zone B counts at a half, Zone C at a quarter, the remainder at an eighth. '
                     f'Area in Zone A terms (ITZA): {e(itza_terms)} = {e(m(itza))}.</p>'))

    # 4 street tone
    if seg:
        tone_rows = [[e(seg["segment_key"].split("#")[0].replace("|", ", ")), e(seg.get("scheme_ref")),
                      e(seg["n"]), e(P.price(seg["median"])), e(f"{P.price(seg['p25'])} – {P.price(seg['p75'])}"),
                      e(P.price(seg["spread"])), e(P.price(za)), e(f"{c['candidate']['z_score']:.2f}"
                                                                   if c.get("candidate") and c["candidate"].get("z_score") is not None else "n/a")]]
        shop_rows = []
        for s in seg["shops"][:60]:
            tag = " (subject)" if s["is_subject"] else " (comparable)" if s["is_comparable"] else ""
            shop_rows.append(("subject" if s["is_subject"] else "",
                              [e(P.short_address(s["address"])) + f'<span class="tag">{e(tag)}</span>',
                               e((s.get("use") or "").capitalize()), e(P.price(s["zone_a_price"])),
                               e(m(s.get("itza"))), e(P.gbp(s.get("rv"))),
                               e(f"{s['z']:.2f}" if s.get("z") is not None else "")]))
        more = (f'<p class="note">Showing 60 of {seg["n"]} shops.</p>' if seg["n"] > 60 else "")
        sections.append(("Street tone", table(
            ["Segment", "Scheme", "Shops", "Median", "Middle half", "Spread", "Subject", "z-score"], tone_rows,
            numeric=(2, 3, 4, 5, 6, 7)) +
            '<p class="note">A segment is one street, postcode sector and VOA valuation scheme. The spread is '
            '1.4826 × the median absolute deviation, at least 5% of the median; the z-score is the subject\'s '
            'distance from the median in spreads.</p>' +
            '<h3>Every shop on the segment</h3>' +
            table(["Address", "Use", "Zone A", "ITZA", "RV", "z"], shop_rows, numeric=(2, 3, 4, 5), cls="dense") + more))

    # 5 comparables
    comp_rows = []
    for i, x in enumerate(comps, 1):
        diff = x["zone_a_price"] - za if za is not None else None
        comp_rows.append([e(i), e(P.short_address(x.get("address")) or x["uarn"])
                          + f'<span class="sub">UARN {e(x["uarn"])}</span>',
                          e((x.get("use") or "").capitalize()), e(P.price(x["zone_a_price"])),
                          e(f"{signed_price(diff)} ({pct(x['zone_a_price'], za)})"), e(m(x.get("itza"))),
                          e(f"{x['distance_m']:,.0f} m"), e(P.gbp(x.get("rv")))])
    sections.append(("Comparables", table(["", "Address", "Use", "Zone A", "Against subject", "ITZA", "Distance", "RV"],
                                          comp_rows, numeric=(3, 4, 5, 6, 7)) +
                     '<p class="note">Chosen from the data service: same street segment first, the same use and an '
                     'ITZA within half to double the subject\'s where possible, nearest first. Distances are between '
                     'postcode centroids.</p>'))

    # 6 calculation
    drop = (za - med) * itza if None not in (za, med, itza) else None
    calc_rows = [
        ["Zone A price of the subject", e(P.price(za))],
        ["Median Zone A price on the segment", e(P.price(med))],
        ["Difference", e(P.price(za - med) if None not in (za, med) else "n/a")],
        ["× area in Zone A terms (ITZA)", e(m(itza))],
        ["= Reduction in rateable value", e(P.gbp(drop))],
        ["Current rateable value", e(P.gbp(current))],
        ("total", ["Proposed rateable value (rounded down to £50)", e(P.gbp(proposed))]),
        [e(f"Multiplier now ({P.band_label(current, retail)})"), e(f"{mult_now * 100:.1f}p")],
        [e(f"Multiplier after ({P.band_label(proposed, retail)})"), e(f"{mult_after * 100:.1f}p")],
        ["Bill now, before reliefs", e(f"£{current * mult_now:,.2f}")],
        ["Bill after, before reliefs", e(f"£{proposed * mult_after:,.2f}")],
        ("total", ["Annual saving (rounded)", e(P.gbp(annual))]),
        ["Years left on the 2026 list (to 31 March 2029)", e(prop.get("years_left", "n/a"))],
        ("total", ["Saving to 31 March 2029", e(P.gbp(prop.get("est_saving")))]),
    ]
    sections.append(("Calculation", table(["Step", "Value"], calc_rows, numeric=(1,), cls="calc")))

    # 7 argument
    paras = "".join(f"<p>{e(p.strip())}</p>" for p in (pack.get("argument") or "").split("\n") if p.strip())
    sections.append(("Argument", f'<div class="argument">{paras}</div>'))

    # 8 advocate
    if verdict:
        reasons = "".join(
            f'<li><p>{e(r.get("reason"))}</p><p class="rec">VOA record: '
            f'{e(P.short_address(record_address(r.get("voa_record"))) or "not found")} (UARN {e(r.get("voa_record"))})</p></li>'
            for r in verdict.get("reasons") or [])
        sections.append(("Independent review by the VOA advocate", f"""
          <p>{verdict_badge} <span class="muted">{e(verdict.get('advocate_version'))}, {e(P.fmt_date(verdict.get('created_at')))}</span></p>
          <p class="note">A separate reviewer saw only this pack and the VOA data, argued the VOA's side and tried to
          kill the case: weaker comparables, different use or size, frontage, maths errors and deliberately priced runs
          of shops.</p>
          <ol class="reasons">{reasons}</ol>"""))
    else:
        sections.append(("Independent review by the VOA advocate", "<p>No verdict recorded yet.</p>"))

    # 9 filing
    if c["stage"]:
        approved_at = next((h.get("ts") for h in c.get("history") or [] if h.get("event") == "owner_approved"), None)
        kv = [("Stage", STAGE_WORDS.get(c["stage"], c["stage"])),
              ("Owner approval", f"Approved {P.fmt_date(approved_at)}" if c["owner_approved"] else "Not yet approved"),
              ("Mock VOA reference", c.get("external_case_ref") or "Not filed"),
              ("Filed", P.fmt_date(c.get("submitted_at")) if c.get("submitted_at") else "Not filed"),
              ("Next action", c.get("next_action") or "None"),
              ("Next deadline", P.fmt_date(c.get("next_deadline")) if c.get("next_deadline") else "None")]
        if c.get("outcome"):
            kv += [("Outcome", c["outcome"]), ("Final rateable value", P.gbp(c.get("final_rv"))),
                   ("Confirmed saving", f"{P.gbp(c.get('confirmed_annual_saving'))} a year")]
        sections.append(("Filing", '<dl class="kv">' + "".join(
            f"<dt>{e(k)}</dt><dd>{e(val)}</dd>" for k, val in kv) + "</dl>"))

    # 10 sources
    sections.append(("Sources", f"""
      <ul class="sources">
        <li>VOA 2026 rating list, compiled list, epoch 0004 ({e(P.SNAPSHOT)}), and its summary valuations.</li>
        <li>Overrated data service: get_valuation, comparables and street_tone for UARN {e(c['uarn'])}.</li>
        <li>Postcode centroids for distances: postcodes.io (ONS open data).</li>
        <li>Multipliers for 2026/27; saving before reliefs, to the end of the 2026 list on 31 March 2029.</li>
        <li>Pack {e(ref)}, version {e(pack.get('pack_version'))}, built by {e(pack.get('builder_version'))} on
            {e(P.fmt_date(created))} and validated against pack.schema.json.</li>
        <li>VOA data is licensed for rating purposes only.</li>
      </ul>"""))

    body = "".join(f'<section><h2><span class="no">{i}</span>{e(title)}</h2>{content}</section>'
                   for i, (title, content) in enumerate(sections, 1))
    generated = P.now()
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Evidence pack {e(ref)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>{CSS}</style></head>
<body>
<div class="toolbar">
  <a href="/#/case/{e(case_id)}">Back to the case</a>
  <button type="button" class="print" onclick="window.print()">Print / Save as PDF</button>
</div>
<main class="page">
  <header class="doc">
    <div>
      <div class="wordmark">Overrated</div>
      <h1>Evidence pack: Check and Challenge</h1>
      <div class="subtitle">{e(P.short_address(c['address']))}, {e(entry.get('postcode'))}</div>
    </div>
    <div class="meta">
      <div><b>Case {e(ref)}</b></div>
      <div>{e(P.fmt_date(created))}</div>
      <div>Pack v{e(pack.get('pack_version'))}</div>
    </div>
  </header>
  {body}
  <footer class="doc">
    <span class="mock">Mock filing: no submission was made to the VOA.</span>
    <span>Generated {e(P.fmt_date(generated))}, {e(P.fmt_time(generated))}</span>
  </footer>
</main>
</body></html>"""


CSS = """
:root { --bg:#f6f6f3; --surface:#fff; --border:#e4e4df; --text:#16181d; --text-2:#5b616e; --muted:#8a909c;
  --action:#1f4fd1; --red:#b42318; --green:#067647; --amber:#b54708; }
* { box-sizing: border-box }
html, body { background: var(--bg) }
body { margin: 0; color: var(--text); font: 13px/1.5 Inter, system-ui, -apple-system, "Segoe UI", sans-serif;
  -webkit-font-smoothing: antialiased; font-variant-numeric: tabular-nums }
a { color: var(--action) }
a:focus-visible, button:focus-visible { outline: 2px solid var(--action); outline-offset: 2px }
.toolbar { width: 210mm; max-width: calc(100% - 32px); margin: 24px auto 0; display: flex;
  justify-content: space-between; align-items: center; gap: 16px }
.print { height: 40px; padding: 0 16px; border: 0; border-radius: 6px; background: var(--action); color: #fff;
  font: 500 14px Inter, system-ui, sans-serif; cursor: pointer }
.page { display: block; width: 210mm; max-width: calc(100% - 32px); margin: 16px auto 48px; background: var(--surface);
  border: 1px solid var(--border); border-radius: 6px; box-shadow: 0 1px 2px rgba(0,0,0,.05); padding: 18mm 18mm 14mm }
header.doc { display: flex; justify-content: space-between; gap: 24px; padding-bottom: 14px;
  border-bottom: 1px solid var(--text) }
.wordmark { font-weight: 600; font-size: 13px; letter-spacing: -.01em }
h1 { font-size: 22px; font-weight: 600; margin: 6px 0 2px; letter-spacing: -.01em; line-height: 1.25 }
.subtitle { color: var(--text-2) }
.meta { text-align: right; color: var(--text-2); font-size: 12px; white-space: nowrap }
.meta b { color: var(--text); font-size: 14px; font-weight: 600 }
section { margin-top: 22px }
h2 { display: flex; gap: 10px; margin: 0 0 10px; padding-bottom: 6px; border-bottom: 1px solid var(--border);
  font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: .04em; color: var(--text-2) }
h2 .no { color: var(--muted); min-width: 14px }
h3 { font-size: 12px; font-weight: 600; margin: 16px 0 6px }
p { margin: 0 0 8px; max-width: 70ch }
.lead { margin-top: 12px }
.note { color: var(--text-2); font-size: 12px; margin-top: 8px }
.muted { color: var(--muted) }
.figures { display: grid; grid-template-columns: repeat(4, 1fr); border: 1px solid var(--border); border-radius: 6px }
.figures > div { padding: 10px 12px; border-left: 1px solid var(--border); display: flex; flex-direction: column; gap: 2px }
.figures > div:first-child { border-left: 0 }
.figures .l { font-size: 11px; color: var(--text-2) }
.figures .v { font-size: 20px; font-weight: 600; letter-spacing: -.01em }
.pos { color: var(--green) }
.status { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; color: var(--text) }
.status .k { color: var(--text-2); font-size: 12px }
.status .sep { width: 1px; height: 14px; background: var(--border); margin: 0 4px }
.badge { display: inline-block; padding: 1px 8px; border-radius: 999px; border: 1px solid currentColor;
  font-size: 11px; font-weight: 600 }
.badge.survive { color: var(--green) } .badge.kill { color: var(--red) } .badge.needs_evidence { color: var(--amber) }
dl.kv { display: grid; grid-template-columns: 200px 1fr; gap: 4px 16px; margin: 0 }
dl.kv dt { color: var(--text-2) } dl.kv dd { margin: 0 }
.table-wrap { width: 100%; overflow-x: auto }
table { width: 100%; border-collapse: collapse; font-size: 12px; page-break-inside: avoid; break-inside: avoid }
table.dense { font-size: 11.5px; page-break-inside: auto; break-inside: auto }
th { text-align: left; font-weight: 500; color: var(--text-2); padding: 6px 10px 6px 0; border-bottom: 1px solid var(--text-2);
  white-space: nowrap }
td { padding: 6px 10px 6px 0; border-bottom: 1px solid var(--border); vertical-align: top }
th.n, td.n { text-align: right; white-space: nowrap }
th:last-child, td:last-child { padding-right: 0 }
tr.subject td { font-weight: 600 }
tr.total td { font-weight: 600; border-top: 1px solid var(--text-2) }
table.dense tr { page-break-inside: avoid; break-inside: avoid }
.tag { color: var(--text-2); font-weight: 400 }
.sub { display: block; color: var(--muted); font-size: 11px }
table.calc td:first-child { color: var(--text-2) }
table.calc tr.total td:first-child { color: var(--text) }
.argument p { font-size: 13px; line-height: 1.6 }
ol.reasons { margin: 8px 0 0; padding-left: 20px }
ol.reasons li { margin-bottom: 8px }
ol.reasons p { margin: 0 }
.rec { color: var(--text-2); font-size: 12px }
ul.sources { margin: 0; padding-left: 18px; color: var(--text-2); font-size: 12px }
footer.doc { display: flex; justify-content: space-between; gap: 16px; flex-wrap: wrap; margin-top: 28px; padding-top: 10px;
  border-top: 1px solid var(--border); color: var(--text-2); font-size: 11px }
.mock { color: var(--amber); font-weight: 600 }
@media (max-width: 760px) {
  .page { padding: 20px 16px; max-width: calc(100% - 32px) }
  header.doc { flex-direction: column; gap: 8px } .meta { text-align: left }
  .figures { grid-template-columns: 1fr 1fr }
  .figures > div:nth-child(3) { border-left: 0 }
  .figures > div:nth-child(n+3) { border-top: 1px solid var(--border) }
  dl.kv { grid-template-columns: 1fr } dl.kv dd { margin-bottom: 6px }
}
@media print {
  @page { size: A4; margin: 16mm 15mm }
  html, body { background: #fff }
  .toolbar { display: none }
  .page { width: auto; max-width: none; margin: 0; padding: 0; border: 0; border-radius: 0; box-shadow: none }
  a { color: inherit; text-decoration: none }
  section { page-break-inside: auto }
  h2, h3 { page-break-after: avoid; break-after: avoid }
}
"""
