"""Render a pack as a one-page PDF for the owner and the case file."""

from __future__ import annotations

from fpdf import FPDF

_REPLACE = {"–": "-", "—": "-", "‘": "'", "’": "'",
            "“": '"', "”": '"', "×": "x", "≤": "<=", "≥": ">="}


def _latin1(text: str) -> str:
    for k, v in _REPLACE.items():
        text = text.replace(k, v)
    return text.encode("latin-1", "replace").decode("latin-1")


def _num(x: float) -> str:
    """405.0 -> '405', 27.5 -> '27.5', 35475 -> '35,475'."""
    return f"{x:,.0f}" if float(x).is_integer() else f"{x:,.1f}"


def _para(pdf: FPDF, text: str, h: float) -> None:
    pdf.multi_cell(pdf.epw, h, _latin1(text), new_x="LMARGIN", new_y="NEXT")


def render_pack_pdf(pack: dict) -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_margins(18, 18)
    w = pdf.epw

    pdf.set_font("Helvetica", "B", 16)
    _para(pdf, f"Check and Challenge evidence pack {pack['case_id']}", 8)
    pdf.set_font("Helvetica", "", 11)
    _para(pdf, f"{pack['address']}  (UARN {pack['uarn']})", 6)
    pdf.ln(3)

    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(w, 7, "Summary", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    c = pack["calc"]
    for line in (
        f"Current rateable value: £{pack['current_rv']:,}",
        f"Proposed rateable value: £{pack['proposed_rv']:,}",
        f"Estimated annual saving: £{pack['annual_saving']:,}",
        f"Zone A price: £{_num(c['zone_a_price'])}/m2 vs street median "
        f"£{_num(c['street_median'])}/m2 (spread £{_num(c['street_spread'])})",
    ):
        pdf.cell(w, 6, _latin1(line), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(w, 7, "Comparables", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "B", 10)
    cols = (40, 40, 40, 40)
    for head, cw in zip(("UARN", "Use", "Zone A £/m2", "Distance (m)"), cols):
        pdf.cell(cw, 6, head, border=1)
    pdf.ln()
    pdf.set_font("Helvetica", "", 10)
    for comp in pack["comparables"]:
        for val, cw in zip((comp["uarn"], comp["use"], _num(comp["zone_a_price"]),
                            _num(comp["distance_m"])), cols):
            pdf.cell(cw, 6, _latin1(str(val)), border=1)
        pdf.ln()
    pdf.ln(3)

    if pack.get("exclusions"):
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(w, 7, "Comparables left out", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)
        for ex in pack["exclusions"]:
            _para(pdf, f"{ex['uarn']} (Zone A £{_num(ex['zone_a_price'])}/m2, "
                       f"{_num(ex['distance_m'])} m): {ex['reason']}", 5)
        pdf.ln(3)

    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(w, 7, "Argument", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    _para(pdf, pack["argument"], 6)
    pdf.ln(4)
    pdf.set_font("Helvetica", "I", 8)
    _para(pdf, f"Prepared by {pack['version']}. Figures from the VOA 2026 rating list, "
               "used for rating purposes only. Not yet filed: filing needs the owner's approval.", 4)
    return bytes(pdf.output())
