"""BEA: NIPA tables through the API (GDP, investment, final sales, saving rate, real PCE, core PCE)
and the release-dates file for the calendar.

The API could not be read from the build environment (no key there), so the table parser follows
the BEA API documentation (https://apps.bea.gov/api/_pdf/bea_web_service_api_user_guide.pdf).
Line labels are matched exactly; when one is missing, the error lists the table's labels so the run
summary shows what to map. See tests/fixtures/README.md.
"""
from __future__ import annotations

import datetime as dt
import json
import re

from .common import SourceError, fetch, month_add, num, quarter_add

URL_API = ("https://apps.bea.gov/api/data/?UserID={k}&method=GetData&DataSetName=NIPA&TableName={t}"
           "&Frequency={f}&Year={y}&ResultFormat=JSON")
URL_DATES = "https://apps.bea.gov/API/signup/release_dates.json"

GDP_LINE = ("T10101", "Gross domestic product")
BFI_LINE = ("T10101", "Fixed investment", "Nonresidential")   # table, section parent, line
FSPDP_LINE = ("T10401", "Final sales to private domestic purchasers")  # NOT VERIFIED (BEA key needed)
SAVE_LINE = ("T20600", "Personal saving as a percentage of disposable personal income")
RPCE_LINE = ("T20806", "Personal consumption expenditures (PCE)")
CORE_LINE = ("T20804", "PCE excluding food and energy")


def parse_api(text: str) -> list[dict]:
    """BEAAPI.Results.Data[] rows -> [{line, label, period, value}] in table order.
    Periods: '2026Q2'; monthly '2026M8' or '2026M08' -> '2026-08'. DataValue has thousands commas."""
    try:
        d = json.loads(text)["BEAAPI"]
    except (ValueError, KeyError, TypeError) as e:
        raise SourceError(f"BEA API: {e}")
    res = d.get("Results") or {}
    if isinstance(res, list):
        res = res[0] if res else {}
    err = res.get("Error") or d.get("Error")
    if err:
        raise SourceError(f"BEA API error {err.get('APIErrorCode')}: {err.get('APIErrorDescription')}")
    rows = []
    for r in res.get("Data") or []:
        p = r.get("TimePeriod", "")
        m = re.fullmatch(r"(\d{4})M(\d{1,2})", p)
        if m:
            p = f"{m.group(1)}-{int(m.group(2)):02d}"
        elif not re.fullmatch(r"\d{4}Q[1-4]", p):
            raise SourceError(f"BEA API: unexpected TimePeriod {p!r}")
        rows.append({"line": int(r["LineNumber"]), "label": " ".join(str(r["LineDescription"]).split()),
                     "period": p, "value": num(r.get("DataValue"))})
    if not rows:
        raise SourceError("BEA API: no data rows")
    return rows


def labels(rows: list[dict]) -> list[str]:
    seen = {}
    for r in rows:
        seen.setdefault(r["line"], r["label"])
    return [f"{k}: {v}" for k, v in sorted(seen.items())]


def series(rows: list[dict], label: str, parent: str | None = None, table: str = "") -> dict[str, float]:
    """{period: value} for the line with this exact label; with `parent`, the first such line after the
    parent line (labels repeat in NIPA tables, so the table is split into sections first)."""
    if isinstance(rows, SourceError):
        raise rows
    lines =sorted({r["line"]: r["label"] for r in rows}.items())
    if parent is not None:
        starts = [n for n, lab in lines if lab == parent]
        if len(starts) != 1:
            raise SourceError(f"BEA {table}: parent line {parent!r} found {len(starts)} times; lines: {labels(rows)}")
        after = [n for n, lab in lines if lab == label and n > starts[0]]
        hits = after[:1]
    else:
        hits = [n for n, lab in lines if lab == label]
    if len(hits) != 1:
        raise SourceError(f"BEA {table}: line {label!r} found {len(hits)} times; lines: {labels(rows)}")
    return {r["period"]: r["value"] for r in rows if r["line"] == hits[0] and r["value"] is not None}


def latest_prior(s: dict[str, float], step) -> dict:
    p = max(s)
    pp = step(p, -1)
    return {"period": p, "latest": s[p], "pperiod": pp, "prior": s.get(pp)}


def _quarterly(tables, line, parent=None) -> dict:
    t, label = line[0], line[-1]
    s = series(tables[t], label, parent=parent, table=t)
    return latest_prior({k: round(v, 1) for k, v in s.items()}, quarter_add)


def gdp(tables) -> dict:
    return _quarterly(tables, GDP_LINE)


def bfi(tables) -> dict:
    return _quarterly(tables, BFI_LINE, parent=BFI_LINE[1])


def fspdp(tables) -> dict:
    return _quarterly(tables, FSPDP_LINE)


def pce(tables) -> dict:
    """Core PCE price index, y/y % from levels 12 months apart."""
    core = series(tables["T20804"], CORE_LINE[1], table="T20804")
    cy = {m: round((v / core[month_add(m, -12)] - 1) * 100, 1) for m, v in core.items() if core.get(month_add(m, -12))}
    if not cy:
        raise SourceError("BEA T20804: fewer than 13 months of core PCE")
    return latest_prior(cy, month_add)


def save(tables) -> dict:
    """Saving rate (%) and real PCE m/m % (from chained-dollar levels) for the last four months."""
    rate = series(tables["T20600"], SAVE_LINE[1], table="T20600")
    real = series(tables["T20806"], RPCE_LINE[1], table="T20806")
    m = max(rate)
    months = [month_add(m, -i) for i in range(3, -1, -1)]
    mm = lambda k: round((real[k] / real[month_add(k, -1)] - 1) * 100, 1) \
        if k in real and real.get(month_add(k, -1)) else None
    return {"months": months, "rate": [round(rate[k], 1) if k in rate else None for k in months],
            "realpce": [mm(k) for k in months]}


TABLES = (("T10101", "Q"), ("T10401", "Q"), ("T20600", "M"), ("T20806", "M"), ("T20804", "M"))


def get_tables(api_key: str, today: dt.date) -> dict[str, list[dict] | SourceError]:
    """Each table, or the SourceError it raised (one failing table does not block the others)."""
    if not api_key:
        raise SourceError("BEA API: no BEA_API_KEY")
    years = f"{today.year - 2},{today.year - 1},{today.year}"
    tables = {}
    for t, f in TABLES:
        url = URL_API.format(k=api_key, t=t, f=f, y=years)
        try:
            tables[t] = parse_api(fetch(url, label=f"BEA API {t}").decode("utf-8"))
        except SourceError as e:
            tables[t] = e
    if all(isinstance(v, SourceError) for v in tables.values()):
        raise SourceError("; ".join(sorted({str(v) for v in tables.values()})))
    return tables


# ---------------------------------------------------------------------------
# Release dates (verified): {"Gross Domestic Product": {"release_dates": [ISO UTC, ...]}, ...}

def parse_dates(text: str) -> list[dict]:
    """[{d, hm, id, ev}] for GDP and Personal Income and Outlays, converted to ET."""
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    try:
        d = json.loads(text)
    except ValueError as e:
        raise SourceError(f"BEA release dates: {e}")
    out = []
    for name, key in (("Gross Domestic Product", "gdp"), ("Personal Income and Outlays", "pce")):
        if name not in d:
            raise SourceError(f"BEA release dates: {name!r} not found")
        for s in sorted(set(d[name]["release_dates"])):
            t = dt.datetime.fromisoformat(s).astimezone(et)
            if key == "gdp":
                # The advance estimate comes 1 month after the quarter ends, the second 2, the third 3.
                lag = (t.month - 1) % 3 + 1
                qend = month_add(t.strftime("%Y-%m"), -lag)
                ev = f"GDP, {('advance', 'second', 'third')[lag - 1]} (Q{int(qend[5:]) // 3})"
            else:
                ref = month_add(t.strftime("%Y-%m"), -1)
                ev = f"Personal income and outlays ({dt.date(int(ref[:4]), int(ref[5:]), 1):%b})"
            out.append({"d": t.date().isoformat(), "hm": (t.hour, t.minute), "id": key, "ev": ev})
    return out


def get_dates() -> list[dict]:
    return parse_dates(fetch(URL_DATES).decode("utf-8"))
