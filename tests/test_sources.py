"""Parser tests against tests/fixtures. Values are checked against the seed in index.html.
Run: python -m pytest tests"""
import datetime as dt
import json

import pytest

from conftest import (FIX, auction_records, bea_tables, gdpnow_rows, hhd_sheets, pmms_rows, raw, text)

import update as u
from sources import (auctions, bea, bls, census, common, dol, eia, factset, fomc, fred, freddie, g17,
                     gdpnow, nyfed, treasury, umich)

PAGE = u.load_page((u.ROOT / "index.html").read_text(encoding="utf-8"))
D = PAGE["DATA"]
SCORE = {r["id"]: r for r in PAGE["SCORE"]}


# --- Treasury -----------------------------------------------------------------

def test_par_csv_matches_page():
    par = treasury.parse_csv(text("treasury_par_2026.csv"), treasury.PAR)
    for i, d in enumerate(D["par"]["dates"]):
        assert [par[d][f] for f in ("y2", "y10", "y30")] == [D["par"][f][i] for f in ("y2", "y10", "y30")]
    assert max(par) == "2026-10-19"
    assert "2026-10-12" not in par  # bond-market holiday: no row


def test_split_matches_page_real_and_breakeven():
    par = treasury.parse_csv(text("treasury_par_2026.csv"), treasury.PAR)
    real = treasury.parse_csv(text("treasury_real_2026.csv"), treasury.REAL)
    s = D["split"]
    assert [real[d]["real"] for d in s["dates"]] == s["real"]
    assert [round(par[d]["y10"] - real[d]["real"], 2) for d in s["dates"]] == s["be"]


def test_xml_second_read_agrees_with_csv():
    csv_ = treasury.parse_csv(text("treasury_par_2026.csv"), treasury.PAR)
    xml = treasury.parse_xml(text("treasury_par_202610.xml"), treasury.PAR)
    for d, v in xml.items():
        assert v == csv_[d]
    rx = treasury.parse_xml(text("treasury_real_202610.xml"), treasury.REAL)
    rc = treasury.parse_csv(text("treasury_real_2026.csv"), treasury.REAL)
    assert all(rx[d] == rc[d] for d in rx)
    assert rx["2026-10-16"] == {"real": 2.74}


def test_csv_header_is_matched_exactly():
    bad = text("treasury_real_2026.csv").replace('"10 YR"', '"10 Yr"', 1)
    with pytest.raises(common.SourceError, match="header"):
        treasury.parse_csv(bad, treasury.REAL)


def test_h15_tips_series_skips_no_data():
    h = treasury.parse_h15(text("h15_data_excerpt.xml"))
    assert h["2026-10-16"] == 2.74 and h["2026-10-15"] == 2.78
    assert "2026-10-12" not in h  # OBS_STATUS="ND"


# --- New York Fed ---------------------------------------------------------------

def test_effr():
    r = nyfed.parse_effr(text("nyfed_effr_last3.json"))
    assert r == {"date": "2026-10-16", "lo": 3.75, "hi": 4.0, "effr": 3.88}


def test_household_credit_matches_page():
    assert nyfed.parse_hhd(hhd_sheets(), "2026Q2") == PAGE["CONS"]["card"]


def test_household_credit_wrong_quarter_fails():
    with pytest.raises(common.SourceError):
        nyfed.parse_hhd(hhd_sheets(), "2026Q3")


# --- FRED (documentation-based samples) ------------------------------------------

def test_fred_api_and_csv_agree_and_skip_missing():
    a = fred.parse_api(text("fred_api_doc_sample.json"))
    c = fred.parse_csv(text("fredgraph_doc_sample.csv"))
    assert a == c and "2026-10-12" not in a
    assert [[d, a[d]] for d in sorted(a)[-6:]] == D["credit"]["daily"]


def test_fred_error_response():
    with pytest.raises(common.SourceError, match="api_key"):
        fred.parse_api(text("fred_api_bad_key.json"))


# --- Freddie Mac -------------------------------------------------------------------

def test_pmms_csv_matches_page():
    w = freddie.parse_csv(text("pmms_history.csv"))
    assert [[d, w[d]] for d in sorted(w)[-6:]] == D["mortgage"]["weekly"]
    assert u.yago(w, "2026-10-15") == D["mortgage"]["yago"] == 6.27


def test_pmms_workbook_agrees_with_csv():
    c, x = freddie.parse_csv(text("pmms_history.csv")), freddie.parse_xlsx_rows(pmms_rows())
    assert all(abs(c[d] - x[d]) < 0.005 for d in x if d in c)
    assert max(x) == "2026-10-15"


# --- EIA ------------------------------------------------------------------------------

def test_eia_api_and_table_agree():
    a, h = eia.parse_api(text("eia_api_rwtc.json")), eia.parse_html(raw("eia_spot_daily.htm"))
    assert a["2026-10-14"] == h["2026-10-14"] == D["wti"]["v"]
    assert max(h) == D["wti"]["date"]


def test_eia_missing_key_error():
    with pytest.raises(common.SourceError, match="API_KEY_MISSING"):
        eia.parse_api(text("eia_api_missing_key.json"))


# --- TreasuryDirect --------------------------------------------------------------------

def test_latest_coupon_auction_matches_page():
    a = auctions.latest_coupon(auction_records())
    page = D["auction"]
    assert {k: a[k] for k in ("date", "term", "high", "btc", "indirect")} == {k: page[k] for k in ("date", "term", "high", "btc", "indirect")}
    assert a["dealer"] == 6.8 and page["dealer"] is None  # the seed had no dealer share; the first run fills it
    assert abs(a["tie"] - 100) <= auctions.SHARE_TIE


def test_auction_skips_tips_frn_and_3_year():
    recs = auction_records()
    newest = max(recs, key=lambda r: r["competitiveAuctionCloseDate"])
    tips = dict(newest, inflationProtected="Yes", competitiveAuctionCloseDate="2026-10-30T00:00:00")
    frn = dict(newest, floatingRate="Yes", competitiveAuctionCloseDate="2026-10-30T00:00:00")
    three = dict(newest, originalSecurityTerm="3-Year", competitiveAuctionCloseDate="2026-10-30T00:00:00")
    assert auctions.latest_coupon(recs + [tips, frn, three])["date"] == "2026-10-08"


def test_upcoming_auctions():
    ev = auctions.upcoming(json.loads(text("td_upcoming.json")))
    assert ev[:4] == [{"d": "2026-10-21", "t": "1:00 pm", "ev": "20-year bond auction"},
                      {"d": "2026-10-22", "t": "1:00 pm", "ev": "5-year TIPS auction"},
                      {"d": "2026-10-26", "t": "1:00 pm", "ev": "2-year note auction"},
                      {"d": "2026-10-27", "t": "1:00 pm", "ev": "5-year note auction"}]
    assert all("auction" in e["ev"] and "week" not in e["ev"].lower() for e in ev)  # no bills, no FRN


# --- FactSet ----------------------------------------------------------------------------

def test_factset_sentences_match_page():
    t = text("factset_101626_extract.txt")
    assert factset.parse_text(t, t) == D["val"]


def test_factset_missing_sentence_gives_null():
    t = text("factset_101626_extract.txt").replace("closing price of", "close of")
    assert factset.parse_text(t, t)["price"] is None


def test_factset_fridays():
    assert factset.fridays(dt.date(2026, 10, 19)) == [dt.date(2026, 10, 16), dt.date(2026, 10, 9)]
    assert factset.fridays(dt.date(2026, 10, 23))[0] == dt.date(2026, 10, 23)


# --- BLS (documentation-based sample) -------------------------------------------------

def test_bls_compute_matches_seed():
    b = bls.compute(bls.parse_api(text("bls_api_v2_doc_sample.json")))
    for sid in ("nfp", "ur", "ahe", "cpi", "core"):
        for k in ("latest", "period", "prior", "pperiod"):
            assert b[sid][k] == SCORE[sid][k], (sid, k)
    assert b["nfp"]["avg3"] == SCORE["nfp"]["avg3"] == 51
    assert b["ppi"]["latest"] == 6.3 and b["ppi"]["period"] == "2026-09"
    assert b["unrate"] == D["unrate"]


def test_bls_error_status():
    with pytest.raises(common.SourceError, match="REQUEST_NOT_PROCESSED"):
        bls.parse_api(text("bls_api_threshold.json"))


def test_bls_calendar():
    ev = bls.parse_ics((FIX / "bls_ics_doc_sample.ics").read_text(encoding="utf-8"))
    assert [(e["d"], e["hm"], e["id"], e["ev"]) for e in ev] == [
        ("2026-11-06", (8, 30), "nfp", "Employment report (Oct)"),
        ("2026-11-12", (8, 30), "cpi", "CPI (Oct)"),
        ("2026-11-13", (8, 30), "ppi", "PPI (Oct)"),      # UTC 13:30 -> 8:30 ET
        ("2026-12-10", (8, 30), "cpi", "CPI (Nov)")]      # folded SUMMARY line


# --- BEA (documentation-based samples) ------------------------------------------------

def test_bea_compute_matches_seed():
    t = bea_tables()
    for k in ("gdp", "bfi", "pce"):
        r = getattr(bea, k)(t)
        assert {x: r[x] for x in ("latest", "period", "prior", "pperiod")} == \
               {x: SCORE[k][x] for x in ("latest", "period", "prior", "pperiod")}
    assert bea.fspdp(t)["latest"] == SCORE["fspdp"]["latest"]
    s = bea.save(t)
    assert s["months"][-2:] == PAGE["CONS"]["save"]["months"] and s["rate"][-2:] == PAGE["CONS"]["save"]["rate"]
    assert s["realpce"][-1] == PAGE["CONS"]["save"]["realpce"][-1]


def test_bea_nonresidential_is_read_inside_fixed_investment():
    t = bea_tables()
    rows = t["T10101"]
    # a decoy 'Nonresidential' line before 'Fixed investment' must not be used
    t["T10101"] = [dict(r, line=0, label="Nonresidential", value=99.0) for r in rows if r["line"] == 1] + rows
    assert bea.bfi(t)["latest"] == 9.0


def test_bea_missing_line_lists_labels():
    t = bea_tables()
    t["T10401"] = [r for r in t["T10401"] if r["label"] != bea.FSPDP_LINE[1]]
    with pytest.raises(common.SourceError, match="lines: .*Final sales to domestic purchasers"):
        bea.fspdp(t)


def test_bea_invalid_key():
    with pytest.raises(common.SourceError, match="Invalid API UserId"):
        bea.parse_api(text("bea_api_invalid_key.json"))


def test_bea_release_dates():
    ev = [e for e in bea.parse_dates(text("bea_release_dates.json")) if "2026-10" <= e["d"] < "2027-01"]
    assert ("2026-10-29", (8, 30), "GDP, advance (Q3)") in [(e["d"], e["hm"], e["ev"]) for e in ev]
    assert ("2026-11-25", "GDP, second (Q3)") in [(e["d"], e["ev"]) for e in ev]
    assert ("2026-10-29", "Personal income and outlays (Sep)") in [(e["d"], e["ev"]) for e in ev]


# --- Census ----------------------------------------------------------------------------

def test_census_samples_match_seed():
    h = census.compute_housing(census.parse_eits(text("census_eits_doc_sample_resconst.json"), "resconst"))
    assert (h["starts"]["latest"], h["permits"]["latest"]) == (SCORE["starts"]["latest"], SCORE["permits"]["latest"])
    r = census.compute_retail(census.parse_eits(text("census_eits_doc_sample_marts.json"), "marts"))
    assert r["months"][-1] == "2026-09" and r["v"][-1] == PAGE["CONS"]["ctrl"]["v"][-1] and r["tie_ok"]


def test_census_missing_code_lists_combinations():
    rows = json.loads(text("census_eits_doc_sample_resconst.json"))
    rows = [rows[0]] + [r for r in rows[1:] if r[4] != "PERMITS"]
    with pytest.raises(common.SourceError, match="combinations: .*STARTS/TOTAL/yes"):
        census.compute_housing(census.parse_eits(json.dumps(rows), "resconst"))


def test_census_missing_key_page_is_an_error():
    with pytest.raises(common.SourceError, match="not JSON"):
        census.parse_eits("<html><head><title>Missing Key</title></head></html>", "marts")


def test_census_calendar():
    ev = census.parse_calendar(raw("census_calendar_2026_excerpt.html"))
    assert {"d": "2026-10-20", "hm": (8, 30), "id": "starts", "period": "2026-09",
            "ev": "Housing starts and permits (Sep)"} in ev
    assert {"d": "2026-10-15", "hm": (8, 30), "id": "retail", "period": "2026-09", "ev": "Retail sales (Sep)"} in ev


# --- Department of Labor (documentation-based sample) ----------------------------------

def test_claims_matches_seed_and_ties():
    c = dol.parse_text(text("dol_claims_doc_sample.txt"))
    assert (c["week"], c["latest"], c["prior"]) == (SCORE["claims"]["period"], SCORE["claims"]["latest"], SCORE["claims"]["prior"])
    assert c["table_prior"] == c["prior_exact"] == 198000


@pytest.mark.parametrize("phrase,prior", [("an increase of 1,000 from", 198), ("a decrease of 4,000 from", 203),
                                           ("unchanged from", 199)])
def test_claims_change_wordings(phrase, prior):
    t = text("dol_claims_doc_sample.txt").replace("an\nincrease of 1,000 from", phrase)
    assert dol.parse_text(t)["prior"] == prior


def test_claims_without_table_row_fails_the_tie():
    t = text("dol_claims_doc_sample.txt").replace("Initial Claims (SA)", "Initial claims SA")
    assert dol.parse_text(t)["table_prior"] is None


# --- Atlanta Fed, G.17, UMich, FOMC -------------------------------------------------------

def test_gdpnow_matches_page():
    g = gdpnow.parse_rows(gdpnow_rows())
    d, v = g["points"][-1]
    assert (d, round(v, 1), g["quarter"]) == (SCORE["gdpnow"]["period"], SCORE["gdpnow"]["latest"], "2026Q3")
    assert gdpnow.parse_page(text("gdpnow_page_text.txt")) == {"v": 3.6, "quarter": "2026Q3", "date": "2026-10-15"}


def test_g17_matches_page():
    g = g17.parse(raw("g17_current_excerpt.html"))
    assert (g["period"], g["latest"], g["prior"], g["text"]) == ("2026-09", SCORE["ip"]["latest"], 0.1, 0.7)


def test_g17_sentence_with_decrease():
    h = raw("g17_current_excerpt.html").replace(b"increased 0.7 percent in September", b"decreased 0.7 percent in September")
    assert g17.parse(h)["text"] == -0.7


def test_umich_matches_page():
    u_ = umich.compute(umich.parse_csv(raw("umich_tbmics.csv"), umich.ICS_HEADER, "ICS_ALL"),
                       umich.parse_csv(raw("umich_tbmpx1px5.csv"), umich.PX_HEADER, "PX_MD"))
    seed = PAGE["CONS"]["umich"]
    assert (u_["month"], u_["ics"], u_["px1"]) == (seed["month"], seed["ics"], seed["px1"])
    assert u_["prior"] == 51.7
    assert umich.parse_next(text("umich_home_text.txt")) == {"d": "2026-10-23", "hm": (10, 0), "id": "umich",
                                                             "ev": "Consumer sentiment, final (Oct)"}


def test_fomc_dates():
    ev = fomc.parse(raw("fomc_2026_panel.html"), [2026, 2027])
    assert [e["d"] for e in ev] == ["2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29",
                                    "2026-09-16", "2026-10-28", "2026-12-09"]


# --- common -------------------------------------------------------------------------------

def test_holidays_and_business_days():
    assert dt.date(2026, 11, 26) in common.federal_holidays(2026)       # Thanksgiving
    assert dt.date(2026, 10, 12) in common.federal_holidays(2026)       # Columbus Day
    assert common.add_business_days(dt.date(2026, 10, 19), 8) == dt.date(2026, 10, 29)
    assert common.parse_time_label("01:00 PM") == (13, 0) and common.time_label(8, 30) == "8:30 am"


def test_fetch_error_hides_url_with_key(monkeypatch):
    import requests

    def boom(*a, **k):
        raise requests.ConnectionError("https://example.invalid/?key=SECRET")
    monkeypatch.setattr(requests, "request", boom)
    with pytest.raises(common.SourceError) as e:
        common.fetch("https://example.invalid/?key=SECRET", waits=(), label="Census EITS")
    assert "SECRET" not in str(e.value)
