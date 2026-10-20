"""EIA: WTI Cushing spot price, daily (API v2 with a key, the spot-price HTML table as fallback)."""
from __future__ import annotations

import datetime as dt
import json

from .common import SourceError, cell_text, fetch, num

URL_API = ("https://api.eia.gov/v2/petroleum/pri/spt/data/?api_key={k}&frequency=daily&data[0]=value"
           "&facets[series][]=RWTC&start={start}&sort[0][column]=period&sort[0][direction]=desc&length=20")
URL_HTML = "https://www.eia.gov/dnav/pet/pet_pri_spt_s1_d.htm"
SERIES = "RWTC"
ROW_LABEL = "WTI - Cushing, Oklahoma"


def parse_api(text: str) -> dict[str, float]:
    """response.data[] of {period 'YYYY-MM-DD', series 'RWTC', value (string), units '$/BBL', ...}."""
    try:
        d = json.loads(text)
    except ValueError as e:
        raise SourceError(f"EIA API: {e}")
    if "error" in d:
        raise SourceError(f"EIA API: {d['error'].get('code') if isinstance(d['error'], dict) else d['error']}")
    try:
        rows = d["response"]["data"]
    except (KeyError, TypeError):
        raise SourceError("EIA API: response.data not found")
    out = {}
    for r in rows:
        if r.get("series") != SERIES or r.get("units") != "$/BBL":
            raise SourceError(f"EIA API: unexpected row {r.get('series')} {r.get('units')}")
        if num(r.get("value")) is not None:
            out[r["period"]] = num(r["value"])
    if not out:
        raise SourceError("EIA API: no values")
    return out


def parse_html(html: bytes | str) -> dict[str, float]:
    """Dates from the 'Series5' header cells (MM/DD/YY); the row whose stub reads exactly
    'WTI - Cushing, Oklahoma'; its value cells are the row's own Data*/Current* cells."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    dates = [dt.datetime.strptime(cell_text(th), "%m/%d/%y").date().isoformat() for th in soup.find_all("th", class_="Series5")]
    if not dates:
        raise SourceError("EIA spot page: date header cells not found")
    stubs = [td for td in soup.find_all("td", class_="DataStub1") if cell_text(td) == ROW_LABEL]
    if len(stubs) != 1:
        raise SourceError(f"EIA spot page: row {ROW_LABEL!r} found {len(stubs)} times")
    row = stubs[0].find_parent("tr", class_="DataRow")
    cells = [td for td in row.find_all("td", recursive=False)
             if any(c.startswith(("Data", "Current")) and c not in ("DataStub", "DataStub2", "DataHist")
                    for c in td.get("class", []))]
    if len(cells) != len(dates):
        raise SourceError(f"EIA spot page: {len(cells)} values for {len(dates)} dates")
    out = {d: num(cell_text(c)) for d, c in zip(dates, cells)}
    return {d: v for d, v in out.items() if v is not None}


def get_api(key: str, today: dt.date) -> dict[str, float]:
    start = (today - dt.timedelta(days=30)).isoformat()
    return parse_api(fetch(URL_API.format(k=key, start=start), label="EIA API").decode("utf-8"))


def get_html() -> dict[str, float]:
    return parse_html(fetch(URL_HTML))
