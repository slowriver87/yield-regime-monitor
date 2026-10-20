"""Federal Reserve G.17: industrial production, total index, percent change (verified Oct 19, 2026).

Primary read: the 'Total index' row of the summary table (six index levels, then six monthly percent
changes). Second read: the opening sentence 'Industrial production (IP) increased 0.7 percent in
September ...'.
"""
from __future__ import annotations

import datetime as dt
import re

from .common import MONTH_NAME, SourceError, cell_text, fetch, month_add, num

URL = "https://www.federalreserve.gov/releases/g17/current/"
TABLE_TITLE = "Industrial Production and Capacity Utilization: Summary"
ROW = "Total index"
MONTH_LABELS = {"Jan.": 1, "Feb.": 2, "Mar.": 3, "Apr.": 4, "May": 5, "June": 6, "July": 7, "Aug.": 8,
                "Sept.": 9, "Oct.": 10, "Nov.": 11, "Dec.": 12}
UP = ("increased", "rose", "edged up", "moved up", "advanced", "climbed", "gained")
DOWN = ("decreased", "fell", "edged down", "moved down", "declined", "dropped")


def parse(html: bytes | str) -> dict:
    """{period, latest, pperiod, prior, text} with text = the sentence's m/m change, or None."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    rel = next((cell_text(x) for x in soup.find_all(class_="dates") if cell_text(x).startswith("Release Date:")), None)
    names = "|".join(MONTH_NAME)
    rm = re.fullmatch(rf"Release Date: ({names}) (\d{{1,2}}), (\d{{4}})", rel or "")
    if not rm:
        raise SourceError("G.17: 'Release Date:' line not found")
    released = dt.date(int(rm.group(3)), MONTH_NAME[rm.group(1)], int(rm.group(2)))
    tables = [t for t in soup.find_all("table") if t.get("title") == TABLE_TITLE]
    if len(tables) != 1:
        raise SourceError(f"G.17: table {TABLE_TITLE!r} found {len(tables)} times")
    t = tables[0]
    labels = None
    for tr in t.find_all("tr"):
        cells = [re.sub(r"\[[rp]\]", "", cell_text(c)).strip() for c in tr.find_all(["th", "td"])]
        if len(cells) == 12 and all(c in MONTH_LABELS for c in cells):
            labels = cells
            break
    if labels is None or labels[:6] != labels[6:]:
        raise SourceError("G.17: month header row not found")
    rows = [[cell_text(c) for c in tr.find_all(["th", "td"])] for tr in t.find_all("tr")]
    hits = [r for r in rows if r and r[0] == ROW]
    if len(hits) != 1 or len(hits[0]) != 14:
        raise SourceError(f"G.17: row {ROW!r} not found once with 13 values")
    pct = [num(v) for v in hits[0][7:13]]
    last_m = MONTH_LABELS[labels[-1]]
    year = released.year if last_m <= released.month else released.year - 1
    period = f"{year}-{last_m:02d}"
    months = [month_add(period, -i) for i in range(5, -1, -1)]
    if [int(m[5:]) for m in months] != [MONTH_LABELS[x] for x in labels[6:]]:
        raise SourceError(f"G.17: months {labels[6:]} are not consecutive")

    text_v = None
    intro = next((cell_text(p) for p in soup.find_all("p") if cell_text(p).startswith("Industrial production (IP)")), "")
    verbs = "|".join(UP + DOWN)
    sm = re.match(rf"Industrial production \(IP\) (?:({verbs}) (\d+\.\d) percent|was unchanged) in ({names})", intro)
    if sm and MONTH_NAME[sm.group(3)] == last_m:
        text_v = 0.0 if sm.group(2) is None else (float(sm.group(2)) * (1 if sm.group(1) in UP else -1))
    return {"period": period, "latest": pct[-1], "pperiod": months[-2], "prior": pct[-2],
            "released": released.isoformat(), "text": text_v}


def get() -> dict:
    return parse(fetch(URL))
