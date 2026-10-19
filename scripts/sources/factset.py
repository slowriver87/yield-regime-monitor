"""FactSet Earnings Insight (weekly PDF): forward 12-month P/E and the S&P 500 close it cites."""
from __future__ import annotations

import datetime as dt
import io
import re

from .common import NotFound, SourceError, fetch, num

URL = ("https://advantage.factset.com/hubfs/Website/Resources%20Section/Research%20Desk/"
       "Earnings%20Insight/EarningsInsight_{mmddyy}.pdf")
RE_PE = re.compile(r"The forward 12-month P/E ratio for the S&P 500 is (\d+\.\d)\.")
RE_PRICE = re.compile(r"(?:above|below) the closing price of ([\d,]+\.\d\d)")
RE_DATE = re.compile(r"^([A-Z][a-z]+ \d{1,2}, \d{4})$", re.M)


def parse_text(text: str, first_page: str) -> dict:
    """{report, pe, price}; pe or price is None when its sentence is missing or ambiguous."""
    dm = RE_DATE.search(first_page)
    if not dm:
        raise SourceError("FactSet: cover date not found on page 1")
    report = dt.datetime.strptime(dm.group(1), "%B %d, %Y").date().isoformat()
    text = " ".join(text.split())
    pes = {m.group(1) for m in RE_PE.finditer(text)}
    prices = {m.group(1) for m in RE_PRICE.finditer(text)}
    return {"report": report,
            "pe": float(pes.pop()) if len(pes) == 1 else None,
            "price": num(prices.pop()) if len(prices) == 1 else None}


def read_pdf(content: bytes) -> dict:
    import pdfplumber
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            pages = [p.extract_text() or "" for p in pdf.pages]
    except Exception as e:
        raise SourceError(f"FactSet PDF: {e}")
    if not pages:
        raise SourceError("FactSet PDF: no pages")
    return parse_text("\n".join(pages), pages[0])


def fridays(today: dt.date) -> list[dt.date]:
    """This week's Friday (if reached), then the one before."""
    last = today - dt.timedelta(days=(today.weekday() - 4) % 7)
    return [last, last - dt.timedelta(7)]


def get(today: dt.date) -> dict:
    errors = []
    for fri in fridays(today):
        try:
            out = read_pdf(fetch(URL.format(mmddyy=fri.strftime("%m%d%y"))))
        except NotFound:
            errors.append(f"{fri:%m%d%y}: not published")
            continue
        except SourceError as e:  # e.g. an HTML page instead of a PDF
            errors.append(f"{fri:%m%d%y}: {e}")
            continue
        if out["report"] != fri.isoformat():
            raise SourceError(f"FactSet: file for {fri} has cover date {out['report']}")
        return out
    raise SourceError("FactSet: " + "; ".join(errors))
