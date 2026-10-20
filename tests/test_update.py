"""Tests for scripts/update.py: page I/O, the publishing checks, and full offline runs.
Run: python -m pytest tests"""
import copy
import datetime as dt
import json
import re
import subprocess

import pytest

from conftest import ROOT, TODAY

import update as u
from sources import common

PAGE = (ROOT / "index.html").read_text(encoding="utf-8")


def blocks():
    return copy.deepcopy(u.load_page(PAGE))


def run(tmp_path, **kw):
    page = tmp_path / "index.html"
    page.write_text(PAGE, encoding="utf-8")
    args = dict(dry_run=False, commit_msg=tmp_path / "msg.txt", issue_body=tmp_path / "issue.md", today=TODAY,
                keys={})
    args.update(kw)
    rc = u.run_update(page, tmp_path / "summary.json", **args)
    return rc, page.read_text(encoding="utf-8")


# --- page ---------------------------------------------------------------------

def test_blocks_round_trip_byte_identical():
    assert u.render_page(PAGE, blocks()) == PAGE


def test_render_changes_only_the_five_lines():
    b = blocks()
    for name in u.WRITTEN:
        b[name] = b[name]
    b["DATA"]["wti"]["v"] = 99.0
    b["CAL"] = b["CAL"][:3]
    new = u.render_page(PAGE, b)
    idx = u.block_lines(PAGE.splitlines())
    changed = [i for i, (x, y) in enumerate(zip(PAGE.splitlines(), new.splitlines())) if x != y]
    assert changed == sorted([idx["DATA"], idx["CAL"]])
    u.verify_page(PAGE, new, b)


def test_render_never_writes_notes_or_hist():
    b = blocks()
    b["NOTES"] = []
    b["HIST"] = {}
    assert u.render_page(PAGE, b) == PAGE


def test_check5_rejects_change_outside_blocks():
    bad = PAGE.replace("For education only. Not financial advice.", "For education only.")
    with pytest.raises(u.CheckFailed, match="check 5"):
        u.verify_page(PAGE, bad, blocks())


@pytest.mark.parametrize("name", ["NOTES", "HIST"])
def test_check5_rejects_notes_or_hist_change(name):
    lines = PAGE.splitlines(keepends=True)
    i = u.block_lines(lines)[name]
    lines[i] = lines[i].replace("2008", "2009", 1) if name == "HIST" else lines[i].replace("2026", "2025", 1)
    with pytest.raises(u.CheckFailed, match="check 5"):
        u.verify_page(PAGE, "".join(lines), blocks())


def test_check7_rejects_broken_script():
    bad = PAGE.replace("const $ = id =>", "const $ = id =>>", 1)
    with pytest.raises(u.CheckFailed, match="check"):
        u.verify_page(PAGE, bad, blocks())


def test_check7_node_reads_blocks_back():
    u.node_check(PAGE, blocks())
    b = blocks()
    b["DATA"]["wti"]["v"] = 1.0
    with pytest.raises(u.CheckFailed, match="check 7"):
        u.node_check(PAGE, b)


def test_education_note_directly_under_header():
    assert re.search(r'</header>\s*<p class="disclaimer"><b>For education only\. Not financial advice\.</b>', PAGE)


def test_check6_shapes():
    u.check_shapes(blocks())
    b = blocks()
    b["DATA"]["credit"]["daily"] = b["DATA"]["credit"]["daily"][1:]
    with pytest.raises(u.CheckFailed, match="credit.daily"):
        u.check_shapes(b)
    b = blocks()
    b["DATA"]["split"]["dates"][3] = b["DATA"]["split"]["dates"][2]
    with pytest.raises(u.CheckFailed, match="ascending"):
        u.check_shapes(b)


# --- summary.json: the page's tile rules, ported -------------------------------

def page_tiles_in_node():
    """Run the page's own derived values and status functions in Node on the seed blocks."""
    script = u.extract_script(PAGE)
    lines = PAGE.splitlines(keepends=True)
    idx = u.block_lines(lines)
    data = "".join(lines[idx[n]] for n in u.BLOCK_NAMES)
    helpers = "\n".join(l for l in script.splitlines() if l.startswith(("const num =", "const last =")))
    derived = script[script.index("// ---------- Derived values"):script.index("// ---------- Header pills")]
    js = data + helpers + "\n" + derived + \
        "\nprocess.stdout.write(JSON.stringify([stDriver(), stCurve(), stHY(), stAuction(), stGap(gap)]));\n"
    return json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)


def test_summary_tiles_match_page_rules():
    t, _ = u.tiles(blocks()["DATA"])
    assert [[x["status"], x["label"]] for x in t] == page_tiles_in_node()


def test_summary_from_current_page():
    s = u.build_summary(blocks()["DATA"])
    assert (s["y10"], s["curve"], s["hy"], s["gap"], s["mort"]) == (5.09, 0.35, 3.31, 0.17, 7.4)
    assert [x["label"] for x in s["tiles"]] == ["Easing", "Not inverted", "Watch", "Solid", "No cushion"]


# --- calendar --------------------------------------------------------------------

def test_thanksgiving_moves_claims_and_mortgage_to_wednesday():
    ev = u.fixed_thursdays(dt.date(2026, 11, 23), dt.date(2026, 11, 27))
    assert {(e["d"], e["id"]) for e in ev} == {("2026-11-25", "claims"), ("2026-11-25", "mort")}


def test_calendar_from_fixtures_covers_the_seed(offline):
    r = u.Run(today=TODAY, old=blocks(), keys={})
    cal = u.build_calendar(r, offline["census_cal"]())
    assert cal[0]["d"] == "2026-10-20" and cal[-1]["d"] == "2026-10-29"
    for c in blocks()["CAL"]:
        assert c in cal
    assert {"d": "2026-10-29", "t": "1:00 pm", "ev": "7-year note auction", "src": "Treasury", "id": "auction"} in cal


def test_calendar_keeps_old_rows_when_a_source_fails(offline):
    def fail():
        raise common.SourceError("down")
    offline["bea_dates"] = fail
    r = u.Run(today=TODAY, old=blocks(), keys={})
    cal = u.build_calendar(r, offline["census_cal"]())
    assert [c for c in cal if c["src"] == "BEA"] == [c for c in blocks()["CAL"] if c["src"] == "BEA"]


# --- full offline runs --------------------------------------------------------------

def test_first_run_passes_seed_checks_and_updates(tmp_path, offline):
    rc, new = run(tmp_path)
    assert rc == 0
    b = u.load_page(new)
    D = b["DATA"]
    assert D["updated"] == "2026-10-19"
    assert D["par"]["dates"] == ["2026-10-16", "2026-10-19"] and D["par"]["y10"] == [5.09, 5.15]
    assert D["split"]["dates"][-1] == "2026-10-19" and len(D["split"]["dates"]) == 12
    assert D["auction"]["dealer"] == 6.8
    assert D["fed"]["date"] == "2026-10-16"
    S = {r["id"]: r for r in b["SCORE"]}
    assert S["ppi"]["prior"] == 5.9 and S["ip"]["prior"] == 0.1 and S["fspdp"]["prior"] == 1.9
    assert [r["id"] for r in b["SCORE"]] == [r["id"] for r in blocks()["SCORE"]]
    for r, o in zip(b["SCORE"], blocks()["SCORE"]):
        assert all(r[k] == o[k] for k in ("id", "grp", "label", "unit", "dp", "src"))
    assert len(b["CONS"]["save"]["months"]) == 4
    assert b["NOTES"] == blocks()["NOTES"] and b["HIST"] == blocks()["HIST"]
    assert not any(u.SEED_MARK in r[5] for r in b["LOG"])
    assert all(r[4] == "Oct 19" for r in b["LOG"])
    log = {r[0]: r for r in b["LOG"]}
    assert log["10Y Treasury (par)"][5] == "New Oct 19. Primary, two reads agree."
    assert log["30Y mortgage"][5] == "No new release."
    msg = (tmp_path / "msg.txt").read_text()
    assert msg.startswith("Data: ") and "Treasury par Oct 19" in msg.splitlines()[0]
    s = json.loads((tmp_path / "summary.json").read_text())
    assert s["y10"] == 5.15 and len(s["tiles"]) == 5
    assert not (tmp_path / "issue.md").exists()


def test_second_run_with_nothing_new_commits_nothing(tmp_path, offline):
    run(tmp_path)
    page = tmp_path / "index.html"
    before = page.read_text(encoding="utf-8")
    (tmp_path / "msg.txt").unlink()
    rc = u.run_update(page, tmp_path / "summary.json", False, tmp_path / "msg.txt", tmp_path / "issue.md",
                      today=TODAY, keys={})
    assert rc == 0 and page.read_text(encoding="utf-8") == before and not (tmp_path / "msg.txt").exists()


def test_dry_run_writes_nothing(tmp_path, offline):
    rc, new = run(tmp_path, dry_run=True)
    assert rc == 0 and new == PAGE and not (tmp_path / "summary.json").exists()


def test_seed_mismatch_stops_and_reports(tmp_path, offline):
    good = offline["bls"]

    def revised():
        b = good()
        b["cpi"]["latest"] = 3.8
        return b
    offline["bls"] = revised
    rc, new = run(tmp_path)
    assert rc == 1 and new == PAGE
    body = (tmp_path / "issue.md").read_text()
    assert "check 3" in body and "SCORE.cpi.latest for 2026-09: page 3.7 vs source 3.8" in body


def test_seed_mismatch_can_be_accepted(tmp_path, offline):
    good = offline["bls"]
    offline["bls"] = lambda: {**good(), "cpi": {**good()["cpi"], "latest": 3.8}}
    rc, new = run(tmp_path, skip_seed=True)
    assert rc == 0 and next(r for r in u.load_page(new)["SCORE"] if r["id"] == "cpi")["latest"] == 3.8


def test_unreached_source_keeps_its_seed_marker(tmp_path, offline):
    def down(*a):
        raise common.SourceError("HTTP 403")
    offline["bls"] = down
    rc, new = run(tmp_path)
    assert rc == 0
    log = {r[0]: r for r in u.load_page(new)["LOG"]}
    for lab in u.LOG_ROWS["bls"]:
        assert log[lab][5].startswith("Not updated: BLS not reached.") and u.SEED_MARK in log[lab][5]
        assert log[lab][1:4] == next(r for r in blocks()["LOG"] if r[0] == lab)[1:4]


def test_two_reads_disagree_fails(tmp_path, offline):
    good = offline["par_xml"]

    def off():
        x = good()
        x["2026-10-19"]["y10"] = 5.16
        return x
    offline["par_xml"] = off
    rc, new = run(tmp_path)
    assert rc == 1 and new == PAGE and "check 1" in (tmp_path / "issue.md").read_text()


def test_auction_tie_failure_nulls_shares_and_opens_issue(tmp_path, offline):
    good = offline["auction"]
    offline["auction"] = lambda: {**good(), "date": "2026-10-21", "term": "20-Year Bond", "tie": 97.0}
    rc, new = run(tmp_path)
    assert rc == 0
    a = u.load_page(new)["DATA"]["auction"]
    assert a["date"] == "2026-10-21" and a["dealer"] is None and a["indirect"] is None
    assert "tie check" in (tmp_path / "issue.md").read_text()
    log = {r[0]: r for r in u.load_page(new)["LOG"]}
    assert log["Latest coupon auction"][5].startswith("Null: source showed Oct 21")


def test_claims_tie_failure_nulls_row(tmp_path, offline):
    good = offline["dol"]
    offline["dol"] = lambda: {**good(), "week": "2026-10-17", "table_prior": 197000}
    rc, new = run(tmp_path)
    s = next(r for r in u.load_page(new)["SCORE"] if r["id"] == "claims")
    assert rc == 0 and s["period"] == "2026-10-17" and s["latest"] is None and s["prior"] is None


def test_fred_unreached_is_not_a_failure(tmp_path, offline):
    def down(*a):
        raise common.SourceError("timed out")
    offline["fred"] = down
    rc, new = run(tmp_path)
    b = u.load_page(new)
    assert rc == 0 and b["DATA"]["credit"] == blocks()["DATA"]["credit"]
    assert not (tmp_path / "issue.md").exists()  # the latest point is only 1 business day old


def test_level_move_over_5_percent_opens_issue(tmp_path, offline):
    offline["factset"] = lambda: {"report": "2026-10-23", "pe": 18.0, "price": 7000.0}
    rc, new = run(tmp_path)
    assert rc == 0
    assert "DATA.val.price: 7744.06 → 7000.0" in (tmp_path / "issue.md").read_text()
    assert "DATA.val.price" in (tmp_path / "msg.txt").read_text()


def test_rate_move_uses_basis_points():
    old = blocks()
    new = copy.deepcopy(old)
    new["DATA"]["par"]["dates"] = ["2026-10-16", "2026-10-19"]
    new["DATA"]["par"]["y10"] = [5.09, 5.40]
    m, levels = u.moves(old, new)
    assert any(x.startswith("DATA.par.y10: 5.09 → 5.4 (+31bp)") for x in m) and not levels


# --- repository hygiene -----------------------------------------------------------

def test_no_personal_information_in_repo():
    email = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
    for p in ROOT.rglob("*"):
        if ".git" in p.parts or not p.is_file() or "__pycache__" in p.parts or ".pytest_cache" in p.parts:
            continue
        t = p.read_text(encoding="utf-8", errors="ignore")
        assert not email.search(t), p
        assert not re.search(r"claude\.ai/", t), p


# --- Census duplicates, the "added" display, contact User-Agent, claims fallback, HY trading days ----

def test_census_duplicate_in_unused_series_is_ignored(offline):
    from conftest import text as fx
    from sources import census
    rows = json.loads(fx("census_eits_doc_sample_resconst.json"))
    h = rows[0]
    rows += [["600", "SINGLE", "0", "no", "ACOMPLETIONS", "yes", "2025-06"],
             ["612", "SINGLE", "1", "no", "ACOMPLETIONS", "yes", "2025-06"]]
    data = census.parse_eits(json.dumps(rows), "resconst")
    assert census.compute_housing(data)["starts"]["latest"] == 1.275
    assert "ACOMPLETIONS/SINGLE/yes" in census.combinations(data)
    # the same duplicate in a series the page uses stops that series and names the time slots
    rows += [["1290", "TOTAL", "2", "no", "ASTARTS", "yes", "2026-08"]]
    with pytest.raises(common.SourceError, match=r"ASTARTS/TOTAL/yes has 2 values for 2026-08: "
                                                 r"time_slot_id 0 = 1275.0, time_slot_id 2 = 1290.0"):
        census.compute_housing(census.parse_eits(json.dumps([h] + rows[1:]), "resconst"))


def test_census_identical_values_in_two_slots_are_one_value():
    from conftest import text as fx
    from sources import census
    rows = json.loads(fx("census_eits_doc_sample_resconst.json"))
    rows += [["1275", "TOTAL", "1", "no", "ASTARTS", "yes", "2026-08"]]
    assert census.compute_housing(census.parse_eits(json.dumps(rows), "resconst"))["starts"]["latest"] == 1.275


def test_run_summary_lists_census_codes_and_added_values(tmp_path, offline, capsys):
    rc, _ = run(tmp_path, dry_run=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "### Census EITS code combinations" in out and "- resconst (4): APERMITS/TOTAL/yes, ASTARTS/TOTAL/yes, PERMITS/TOTAL/no, STARTS/TOTAL/no" in out
    # July and August are added inside the control-group window: no pairing with September's value
    assert "| `CONS.ctrl.v` | added: 0.1 | 2026-07 | Census |" in out
    # the newest par date still shows the previous newest value
    assert "| `DATA.par.y10` | 5.09 (2026-10-16) → 5.15 | 2026-10-19 | Treasury |" in out


def test_contact_user_agent_for_bls_and_dol(monkeypatch):
    import requests
    from sources import bls, dol
    seen = []

    def fake(method, url, headers=None, timeout=None, **kw):
        seen.append((url, headers["User-Agent"]))
        raise requests.ConnectionError("refused")
    monkeypatch.setattr(requests, "request", fake)
    monkeypatch.setattr(common.time, "sleep", lambda s: None)  # skip the 5/15/45 s retry waits
    token = "contact-token-for-test"
    for call in (lambda: bls.get("", TODAY, token), lambda: bls.get_calendar(token), lambda: dol.get(token)):
        with pytest.raises(common.SourceError) as e:
            call()
        assert token not in str(e.value)
    assert [u for u, _ in seen] == [bls.URL_API] * 4 + [bls.URL_ICS] * 4 + [dol.URL] * 4
    assert all(ua == f"yield-regime-monitor/1.0 (automated data refresh; contact: {token})" for _, ua in seen)
    seen.clear()
    with pytest.raises(common.SourceError):
        dol.get(None)
    assert seen[0][1] == common.USER_AGENT  # no contact set: the browser User-Agent


def test_contact_is_never_printed(tmp_path, offline, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    token = "contact-token-for-test"
    rc, _ = run(tmp_path, keys={"CONTACT_EMAIL": token})
    out = capsys.readouterr().out
    assert rc == 0 and token not in out and "Contact User-Agent for BLS and DOL: set." in out
    for p in tmp_path.iterdir():
        assert token not in p.read_text(encoding="utf-8")


def test_claims_fall_back_to_fred_icsa(tmp_path, offline):
    from sources import fred

    def blocked(*a):
        raise common.SourceError("https://www.dol.gov/ui/data.pdf: HTTP 403")
    offline["dol"] = blocked
    good = offline["fred_icsa"]
    offline["fred_icsa"] = lambda: {**good(), "2026-10-17": 207000.0}
    rc, new = run(tmp_path)
    b = u.load_page(new)
    s = next(r for r in b["SCORE"] if r["id"] == "claims")
    assert rc == 0 and (s["period"], s["latest"], s["pperiod"], s["prior"]) == ("2026-10-17", 207, "2026-10-10", 199)
    row = next(r for r in b["LOG"] if r[0] == "Initial claims")
    assert row[1:4] == ["207k", "Week to Oct 17", "FRED ICSA (fallback)"]
    assert row[5] == "New Oct 17. Fallback: FRED ICSA."
    assert fred.icsa({"2026-10-03": 198000.0, "2026-10-10": 199000.0})["latest"] == 199


def test_claims_fallback_same_week_rereads_seed(tmp_path, offline):
    def blocked(*a):
        raise common.SourceError("HTTP 403")
    offline["dol"] = blocked
    rc, new = run(tmp_path)  # ICSA shows the seeded week: values must equal the seed (199 / 198)
    row = next(r for r in u.load_page(new)["LOG"] if r[0] == "Initial claims")
    assert rc == 0 and row[5] == "No new release. Fallback: FRED ICSA."


def test_hy_oas_skips_bond_market_holiday_oct_12(tmp_path, offline):
    from sources import fred
    good = offline["fred"]

    def with_columbus_day():
        obs, src = good()
        return {**obs, "2026-10-12": 3.11}, src   # FRED publishes a value on Columbus Day
    offline["fred"] = with_columbus_day
    rc, new = run(tmp_path)
    daily = u.load_page(new)["DATA"]["credit"]["daily"]
    assert rc == 0 and [d for d, _ in daily] == ["2026-10-08", "2026-10-09", "2026-10-13", "2026-10-14",
                                                 "2026-10-15", "2026-10-16"]
    assert daily == blocks()["DATA"]["credit"]["daily"]   # the five-session change spans trading days
    trading = {"2026-10-09", "2026-10-13"}
    kept = fred.trading_days_only({"2026-10-09": 1.0, "2026-10-12": 1.0, "2026-10-13": 1.0, "2026-10-14": 1.0}, trading)
    assert sorted(kept) == ["2026-10-09", "2026-10-13", "2026-10-14"]  # a business day after the par file is kept


def test_failed_tie_on_a_week_the_page_holds_keeps_the_page_value(tmp_path, offline):
    """Runs #2 and #3: the claims table was not found on a re-read of the seeded week. The page value
    stays (never null under an old date), the seed note stays, and the issue says why."""
    good = offline["dol"]
    offline["dol"] = lambda: {**good(), "table_prior": None}
    rc, new = run(tmp_path)
    assert rc == 0
    b = u.load_page(new)
    s = next(r for r in b["SCORE"] if r["id"] == "claims")
    assert (s["latest"], s["prior"]) == (199, 198)
    row = next(r for r in b["LOG"] if r[0] == "Initial claims")
    assert row[5].startswith("Kept page value: re-read of Oct 10 failed (the row failed the tie check")
    assert u.SEED_MARK in row[5]
    assert "the page value was kept" in (tmp_path / "issue.md").read_text()


@pytest.mark.parametrize("src,patch,label,kept", [
    ("auction", lambda g: (lambda: {**g(), "tie": 97.0}), "Latest coupon auction", lambda b: b["DATA"]["auction"]),
    ("g17", lambda g: (lambda: {**g(), "text": 0.5}), "Industrial production",
     lambda b: next(r for r in b["SCORE"] if r["id"] == "ip")["latest"]),
    ("gdpnow_page", lambda g: (lambda: {**g(), "v": 3.9}), "GDPNow",
     lambda b: next(r for r in b["SCORE"] if r["id"] == "gdpnow")["latest"]),
    ("factset", lambda g: (lambda: {**g(), "pe": None}), "Forward P/E (FactSet)", lambda b: b["DATA"]["val"]),
])
def test_failed_reread_of_same_date_keeps_page_value(tmp_path, offline, src, patch, label, kept):
    offline[src] = patch(offline[src])
    rc, new = run(tmp_path)
    b = u.load_page(new)
    assert rc == 0 and kept(b) == kept(blocks())
    row = next(r for r in b["LOG"] if r[0] == label)
    assert row[5].startswith("Kept page value: re-read of")
