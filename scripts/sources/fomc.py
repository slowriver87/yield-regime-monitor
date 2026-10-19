"""Federal Reserve FOMC meeting calendar (verified Oct 19, 2026): decision on the second day, 2:00 pm."""
from __future__ import annotations

import datetime as dt
import re

from .common import MONTH_NAME, SourceError, cell_text, fetch

URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"


def parse(html: bytes | str, years: list[int]) -> list[dict]:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for y in years:
        panels = [p for p in soup.select(".panel")
                  if p.find(class_="panel-heading") and cell_text(p.find(class_="panel-heading")) == f"{y} FOMC Meetings"]
        if len(panels) != 1:
            continue  # next year's panel may not be posted yet
        for row in panels[0].select(".fomc-meeting"):
            mo, da = row.select_one(".fomc-meeting__month"), row.select_one(".fomc-meeting__date")
            if not mo or not da:
                continue
            months = cell_text(mo).split("/")  # a meeting can span two months, e.g. 'Apr/May'
            m = re.fullmatch(r"(\d{1,2})-(\d{1,2})\*?", cell_text(da))
            if not m:
                continue  # e.g. notation votes or unscheduled meetings
            last = months[-1].strip()
            month = next((v for k, v in MONTH_NAME.items() if k == last or k[:3] == last[:3]), None)
            if month is None:
                raise SourceError(f"FOMC calendar: unexpected month {cell_text(mo)!r}")
            out.append({"d": dt.date(y, month, int(m.group(2))).isoformat(), "hm": (14, 0), "id": "fomc",
                        "ev": "FOMC decision"})
    if not out:
        raise SourceError("FOMC calendar: no meetings found")
    return out


def get(years: list[int]) -> list[dict]:
    return parse(fetch(URL), years)
