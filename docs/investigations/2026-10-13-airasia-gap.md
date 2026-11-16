# AirAsia X fare gap: 7–12 October 2026

## Finding

The missing fresh daily prices are primarily an **upstream quote availability gap**, not evidence that the airline disappeared from the scan. Google returned D7 flight schedules with empty price fields. The existing cache correctly displays qualifying old prices without writing them as new daily observations. The change makes incomplete results observable; it does not restore prices Google does not supply.

Google/AirAsia's internal reason for withholding quotes is not established. Do not attribute it to a promotion, sold-out flights, or a permanent airline/API change.

## Evidence

Repository baseline: `17ec9ed` (latest retrieved main, already contains the 13 October local-date scan). Investigation window remains 7–12 October. Counts below are from each local day's last saved `docs/data.json` revision. They count airline/date/direction queries, not distinct travel days or flight totals.

| Local date | Data commit | D7 queries | Queries with flights | Queries with prices | Recorded errors |
| --- | --- | ---: | ---: | ---: | ---: |
| 7 Oct | 67979dcb | 68 | 68 | 68 | 0 |
| 8 Oct | 24f972af | 68 | 67 | 0 | 0 |
| 9 Oct | 4a424159 | 68 | 68 | 0 | 0 |
| 10 Oct | 76de983a | 68 | 68 | 0 | 0 |
| 11 Oct | 1902c8cd | 68 | 62 | 0 | 0 |
| 12 Oct | dfe2144f | 68 | 66 | 0 | 0 |

- `docs/history.json`: last fresh AirAsia X daily minimum is RM1,941 on 7 October.
- 8–12 October page data retains AirAsia X with `old=2026-10-07` and the existing missing-price warning. Thus “no new history entry” does not mean “no airline displayed.”
- [8 October diagnostic run](https://github.com/4hYang3074/Japan-Flight-Monitor/actions/runs/37756224016), job `113241338500`: three outbound dates (25 March, 5 April, 15 April 2027), MYR/USD, gl=MY, one-way/round-trip and unrestricted-airline variants all returned D7 with `price=None`. Printed raw price container was `[[], <booking token>]`. The no-currency variant on 25 March returned `errorHasStatus`. The diagnostic inspected **both** result blocks, which rules out a price hidden solely in the other block for those samples.
- [12 October daily run](https://github.com/4hYang3074/Japan-Flight-Monitor/actions/runs/38191486221), job `114624988644`: `done in 2200s, errors=0`; build produced KUL–KIX 341 rows and SIN–KIX 1,993 rows, committing `dfe2144`. It had no diagnostic artifacts. This log does not distinguish each zero-result response.
- Watch records `2026-10-07T17:28:07Z` and `2026-10-09T19:33:58Z` (8 and 10 October in Malaysia) contain `combos=0`, `errors=0`, and no query coverage. Their precise reasons cannot be reconstructed. The webpage previously removed them with `filter(r => r.compared)`.
- Existing commits `9899327`, `aa0bf89`, and `e4c5dae` already introduced schedule preservation, cached prices, and official booking links; those fixes are reused.
- A current local probe for KUL→KIX, D7, 5 April 2027 returned a Google “Before you continue” consent page, without `ds:1`. This is a current source/interstitial failure, **not historical evidence** explaining 8–12 October.

## Failure chain and changes

1. Empty price fields correctly become `price=None`; `combine()` cannot produce fresh round trips without prices (or an actual round-trip quote). No fresh daily statistic should be invented.
2. `errorHasStatus=true` previously returned `[]` with no error. It now raises `SourceError`, is retried, and is recorded as `source_error`. Transport failures and recognizable consent/access pages are also source errors. Missing/malformed data is a parse error.
3. Both known result blocks are parsed and duplicate flights merged, preferring available/lower quotes. This closes a separate latent parsing gap; it is not claimed as the cause of this incident.
4. Valid empty result lists are `no_flights`: the source listed no flights for that query. This is **not proof the airline has no operating flights**. Legacy ambiguous empty results remain unknown, rather than being reinterpreted as confirmed absence.
5. A shared quality summary reuses query coverage plus fresh same-airline core-night combinations. It distinguishes unpriced, source/parse failure, missing scan records, valid empty results, and flights that cannot form a priced round trip. Actual round-trip quotes satisfy availability even when one-way prices are absent.
6. The builder adds per-airline status and an incomplete-comparison banner while preserving normal airlines. The existing seven-day exact-flight cache still applies only to schedules actually returned. No synthetic schedules are reconstructed when an airline is entirely absent. Stale route fallback does not advertise prior deals as current deals.
7. Watch records now retain quality summaries. All scans remain visible; only comparable scans enter average price-change statistics. Old records without evidence display “reason not saved.”
8. Daily, slot and promo-triggered scans publish usable data first, then fail a completeness gate for missing fresh airline combinations or query errors. A summary and 14-day artifacts retain raw structured scan data and quality results. Existing downstream notification steps are skipped when the gate fails; no new notification service or credentials are added.

## Changed files

- `scraper/scan.py`: parse/source classifications, both result blocks, deduplication and query scope validation.
- `scraper/quality.py`: shared status summary and workflow gate.
- `scraper/build.py`, `scraper/watch.py`: propagate current quality, reuse cache and combination logic.
- `docs/index.html`: visible missing-data states and zero-comparison watch rows.
- `.github/workflows/{daily-scan,airasia-watch,promo-watch}.yml`: post-publication gate and diagnostic artifacts.
- `.github/workflows/tests.yml`, `tests/test_quality.py`, `tests/test_web.cjs`: offline regression checks.

## Validation

- `python -m unittest discover -s tests -v`: 14 tests passed.
- `node --test tests/test_web.cjs`: 1 test passed (zero-combination rendering, legacy records, unbiased averages).
- Inline webpage JavaScript syntax, all workflow YAML files, and `git diff --check`: passed.
- Pipeline tests run the real builder/watch/gate against temporary fixtures, covering D7 unpriced + healthy MH, cache carry-forward and expiry, no synthetic missing flights, restored fresh D7 quotes and history exclusion for old prices.
- Production JSON, API keys and `config.yaml` are not modified. Fixtures are synthetic, modeled on the logged price-container shape; they are not claimed as full archived Google responses.

## Remaining limits / deployment

Full historical raw HTML was not retained. The diagnostics substantiate upstream missing prices for sampled dates; they cannot explain every empty query on every day. No successful live full-range rescan has been completed in this environment because the current Google response is a consent page.

Deploying the patch will expose missing quotes, not create live quotes. It will likely make scans red while D7 (or any other configured carrier) lacks fresh comparable prices, even though healthy airline data continues to publish. Restoring prices ultimately requires Google to provide quotes again or a separately evaluated accessible fare source.
