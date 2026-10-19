"""Treasury par and real yield curves (CSV primary, XML second read), H.15 TIPS last resort."""
from __future__ import annotations

import csv
import io
import re
import zipfile

from .common import SourceError, fetch, mdy, num

BASE = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates"
PAR, REAL = "daily_treasury_yield_curve", "daily_treasury_real_yield_curve"
URL_H15_ZIP = "https://www.federalreserve.gov/releases/h15/data/FRB_h15_xml.zip"
H15_SERIES = "RIFLGFCY10_XII_N.B"

# Exact CSV header labels (the par file mixes "Mo" and "Month"; the real file uses upper-case "YR")
PAR_HEADER = ["Date", "1 Mo", "1.5 Month", "2 Mo", "3 Mo", "4 Mo", "6 Mo", "1 Yr", "2 Yr", "3 Yr",
              "5 Yr", "7 Yr", "10 Yr", "20 Yr", "30 Yr"]
REAL_HEADER = ["Date", "5 YR", "7 YR", "10 YR", "20 YR", "30 YR"]
PAR_COLS = {"y2": "2 Yr", "y10": "10 Yr", "y30": "30 Yr"}
REAL_COLS = {"real": "10 YR"}
# XML elements (verified Oct 19, 2026)
PAR_XML = {"y2": "BC_2YEAR", "y10": "BC_10YEAR", "y30": "BC_30YEAR"}
REAL_XML = {"real": "TC_10YEAR"}


def csv_url(kind: str, year: int) -> str:
    return (f"{BASE}/daily-treasury-rates.csv/{year}/all?type={kind}"
            f"&field_tdr_date_value={year}&page&_format=csv")


def xml_url(kind: str, yyyymm: str) -> str:
    return f"{BASE}/pages/xml?data={kind}&field_tdr_date_value_month={yyyymm}"


def parse_csv(text: str, kind: str) -> dict[str, dict[str, float | None]]:
    """{iso date: {field: value}}. Rows are newest first; dates MM/DD/YYYY."""
    header, cols = (PAR_HEADER, PAR_COLS) if kind == PAR else (REAL_HEADER, REAL_COLS)
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    if not rows or [h.strip() for h in rows[0]] != header:
        raise SourceError(f"Treasury {kind} CSV: unexpected header {rows[0] if rows else None}")
    idx = {f: header.index(lab) for f, lab in cols.items()}
    out = {}
    for r in rows[1:]:
        if not r or not r[0].strip():
            continue
        try:
            d = mdy(r[0]).isoformat()
        except ValueError:
            raise SourceError(f"Treasury {kind} CSV: unexpected date {r[0]!r}")
        out[d] = {f: num(r[i]) if i < len(r) else None for f, i in idx.items()}
    if not out:
        raise SourceError(f"Treasury {kind} CSV: no rows")
    return out


def parse_xml(text: str, kind: str) -> dict[str, dict[str, float | None]]:
    """OData Atom feed: one <m:properties> per day with d:NEW_DATE and d:<element> values."""
    elems = PAR_XML if kind == PAR else REAL_XML
    out = {}
    for props in re.findall(r"<m:properties>(.*?)</m:properties>", text, flags=re.S):
        dm = re.search(r"<d:NEW_DATE[^>]*>(\d{4}-\d{2}-\d{2})T", props)
        if not dm:
            raise SourceError(f"Treasury {kind} XML: entry without d:NEW_DATE")
        vals = {}
        for f, el in elems.items():
            m = re.search(rf"<d:{el}\b[^>]*?(?:/>|>([^<]*)</d:{el}>)", props)
            vals[f] = num(m.group(1)) if m and m.group(1) is not None else None
        out[dm.group(1)] = vals
    if not out:
        raise SourceError(f"Treasury {kind} XML: no entries")
    return out


def parse_h15(xml: str) -> dict[str, float]:
    """H.15 SDMX data: the daily series RIFLGFCY10_XII_N.B; 'ND' observations are skipped."""
    m = re.search(rf'<kf:Series\b[^>]*\bSERIES_NAME="{re.escape(H15_SERIES)}"[^>]*>(.*?)</kf:Series>', xml, flags=re.S)
    if not m:
        raise SourceError(f"H.15: series {H15_SERIES} not found")
    out = {}
    for o in re.finditer(r"<frb:Obs\b([^>]*)/>", m.group(1)):
        a = dict(re.findall(r'(\w+)="([^"]*)"', o.group(1)))
        if a.get("OBS_STATUS") == "A" and num(a.get("OBS_VALUE")) is not None:
            out[a["TIME_PERIOD"]] = num(a["OBS_VALUE"])
    if not out:
        raise SourceError("H.15: no observations")
    return out


def read_h15_zip(content: bytes) -> dict[str, float]:
    try:
        z = zipfile.ZipFile(io.BytesIO(content))
        xml = z.read("H15_data.xml").decode("utf-8")
    except (zipfile.BadZipFile, KeyError) as e:
        raise SourceError(f"H.15 zip: {e}")
    return parse_h15(xml)


def get_csv(kind: str, years: list[int]) -> dict:
    out = {}
    for y in years:
        out.update(parse_csv(fetch(csv_url(kind, y)).decode("utf-8-sig"), kind))
    return out


def get_xml(kind: str, months: list[str]) -> dict:
    out = {}
    for m in months:
        out.update(parse_xml(fetch(xml_url(kind, m)).decode("utf-8"), kind))
    return out


def get_h15() -> dict[str, float]:
    return read_h15_zip(fetch(URL_H15_ZIP))
