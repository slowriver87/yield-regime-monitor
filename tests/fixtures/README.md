# Fixtures

Layouts printed on Oct 19, 2026 from a cloud build environment (not a GitHub runner), before the
parsers were written. Fields are mapped by the exact labels below. Personal names and contact
details in the source files (FactSet cover page, UMich home page, H.15 header) were removed.

## Downloaded files (live)

| Source | Fixture | Layout as printed |
|---|---|---|
| Treasury par CSV | `treasury_par_2026.csv` (first 30 rows) | Header `Date,"1 Mo","1.5 Month","2 Mo","3 Mo","4 Mo","6 Mo","1 Yr","2 Yr","3 Yr","5 Yr","7 Yr","10 Yr","20 Yr","30 Yr"`; newest first; `MM/DD/YYYY`; no row on Oct 12 (holiday). Oct 19 was posted by about 7:20 pm ET. |
| Treasury real CSV | `treasury_real_2026.csv` | Header `Date,"5 YR","7 YR","10 YR","20 YR","30 YR"`. |
| Treasury par XML | `treasury_par_202610.xml` | Atom/OData; `m:properties` with `d:NEW_DATE` (`2026-10-19T00:00:00`), `d:BC_2YEAR`, `d:BC_10YEAR`, `d:BC_30YEAR` (also `BC_1_5MONTH`, `BC_30YEARDISPLAY`). |
| Treasury real XML (was NOT VERIFIED) | `treasury_real_202610.xml` | Same feed; elements `d:TC_5YEAR`, `d:TC_7YEAR`, `d:TC_10YEAR`, `d:TC_20YEAR`, `d:TC_30YEAR`. |
| Fed H.15 zip (was NOT VERIFIED) | `h15_data_excerpt.xml` | Zip holds `H15_data.xml` (70 MB SDMX), `H15_struct.xml`, three `.xsd`. Daily series `<kf:Series ... SERIES_NAME="RIFLGFCY10_XII_N.B" FREQ="9">` with `<frb:Obs OBS_STATUS="A" OBS_VALUE="2.74" TIME_PERIOD="2026-10-16"/>`; holidays `OBS_STATUS="ND" OBS_VALUE="-9999"`. `..._N.A` is the annual series. |
| NY Fed EFFR | `nyfed_effr_last3.json` | `refRates[]`, newest first; `effectiveDate`, `percentRate`, `targetRateFrom`, `targetRateTo` are numbers. |
| Freddie Mac CSV | `pmms_history.csv` (header + last 70 weeks) | Header `date,pmms30,...`; oldest first; `M/D/YYYY`. |
| Freddie Mac workbook (was NOT VERIFIED) | `pmms_historicalweeklydata_rows.json` | One sheet `Full History`; header split over three rows ending with A=`Week`; column B = `U.S.` / `30 yr` / `FRM`; dates are datetimes; a disclaimer row at the end. 1980 values carry 3 decimals where the CSV rounds to 2. |
| EIA API v2 (was NOT VERIFIED) | `eia_api_rwtc.json` | Read with EIA's public `DEMO_KEY`. `response.data[]` of `{period, duoarea, product, process, series:"RWTC", series-description, value (string), units:"$/BBL"}`; `response.total`, `dateFormat`. Without a key: `eia_api_missing_key.json`. |
| EIA spot table | `eia_spot_daily.htm` | Date headers `th.Series5` (`10/14/26`); row stub `td.DataStub1` = `WTI - Cushing, Oklahoma` inside a nested table; values `td.DataB`, newest `td.Current2`; `Release Date: 10/15/2026`, `Next Release Date: 10/21/2026`. |
| EIA RWTCd.xls (was NOT VERIFIED) | none | Binary OLE2 `.xls` (471 KB); needs `xlrd`, so it is not used: the API and the table cover WTI. |
| TreasuryDirect auctioned (was NOT VERIFIED) | `td_auctioned_note.json`, `td_auctioned_bond.json` (newest 12 / 6) | JSON list, values are strings. **No `auctionDate` field**: the auction day is `competitiveAuctionCloseDate` (`2026-10-08T00:00:00`). Term: `originalSecurityTerm` (`30-Year`) + `securityType` (`Bond`); `securityTerm` is the remaining term of a reopening (`29-Year 10-Month`). `highYield`, `bidToCoverRatio`, `inflationProtected`, `floatingRate`. Shares: `primaryDealersCompetitiveParAwarded`, `directBiddersCompetitiveParAwarded`, `indirectBiddersCompetitiveParAwarded`, summing to `subtotalCompetitiveParAwardedInsideOfOfferingAmount`. The handoff's `primaryDealerAccepted`-style names do not exist. |
| TreasuryDirect upcoming | `td_upcoming.json` | Same fields; `competitiveAuctionCloseTime` is `01:00 PM` once announced, blank before. Includes bills and FRNs (`floatingRate: "Yes"`). |
| FactSet PDF | `factset_101626_extract.txt` | 36 pages. Cover date line `October 16, 2026`; the P/E sentence appears twice with the same value; the price sentence once. |
| GDPNow workbook (was NOT VERIFIED) | `gdpnow_currentqtrevolution.json` | 56 sheets. `CurrentQtrEvolution`: header `Date, Major Releases, GDP*` repeated per month block (A-C, D-F, G-I); first row `Initial GDPNow 26:Q3 forecast`; Oct 15 = 3.6429. |
| GDPNow page | `gdpnow_page_text.txt` | `3.6%% Third-Quarter GDPNow Estimate for 2026:Q3 Updated: October 15, 2026`. |
| Fed G.17 (was NOT VERIFIED) | `g17_current_excerpt.html` | `div.dates` `Release Date: October 16, 2026`; opening sentence `Industrial production (IP) increased 0.7 percent in September ...`; table `title="Industrial Production and Capacity Utilization: Summary"`, month row `Apr. [r] ... Sept. [p]` twice, row `Total index` = 6 levels, 6 % changes, y/y. |
| UMich CSVs (were NOT VERIFIED) | `umich_tbmics.csv`, `umich_tbmpx1px5.csv` | Plain UTF-8 CSV with CRLF (served as `application/octet-stream`). `Month,YYYY,ICS_ALL` and `Month,YYYY,PX_MD,PX5_MD`; full month names. Last row September 2026: 48.1 and 4.6. |
| UMich home page | `umich_home_text.txt` | `Next data release: Friday, October 23, 2026 for Final October data at 10am ET`. |
| NY Fed household credit (was NOT VERIFIED) | `hhd_c_report_2026q2_sheets.json` | 70 sheets. `Page 14 Data` "New Seriously Delinquent* Balances by Loan Type", columns `AUTO, CC, MORTGAGE, HELOC, STUDENT LOAN, OTHER, ALL`, rows `26:Q2` (CC 6.97; 25:Q2 6.93). `Page 3 Data` "Total Debt Balance and Its Composition", trillions, column `Credit Card` (26:Q2 1.263). An unpublished quarter redirects (302) to `/errors/404`. |
| Census calendar | `census_calendar_2026_excerpt.html` | `Indicator | Release Date | Time | Period Covered | code | period code`. |
| BEA release dates | `bea_release_dates.json` | Dict by release name; `release_dates` ISO with UTC offset. |
| FOMC calendar | `fomc_2026_panel.html` | `.panel` with heading `2026 FOMC Meetings`; rows `.fomc-meeting__month`, `.fomc-meeting__date` (`17-18*`). |

## Not reachable from the build environment: documentation-based samples

Structure follows the provider's documentation; **the numbers are invented** so that they equal the
page's seed values, which lets the tests exercise the seed check. Replace each with a real printout
from the first runner dry run (the run summary lists any label or code it could not find).

| Source | Why not verified | Sample |
|---|---|---|
| BLS API v2 | No `BLS_API_KEY` here; the keyless quota for the shared address was spent (`bls_api_threshold.json`). | `bls_api_v2_doc_sample.json` |
| BLS calendar `.ics` | bls.gov answered 403. | `bls_ics_doc_sample.ics` |
| BEA API | No `BEA_API_KEY` (`bea_api_invalid_key.json` is the live error). The T10401 line label is a guess. | `bea_api_doc_sample_T*.json` |
| Census EITS | No `CENSUS_API_KEY` (live answer: "Missing Key"). Category codes `44X72, 441, 447, 444, 722 / SM` and `STARTS, PERMITS / TOTAL` are from the EITS documentation. | `census_eits_doc_sample_*.json` |
| FRED API | No `FRED_API_KEY` (`fred_api_bad_key.json` is the live error). | `fred_api_doc_sample.json` |
| fredgraph.csv | Timed out from here (15 s and 30 s). Header assumed `observation_date,BAMLH0A0HYM2` (older files: `DATE`). | `fredgraph_doc_sample.csv` |
| DOL data.pdf | dol.gov answered 403. The headline and release lines are the handoff's verified wording; the `Initial Claims (SA)` table row is assumed. | `dol_claims_doc_sample.txt` |
