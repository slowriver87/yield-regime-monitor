"""Census: EITS API (retail sales control group, housing starts and permits) and the release calendar.

The EITS API needs a key and none was available in the build environment, so the category and
data-type codes below follow the EITS documentation and are NOT VERIFIED. When an expected code
combination is missing, the error lists the combinations the response does have, so the run summary
shows what to map. The calendar format was verified on Oct 19, 2026.
"""
from __future__ import annotations

import datetime as dt
import json
import re

from .common import MONTH_NAME, SourceError, cell_text, fetch, month_add, parse_time_label

# geo_level_code and time_slot_name are listed in the dataset's variables.json (fixture
# census_eits_resconst_variables.json); without geo_level_code, regional rows were indistinguishable
URL_EITS = ("https://api.census.gov/data/timeseries/eits/{ds}?get=cell_value,data_type_code,time_slot_id,"
            "time_slot_name,geo_level_code,error_data,category_code,seasonally_adj&time=from+{y}&key={k}")
URL_CAL = "https://www.census.gov/economic-indicators/calendar-listview-{y}.html"

# (category_code, data_type_code, seasonally_adj)
RETAIL_TOTAL = ("44X72", "SM", "yes")
RETAIL_EXCLUDED = {"autos": ("441", "SM", "yes"), "gas": ("447", "SM", "yes"),
                   "building": ("444", "SM", "yes"), "food_services": ("722", "SM", "yes")}
# Annual-rate series carry an "A" prefix (ASTARTS, APERMITS); the plain codes are monthly counts and
# exist only not seasonally adjusted (verified in the Oct 20, 2026 runner dry run)
STARTS = ("ASTARTS", "TOTAL", "yes")
PERMITS = ("APERMITS", "TOTAL", "yes")
HOUSING_RANGE = (300, 3000)  # thousands of units, SAAR: a value outside means a wrong code

CAL_EVENTS = {
    "New Residential Construction (Building Permits, Housing Starts, and Housing Completions)":
        ("starts", "Housing starts and permits"),
    "New Residential Sales": ("nhs", "New home sales"),
    "Advance Monthly Sales for Retail and Food Services": ("retail", "Retail sales"),
}


class Eits(dict):
    """{(category, data type, seasonally_adj): {YYYY-MM: {(geo_level_code, time_slot_id): value}}}.
    `slot_names` maps time_slot_id -> time_slot_name; `has_geo` is True when the response carries
    geo_level_code (then only the US rows feed the page)."""
    slot_names: dict
    has_geo: bool


def parse_eits(text: str, ds: str) -> Eits:
    """Array of arrays with a header row. Rows with error_data 'yes' (margins of error) are skipped.
    Several values for one month are kept here; only a series the page uses must have one US value
    per month (see `_need`)."""
    try:
        rows = json.loads(text)
    except ValueError:
        raise SourceError(f"Census EITS {ds}: not JSON ({text[:60].strip()!r})")
    if not rows or not isinstance(rows[0], list):
        raise SourceError(f"Census EITS {ds}: no header row")
    h = rows[0]
    need = ["cell_value", "data_type_code", "error_data", "category_code", "seasonally_adj", "time"]
    if any(n not in h for n in need):
        raise SourceError(f"Census EITS {ds}: unexpected header {h}")
    ix = {n: h.index(n) for n in need}
    opt = {n: h.index(n) if n in h else None for n in ("time_slot_id", "geo_level_code", "time_slot_name")}
    out = Eits()
    out.slot_names, out.has_geo = {}, opt["geo_level_code"] is not None
    for r in rows[1:]:
        if str(r[ix["error_data"]]).lower() != "no":
            continue
        if not re.fullmatch(r"\d{4}-\d{2}", str(r[ix["time"]])):
            continue
        try:
            v = float(r[ix["cell_value"]])
        except (TypeError, ValueError):
            continue
        key = (r[ix["category_code"]], r[ix["data_type_code"]], str(r[ix["seasonally_adj"]]).lower())
        sid = str(r[opt["time_slot_id"]]) if opt["time_slot_id"] is not None else ""
        geo = str(r[opt["geo_level_code"]]) if out.has_geo else ""
        if opt["time_slot_name"] is not None:
            out.slot_names.setdefault(sid, str(r[opt["time_slot_name"]]))
        out.setdefault(key, {}).setdefault(r[ix["time"]], {})[(geo, sid)] = v
    if not out:
        raise SourceError(f"Census EITS {ds}: no data rows")
    return out


def combinations(data: dict) -> list[str]:
    """Distinct category/data type/seasonally_adj combinations, for the run summary."""
    return sorted(f"{c}/{d}/{s}" for c, d, s in data)


def _where(geo: str, sid: str) -> str:
    return f"time_slot_id {sid or '(none)'}" + (f" geo {geo}" if geo else "")


def _need(data: Eits, key: tuple, ds: str) -> dict[str, float]:
    """{YYYY-MM: value} for a series the page uses: the US rows when the response has regions, and
    exactly one value per month."""
    if key not in data:
        raise SourceError(f"Census EITS {ds}: {'/'.join(key)} not found; combinations: {combinations(data)}")
    out = {}
    for m, cells in data[key].items():
        if getattr(data, "has_geo", False):
            cells = {k: v for k, v in cells.items() if k[0] == "US"}
            if not cells:
                continue
        vals = set(cells.values())
        if len(vals) > 1:
            listed = ", ".join(f"{_where(*k)} = {v}" for k, v in sorted(cells.items()))
            raise SourceError(f"Census EITS {ds}: {'/'.join(key)} has {len(cells)} values for {m}: {listed}")
        out[m] = vals.pop()
    if not out:
        raise SourceError(f"Census EITS {ds}: {'/'.join(key)} has no US rows")
    return out


class HousingRangeError(SourceError):
    """A starts or permits value is outside the plausible range; `diagnostics` holds the breakdown."""

    def __init__(self, msg: str, diagnostics: list[str]):
        super().__init__(msg)
        self.diagnostics = diagnostics


def housing_diagnostics(data: Eits, months: int = 6) -> list[str]:
    """Every ASTARTS and APERMITS combination in the response, by region and time slot, last six
    months. EITS has no unit field, so values are shown as returned."""
    lines = ["EITS returns no unit field; values are cell_value as returned (the page expects thousands, SAAR)."]
    for cat in (STARTS[0], PERMITS[0]):
        for key in sorted(k for k in data if k[0] == cat):
            last = sorted(data[key])[-months:]
            cells = sorted({c for m in last for c in data[key][m]})
            for geo, sid in cells:
                name = getattr(data, "slot_names", {}).get(sid)
                vals = ", ".join(f"{m} {data[key][m][(geo, sid)]:g}" if (geo, sid) in data[key][m] else f"{m} n/a"
                                 for m in last)
                lines.append(f"{'/'.join(key)} geo {geo or '(not returned)'} time_slot_id {sid or '(none)'}"
                             + (f" ({name})" if name else "") + f": {vals}")
    return lines


def compute_retail(data: dict) -> dict:
    """Control group = total retail and food services minus autos, gasoline, building materials and
    food services ($ millions, SA); m/m % for the last three months."""
    tot = _need(data, RETAIL_TOTAL, "marts")
    ex = {k: _need(data, key, "marts") for k, key in RETAIL_EXCLUDED.items()}
    ctrl, ties = {}, []
    for m, t in tot.items():
        if all(m in s for s in ex.values()):
            ctrl[m] = t - sum(s[m] for s in ex.values())
            ties.append((m, all(s[m] < t for s in ex.values())))
    m = max(ctrl)
    months = [month_add(m, -i) for i in (2, 1, 0)]
    mm = lambda k: round((ctrl[k] / ctrl[month_add(k, -1)] - 1) * 100, 1) if k in ctrl and month_add(k, -1) in ctrl else None
    return {"months": months, "v": [mm(k) for k in months],
            "tie_ok": all(ok for k, ok in ties if k in months)}


def compute_housing(data: dict) -> dict:
    out = {}
    for name, key in (("starts", STARTS), ("permits", PERMITS)):
        s = _need(data, key, "resconst")
        m = max(s)
        for k in (m, month_add(m, -1)):
            if k in s and not HOUSING_RANGE[0] <= s[k] <= HOUSING_RANGE[1]:
                raise HousingRangeError(f"Census EITS resconst: {name} {k} = {s[k]} is outside {HOUSING_RANGE} thousand",
                                        housing_diagnostics(data))
        out[name] = {"period": m, "latest": round(s[m] / 1000, 3), "pperiod": month_add(m, -1),
                     "prior": round(s[month_add(m, -1)] / 1000, 3) if month_add(m, -1) in s else None}
    return out


def get_eits(ds: str, api_key: str, today: dt.date) -> Eits:
    if not api_key:
        raise SourceError("Census EITS: no CENSUS_API_KEY")
    url = URL_EITS.format(ds=ds, y=today.year - 1, k=api_key)
    return parse_eits(fetch(url, label=f"Census EITS {ds}").decode("utf-8"), ds)


# ---------------------------------------------------------------------------
# Calendar (verified): Indicator | Release Date | Time | Period Covered | code | period code

def parse_calendar(html: bytes | str) -> list[dict]:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    out = []
    names = "|".join(MONTH_NAME)
    for tr in soup.find_all("tr"):
        c = [cell_text(x) for x in tr.find_all(["td", "th"])]
        if len(c) < 4 or c[0] not in CAL_EVENTS:
            continue
        d = re.fullmatch(rf"({names}) (\d{{1,2}}), (\d{{4}})", c[1])
        p = re.fullmatch(rf"({names}) (\d{{4}})", c[3])
        if not d or not p:
            raise SourceError(f"Census calendar: unexpected row {c[:4]}")
        key, label = CAL_EVENTS[c[0]]
        out.append({"d": dt.date(int(d.group(3)), MONTH_NAME[d.group(1)], int(d.group(2))).isoformat(),
                    "hm": parse_time_label(c[2]), "id": key, "period": f"{p.group(2)}-{MONTH_NAME[p.group(1)]:02d}",
                    "ev": f"{label} ({p.group(1)[:3]})"})
    if not out:
        raise SourceError("Census calendar: no rows for the indicators used")
    return out


def get_calendar(years: list[int]) -> list[dict]:
    out = []
    for y in years:
        out += parse_calendar(fetch(URL_CAL.format(y=y)))
    return out
