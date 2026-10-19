"""FRED: ICE BofA US High Yield OAS (BAMLH0A0HYM2). The only source for HY OAS; never blocks a run.

Neither format could be fetched from the build environment (no API key there, and fredgraph.csv
timed out), so both parsers follow FRED's documentation; see tests/fixtures/README.md.
"""
from __future__ import annotations

import csv
import io
import json

from .common import FRED_TIMEOUT, SourceError, fetch_once, num

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
