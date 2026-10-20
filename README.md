# Yield Regime Monitor

**For education only. Not financial advice.** Figures come from public sources and are checked, but may contain errors. Check the original source before relying on it.

A one-page dashboard of what is driving U.S. Treasury yields: real yields versus breakevens, the curve, high-yield spreads, auction demand, the equity yield gap, and the rate-sensitive parts of the economy. The page is `index.html`, served by GitHub Pages.

## Sources

| Data | Primary | Second read / fallback |
|---|---|---|
| Treasury par yields | Treasury daily par yield curve CSV | Treasury XML feed (must agree to 0.001) |
| 10Y real yield | Treasury daily real yield curve CSV | Treasury XML, then the Fed's H.15 zip (`RIFLGFCY10_XII_N.B`) |
| Fed funds | New York Fed EFFR API | none |
| HY OAS | FRED `BAMLH0A0HYM2` (ICE BofA), one call, 15 s | fredgraph.csv, one call; never blocks the run |
| 30Y mortgage | Freddie Mac PMMS CSV | PMMS workbook |
| WTI | EIA API v2 (`RWTC`) | EIA spot price table |
| Auctions | TreasuryDirect TA_WS JSON | none |
| Forward P/E | FactSet Earnings Insight PDF | none |
| Payrolls, unemployment, earnings, CPI, PPI | BLS API v2 | none |
| GDP, investment, final sales, saving rate, real PCE, core PCE | BEA API | none |
| Starts, permits, retail control group | Census EITS API | none |
| Initial claims | Department of Labor `data.pdf` | FRED `ICSA` (no tie check) |
| GDPNow | Atlanta Fed workbook | Atlanta Fed page |
| Industrial production | Fed G.17 summary table | the release's opening sentence |
| Consumer sentiment | University of Michigan CSV files (final readings) | none |
| Card delinquency | New York Fed Household Debt and Credit workbook | none |
| Calendar | Census, BEA, BLS, TreasuryDirect, Fed (FOMC), UMich; claims and mortgage rates every Thursday | — |

`tests/fixtures/README.md` lists the layout of every file, which ones were verified by download, and which parsers still follow documentation only.

## How the update works

Every number on the page comes from seven one-line JavaScript objects between the `// ===== DATA BLOCKS` markers in `index.html`. The script writes five of them (`DATA`, `SCORE`, `CONS`, `CAL`, `LOG`). `NOTES` is written by hand and `HIST` is static; neither is ever touched.

`.github/workflows/update.yml` runs `scripts/update.py` on weekdays at 23:20, 02:20 and 12:20 UTC. The script is plain Python with no AI. It fetches each source (browser User-Agent, 60 s timeout, retries after 5, 15 and 45 s), maps fields by exact label, rewrites the five lines and runs the checks:

1. Treasury CSV and XML agree on the newest date.
2. Ties: breakeven = par − real; auction bidder shares sum to 100 within 0.2; the claims prior matches the release table; payroll changes equal the level differences; control-group components are each smaller than the total.
3. Seed check (first run only, per source): a value re-read for the same data date must equal the value already on the page. Any difference stops the run and is reported. A source that is not reached keeps its "Seeded" log note, so its check runs on a later run.
4. Moves over 5% (25bp for yields and spreads) are listed in the run summary and the commit message; a level series moving more than 5% also opens an issue.
5. Only the five data lines changed; `NOTES` and `HIST` are byte-identical.
6. Array lengths and date order.
7. The page script parses (`node --check`) and Node reads the blocks back unchanged.

Every value keeps its own data date. A source with nothing new keeps its old value and date (the page flags anything more than 10 days older than `DATA.updated`). A source that shows a newer date but whose value cannot be read or fails a tie gets `null` under the new date, a log row saying so, and an issue. HY OAS keeps only dates that are in the Treasury par yield file, so bond-market holidays are skipped and the five-session change spans trading days. It also writes `data/summary.json` for other pages.

The workflow commits only when a data value changed and every check passed, using the repository variables `COMMIT_NAME` and `COMMIT_EMAIL`. If a check fails, nothing is committed, the run fails, and an issue titled "Data update needs attention" is opened or commented on.

### Manual run

Actions → Update data → Run workflow. `dry_run` defaults to true: the run fetches, parses and checks, then writes to the job summary every value that would change (old → new, data date, source), the moves, the sources not reached and any label or code a parser could not find. It commits nothing. `skip_seed_checks` accepts source values that differ from the seeded page values; use it only after reviewing a dry run.

Locally:

```
pip install -r requirements.txt
python -m pytest tests
python scripts/update.py --dry-run
```

### Repository settings

- Secrets: `BLS_API_KEY`, `BEA_API_KEY`, `CENSUS_API_KEY`, `FRED_API_KEY`, `EIA_API_KEY`.
- Variables: `COMMIT_NAME`, `COMMIT_EMAIL` (the GitHub no-reply address) for the commit author.
- Secret `CONTACT_EMAIL`: the contact address sent, at run time only, in the User-Agent for BLS and the Department of Labor, which block anonymous automated clients. It is a secret so that GitHub masks it in the job log.
- Pages: deploy from the default branch, root folder.

## Not automated

The `NOTES` block, the explanatory text on the page (reviewed by hand monthly), and the items dropped for lack of a free official source: FedWatch odds, the dollar index, gold, the daily S&P 500 close, PMIs, Conference Board confidence, existing-home sales and the investment-grade yield.
