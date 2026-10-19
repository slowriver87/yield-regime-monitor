"""TreasuryDirect auction results and upcoming auctions (TA_WS JSON; field names verified Oct 19, 2026).

The results carry no 'auctionDate': the auction day is competitiveAuctionCloseDate. Bidder shares
come from the *CompetitiveParAwarded fields; their sum must equal
subtotalCompetitiveParAwardedInsideOfOfferingAmount.
"""
from __future__ import annotations

import json

from .common import SourceError, fetch, num, parse_time_label, time_label

URL_AUCTIONED = "https://www.treasurydirect.gov/TA_WS/securities/auctioned?format=json&type={t}"
URL_UPCOMING = "https://www.treasurydirect.gov/TA_WS/securities/upcoming?format=json"
NOMINAL_TERMS = ("2-Year", "5-Year", "7-Year", "10-Year", "20-Year", "30-Year")
COUPON_TYPES = ("Note", "Bond")
DEFAULT_CLOSE = "01:00 PM"  # coupon auctions close at 1:00 pm ET; used while the time is still blank
SHARE_TIE = 0.2


def _records(text: str, what: str) -> list[dict]:
    try:
        recs = json.loads(text)
    except ValueError as e:
        raise SourceError(f"TreasuryDirect {what}: {e}")
    if not isinstance(recs, list):
        raise SourceError(f"TreasuryDirect {what}: expected a list")
    return recs


def is_nominal_coupon(r: dict) -> bool:
    return (r.get("securityType") in COUPON_TYPES and r.get("inflationProtected") == "No"
            and r.get("floatingRate") == "No" and r.get("originalSecurityTerm") in NOMINAL_TERMS)


def latest_coupon(records: list[dict]) -> dict:
    """The most recent nominal 2/5/7/10/20/30-year auction with results -> DATA.auction fields."""
    done = [r for r in records if is_nominal_coupon(r) and num(r.get("bidToCoverRatio")) is not None]
    if not done:
        raise SourceError("TreasuryDirect: no nominal coupon auction results")
    r = max(done, key=lambda x: x["competitiveAuctionCloseDate"])
    pd, di, ind = (num(r.get(k)) for k in ("primaryDealersCompetitiveParAwarded",
                                           "directBiddersCompetitiveParAwarded",
                                           "indirectBiddersCompetitiveParAwarded"))
    comp = num(r.get("subtotalCompetitiveParAwardedInsideOfOfferingAmount"))
    out = {"date": r["competitiveAuctionCloseDate"][:10], "term": f"{r['originalSecurityTerm']} {r['securityType']}",
           "high": num(r.get("highYield")), "btc": num(r.get("bidToCoverRatio")), "dealer": None, "indirect": None,
           "tie": None}
    if None not in (pd, di, ind) and pd + di + ind > 0:
        tot = pd + di + ind
        out["dealer"], out["indirect"] = round(pd / tot * 100, 1), round(ind / tot * 100, 1)
        # Tie: primary + direct + indirect, as % of the competitive amount accepted, is 100 within 0.2
        out["tie"] = tot / comp * 100 if comp else None
    return out


def upcoming(records: list[dict]) -> list[dict]:
    """Coupon auctions (TIPS included, FRNs and bills not) -> [{d, t, ev}]."""
    out = []
    for r in records:
        if r.get("securityType") not in COUPON_TYPES or r.get("floatingRate") != "No":
            continue
        term = r.get("originalSecurityTerm", "")
        if not term.endswith("-Year"):
            continue
        kind = "TIPS" if r.get("inflationProtected") == "Yes" else r["securityType"].lower()
        h, m = parse_time_label(r.get("competitiveAuctionCloseTime") or DEFAULT_CLOSE)
        out.append({"d": r["competitiveAuctionCloseDate"][:10], "t": time_label(h, m),
                    "ev": f"{term.lower()} {kind} auction"})
    return out


def get_latest() -> dict:
    recs = []
    for t in COUPON_TYPES:
        recs += _records(fetch(URL_AUCTIONED.format(t=t)).decode("utf-8"), f"auctioned {t}")
    return latest_coupon(recs)


def get_upcoming() -> list[dict]:
    return upcoming(_records(fetch(URL_UPCOMING).decode("utf-8"), "upcoming"))
