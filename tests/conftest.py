"""Shared fixtures: every source getter patched to read tests/fixtures instead of the network."""
import datetime as dt
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
FIX = Path(__file__).parent / "fixtures"
TODAY = dt.date(2026, 10, 19)

from sources import (auctions, bea, bls, census, dol, eia, factset, fomc, fred, freddie,  # noqa: E402
                     g17, gdpnow, nyfed, treasury, umich)


def text(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def raw(name: str) -> bytes:
    return (FIX / name).read_bytes()


def with_dates(rows):
    """JSON rows from a workbook fixture: ISO datetime strings back to datetimes."""
    f = lambda c: dt.datetime.fromisoformat(c) if isinstance(c, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", c) else c
    return [[f(c) for c in r] for r in rows]


def gdpnow_rows():
    return with_dates(json.loads(text("gdpnow_currentqtrevolution.json"))["CurrentQtrEvolution"])


def pmms_rows():
    d = json.loads(text("pmms_historicalweeklydata_rows.json"))
    return with_dates(d["head"] + d["tail"])


def hhd_sheets():
    return json.loads(text("hhd_c_report_2026q2_sheets.json"))


def bea_tables():
    return {t: bea.parse_api(text(f"bea_api_doc_sample_{t}.json")) for t, _ in bea.TABLES}


def auction_records():
    return json.loads(text("td_auctioned_note.json")) + json.loads(text("td_auctioned_bond.json"))


@pytest.fixture
def offline(monkeypatch):
    """Patch every getter to its fixture. Returns a dict a test can edit to change one source."""
    src = {
        "effr": lambda: nyfed.parse_effr(text("nyfed_effr_last3.json")),
        "par_csv": lambda: treasury.parse_csv(text("treasury_par_2026.csv"), treasury.PAR),
        "real_csv": lambda: treasury.parse_csv(text("treasury_real_2026.csv"), treasury.REAL),
        "par_xml": lambda: treasury.parse_xml(text("treasury_par_202610.xml"), treasury.PAR),
        "real_xml": lambda: treasury.parse_xml(text("treasury_real_202610.xml"), treasury.REAL),
        "fred": lambda: (fred.parse_api(text("fred_api_doc_sample.json")), "FRED API"),
        "pmms_csv": lambda: freddie.parse_csv(text("pmms_history.csv")),
        "pmms_xlsx": lambda: freddie.parse_xlsx_rows(pmms_rows()),
        "eia_api": lambda: eia.parse_api(text("eia_api_rwtc.json")),
        "eia_html": lambda: eia.parse_html(raw("eia_spot_daily.htm")),
        "auction": lambda: auctions.latest_coupon(auction_records()),
        "upcoming": lambda: auctions.upcoming(json.loads(text("td_upcoming.json"))),
        "factset": lambda: factset.parse_text(text("factset_101626_extract.txt"), text("factset_101626_extract.txt")),
        "bls": lambda: bls.compute(bls.parse_api(text("bls_api_v2_doc_sample.json"))),
        "bls_cal": lambda: bls.parse_ics((FIX / "bls_ics_doc_sample.ics").read_text(encoding="utf-8")),
        "dol": lambda: dol.parse_text(text("dol_claims_doc_sample.txt")),
        "bea": bea_tables,
        "bea_dates": lambda: bea.parse_dates(text("bea_release_dates.json")),
        "gdpnow_xlsx": lambda: gdpnow.parse_rows(gdpnow_rows()),
        "gdpnow_page": lambda: gdpnow.parse_page(text("gdpnow_page_text.txt")),
        "g17": lambda: g17.parse(raw("g17_current_excerpt.html")),
        "marts": lambda: census.parse_eits(text("census_eits_doc_sample_marts.json"), "marts"),
        "resconst": lambda: census.parse_eits(text("census_eits_doc_sample_resconst.json"), "resconst"),
        "census_cal": lambda: census.parse_calendar(raw("census_calendar_2026_excerpt.html")),
        "umich": lambda: umich.compute(umich.parse_csv(raw("umich_tbmics.csv"), umich.ICS_HEADER, "ICS_ALL"),
                                       umich.parse_csv(raw("umich_tbmpx1px5.csv"), umich.PX_HEADER, "PX_MD")),
        "umich_next": lambda: umich.parse_next(text("umich_home_text.txt")),
        "hhd": lambda q: nyfed.parse_hhd(hhd_sheets(), q) if q == "2026Q2" else None,
        "fomc": lambda years: fomc.parse(raw("fomc_2026_panel.html"), years),
    }
    call = lambda k, *a: src[k](*a)
    monkeypatch.setattr(nyfed, "get_effr", lambda: call("effr"))
    monkeypatch.setattr(treasury, "get_csv", lambda kind, years: call("par_csv" if kind == treasury.PAR else "real_csv"))
    monkeypatch.setattr(treasury, "get_xml", lambda kind, months: call("par_xml" if kind == treasury.PAR else "real_xml"))
    monkeypatch.setattr(treasury, "get_h15", lambda: treasury.parse_h15(text("h15_data_excerpt.xml")))
    monkeypatch.setattr(fred, "get", lambda key: call("fred"))
    monkeypatch.setattr(freddie, "get_csv", lambda: call("pmms_csv"))
    monkeypatch.setattr(freddie, "get_xlsx", lambda: call("pmms_xlsx"))
    monkeypatch.setattr(eia, "get_api", lambda key, today: call("eia_api"))
    monkeypatch.setattr(eia, "get_html", lambda: call("eia_html"))
    monkeypatch.setattr(auctions, "get_latest", lambda: call("auction"))
    monkeypatch.setattr(auctions, "get_upcoming", lambda: call("upcoming"))
    monkeypatch.setattr(factset, "get", lambda today: call("factset"))
    monkeypatch.setattr(bls, "get", lambda key, today: call("bls"))
    monkeypatch.setattr(bls, "get_calendar", lambda: call("bls_cal"))
    monkeypatch.setattr(dol, "get", lambda: call("dol"))
    monkeypatch.setattr(bea, "get_tables", lambda key, today: call("bea"))
    monkeypatch.setattr(bea, "get_dates", lambda: call("bea_dates"))
    monkeypatch.setattr(gdpnow, "get_xlsx", lambda: call("gdpnow_xlsx"))
    monkeypatch.setattr(gdpnow, "get_page", lambda: call("gdpnow_page"))
    monkeypatch.setattr(g17, "get", lambda: call("g17"))
    monkeypatch.setattr(census, "get_eits", lambda ds, key, today: call(ds))
    monkeypatch.setattr(census, "get_calendar", lambda years: call("census_cal"))
    monkeypatch.setattr(umich, "get", lambda: call("umich"))
    monkeypatch.setattr(umich, "get_next", lambda: call("umich_next"))
    monkeypatch.setattr(nyfed, "get_hhd", lambda q: call("hhd", q))
    monkeypatch.setattr(fomc, "get", lambda years: call("fomc", years))
    return src
