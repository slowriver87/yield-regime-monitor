"""BLS: API v2 (payrolls, unemployment, earnings, CPI, PPI) and the release calendar (.ics).

The API could not be read from the build environment (no key there; the shared keyless quota was
spent) and bls.gov answered 403 to the .ics, so both parsers follow the BLS documentation:
https://www.bls.gov/developers/api_signature_v2.htm. See tests/fixtures/README.md.
"""
from __future__ import annotations

import datetime as dt
import json
import re

from .common import SourceError, fetch, month_add, num

URL_API = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
URL_ICS = "https://www.bls.gov/schedule/news_release/bls.ics"
SERIES = {"nfp": "CES0000000001", "ur": "LNS14000000", "ahe": "CES0500000003",
          "cpi": "CUUR0000SA0", "core": "CUUR0000SA0L1E", "ppi": "WPUFD4"}
ICS_EVENTS = {"Employment Situation": ("nfp", "Employment report"),
              "Consumer Price Index": ("cpi", "CPI"),
              "Producer Price Index": ("ppi", "PPI")}


def parse_api(text: str) -> dict[str, dict]:
    """{seriesID: {"levels": {YYYY-MM: value}, "net1": {YYYY-MM: 1-month net change}}}.
    Periods M01..M12 only (M13 is an annual average); a value of '-' is missing."""
    try:
        d = json.loads(text)
    except ValueError as e:
        raise SourceError(f"BLS API: {e}")
    if d.get("status") != "REQUEST_SUCCEEDED":
        raise SourceError(f"BLS API: {d.get('status')}: {' '.join(d.get('message') or [])}"[:300])
    out = {}
    for s in d.get("Results", {}).get("series", []):
        levels, net1 = {}, {}
        for o in s.get("data", []):
            m = re.fullmatch(r"M(0[1-9]|1[0-2])", o.get("period", ""))
            if not m:
                continue
            mo = f"{int(o['year']):04d}-{m.group(1)}"
            v = num(o.get("value"))
            if v is not None:
                levels[mo] = v
            n1 = num(((o.get("calculations") or {}).get("net_changes") or {}).get("1"))
            if n1 is not None:
                net1[mo] = n1
        out[s.get("seriesID")] = {"levels": levels, "net1": net1}
    missing = [sid for sid in SERIES.values() if sid not in out or not out[sid]["levels"]]
    if missing:
        raise SourceError(f"BLS API: no data for {missing}")
    return out


def yoy(levels: dict[str, float], m: str) -> float | None:
    a, b = levels.get(m), levels.get(month_add(m, -12))
    return round((a / b - 1) * 100, 1) if a is not None and b else None


def compute(series: dict[str, dict]) -> dict:
    """The SCORE and DATA.unrate values. Each indicator's newest month is its own."""
    out = {}
    lv = series[SERIES["nfp"]]["levels"]
    m = max(lv)
    chg = lambda k: (lv[k] - lv[month_add(k, -1)]) if k in lv and month_add(k, -1) in lv else None
    a3 = (lv[m] - lv[month_add(m, -3)]) / 3 if month_add(m, -3) in lv else None
    out["nfp"] = {"period": m, "latest": round(chg(m)) if chg(m) is not None else None,
                  "pperiod": month_add(m, -1),
                  "prior": round(chg(month_add(m, -1))) if chg(month_add(m, -1)) is not None else None,
                  "avg3": round(a3) if a3 is not None else None,
                  # tie: the computed change against BLS's own 1-month net change, where given
                  "net1": {k: series[SERIES["nfp"]]["net1"].get(k) for k in (m, month_add(m, -1))},
                  "chg": {k: chg(k) for k in (m, month_add(m, -1))}}
    u = series[SERIES["ur"]]["levels"]
    m = max(u)
    out["ur"] = {"period": m, "latest": u[m], "pperiod": month_add(m, -1), "prior": u.get(month_add(m, -1))}
    months = [month_add(m, -i) for i in range(5, -1, -1)]
    out["unrate"] = {"months": months, "v": [u.get(k) for k in months]}
    for key in ("ahe", "cpi", "core", "ppi"):
        lv = series[SERIES[key]]["levels"]
        m = max(lv)
        out[key] = {"period": m, "latest": yoy(lv, m), "pperiod": month_add(m, -1), "prior": yoy(lv, month_add(m, -1))}
    return out


def get(api_key: str | None, today: dt.date) -> dict:
    body = {"seriesid": list(SERIES.values()), "startyear": str(today.year - 2), "endyear": str(today.year),
            "calculations": True}
    if api_key:
        body["registrationkey"] = api_key
    text = fetch(URL_API, method="POST", json=body, label="BLS API").decode("utf-8")
    return compute(parse_api(text))


# ---------------------------------------------------------------------------
# Release calendar

def _unfold(text: str) -> list[str]:
    out = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def _unescape(s: str) -> str:
    return re.sub(r"\\([,;\\nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), s).strip()


def parse_ics(text: str) -> list[dict]:
    """[{d, t, id, ev}] for the three releases the page uses. DTSTART is local US-Eastern time
    (TZID=US-Eastern) or UTC with a trailing Z."""
    from zoneinfo import ZoneInfo
    if "BEGIN:VCALENDAR" not in text:
        raise SourceError("BLS calendar: not a VCALENDAR")
    et = ZoneInfo("America/New_York")
    out, ev = [], None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            ev = {}
        elif line == "END:VEVENT" and ev is not None:
            name = ev.get("SUMMARY")
            if name in ICS_EVENTS and "DTSTART" in ev:
                m = re.fullmatch(r"(\d{8})T(\d{2})(\d{2})\d{2}(Z?)", ev["DTSTART"])
                if not m:
                    raise SourceError(f"BLS calendar: unexpected DTSTART {ev['DTSTART']!r}")
                t = dt.datetime.strptime(m.group(1) + m.group(2) + m.group(3), "%Y%m%d%H%M")
                if m.group(4):
                    t = t.replace(tzinfo=dt.timezone.utc).astimezone(et).replace(tzinfo=None)
                key, label = ICS_EVENTS[name]
                ref = month_add(t.strftime("%Y-%m"), -1)
                out.append({"d": t.date().isoformat(), "hm": (t.hour, t.minute), "id": key,
                            "ev": f"{label} ({dt.date(int(ref[:4]), int(ref[5:]), 1):%b})"})
            ev = None
        elif ev is not None and ":" in line:
            k, v = line.split(":", 1)
            ev[k.split(";", 1)[0].upper()] = _unescape(v)
    return out


def get_calendar() -> list[dict]:
    return parse_ics(fetch(URL_ICS).decode("utf-8", "replace"))
