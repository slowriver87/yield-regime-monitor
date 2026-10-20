"""Atlanta Fed GDPNow: the workbook's CurrentQtrEvolution sheet, with the web page as second read."""
from __future__ import annotations

import datetime as dt
import io
import re

from .common import MONTH_NAME, SourceError, fetch, visible_text

URL_XLSX = ("https://www.atlantafed.org/-/media/Project/Atlanta/FRBA/Documents/research-and-data/data/"
            "gdpnow/GDPTrackingModelDataAndForecasts.xlsx")
URL_PAGE = "https://www.atlantafed.org/research-and-data/data/gdpnow"
SHEET = "CurrentQtrEvolution"
GROUP = ["Date", "Major Releases", "GDP*"]   # the header repeats once per month block
RE_INITIAL = re.compile(r"Initial GDPNow (\d{2}):Q([1-4]) forecast")


def parse_rows(rows: list[list]) -> dict:
    """{quarter '2026Q3', points [(date, value)] oldest first}."""
    if not rows:
        raise SourceError(f"GDPNow {SHEET}: empty sheet")
    head = [c.strip() if isinstance(c, str) else c for c in rows[0]]
    starts = [j for j in range(len(head) - 2) if head[j:j + 3] == GROUP]
    if not starts:
        raise SourceError(f"GDPNow {SHEET}: header {GROUP} not found")
    pts, quarter = {}, None
    for r in rows[1:]:
        for j in starts:
            d, rel, v = (r[j:j + 3] + [None] * 3)[:3]
            if isinstance(rel, str) and RE_INITIAL.search(rel):
                q = RE_INITIAL.search(rel)
                quarter = f"20{q.group(1)}Q{q.group(2)}"
            if isinstance(d, (dt.datetime, dt.date)) and isinstance(v, (int, float)):
                pts[(d.date() if isinstance(d, dt.datetime) else d).isoformat()] = float(v)
    if not pts:
        raise SourceError(f"GDPNow {SHEET}: no estimates")
    if quarter is None:
        raise SourceError(f"GDPNow {SHEET}: 'Initial GDPNow YY:Qn forecast' row not found")
    return {"quarter": quarter, "points": sorted(pts.items())}


def read_xlsx(content: bytes) -> dict:
    import openpyxl
    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        rows = [list(r) for r in wb[SHEET].iter_rows(values_only=True)]
    except Exception as e:
        raise SourceError(f"GDPNow workbook: {e}")
    return parse_rows(rows)


def parse_page(html: bytes | str) -> dict:
    """'3.6%% ... GDPNow Estimate for 2026:Q3 Updated: October 15, 2026' (the doubled % is on the page)."""
    t = visible_text(html)
    names = "|".join(MONTH_NAME)
    m = re.search(rf"(-?\d+\.\d)%%? [A-Za-z-]+ GDPNow Estimate for (\d{{4}}):Q([1-4]) Updated: ({names}) (\d{{1,2}}), (\d{{4}})", t)
    if not m:
        raise SourceError("GDPNow page: estimate text not found")
    return {"v": float(m.group(1)), "quarter": f"{m.group(2)}Q{m.group(3)}",
            "date": dt.date(int(m.group(6)), MONTH_NAME[m.group(4)], int(m.group(5))).isoformat()}


def get_xlsx() -> dict:
    return read_xlsx(fetch(URL_XLSX))


def get_page() -> dict:
    return parse_page(fetch(URL_PAGE))
