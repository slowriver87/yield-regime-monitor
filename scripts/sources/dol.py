"""Department of Labor weekly claims release (data.pdf): seasonally adjusted initial claims.

dol.gov answered 403 to the build environment, so this parser follows the sentence and table
wording quoted in HANDOFF.md (verified there through a web reader) and is NOT VERIFIED against a
downloaded file. See tests/fixtures/README.md.
"""
from __future__ import annotations

import datetime as dt
import io
import re

from .common import MONTH_NAME, SourceError, fetch

URL = "https://www.dol.gov/ui/data.pdf"
NAMES = "|".join(MONTH_NAME)
RE_RELEASE = re.compile(rf"EMBARGOED UNTIL 8:30 A\.M\. \(Eastern\) [A-Z][a-z]+day, ({NAMES}) (\d{{1,2}}), (\d{{4}})")
RE_HEAD = re.compile(
    rf"In the week ending ({NAMES}) (\d{{1,2}}), the advance figure for seasonally adjusted initial claims "
    rf"was ([\d,]+), (?:an? (increase|decrease) of ([\d,]+) from|unchanged from) the previous week's "
    rf"(?:revised|unrevised) level")
# First-page table row: 'Initial Claims (SA) 199,000 198,000 +1,000 ...' (advance week, prior week, change)
RE_TABLE = re.compile(r"^Initial Claims \(SA\) ([\d,]+) ([\d,]+) ([+-]?[\d,]+)", re.M)


def parse_text(text: str) -> dict:
    """{week, released, latest, prior, table_prior} in thousands; table_prior is None when the table
    row is not found (the tie check then fails)."""
    flat = " ".join(text.split())
    rel = RE_RELEASE.search(flat)
    m = RE_HEAD.search(flat)
    if not m:
        raise SourceError("DOL: headline sentence not found")
    if not rel:
        raise SourceError("DOL: release line not found")
    released = dt.date(int(rel.group(3)), MONTH_NAME[rel.group(1)], int(rel.group(2)))
    week = dt.date(released.year, MONTH_NAME[m.group(1)], int(m.group(2)))
    if week > released:  # a December week in a January release
        week = week.replace(year=week.year - 1)
    level = int(m.group(3).replace(",", ""))
    change = int(m.group(5).replace(",", "")) if m.group(5) else 0
    prior = level - change if m.group(4) == "increase" else level + change
    t = RE_TABLE.search("\n".join(" ".join(l.split()) for l in text.splitlines()))
    table_prior = int(t.group(2).replace(",", "")) if t else None
    if t and int(t.group(1).replace(",", "")) != level:
        table_prior = None  # the table's advance figure must be the headline figure
    return {"week": week.isoformat(), "released": released.isoformat(),
            "latest": round(level / 1000), "prior": round(prior / 1000), "prior_exact": prior,
            "table_prior": table_prior}


def read_pdf(content: bytes) -> dict:
    import pdfplumber
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            text = "\n".join(p.extract_text() or "" for p in pdf.pages[:3])
    except Exception as e:
        raise SourceError(f"DOL PDF: {e}")
    return parse_text(text)


def get() -> dict:
    return read_pdf(fetch(URL))
