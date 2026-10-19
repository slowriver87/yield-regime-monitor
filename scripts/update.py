#!/usr/bin/env python3
"""Refresh the Yield Regime Monitor data blocks from public sources.

Fetches each source, rewrites the five script-written lines in index.html (DATA, SCORE, CONS, CAL,
LOG), runs the checks from HANDOFF.md and writes data/summary.json. NOTES, HIST and everything else
in the page are never touched. The workflow commits only if this script exits 0 and reports
changed=true.

Exit codes: 0 = updated, or nothing new; 1 = a check failed (nothing written).
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sources import (auctions, bea, bls, census, dol, eia, factset, fomc, fred, freddie,  # noqa: E402
                     g17, gdpnow, nyfed, treasury, umich)
from sources.common import (CheckFailed, SourceError, add_business_days, business_days_between,  # noqa: E402
                            day_label, is_business_day, mon_abbr, parse_iso, quarter_add, time_label)

ROOT = Path(__file__).resolve().parent.parent
START_MARK = "// ===== DATA BLOCKS"
END_MARK = "// ===== END DATA BLOCKS ====="
BLOCK_NAMES = ("DATA", "SCORE", "CONS", "CAL", "LOG", "NOTES", "HIST")
WRITTEN = ("DATA", "SCORE", "CONS", "CAL", "LOG")
SEED_MARK = "Seeded from the previous version of this page"
SEED_NOTE = SEED_MARK + "; the first script run re-reads it."
MINUS = "−"
EN_DASH = "–"
RATE_MOVE = 0.25        # percentage points, for yields, spreads and rates
PCT_MOVE = 5.0          # percent, for everything else
TWO_READ_TOL = 0.001
CAL_IDS = {"starts", "nhs", "claims", "mort", "umich", "auction", "gdp", "pce", "cpi", "ppi", "nfp", "retail", "fomc"}

# LOG rows (exact indicator labels) for each source, in page order
LOG_ROWS = {
    "nyfed": ["Fed funds target range"],
    "par": ["2Y Treasury (par)", "10Y Treasury (par)", "30Y Treasury (par)"],
    "real": ["10Y real yield (par)", "10Y breakeven"],
    "fred": ["HY OAS"],
    "freddie": ["30Y mortgage"],
    "eia": ["WTI spot (Cushing)"],
    "auction": ["Latest coupon auction"],
    "factset": ["Forward P/E (FactSet)"],
    "bls": ["Unemployment rate", "Nonfarm payrolls", "Average hourly earnings", "CPI / core CPI", "PPI final demand"],
    "dol": ["Initial claims"],
    "bea": ["Core PCE", "Real GDP / final sales / business investment", "Saving rate / real spending"],
    "gdpnow": ["GDPNow"],
    "g17": ["Industrial production"],
    "housing": ["Housing starts / permits"],
    "retail": ["Retail sales control group"],
    "umich": ["UMich sentiment (final)"],
    "hhd": ["Card flow into 90+ days"],
}
SOURCE_NAMES = {"nyfed": "New York Fed", "par": "Treasury", "real": "Treasury", "fred": "FRED",
                "freddie": "Freddie Mac", "eia": "EIA", "auction": "TreasuryDirect", "factset": "FactSet",
                "bls": "BLS", "dol": "Department of Labor", "bea": "BEA", "gdpnow": "Atlanta Fed",
                "g17": "Federal Reserve G.17", "housing": "Census", "retail": "Census",
                "umich": "University of Michigan", "hhd": "New York Fed"}
SCORE_SOURCE = {"mort": "freddie", "starts": "housing", "permits": "housing", "gdp": "bea", "fspdp": "bea",
                "bfi": "bea", "gdpnow": "gdpnow", "ip": "g17", "nfp": "bls", "ur": "bls", "ahe": "bls",
                "claims": "dol", "cpi": "bls", "core": "bls", "ppi": "bls", "pce": "bea"}
COMMIT_NAMES = {**SOURCE_NAMES, "par": "Treasury par", "real": "Treasury real", "hhd": "NY Fed household credit",
                "housing": "Census housing", "retail": "Census retail"}
ROW_SOURCE = {lab: src for src, labs in LOG_ROWS.items() for lab in labs}


# ---------------------------------------------------------------------------
# The page: seven one-line data blocks, five of them written here

def dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def block_lines(lines: list[str]) -> dict[str, int]:
    starts = [i for i, l in enumerate(lines) if l.startswith(START_MARK)]
    ends = [i for i, l in enumerate(lines) if l.rstrip("\r\n") == END_MARK]
    if len(starts) != 1 or len(ends) != 1 or ends[0] - starts[0] != len(BLOCK_NAMES) + 1:
        raise CheckFailed(f"data block markers not found, or not exactly {len(BLOCK_NAMES)} lines between them")
    idx = {}
    for i in range(starts[0] + 1, ends[0]):
        m = re.match(r"const (\w+) = ", lines[i])
        if not m or m.group(1) not in BLOCK_NAMES or m.group(1) in idx:
            raise CheckFailed(f"unexpected line {i + 1} between the data markers")
        idx[m.group(1)] = i
    return idx


def parse_block(line: str):
    body = line.rstrip("\r\n")
    if not body.endswith(";"):
        raise CheckFailed("data line does not end with ';'")
    return json.loads(body[body.index("=") + 1:-1].strip())


def load_page(html: str) -> dict:
    lines = html.splitlines(keepends=True)
    return {n: parse_block(lines[i]) for n, i in block_lines(lines).items()}


def render_page(html: str, blocks: dict) -> str:
    """Replace only the five script-written lines."""
    lines = html.splitlines(keepends=True)
    idx = block_lines(lines)
    for name in WRITTEN:
        old = lines[idx[name]]
        ending = old[len(old.rstrip("\r\n")):]
        lines[idx[name]] = f"const {name} = {dump(blocks[name])};{ending}"
    return "".join(lines)


def extract_script(html: str) -> str:
    scripts = re.findall(r"<script\b[^>]*>(.*?)</script>", html, flags=re.S | re.I)
    if len(scripts) != 1:
        raise CheckFailed(f"expected one <script>, found {len(scripts)}")
    return scripts[0]


def _node(path: str, *args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["node", *args, path], capture_output=True, text=True)
    except FileNotFoundError:
        raise CheckFailed("check 7: node not found; cannot check the page script")


def node_check(html: str, blocks: dict) -> None:
    """Check 7: the script parses, and Node reads the seven blocks back as the same values."""
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "page.js")
        Path(p).write_text(extract_script(html), encoding="utf-8")
        r = _node(p, "--check")
        if r.returncode != 0:
            raise CheckFailed("check 7: page script does not parse: " + (r.stderr.strip().splitlines() or ["?"])[-1])
        lines = html.splitlines(keepends=True)
        idx = block_lines(lines)
        js = "".join(lines[idx[n]] for n in BLOCK_NAMES)
        js += "process.stdout.write(JSON.stringify({" + ",".join(BLOCK_NAMES) + "}));\n"
        q = os.path.join(tmp, "blocks.js")
        Path(q).write_text(js, encoding="utf-8")
        r = _node(q)
        if r.returncode != 0:
            raise CheckFailed("check 7: data blocks do not evaluate: " + (r.stderr.strip().splitlines() or ["?"])[-1])
        if json.loads(r.stdout) != blocks:
            raise CheckFailed("check 7: Node reads the data blocks differently")


def verify_page(before: str, after: str, blocks: dict) -> None:
    """Checks 5 and 7."""
    b, a = before.splitlines(keepends=True), after.splitlines(keepends=True)
    ib, ia = block_lines(b), block_lines(a)
    if ib != ia or len(a) != len(b):
        raise CheckFailed("check 5: page line structure changed")
    skip = {ib[n] for n in WRITTEN}
    diff = [i + 1 for i in range(len(b)) if i not in skip and a[i] != b[i]]
    if diff:
        raise CheckFailed(f"check 5: lines outside the five data lines changed: {diff[:10]}")
    for n in ("NOTES", "HIST"):
        if a[ia[n]].encode("utf-8") != b[ib[n]].encode("utf-8"):
            raise CheckFailed(f"check 5: {n} changed")
    node_check(after, blocks)


# ---------------------------------------------------------------------------
# Formatting for the check log

def pct_s(v, dp=2) -> str:
    return "n/a" if v is None else f"{v:.{dp}f}%"


def signed(v, dp=1, unit="") -> str:
    if v is None:
        return "n/a"
    r = round(v, dp)
    return f"{'+' if r > 0 else MINUS if r < 0 else ''}{abs(r):.{dp}f}{unit}"


def period_label(p: str) -> str:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p or ""):
        return day_label(p)
    if re.fullmatch(r"\d{4}-\d{2}", p or ""):
        return mon_abbr(p)
    if re.fullmatch(r"\d{4}Q\d", p or ""):
        return p[4:]
    return p or "n/a"


# ---------------------------------------------------------------------------
# The run

@dataclass
class Run:
    today: dt.date
    old: dict
    keys: dict
    skip_seed: bool = False
    new: dict = field(default_factory=dict)
    log: dict = field(default_factory=dict)         # LOG row label -> new row
    attention: list = field(default_factory=list)   # reasons to open the issue
    unreached: list = field(default_factory=list)
    notes: list = field(default_factory=list)       # lines for the run summary
    fresh: dict = field(default_factory=dict)       # source -> newest data date read this run
    seed_mismatch: list = field(default_factory=list)
    layouts: list = field(default_factory=list)     # printouts for the summary when a parser fails
    new_sources: set = field(default_factory=set)   # sources with a new data date this run

    def __post_init__(self):
        self.new = copy.deepcopy(self.old)

    @property
    def D(self):
        return self.new["DATA"]

    def score(self, sid: str) -> dict:
        return next(r for r in self.new["SCORE"] if r["id"] == sid)

    def old_score(self, sid: str) -> dict:
        return next(r for r in self.old["SCORE"] if r["id"] == sid)

    def seeded(self, src: str) -> bool:
        rows = {r[0]: r for r in self.old["LOG"]}
        return any(SEED_MARK in rows.get(lab, ["", "", "", "", "", ""])[5] for lab in LOG_ROWS[src])

    def write_log(self, label: str, value: str, date: str, result: str, source: str | None = None,
                  reread: bool = True):
        """Rewrite one LOG row. With reread=False the source's values were not compared, so a seed
        marker stays on the row and the seed check still runs on a later run."""
        old = next((r for r in self.old["LOG"] if r[0] == label), None)
        if old is None:
            raise CheckFailed(f"LOG row {label!r} not found")
        if not reread and SEED_MARK in old[5]:
            result = f"{result} {SEED_NOTE}"
        if result.startswith("New "):
            self.new_sources.add(ROW_SOURCE[label])
        self.log[label] = [label, value, date, source or old[3], day_label(self.today.isoformat()), result]

    def not_reached(self, src: str, err: Exception | str, what: str | None = None):
        what = what or SOURCE_NAMES[src]
        self.unreached.append(f"{what}: {err}")
        for lab in LOG_ROWS[src]:
            old = next(r for r in self.old["LOG"] if r[0] == lab)
            self.write_log(lab, old[1], old[2], f"Not updated: {what} not reached.", reread=False)

    def flag(self, src: str, date: str, why: str):
        """A value was nulled for a new data date: say so in the issue."""
        self.attention.append(f"{SOURCE_NAMES[src]}: showed {date} but {why}; the value was set to null.")


def new_result(date: str, how: str) -> str:
    return f"New {period_label(date)}. {how}"


def null_result(date: str, why: str) -> str:
    return f"Null: source showed {period_label(date)} but {why}; issue opened."


def is_new(new_date: str | None, old_date: str | None) -> bool:
    return bool(new_date) and (not old_date or new_date > old_date)


# ---------------------------------------------------------------------------
# Sources -> blocks

def do_nyfed(run: Run):
    try:
        r = nyfed.get_effr()
    except SourceError as e:
        return run.not_reached("nyfed", e)
    old = run.old["DATA"]["fed"]
    if r["date"] < old["date"]:
        return run.not_reached("nyfed", f"newest record {r['date']} is older than the page's {old['date']}")
    run.D["fed"] = r
    run.fresh["nyfed"] = r["date"]
    res = new_result(r["date"], "Single source.") if is_new(r["date"], old["date"]) else "No new release."
    run.write_log("Fed funds target range", f"{r['lo']:.2f}{EN_DASH}{r['hi']:.2f}%" if r["lo"] is not None else "n/a",
                  period_label(r["date"]), res)


def _years_for(today: dt.date) -> list[int]:
    return [today.year - 1, today.year] if today.month == 1 else [today.year]


def do_treasury(run: Run):
    years = _years_for(run.today)
    par, par_src, xml_note, err = None, "", "", None
    try:
        par = treasury.get_csv(treasury.PAR, years)
        if len(par) < 13 and run.today.year - 1 not in years:
            years = [run.today.year - 1] + years
            par = treasury.get_csv(treasury.PAR, years)
        par_src = "csv"
    except SourceError as e:
        err = e
    newest_month = (max(par) if par else run.today.isoformat())[:7]
    months = [newest_month.replace("-", "")]
    xml = None
    try:
        if par is None:  # fallback: the XML feed for this month and the one before
            prev = (dt.date(int(newest_month[:4]), int(newest_month[5:]), 1) - dt.timedelta(1)).strftime("%Y%m")
            months = [prev] + months
        xml = treasury.get_xml(treasury.PAR, months)
    except SourceError as e:
        xml_note = f"XML not read ({e})"
    if par is None:
        if xml is None:
            run.not_reached("par", err)
            run.not_reached("real", "par yields not read", "Treasury par curve")
            return
        par, par_src = xml, "xml"
        run.notes.append(f"Treasury par CSV not read ({err}); XML used.")

    newest = max(par)
    if newest < run.old["DATA"]["par"]["dates"][-1]:
        run.not_reached("par", f"newest date {newest} is older than the page's")
        return run.not_reached("real", "par yields older than the page", "Treasury par curve")
    if par_src == "csv":
        if xml is not None and newest in xml:
            bad = [f for f in ("y2", "y10", "y30")
                   if par[newest][f] is not None and xml[newest][f] is not None
                   and abs(par[newest][f] - xml[newest][f]) > TWO_READ_TOL + 1e-9]
            if bad:
                raise CheckFailed(f"check 1: Treasury CSV and XML differ for {newest} on {bad}")
            how = "Primary, two reads agree."
        else:
            how = "Primary, one read: " + (xml_note or f"XML not yet showing {period_label(newest)}") + "."
    else:
        how = "Fallback: Treasury XML (CSV not reached)."
    old_par = run.old["DATA"]["par"]
    dates = sorted(par)[-2:]
    run.D["par"] = {"dates": dates, **{f: [par[d][f] for d in dates] for f in ("y2", "y10", "y30")}}
    run.fresh["par"] = newest
    fresh = is_new(newest, old_par["dates"][-1])
    for f, lab in (("y2", "2Y Treasury (par)"), ("y10", "10Y Treasury (par)"), ("y30", "30Y Treasury (par)")):
        v = par[newest][f]
        if v is None and fresh:
            run.flag("par", newest, f"its {lab} value was blank")
            res = null_result(newest, "the value was blank")
        else:
            res = new_result(newest, how) if fresh else "No new release."
        run.write_log(lab, pct_s(v), period_label(newest), res)

    # Real yields: CSV, then XML, then the Fed's H.15
    real, real_how, real_source = None, "Primary.", "Treasury real yield curve"
    errors = []
    try:
        real = {d: v["real"] for d, v in treasury.get_csv(treasury.REAL, years).items()}
    except SourceError as e:
        errors.append(str(e))
    if real is None:
        try:
            ms = sorted({d[:7].replace("-", "") for d in sorted(par)[-14:]})
            real = {d: v["real"] for d, v in treasury.get_xml(treasury.REAL, ms).items()}
            real_how, real_source = "Fallback: Treasury XML.", "Treasury real yield curve (XML)"
        except SourceError as e:
            errors.append(str(e))
    if real is None:
        try:
            real = treasury.get_h15()
            real_how, real_source = "Fallback: Fed H.15 10Y TIPS.", "Fed H.15 (fallback)"
        except SourceError as e:
            errors.append(str(e))
    if real is None:
        return run.not_reached("real", "; ".join(errors), "Treasury real curve and H.15")
    both = [d for d in sorted(par) if d in real and real[d] is not None and par[d]["y10"] is not None][-12:]
    if not both:
        return run.not_reached("real", "no date in both the par and real files", "Treasury real curve")
    split = {"dates": both, "real": [real[d] for d in both],
             "be": [round(par[d]["y10"] - real[d], 2) for d in both]}
    for d, r, b in zip(split["dates"], split["real"], split["be"]):  # check 2 (tie)
        if abs(round(par[d]["y10"] - r, 2) - b) > 1e-9:
            raise CheckFailed(f"check 2: breakeven {d} is not par − real")
    run.D["split"] = split
    run.fresh["real"] = both[-1]
    old_last = run.old["DATA"]["split"]["dates"][-1]
    fresh = is_new(both[-1], old_last)
    run.write_log("10Y real yield (par)", pct_s(split["real"][-1]), period_label(both[-1]),
                  new_result(both[-1], real_how) if fresh else "No new release.", real_source)
    run.write_log("10Y breakeven", pct_s(split["be"][-1]), period_label(both[-1]),
                  new_result(both[-1], "Computed: par − real, same date.") if fresh else "No new release.",
                  "Computed: par − real")


def do_fred(run: Run):
    old = run.old["DATA"]["credit"]
    old_last = old["daily"][-1][0] if old["daily"] else None
    try:
        obs, src = fred.get(run.keys.get("FRED_API_KEY"))
    except SourceError as e:
        run.not_reached("fred", e)
        if old_last and business_days_between(parse_iso(old_last), run.today) > 5:
            run.attention.append(f"HY OAS: FRED not reached and the latest point ({old_last}) is more than "
                                 "5 business days old.")
        return
    daily = [[d, round(v, 2)] for d, v in sorted(obs.items())][-6:]
    if len(daily) < 6:
        return run.not_reached("fred", f"only {len(daily)} observations")
    if daily[-1][0] < (old_last or ""):
        return run.not_reached("fred", f"newest observation {daily[-1][0]} is older than the page's {old_last}")
    run.D["credit"] = {**old, "daily": daily}
    run.fresh["fred"] = daily[-1][0]
    run.write_log("HY OAS", pct_s(daily[-1][1]), period_label(daily[-1][0]),
                  new_result(daily[-1][0], f"Single source ({src}).") if is_new(daily[-1][0], old_last)
                  else "No new release.")


def yago(weekly: dict[str, float], last: str) -> float | None:
    """The 30Y print 52 weeks earlier: the nearest date at least 364 days before the latest."""
    cut = parse_iso(last) - dt.timedelta(364)
    c = [d for d in weekly if parse_iso(d) <= cut]
    return weekly[max(c)] if c else None


def do_freddie(run: Run):
    csv_v, xlsx_v, errs = None, None, []
    try:
        csv_v = freddie.get_csv()
    except SourceError as e:
        errs.append(str(e))
    try:
        xlsx_v = freddie.get_xlsx()
    except SourceError as e:
        errs.append(str(e))
    w = csv_v or xlsx_v
    if w is None:
        return run.not_reached("freddie", "; ".join(errs))
    last = max(w)
    old = run.old["DATA"]["mortgage"]
    old_last = old["weekly"][-1][0]
    if last < old_last:
        return run.not_reached("freddie", f"newest week {last} is older than the page's {old_last}")
    weekly = [[d, w[d]] for d in sorted(w)[-6:]]
    fresh = is_new(last, old_last)
    if csv_v is not None and xlsx_v is not None and last in xlsx_v:
        if abs(csv_v[last] - xlsx_v[last]) > 0.005 + 1e-9:
            weekly[-1][1] = None
            how = None
        else:
            how = "Primary, two reads agree."
    elif csv_v is not None:
        how = "Primary, one read (workbook " + ("not read" if xlsx_v is None else f"not yet showing {period_label(last)}") + ")."
    else:
        how = "Fallback: workbook (CSV not reached)."
    run.D["mortgage"] = {**old, "weekly": weekly, "yago": yago(w, last)}
    s = run.score("mort")
    prev = sorted(w)[-2]
    s.update(latest=weekly[-1][1], period=last, prior=w[prev], pperiod=prev)
    run.fresh["freddie"] = last
    if how is None:
        if fresh:
            run.flag("freddie", last, "the CSV and the workbook disagree")
        res = null_result(last, "the CSV and the workbook disagree")
    else:
        res = new_result(last, how) if fresh else "No new release."
    run.write_log("30Y mortgage", pct_s(weekly[-1][1]), period_label(last), res)


def do_eia(run: Run):
    api, html, errs = None, None, []
    key = run.keys.get("EIA_API_KEY")
    if key:
        try:
            api = eia.get_api(key, run.today)
        except SourceError as e:
            errs.append(str(e))
    else:
        errs.append("EIA API: no EIA_API_KEY")
    try:
        html = eia.get_html()
    except SourceError as e:
        errs.append(str(e))
    v = api or html
    if not v:
        return run.not_reached("eia", "; ".join(errs))
    d = max(v)
    old = run.old["DATA"]["wti"]
    if d < old["date"]:
        return run.not_reached("eia", f"newest price {d} is older than the page's {old['date']}")
    val = v[d]
    if api and html and d in html:
        how = "Primary (API); spot table agrees." if abs(api[d] - html[d]) < 0.005 else None
    elif api:
        how = "Primary (API), one read."
    else:
        how = "Fallback: spot price table." if key else "Spot price table, one read (no API key)."
    fresh = is_new(d, old["date"])
    if how is None:
        val = None
        if fresh:
            run.flag("eia", d, "the API and the spot table disagree")
    run.D["wti"] = {"date": d, "v": val}
    run.fresh["eia"] = d
    res = (null_result(d, "the API and the spot table disagree") if how is None
           else new_result(d, how) if fresh else "No new release.")
    run.write_log("WTI spot (Cushing)", "n/a" if val is None else f"${val:.2f}", period_label(d), res)


def do_auction(run: Run):
    try:
        a = auctions.get_latest()
    except SourceError as e:
        return run.not_reached("auction", e)
    old = run.old["DATA"]["auction"]
    if a["date"] < old["date"]:
        return run.not_reached("auction", f"newest auction {a['date']} is older than the page's {old['date']}")
    fresh = is_new(a["date"], old["date"])
    tie = a.pop("tie")
    ok = tie is not None and abs(tie - 100) <= auctions.SHARE_TIE
    if not ok:
        a["dealer"] = a["indirect"] = None
        if fresh:
            run.flag("auction", a["date"], "the bidder shares failed the tie check")
    run.D["auction"] = a
    run.fresh["auction"] = a["date"]
    short = re.sub(r"-Year.*", "Y", a["term"])
    res = (null_result(a["date"], "the bidder shares failed the tie check") if not ok
           else new_result(a["date"], f"Single source; bidder shares sum to {tie:.1f}%.") if fresh
           else "No new release.")
    run.write_log("Latest coupon auction", f"{short}, btc {a['btc']:.2f}" if a["btc"] is not None else "n/a",
                  period_label(a["date"]), res)


def do_factset(run: Run):
    try:
        v = factset.get(run.today)
    except SourceError as e:
        return run.not_reached("factset", e)
    old = run.old["DATA"]["val"]
    if v["report"] < old["report"]:
        return run.not_reached("factset", f"newest report {v['report']} is older than the page's {old['report']}")
    fresh = is_new(v["report"], old["report"])
    run.D["val"] = v
    run.fresh["factset"] = v["report"]
    missing = [k for k in ("pe", "price") if v[k] is None]
    if missing and fresh:
        run.flag("factset", v["report"], f"the {' and '.join(missing)} sentence could not be read")
    res = (null_result(v["report"], f"the {' and '.join(missing)} sentence could not be read") if missing
           else new_result(v["report"], "Single source.") if fresh else "No new release.")
    run.write_log("Forward P/E (FactSet)", "n/a" if v["pe"] is None else f"{v['pe']:.1f}",
                  f"{period_label(v['report'])} report", res)


def _set_score(run: Run, sid: str, r: dict, extra: tuple = ()) -> bool:
    """Write latest/period/prior/pperiod (and extra keys) into a SCORE row; True when the period is new."""
    s, old = run.score(sid), run.old_score(sid)
    if old.get("period") and r["period"] < old["period"]:
        raise SourceError(f"{sid}: newest period {r['period']} is older than the page's {old['period']}")
    s.update({k: r[k] for k in ("latest", "period", "prior", "pperiod") + extra})
    return is_new(r["period"], old.get("period"))


def do_bls(run: Run):
    try:
        b = bls.get(run.keys.get("BLS_API_KEY"), run.today)
    except SourceError as e:
        return run.not_reached("bls", e)
    run.fresh["bls"] = b["ur"]["period"]
    # Check 2: the payroll changes equal the level differences (and BLS's own net change, where given)
    n = b["nfp"]
    bad = [k for k, c in n["chg"].items() if c is None or (n["net1"].get(k) is not None and abs(n["net1"][k] - c) > 0.5)]
    nfp = {k: n[k] for k in ("period", "latest", "pperiod", "prior", "avg3")}
    if bad:
        nfp.update(latest=None, prior=None, avg3=None)
    rows = {}
    try:
        fresh = _set_score(run, "nfp", nfp, ("avg3",))
        if bad and fresh:
            run.flag("bls", nfp["period"], f"payroll changes for {bad} did not tie to the level differences")
        rows["Nonfarm payrolls"] = (signed(nfp["latest"], 0, "k"), nfp["period"], fresh,
                                    None if not bad else "payroll changes did not tie to the levels")
        for sid, lab, fmt in (("ur", "Unemployment rate", lambda r: pct_s(r["latest"], 1)),
                              ("ahe", "Average hourly earnings", lambda r: f"{pct_s(r['latest'], 1)} y/y"),
                              ("ppi", "PPI final demand", lambda r: f"{pct_s(r['latest'], 1)} y/y")):
            rows[lab] = (fmt(b[sid]), b[sid]["period"], _set_score(run, sid, b[sid]), None)
        f1, f2 = _set_score(run, "cpi", b["cpi"]), _set_score(run, "core", b["core"])
        rows["CPI / core CPI"] = (f"{pct_s(b['cpi']['latest'], 1)} / {pct_s(b['core']['latest'], 1)} y/y",
                                  b["cpi"]["period"], f1 or f2, None)
    except SourceError as e:
        return run.not_reached("bls", e)
    run.D["unrate"] = b["unrate"]
    for lab, (val, per, fresh, why) in rows.items():
        res = null_result(per, why) if why else new_result(per, "Single source.") if fresh else "No new release."
        run.write_log(lab, val, period_label(per), res)


def do_dol(run: Run):
    try:
        c = dol.get()
    except SourceError as e:
        return run.not_reached("dol", e)
    r = {"period": c["week"], "latest": c["latest"], "pperiod": (parse_iso(c["week"]) - dt.timedelta(7)).isoformat(),
         "prior": c["prior"]}
    ok = c["table_prior"] is not None and c["table_prior"] == c["prior_exact"]
    if not ok:
        r.update(latest=None, prior=None)
    try:
        fresh = _set_score(run, "claims", r)
    except SourceError as e:
        return run.not_reached("dol", e)
    run.fresh["dol"] = c["week"]
    why = "the row failed the tie check (prior vs the release table)"
    if not ok and fresh:
        run.flag("dol", c["week"], why)
    res = null_result(c["week"], why) if not ok else new_result(c["week"], "Single source; prior ties to the table.") \
        if fresh else "No new release."
    run.write_log("Initial claims", "n/a" if r["latest"] is None else f"{r['latest']}k",
                  f"Week to {period_label(c['week'])}", res)


def do_bea(run: Run):
    try:
        tables = bea.get_tables(run.keys.get("BEA_API_KEY"), run.today)
    except SourceError as e:
        return run.not_reached("bea", e)
    got, errs = {}, []
    for k in ("gdp", "bfi", "fspdp", "pce", "save"):
        try:
            got[k] = getattr(bea, k)(tables)
        except SourceError as e:
            errs.append(str(e))
            if "found" in str(e):
                run.layouts.append(f"BEA {k}: {e}")
    fresh = {}
    for k in ("gdp", "bfi", "fspdp", "pce"):
        if k in got:
            try:
                fresh[k] = _set_score(run, k, got[k])
            except SourceError as e:
                errs.append(str(e))
                got.pop(k)
    if "save" in got:
        old = run.old["CONS"]["save"]
        if got["save"]["months"][-1] < old["months"][-1]:
            errs.append("BEA save: older than the page")
            got.pop("save")
        else:
            run.new["CONS"]["save"] = got["save"]
            fresh["save"] = is_new(got["save"]["months"][-1], old["months"][-1])
    if errs:
        run.unreached.append("BEA: " + "; ".join(errs))
    if "pce" in got:
        p = got["pce"]
        run.write_log("Core PCE", f"{pct_s(p['latest'], 1)} y/y", period_label(p["period"]),
                      new_result(p["period"], "Single source.") if fresh["pce"] else "No new release.")
        run.fresh["bea"] = p["period"]
    else:
        old = next(r for r in run.old["LOG"] if r[0] == "Core PCE")
        run.write_log("Core PCE", old[1], old[2], "Not updated: BEA not read.", reread=False)
    gq = [k for k in ("gdp", "fspdp", "bfi") if k in got]
    if gq:
        s = {k: run.score(k) for k in ("gdp", "fspdp", "bfi")}
        per = s["gdp"]["period"]
        run.write_log("Real GDP / final sales / business investment",
                      " / ".join(pct_s(s[k]["latest"], 1) for k in ("gdp", "fspdp", "bfi")), period_label(per),
                      (new_result(per, "Single source.") if any(fresh.get(k) for k in gq) else "No new release.")
                      + ("" if len(gq) == 3 else f" Not read: {', '.join(k for k in ('gdp', 'fspdp', 'bfi') if k not in got)}."),
                      reread=len(gq) == 3)
    else:
        old = next(r for r in run.old["LOG"] if r[0] == "Real GDP / final sales / business investment")
        run.write_log(old[0], old[1], old[2], "Not updated: BEA not read.", reread=False)
    if "save" in got:
        sv = got["save"]
        run.write_log("Saving rate / real spending", f"{pct_s(sv['rate'][-1], 1)} / {signed(sv['realpce'][-1], 1, '%')}",
                      period_label(sv["months"][-1]),
                      new_result(sv["months"][-1], "Single source.") if fresh["save"] else "No new release.")
    else:
        old = next(r for r in run.old["LOG"] if r[0] == "Saving rate / real spending")
        run.write_log(old[0], old[1], old[2], "Not updated: BEA not read.", reread=False)


def do_gdpnow(run: Run):
    x, page, errs = None, None, []
    try:
        x = gdpnow.get_xlsx()
    except SourceError as e:
        errs.append(str(e))
    try:
        page = gdpnow.get_page()
    except SourceError as e:
        errs.append(str(e))
    if x is None and page is None:
        return run.not_reached("gdpnow", "; ".join(errs))
    if x is not None:
        d, v = x["points"][-1]
        q = x["quarter"]
        prev = x["points"][-2] if len(x["points"]) > 1 else (None, None)
        r = {"period": d, "latest": round(v, 1), "pperiod": prev[0], "prior": round(prev[1], 1) if prev[1] is not None else None}
        if page is not None and page["date"] == d:
            how = "Primary, two reads agree." if page["v"] == r["latest"] and page["quarter"] == q else None
        else:
            how = "Primary, one read (page " + ("not read" if page is None else f"shows {period_label(page['date'])}") + ")."
    else:
        q = page["quarter"]
        r = {"period": page["date"], "latest": page["v"], "pperiod": None, "prior": None}
        how = "Fallback: GDPNow page (workbook not read)."
    if how is None:
        r["latest"] = None
    r["note"] = f"{q[4:]} {q[:4]} estimate"
    try:
        fresh = _set_score(run, "gdpnow", r, ("note",))
    except SourceError as e:
        return run.not_reached("gdpnow", e)
    run.fresh["gdpnow"] = r["period"]
    if how is None and fresh:
        run.flag("gdpnow", r["period"], "the workbook and the page disagree")
    res = null_result(r["period"], "the workbook and the page disagree") if how is None \
        else new_result(r["period"], how) if fresh else "No new release."
    run.write_log("GDPNow", pct_s(r["latest"], 1), period_label(r["period"]), res)


def do_g17(run: Run):
    try:
        g = g17.get()
    except SourceError as e:
        return run.not_reached("g17", e)
    r = {k: g[k] for k in ("period", "latest", "pperiod", "prior")}
    if g["text"] is None:
        how = "Table, one read (release sentence not matched)."
    elif abs(g["text"] - g["latest"]) < 1e-9:
        how = "Table and release sentence agree."
    else:
        how = None
        r["latest"] = None
    try:
        fresh = _set_score(run, "ip", r)
    except SourceError as e:
        return run.not_reached("g17", e)
    run.fresh["g17"] = g["period"]
    if how is None and fresh:
        run.flag("g17", g["period"], "the table and the release sentence disagree")
    res = null_result(g["period"], "the table and the release sentence disagree") if how is None \
        else new_result(g["period"], how) if fresh else "No new release."
    run.write_log("Industrial production", "n/a" if r["latest"] is None else f"{signed(r['latest'], 1, '%')} m/m",
                  period_label(g["period"]), res)


def do_census(run: Run, cal: list[dict] | None):
    key = run.keys.get("CENSUS_API_KEY")
    try:
        h = census.compute_housing(census.get_eits("resconst", key, run.today))
        f1, f2 = _set_score(run, "starts", h["starts"]), _set_score(run, "permits", h["permits"])
        run.fresh["housing"] = h["starts"]["period"]
        per = h["starts"]["period"]
        run.write_log("Housing starts / permits",
                      " / ".join("n/a" if h[k]["latest"] is None else f"{h[k]['latest']:.3f}M" for k in ("starts", "permits")),
                      period_label(per), new_result(per, "Single source.") if (f1 or f2) else "No new release.")
    except SourceError as e:
        if "not found" in str(e) or "outside" in str(e):
            run.layouts.append(str(e))
        run.not_reached("housing", e, "Census")
    try:
        rt = census.compute_retail(census.get_eits("marts", key, run.today))
        old = run.old["CONS"]["ctrl"]
        last = rt["months"][-1]
        if last < old["months"][-1]:
            raise SourceError(f"control group month {last} is older than the page's {old['months'][-1]}")
        fresh = is_new(last, old["months"][-1])
        released = next((c["d"] for c in (cal or []) if c["id"] == "retail" and c.get("period") == last), None)
        if released is None:
            released = old["released"] if last == old["months"][-1] else None
        v = rt["v"] if rt["tie_ok"] else [None] * len(rt["v"])
        run.new["CONS"]["ctrl"] = {"months": rt["months"], "v": v, "released": released}
        run.fresh["retail"] = last
        why = "a component was not smaller than the total (tie check)"
        if not rt["tie_ok"] and fresh:
            run.flag("retail", last, why)
        res = null_result(last, why) if not rt["tie_ok"] else new_result(last, "Computed from Census retail sales.") \
            if fresh else "No new release."
        run.write_log("Retail sales control group", "n/a" if v[-1] is None else f"{signed(v[-1], 1, '%')} m/m",
                      period_label(last), res)
    except SourceError as e:
        if "not found" in str(e):
            run.layouts.append(str(e))
        run.not_reached("retail", e, "Census")


def do_umich(run: Run):
    try:
        u = umich.get()
    except SourceError as e:
        return run.not_reached("umich", e)
    old = run.old["CONS"]["umich"]
    if u["month"] < old["month"]:
        return run.not_reached("umich", f"newest month {u['month']} is older than the page's {old['month']}")
    run.new["CONS"]["umich"] = u
    run.fresh["umich"] = u["month"]
    run.write_log("UMich sentiment (final)", "n/a" if u["ics"] is None else f"{u['ics']:.1f}", period_label(u["month"]),
                  new_result(u["month"], "Final reading.") if is_new(u["month"], old["month"]) else "No new release.")


def do_hhd(run: Run):
    old = run.old["CONS"]["card"]
    nxt = quarter_add(old["q"], 1)
    what = "New York Fed household credit workbook"
    try:
        new = nyfed.get_hhd(nxt)
    except SourceError as e:
        # The new quarter's file exists but cannot be read: a release week, so open the issue
        run.attention.append(f"New York Fed household credit: the {nxt} workbook could not be read: {e}")
        return run.not_reached("hhd", e, what)
    cur = None
    if new is None:
        try:
            cur = nyfed.get_hhd(old["q"])   # re-read: applies revisions, and the seed check on the first run
        except SourceError as e:
            return run.not_reached("hhd", e, what)
    got = new or cur
    if got is None:
        return run.not_reached("hhd", f"no workbook for {old['q']} or {nxt}", "New York Fed household credit workbook")
    run.new["CONS"]["card"] = got
    run.fresh["hhd"] = got["q"]
    run.write_log("Card flow into 90+ days", pct_s(got["flow90"]), period_label(got["q"]),
                  new_result(got["q"], "Single source.") if new else "No new release.")


# ---------------------------------------------------------------------------
# Calendar

def fixed_thursdays(start: dt.date, end: dt.date) -> list[dict]:
    """Claims (8:30 am) and the Freddie Mac rate (12:00 pm) every Thursday; a Thursday holiday moves
    both to Wednesday (e.g. Thanksgiving: Wednesday Nov 25, 2026)."""
    out, d = [], start
    while d <= end:
        thursday = d.weekday() == 3 and is_business_day(d)
        moved = d.weekday() == 2 and not is_business_day(d + dt.timedelta(1))
        if thursday or moved:
            out.append({"d": d.isoformat(), "hm": (8, 30), "id": "claims", "ev": "Initial jobless claims",
                        "src": "Department of Labor"})
            out.append({"d": d.isoformat(), "hm": (12, 0), "id": "mort", "ev": "30-year mortgage rate",
                        "src": "Freddie Mac"})
        d += dt.timedelta(1)
    return out


def _minutes(t: str) -> int:
    from sources.common import parse_time_label
    h, m = parse_time_label(t)
    return h * 60 + m


def build_calendar(run: Run, census_cal: list[dict] | None) -> list[dict]:
    start, end = run.today, add_business_days(run.today, 8)
    years = sorted({start.year, end.year})
    groups: dict[str, list[dict] | None] = {}

    def group(src, fn):
        try:
            groups[src] = [{**e, "src": src} for e in fn()]
        except SourceError as e:
            groups[src] = None
            run.unreached.append(f"{src} calendar: {e}")

    groups["Census"] = [{**e, "src": "Census"} for e in census_cal] if census_cal is not None else None
    group("Treasury", lambda: [{**e, "hm": None, "id": "auction"} for e in auctions.get_upcoming()])
    group("University of Michigan", lambda: [umich.get_next()])
    group("Federal Reserve", lambda: fomc.get(years))
    group("BEA", bea.get_dates)
    group("BLS", bls.get_calendar)
    items = fixed_thursdays(start, end)
    for src, evs in groups.items():
        if evs is None:  # keep this source's rows from the last calendar while they are still ahead
            items += [{**c, "keep": True} for c in run.old["CAL"] if c["src"] == src and c["d"] >= start.isoformat()]
            continue
        items += evs
    out = []
    for e in items:
        if not start.isoformat() <= e["d"] <= end.isoformat():
            continue
        t = e["t"] if e.get("t") else time_label(*e["hm"])
        if e["id"] not in CAL_IDS:
            raise CheckFailed(f"calendar id {e['id']!r} is not one the page knows")
        out.append({"d": e["d"], "t": t, "ev": e["ev"], "src": e["src"], "id": e["id"]})
    out.sort(key=lambda c: (c["d"], _minutes(c["t"])))
    seen, uniq = set(), []
    for c in out:
        k = (c["d"], c["t"], c["ev"])
        if k not in seen:
            seen.add(k)
            uniq.append(c)
    return uniq


# ---------------------------------------------------------------------------
# Diffs, seed checks, moves

def flatten(b: dict) -> dict[str, tuple[str, dict]]:
    """{field: (source, {data date: value})} for every value the script writes."""
    D, C = b["DATA"], b["CONS"]
    out = {
        "DATA.fed.lo": ("nyfed", {D["fed"]["date"]: D["fed"]["lo"]}),
        "DATA.fed.hi": ("nyfed", {D["fed"]["date"]: D["fed"]["hi"]}),
        "DATA.fed.effr": ("nyfed", {D["fed"]["date"]: D["fed"]["effr"]}),
        "DATA.split.real": ("real", dict(zip(D["split"]["dates"], D["split"]["real"]))),
        "DATA.split.be": ("real", dict(zip(D["split"]["dates"], D["split"]["be"]))),
        "DATA.credit.daily": ("fred", {d: v for d, v in D["credit"]["daily"]}),
        "DATA.mortgage.weekly": ("freddie", {d: v for d, v in D["mortgage"]["weekly"]}),
        "DATA.mortgage.yago": ("freddie", {D["mortgage"]["weekly"][-1][0]: D["mortgage"]["yago"]}),
        "DATA.wti.v": ("eia", {D["wti"]["date"]: D["wti"]["v"]}),
        "DATA.val.pe": ("factset", {D["val"]["report"]: D["val"]["pe"]}),
        "DATA.val.price": ("factset", {D["val"]["report"]: D["val"]["price"]}),
        "DATA.unrate.v": ("bls", dict(zip(D["unrate"]["months"], D["unrate"]["v"]))),
        "CONS.ctrl.v": ("retail", dict(zip(C["ctrl"]["months"], C["ctrl"]["v"]))),
        "CONS.ctrl.released": ("retail", {C["ctrl"]["months"][-1]: C["ctrl"]["released"]}),
        "CONS.save.rate": ("bea", dict(zip(C["save"]["months"], C["save"]["rate"]))),
        "CONS.save.realpce": ("bea", dict(zip(C["save"]["months"], C["save"]["realpce"]))),
        "CONS.umich.ics": ("umich", {C["umich"]["month"]: C["umich"]["ics"]}),
        "CONS.umich.px1": ("umich", {C["umich"]["month"]: C["umich"]["px1"]}),
        "CONS.umich.prior": ("umich", {C["umich"]["month"]: C["umich"]["prior"]}),
        "CONS.card.flow90": ("hhd", {C["card"]["q"]: C["card"]["flow90"]}),
        "CONS.card.yago": ("hhd", {C["card"]["q"]: C["card"]["yago"]}),
        "CONS.card.bal": ("hhd", {C["card"]["q"]: C["card"]["bal"]}),
    }
    for f in ("y2", "y10", "y30"):
        out[f"DATA.par.{f}"] = ("par", dict(zip(D["par"]["dates"], D["par"][f])))
    for k in ("term", "high", "btc", "dealer", "indirect"):
        out[f"DATA.auction.{k}"] = ("auction", {D["auction"]["date"]: D["auction"][k]})
    for r in b["SCORE"]:
        src = SCORE_SOURCE[r["id"]]
        out[f"SCORE.{r['id']}.latest"] = (src, {r["period"]: r["latest"]} if r.get("period") else {})
        out[f"SCORE.{r['id']}.prior"] = (src, {r["pperiod"]: r["prior"]} if r.get("pperiod") else {})
        for k in ("avg3", "note"):
            if k in r:
                out[f"SCORE.{r['id']}.{k}"] = (src, {r["period"]: r[k]})
    return out


RATE_FIELDS = {"DATA.fed.lo", "DATA.fed.hi", "DATA.fed.effr", "DATA.par.y2", "DATA.par.y10", "DATA.par.y30",
               "DATA.split.real", "DATA.split.be", "DATA.credit.daily", "DATA.mortgage.weekly",
               "DATA.mortgage.yago", "DATA.auction.high", "SCORE.mort.latest", "SCORE.mort.prior"}
LEVEL_FIELDS = {"DATA.val.price", "CONS.card.bal", "SCORE.starts.latest", "SCORE.permits.latest"}


@dataclass
class Diff:
    field: str
    source: str
    date: str
    old: object
    new: object
    old_date: str | None
    revision: bool


def diff_blocks(old: dict, new: dict) -> list[Diff]:
    fo, fn = flatten(old), flatten(new)
    out = []
    for f, (src, nv) in fn.items():
        ov = fo.get(f, (src, {}))[1]
        for d in sorted(set(nv) | set(ov)):
            if d not in nv:
                continue  # dropped off the end of a rolling window
            if d in ov and ov[d] == nv[d]:
                continue
            od = d if d in ov else (max(ov) if ov else None)
            out.append(Diff(f, src, d, ov.get(d) if d in ov else (ov[od] if od else None), nv[d], od,
                            d in ov and ov[d] is not None))
    return out


def seed_check(run: Run, diffs: list[Diff]) -> None:
    """Check 3 (first run only): a value re-read for the same data date must equal the seeded one."""
    bad = [d for d in diffs if d.revision and run.seeded(d.source)]
    if bad and not run.skip_seed:
        run.seed_mismatch = [f"{d.field} for {d.date}: page {d.old} vs source {d.new} ({SOURCE_NAMES[d.source]})" for d in bad]
        raise CheckFailed("check 3: seed values differ from the sources (nothing overwritten):\n"
                          + "\n".join(f"- {x}" for x in run.seed_mismatch))


def moves(old: dict, new: dict) -> tuple[list[str], list[str]]:
    """Check 4: newest value vs the previous newest value. Returns (all moves, level-series moves)."""
    fo, fn = flatten(old), flatten(new)
    out, levels = [], []
    for f, (src, nv) in fn.items():
        ov = fo.get(f, (src, {}))[1]
        if not nv or not ov:
            continue
        a, b = ov[max(ov)], nv[max(nv)]
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)) or isinstance(a, bool) or a == b:
            continue
        if f in RATE_FIELDS:
            if abs(b - a) > RATE_MOVE + 1e-9:
                out.append(f"{f}: {a} → {b} ({(b - a) * 100:+.0f}bp)")
        elif a and abs(b / a - 1) * 100 > PCT_MOVE:
            line = f"{f}: {a} → {b} ({(b / a - 1) * 100:+.1f}%)"
            out.append(line)
            if f in LEVEL_FIELDS:
                levels.append(line)
    return out, levels


def check_shapes(b: dict) -> None:
    """Check 6: array lengths and order."""
    D = b["DATA"]
    s = D["split"]
    if not (len(s["dates"]) == len(s["real"]) == len(s["be"]) <= 12):
        raise CheckFailed("check 6: split arrays have unequal length or more than 12 entries")
    if len(D["credit"]["daily"]) != 6:
        raise CheckFailed("check 6: credit.daily does not have 6 entries")
    if len(D["mortgage"]["weekly"]) != 6:
        raise CheckFailed("check 6: mortgage.weekly does not have 6 entries")
    if not (len(D["unrate"]["months"]) == len(D["unrate"]["v"]) == 6):
        raise CheckFailed("check 6: unrate does not have 6 months")
    if not (len(D["par"]["dates"]) == len(D["par"]["y2"]) == len(D["par"]["y10"]) == len(D["par"]["y30"]) == 2):
        raise CheckFailed("check 6: par arrays do not have 2 entries")
    C = b["CONS"]
    if not (len(C["ctrl"]["months"]) == len(C["ctrl"]["v"])):
        raise CheckFailed("check 6: ctrl arrays have unequal length")
    if not (len(C["save"]["months"]) == len(C["save"]["rate"]) == len(C["save"]["realpce"])):
        raise CheckFailed("check 6: save arrays have unequal length")
    seqs = {"split.dates": s["dates"], "par.dates": D["par"]["dates"],
            "credit.daily": [x[0] for x in D["credit"]["daily"]], "credit.anchors": [x[0] for x in D["credit"]["anchors"]],
            "mortgage.weekly": [x[0] for x in D["mortgage"]["weekly"]],
            "mortgage.monthly": [x[0] for x in D["mortgage"]["monthly"]], "unrate.months": D["unrate"]["months"],
            "ctrl.months": C["ctrl"]["months"], "save.months": C["save"]["months"]}
    for name, seq in seqs.items():
        if any(b2 <= a2 for a2, b2 in zip(seq, seq[1:])):
            raise CheckFailed(f"check 6: {name} is not ascending without duplicates")
    cal = [(c["d"], _minutes(c["t"])) for c in b["CAL"]]
    if cal != sorted(cal):
        raise CheckFailed("check 6: CAL is not sorted by date and time")


# ---------------------------------------------------------------------------
# summary.json (the page's tile rules, ported)

def tiles(D: dict) -> tuple[list[dict], dict]:
    last = lambda a: a[-1] if a else None
    numv = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
    P, S, C, V, A = D["par"], D["split"], D["credit"], D["val"], D["auction"]
    y2, y10 = last(P["y2"]), last(P["y10"])
    curve = y10 - y2 if numv(y2) and numv(y10) else None
    real, be = last(S["real"]), last(S["be"])
    d_real = real - S["real"][0] if numv(real) and numv(S["real"][0]) else None
    d_be = be - S["be"][0] if numv(be) and numv(S["be"][0]) else None
    hy = C["daily"][-1][1] if C["daily"] else None
    hy5 = C["daily"][-6][1] if len(C["daily"]) > 5 else None
    hy_chg5 = hy - hy5 if numv(hy) and numv(hy5) else None
    ey = 100 / V["pe"] if numv(V["pe"]) and V["pe"] > 0 else None
    gap = ey - y10 if numv(ey) and numv(y10) else None

    def driver():
        if not numv(d_real) or not numv(d_be):
            return "none", "Not known"
        if d_real < 0 and d_be < 0:
            return "good", "Easing"
        return ("good", "Growth-led") if abs(d_real) >= abs(d_be) else ("serious", "Inflation-led")

    def curve_st():
        if not numv(curve):
            return "none", "Not known"
        if curve > 0:
            return "good", "Not inverted"
        return ("warn", "Flat") if curve >= -0.25 else ("crit", "Inverted")

    def hy_st():
        if not numv(hy):
            return "none", "Not known"
        if hy > 5:
            return "crit", "Credit stress"
        if hy >= 3.5:
            return "serious", "Stress"
        if hy >= 3 or (numv(hy_chg5) and hy_chg5 > 0.25):
            return "warn", "Watch"
        return "good", "Calm"

    def auction_st():
        if not numv(A["btc"]):
            return "none", "Not known"
        weak = A["btc"] < 2.4 or (numv(A["dealer"]) and A["dealer"] > 20)
        return ("warn", "Soft") if weak else ("good", "Solid")

    def gap_st():
        if not numv(gap):
            return "none", "Not known"
        if gap >= 1.5:
            return "good", "Cushion"
        return ("warn", "Thin") if gap >= 0.5 else ("crit", "No cushion")

    names = ("What's driving the 10Y?", "Curve shape (10Y − 2Y)", "High-yield credit spread", "Auction demand",
             "Equity yield gap")
    out = [{"name": n, "status": s, "label": l}
           for n, (s, l) in zip(names, (driver(), curve_st(), hy_st(), auction_st(), gap_st()))]
    return out, {"curve": curve, "gap": gap, "hy": hy, "real": real, "be": be}


def build_summary(D: dict) -> dict:
    t, v = tiles(D)
    r2 = lambda x: round(x, 2) if isinstance(x, (int, float)) else None
    return {"updated": D["updated"], "yields_date": D["par"]["dates"][-1], "y2": D["par"]["y2"][-1],
            "y10": D["par"]["y10"][-1], "y30": D["par"]["y30"][-1], "curve": r2(v["curve"]),
            "real": v["real"], "be": v["be"], "split_date": D["split"]["dates"][-1],
            "hy": v["hy"], "hy_date": D["credit"]["daily"][-1][0] if D["credit"]["daily"] else None,
            "gap": r2(v["gap"]), "fed": {"lo": D["fed"]["lo"], "hi": D["fed"]["hi"], "date": D["fed"]["date"]},
            "mort": D["mortgage"]["weekly"][-1][1], "mort_date": D["mortgage"]["weekly"][-1][0], "tiles": t}


# ---------------------------------------------------------------------------
# Running

class Report:
    def __init__(self):
        self.lines: list[str] = []

    def __call__(self, line: str = ""):
        self.lines.append(line)
        print(line)

    def write(self):
        path = os.environ.get("GITHUB_STEP_SUMMARY")
        if path:
            with open(path, "a", encoding="utf-8") as f:
                f.write("\n".join(self.lines) + "\n")


def set_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")


def et_today() -> dt.date:
    from zoneinfo import ZoneInfo
    return dt.datetime.now(ZoneInfo("America/New_York")).date()


def update(run: Run) -> None:
    """Every source, in page order; then the calendar and the log."""
    census_cal = None
    try:
        years = sorted({run.today.year, add_business_days(run.today, 8).year})
        census_cal = census.get_calendar(years)
    except SourceError as e:
        run.unreached.append(f"Census calendar: {e}")
    for fn in (do_nyfed, do_treasury, do_fred, do_freddie, do_eia, do_auction, do_factset, do_bls, do_dol,
               do_bea, do_gdpnow, do_g17):
        fn(run)
    do_census(run, census_cal)
    do_umich(run)
    do_hhd(run)
    run.new["CAL"] = build_calendar(run, census_cal)
    run.new["LOG"] = [run.log.get(r[0], r) for r in run.old["LOG"]]


def commit_message(run: Run, all_moves: list[str]) -> str:
    fresh = [f"{COMMIT_NAMES[s]} {period_label(d)}" for s, d in run.fresh.items() if s in run.new_sources]
    lines = [f"Data: {', '.join(fresh) if fresh else 'revisions and first reads'}", ""]
    lines.append("Data dates read: " + "; ".join(f"{COMMIT_NAMES[s]} {d}" for s, d in run.fresh.items()))
    if all_moves:
        lines += ["", f"Moves over {PCT_MOVE:.0f}% (or {RATE_MOVE * 100:.0f}bp for yields and spreads):"]
        lines += [f"- {m}" for m in all_moves]
    if run.unreached:
        lines += ["", "Not reached:"] + [f"- {u[:200]}" for u in run.unreached]
    return "\n".join(lines) + "\n"


def run_update(page: Path, summary_path: Path, dry_run: bool, commit_msg: Path | None = None,
               issue_body: Path | None = None, today: dt.date | None = None, keys: dict | None = None,
               skip_seed: bool = False, updater=update) -> int:
    say = Report()
    say(f"## Yield Regime Monitor update{' (dry run)' if dry_run else ''}")
    set_output("changed", "false")
    set_output("attention", "false")
    today = today or et_today()
    keys = keys if keys is not None else {k: os.environ.get(k, "") for k in
                                         ("BLS_API_KEY", "BEA_API_KEY", "CENSUS_API_KEY", "FRED_API_KEY", "EIA_API_KEY")}
    run = None
    try:
        html = page.read_text(encoding="utf-8")
        old = load_page(html)
        run = Run(today=today, old=old, keys=keys, skip_seed=skip_seed)
        say(f"- Run date (ET): {today}. Page data blocks last written {old['DATA']['updated']}.")
        updater(run)
        diffs = diff_blocks(old, run.new)
        seed_check(run, diffs)
        data_changed = bool(diffs)
        if data_changed:
            run.new["DATA"]["updated"] = today.isoformat()
        check_shapes(run.new)
        all_moves, level_moves = moves(old, run.new)
        new_html = render_page(html, run.new)
        verify_page(html, new_html, run.new)
    except CheckFailed as e:
        say(f"\n**Check failed, nothing written:** {e}")
        if run and run.unreached:
            say("\n### Sources not reached")
            for u in run.unreached:
                say(f"- {u}")
        if run and run.layouts:
            say("\n### Layouts to map")
            for l in run.layouts:
                say(f"- {l}")
        if issue_body:
            issue_body.write_text(f"The data update failed a check and committed nothing.\n\n{e}\n", encoding="utf-8")
        say.write()
        return 1

    if level_moves:
        run.attention += [f"Level series moved more than {PCT_MOVE:.0f}%: {m}" for m in level_moves]
    say("\nAll checks passed.\n")
    say("### Values that change\n")
    if diffs:
        say("| Field | Old → new | Data date | Source |")
        say("|---|---|---|---|")
        for d in diffs:
            od = f" ({d.old_date})" if d.old_date and d.old_date != d.date else ""
            say(f"| `{d.field}` | {dump(d.old)}{od} → {dump(d.new)}{' (revised)' if d.revision else ''} | {d.date} | {SOURCE_NAMES[d.source]} |")
    else:
        say("Nothing new from any source.")
    cal_changed = run.new["CAL"] != old["CAL"]
    say(f"\nCalendar: {len(run.new['CAL'])} items{' (changed)' if cal_changed else ''}.")
    say(f"\n### Moves over {PCT_MOVE:.0f}% (or {RATE_MOVE * 100:.0f}bp for yields and spreads)\n")
    say("\n".join(f"- {m}" for m in all_moves) if all_moves else "None.")
    say("\n### Sources not reached\n")
    say("\n".join(f"- {u}" for u in run.unreached) if run.unreached else "None.")
    if run.layouts:
        say("\n### Layouts to map (a parser could not find its fields)\n")
        say("\n".join(f"- {l}" for l in run.layouts))
    if run.notes:
        say("\n### Notes\n")
        say("\n".join(f"- {n}" for n in run.notes))
    if run.attention:
        say("\n**Needs attention (an issue will be opened):**")
        for a in run.attention:
            say(f"- {a}")
    if skip_seed:
        say("\nSeed checks were skipped for this run (workflow input).")

    if not data_changed:
        say("\nNothing new: no commit.")
    elif dry_run:
        say("\nDry run: nothing written.")
    else:
        page.write_text(new_html, encoding="utf-8")
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(build_summary(run.new["DATA"]), ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")
        if commit_msg:
            commit_msg.write_text(commit_message(run, all_moves), encoding="utf-8")
        set_output("changed", "true")
    if run.attention and not dry_run:
        if issue_body:
            issue_body.write_text("\n\n".join(run.attention) + "\n", encoding="utf-8")
        set_output("attention", "true")
    say.write()
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="fetch, parse and check; write nothing")
    ap.add_argument("--page", type=Path, default=ROOT / "index.html")
    ap.add_argument("--summary", type=Path, default=ROOT / "data" / "summary.json")
    ap.add_argument("--commit-msg", type=Path, help="where to write the commit message")
    ap.add_argument("--issue-body", type=Path, help="where to write the text for a 'needs attention' issue")
    ap.add_argument("--skip-seed-checks", action="store_true",
                    help="accept source values that differ from the seeded ones (after reviewing them)")
    ap.add_argument("--summary-only", action="store_true",
                    help="rewrite data/summary.json from the page and exit (no fetching)")
    a = ap.parse_args(argv)
    if a.summary_only:
        data = load_page(a.page.read_text(encoding="utf-8"))["DATA"]
        a.summary.parent.mkdir(parents=True, exist_ok=True)
        a.summary.write_text(json.dumps(build_summary(data), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 0
    return run_update(a.page, a.summary, a.dry_run, a.commit_msg, a.issue_body, skip_seed=a.skip_seed_checks)


if __name__ == "__main__":
    sys.exit(main())
