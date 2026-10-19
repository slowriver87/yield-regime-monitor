"""University of Michigan Surveys of Consumers: final monthly readings (CSV files, verified Oct 19, 2026)
and the next release date from the home page."""
from __future__ import annotations

import csv
import datetime as dt
import io
import re

from .common import MONTH_NAME, SourceError, fetch, month_of, num, visible_text

URL_ICS = "https://www.sca.isr.umich.edu/files/tbmics.csv"
URL_PX = "https://www.sca.isr.umich.edu/files/tbmpx1px5.csv"
URL_HOME = "https://www.sca.isr.umich.edu/"
ICS_HEADER = ["Month", "YYYY", "ICS_ALL"]
PX_HEADER = ["Month", "YYYY", "PX_MD", "PX5_MD"]


def parse_csv(content: bytes, header: list[str], col: str) -> dict[str, float]:
    """{YYYY-MM: value}; rows like 'September,2026,48.1' (CRLF line ends)."""
    rows = list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))
    if not rows or [h.strip() for h in rows[0]] != header:
        raise SourceError(f"UMich CSV: unexpected header {rows[0] if rows else None}")
    j = header.index(col)
    out = {}
    for r in rows[1:]:
        if len(r) > j and r[0].strip() in MONTH_NAME and num(r[j]) is not None:
            out[month_of(r[0].strip(), r[1])] = num(r[j])
    if not out:
        raise SourceError("UMich CSV: no rows")
    return out


def compute(ics: dict[str, float], px: dict[str, float]) -> dict:
    m = max(ics)
    prev = max((k for k in ics if k < m), default=None)
    return {"month": m, "ics": ics[m], "px1": px.get(m), "prior": ics.get(prev) if prev else None}


def parse_next(html: bytes | str) -> dict | None:
    """'Next data release: Friday, October 23, 2026 for Final October data at 10am ET'."""
    t = visible_text(html)
    names = "|".join(MONTH_NAME)
    m = re.search(rf"Next data release: [A-Z][a-z]+day, ({names}) (\d{{1,2}}), (\d{{4}}) for "
                  rf"(Final|Preliminary) ({names}) data at (\d{{1,2}})(?::(\d{{2}}))?\s?([ap])m ET", t)
    if not m:
        return None
    h = int(m.group(6)) % 12 + (12 if m.group(8) == "p" else 0)
    return {"d": dt.date(int(m.group(3)), MONTH_NAME[m.group(1)], int(m.group(2))).isoformat(),
            "hm": (h, int(m.group(7) or 0)), "id": "umich",
            "ev": f"Consumer sentiment, {m.group(4).lower()} ({m.group(5)[:3]})"}


def get() -> dict:
    return compute(parse_csv(fetch(URL_ICS), ICS_HEADER, "ICS_ALL"), parse_csv(fetch(URL_PX), PX_HEADER, "PX_MD"))


def get_next() -> dict:
    nxt = parse_next(fetch(URL_HOME))
    if nxt is None:
        raise SourceError("UMich home page: 'Next data release' sentence not found")
    return nxt
