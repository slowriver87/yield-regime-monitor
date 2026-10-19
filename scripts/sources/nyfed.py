"""New York Fed: EFFR API (fed funds) and the Household Debt and Credit workbook (card stress)."""
from __future__ import annotations

import io
import json
import re

from .common import NotFound, SourceError, fetch, quarter_add

URL_EFFR = "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/3.json"
HHD = "https://www.newyorkfed.org/medialibrary/interactives/householdcredit/data/xls/hhd_c_report_{y}q{q}.xlsx"


def parse_effr(text: str) -> dict:
    """Newest refRates record -> {date, lo, hi, effr}. Values are JSON numbers."""
    try:
        recs = json.loads(text)["refRates"]
    except (ValueError, KeyError, TypeError) as e:
        raise SourceError(f"EFFR: {e}")
    recs = [r for r in recs if r.get("type") == "EFFR"]
    if not recs:
        raise SourceError("EFFR: no EFFR records")
    r = max(recs, key=lambda x: x["effectiveDate"])
    out = {"date": r["effectiveDate"], "lo": r.get("targetRateFrom"), "hi": r.get("targetRateTo"),
           "effr": r.get("percentRate")}
    for k in ("lo", "hi", "effr"):
        if not isinstance(out[k], (int, float)):
            out[k] = None
    return out


def get_effr() -> dict:
    return parse_effr(fetch(URL_EFFR).decode("utf-8"))


# ---------------------------------------------------------------------------
# Household Debt and Credit (verified on the 2026 Q2 workbook)

FLOW_SHEET, FLOW_TITLE, FLOW_COL = "Page 14 Data", "New Seriously Delinquent* Balances by Loan Type", "CC"
BAL_SHEET, BAL_TITLE, BAL_COL = "Page 3 Data", "Total Debt Balance and Its Composition", "Credit Card"


def _qlabel(q: str) -> str:
    """'2026Q2' -> '26:Q2' (the workbook's row labels)."""
    return f"{q[2:4]}:Q{q[5]}"


def _column(rows: list[list], title: str, col: str, sheet: str) -> dict[str, float]:
    if not rows or not isinstance(rows[0][0], str) or rows[0][0].strip() != title:
        raise SourceError(f"HHD {sheet}: title {title!r} not found in A1")
    hi = next((i for i, r in enumerate(rows[:10]) if any(isinstance(c, str) and c.strip() == col for c in r[1:])), None)
    if hi is None:
        raise SourceError(f"HHD {sheet}: column {col!r} not found")
    hits = [j for j, c in enumerate(rows[hi]) if isinstance(c, str) and c.strip() == col]
    if len(hits) != 1:
        raise SourceError(f"HHD {sheet}: column {col!r} found {len(hits)} times")
    j = hits[0]
    out = {}
    for r in rows[hi + 1:]:
        if r and isinstance(r[0], str) and re.fullmatch(r"\d\d:Q[1-4]", r[0].strip()) and j < len(r):
            if isinstance(r[j], (int, float)):
                out[r[0].strip()] = float(r[j])
    return out


def parse_hhd(sheets: dict[str, list[list]], q: str) -> dict:
    """{q, flow90, yago, bal} for quarter q ('2026Q2') from the two sheets' rows."""
    flow = _column(sheets[FLOW_SHEET], FLOW_TITLE, FLOW_COL, FLOW_SHEET)
    bal = _column(sheets[BAL_SHEET], BAL_TITLE, BAL_COL, BAL_SHEET)
    lab, ylab = _qlabel(q), _qlabel(quarter_add(q, -4))
    if lab not in flow or lab not in bal:
        raise SourceError(f"HHD: quarter {lab} not in the workbook")
    newest = max(flow, key=lambda s: ((1900 if int(s[:2]) >= 50 else 2000) + int(s[:2]), s[-1]))
    if newest != lab:
        raise SourceError(f"HHD: newest quarter is {newest}, expected {lab}")
    return {"q": q, "flow90": round(flow[lab], 2), "yago": round(flow[ylab], 2) if ylab in flow else None,
            "bal": round(bal[lab] * 1000)}


def read_hhd(content: bytes, q: str) -> dict:
    import openpyxl
    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        sheets = {n: [list(r) for r in wb[n].iter_rows(values_only=True)] for n in (FLOW_SHEET, BAL_SHEET)}
    except Exception as e:  # not a workbook, or a sheet is missing
        raise SourceError(f"HHD workbook: {e}")
    return parse_hhd(sheets, q)


def get_hhd(q: str) -> dict | None:
    """The report for quarter q, or None while it is not published (404)."""
    try:
        content = fetch(HHD.format(y=q[:4], q=q[5]))
    except NotFound:
        return None
    return read_hhd(content, q)
