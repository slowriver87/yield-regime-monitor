"""Freddie Mac PMMS: 30-year fixed rate, weekly (CSV primary, workbook second read and fallback)."""
from __future__ import annotations

import csv
import datetime as dt
import io

from .common import SourceError, fetch, mdy, num

URL_CSV = "https://www.freddiemac.com/pmms/docs/PMMS_history.csv"
URL_XLSX = "https://www.freddiemac.com/pmms/docs/historicalweeklydata.xlsx"
CSV_HEADER = ["date", "pmms30", "pmms30p", "pmms15", "pmms15p", "pmms51", "pmms51p", "pmms51m", "pmms51spread"]
XLSX_SHEET = "Full History"
XLSX_COL = "U.S. 30 yr FRM"   # the three header rows above the data, joined


def parse_csv(text: str) -> dict[str, float]:
    """{iso date: 30Y rate}. Oldest first, dates M/D/YYYY, old rows have blank or ' ' cells."""
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    if not rows or [h.strip() for h in rows[0]] != CSV_HEADER:
        raise SourceError(f"PMMS CSV: unexpected header {rows[0] if rows else None}")
    out = {}
    for r in rows[1:]:
        if len(r) > 1 and r[0].strip() and num(r[1]) is not None:
            out[mdy(r[0]).isoformat()] = num(r[1])
    if not out:
        raise SourceError("PMMS CSV: no rows")
    return out


def parse_xlsx_rows(rows: list[list]) -> dict[str, float]:
    """Sheet 'Full History': headers split over three rows ending with the row whose A cell is 'Week'."""
    hi = next((i for i, r in enumerate(rows[:20]) if r and isinstance(r[0], str) and r[0].strip() == "Week"), None)
    if hi is None or hi < 2:
        raise SourceError("PMMS workbook: 'Week' header row not found")
    width = max(len(r) for r in rows[hi - 2:hi + 1])
    heads = [" ".join(str(rows[k][j]).strip() for k in range(hi - 2, hi + 1)
                      if j < len(rows[k]) and rows[k][j] is not None) for j in range(width)]
    hits = [j for j, h in enumerate(heads) if h == XLSX_COL]
    if len(hits) != 1:
        raise SourceError(f"PMMS workbook: column {XLSX_COL!r} found {len(hits)} times")
    j = hits[0]
    out = {}
    for r in rows[hi + 1:]:
        if r and isinstance(r[0], (dt.datetime, dt.date)) and j < len(r) and num(r[j]) is not None:
            d = r[0].date() if isinstance(r[0], dt.datetime) else r[0]
            out[d.isoformat()] = num(r[j])
    if not out:
        raise SourceError("PMMS workbook: no rows")
    return out


def read_xlsx(content: bytes) -> dict[str, float]:
    import openpyxl
    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        rows = [list(r) for r in wb[XLSX_SHEET].iter_rows(values_only=True)]
    except Exception as e:
        raise SourceError(f"PMMS workbook: {e}")
    return parse_xlsx_rows(rows)


def get_csv() -> dict[str, float]:
    return parse_csv(fetch(URL_CSV).decode("utf-8-sig"))


def get_xlsx() -> dict[str, float]:
    return read_xlsx(fetch(URL_XLSX))
