"""FRED: ICE BofA US High Yield OAS (BAMLH0A0HYM2). The only source for HY OAS; never blocks a run.

Neither format could be fetched from the build environment (no API key there, and fredgraph.csv
timed out), so both parsers follow FRED's documentation; see tests/fixtures/README.md.
"""
from __future__ import annotations

import csv
import io
import json

from .common import FRED_TIMEOUT, SourceError, fetch_once, is_business_day, num, parse_iso

SERIES = "BAMLH0A0HYM2"
URL_API = ("https://api.stlouisfed.org/fred/series/observations?series_id={s}&api_key={k}"
           "&file_type=json&sort_order=desc&limit=10")
URL_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={s}"


def parse_api(text: str) -> dict[str, float]:
    """observations[] of {date, value}; value is a string, '.' when missing."""
    try:
        d = json.loads(text)
    except ValueError as e:
        raise SourceError(f"FRED API: {e}")
    if "observations" not in d:
        raise SourceError(f"FRED API: {d.get('error_message', 'no observations')}")
    out = {o["date"]: num(o["value"]) for o in d["observations"]}
    return {k: v for k, v in out.items() if v is not None}


def parse_csv(text: str) -> dict[str, float]:
    """Header 'observation_date,BAMLH0A0HYM2' (older files: 'DATE,...'); blank or '.' when missing."""
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    if not rows or len(rows[0]) != 2 or rows[0][0] not in ("observation_date", "DATE") or rows[0][1] != SERIES:
        raise SourceError(f"fredgraph.csv: unexpected header {rows[0] if rows else None}")
    out = {}
    for r in rows[1:]:
        if len(r) == 2 and num(r[1]) is not None:
            out[r[0]] = num(r[1])
    if not out:
        raise SourceError("fredgraph.csv: no observations")
    return out


def trading_days_only(obs: dict[str, float], trading: set[str] | None) -> dict[str, float]:
    """Keep the dates that are in the Treasury par yield file, so bond-market holidays (e.g. Oct 12,
    Columbus Day) drop out. A date after the newest par date is kept when it is a business day,
    since the day's par yields post later than FRED."""
    if not trading:
        return {d: v for d, v in obs.items() if is_business_day(parse_iso(d))}
    last = max(trading)
    return {d: v for d, v in obs.items() if d in trading or (d > last and is_business_day(parse_iso(d)))}


def get_api_series(series: str, api_key: str | None, limit: int = 10) -> dict[str, float]:
    """One FRED API call (15-second timeout), newest first."""
    if not api_key:
        raise SourceError("FRED API: no FRED_API_KEY")
    url = URL_API.format(s=series, k=api_key).replace("limit=10", f"limit={limit}")
    return parse_api(fetch_once(url, FRED_TIMEOUT, label=f"FRED API {series}").decode("utf-8"))


def icsa(obs: dict[str, float]) -> dict:
    """Initial claims from FRED ICSA (week-ending Saturday, seasonally adjusted). FRED's unit is
    'Number', so values are divided by 1,000 to match the page's thousands."""
    weeks = sorted(obs)
    if len(weeks) < 2:
        raise SourceError("FRED ICSA: fewer than two weeks")
    k = {w: round(obs[w] / 1000) for w in weeks[-2:]}
    if not all(50 <= v <= 2000 for v in k.values()):
        raise SourceError(f"FRED ICSA: values outside 50-2,000 thousand: {k}")
    return {"week": weeks[-1], "latest": k[weeks[-1]], "pweek": weeks[-2], "prior": k[weeks[-2]]}


def get(api_key: str | None) -> tuple[dict[str, float], str]:
    """(observations, which source). Each URL is called once with a 15-second timeout."""
    errors = []
    if api_key:
        try:
            return parse_api(fetch_once(URL_API.format(s=SERIES, k=api_key), FRED_TIMEOUT,
                                        label="FRED API").decode("utf-8")), "FRED API"
        except SourceError as e:
            errors.append(str(e))
    else:
        errors.append("FRED API: no FRED_API_KEY")
    try:
        return parse_csv(fetch_once(URL_CSV.format(s=SERIES), FRED_TIMEOUT).decode("utf-8")), "fredgraph.csv"
    except SourceError as e:
        errors.append(str(e))
    raise SourceError("; ".join(errors))
