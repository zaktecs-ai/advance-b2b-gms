# Architecture

## 1. Purpose

Advance B2B GMS is a command-line Google Maps B2B lead scraper for Linux
servers and VPS machines. Its main goals are:

- collect useful business facts from Google Maps;
- enrich each business website without an unbounded crawl;
- keep duplicate and incomplete data out of the final export;
- survive temporary blocks, browser failures, and process restarts; and
- make every exported column traceable to a real producer.

The code is standalone and clean-room. It does not import or vendor code from
another scraper project.

## 2. Runtime flow

The complete run is easiest to understand as a pipeline:

```text
config.yaml
    ↓
load_config() → AppConfig
    ↓
Pipeline.run()
    ↓
MapsCollector.collect()       Playwright reads raw Maps data
    ↓
normalize_listing()            pure cleaning and schema projection
    ↓
identity deduplication
    ↓
pre-enrichment filters
    ↓
Enricher                      fetch → crawl → email/social/tech/signals
    ↓                         optional decision-maker extraction
review analysis + MX/SMTP verification
    ↓
post-enrichment filters
    ↓
quality gate
    ↓
atomic CSV append → checkpoint commit → XLSX + summary
```

Each stage has one job. The browser stage does not decide how a value should be
stored, and the pure transformation stage does not know anything about
Playwright.

## 3. Module responsibilities

### Configuration: `scraper/config.py`

`load_config()` reads YAML, resolves `${ENV_VAR}` references, and validates the
result with Pydantic before scraping starts. `job.default_country` must be an
ISO alpha-2 code and is the fallback region for national phone numbers.

### Maps browser adapter: `scraper/maps/collector.py`

`MapsCollector` owns Playwright activity only:

1. open the Maps search URL with the configured `hl` and `gl` values;
2. dismiss a consent wall when possible;
3. detect a bot challenge and leave the query retryable;
4. scroll the results feed;
5. click each result card in place and wait for its detail panel;
6. read the visible fields with layered selectors; and
7. return raw observations plus internal progress/review metadata.

The collector intentionally does **not** normalize addresses, decide the
country of a national phone number, or project arbitrary keys into the export.

### Pure Maps transformation: `scraper/maps/transform.py`

This is the boundary between untrusted browser output and the pipeline. It:

- keeps only `models.OUTPUT_COLUMNS` plus the internal `_reviews`, `_position`,
  and `_total` values used by the pipeline;
- repairs text and HTML entities through `normalize_text()`;
- formats URLs and phone numbers;
- validates numeric ranges;
- decomposes addresses conservatively; and
- supplies `N/A` only as a final missing-value marker.

No network or browser object is allowed in this module.

### Models and exports: `scraper/models.py` and `scraper/export/`

`OUTPUT_COLUMNS` is the single source of truth for CSV headers, XLSX headers,
row order, and the `BusinessRecord` contract. The schema currently has **75
columns**. Unsupported Maps surfaces are not represented, including timezone,
popular times, competitors, ownership posts, ad-spend flags, gas prices,
featured questions, and rating buckets.

`AtomicCSVWriter` flushes and fsyncs every row. If an existing CSV header does
not match the active schema, it fails closed instead of mixing incompatible
exports.

### Website enrichment: `scraper/websites/enricher.py`

`Enricher` fetches the homepage with `httpx`, visits a bounded set of relevant
internal pages, and aggregates their content. It then calls focused pure or
mostly-pure helpers for:

- email extraction and cleaning;
- social profile classification;
- Wappalyzer/regex technology detection;
- GA4, GTM, Meta Pixel, advertising, booking, chat, and business signals; and
- optional decision-maker extraction from the fetched text.

The result is the `Enrichment` dataclass. The pipeline maps that dataclass into
output columns; it does not reach into the fetcher or detector internals.

### Decision makers: `scraper/signals/detector.py`

`extract_decision_maker()` returns `(name, title)` when a high-confidence
name/title pattern is found. The feature is enabled with:

```yaml
enrichment:
  decision_makers: true
```

When disabled, the extractor is not called and the final columns remain `N/A`.
This avoids adding another crawl while keeping the feature opt-in.

### Analysis and verification

- `analysis/engine.py` calculates sentiment, review keywords, lead score, pitch
  hook, and the representative review.
- `email/verification.py` performs optional MX and SMTP checks.
- `validation/quality.py` rejects records with missing names, invalid ratings,
  or control characters.

The AI pitch hook is optional. If its key is missing or its request fails, the
rule-based hook remains in place.

## 4. Data-quality rules

### Text

`normalize_text()` repairs common UTF-8/Windows-1252 mojibake with `ftfy`,
decodes HTML entities, removes tags and control/bidi noise, and preserves valid
non-English scripts and diacritics. It returns `N/A` only for empty or unusable
input.

### Phone numbers

`normalize_phone()` uses `phonenumbers` and returns strict E.164 strings such
as `+923001234567`. Explicit `+...` and `00...` numbers are parsed globally.
For a national number, the transformation uses the explicit address country
when available and otherwise `job.default_country`. Impossible values return
`N/A`; carrier assignment is not claimed by the scraper.

### Addresses

`decompose_address()` recognizes common US, Canadian, UK, EU, Australian,
New Zealand, Japanese, Brazilian, and other postal patterns. It only assigns a
city or region when the surrounding delimiters provide evidence. Unknown or
contradictory formats stay unresolved rather than turning a street number or
random word into a city.

## 5. Deduplication and resumability

The identity ladder is:

1. `kgmid`;
2. `place_id`;
3. canonical website domain plus city;
4. normalized phone; and
5. normalized business name plus city.

Weak fallback signals are never allowed to merge two records that have distinct
strong IDs.

`CheckpointStore` keeps query status and record stage in SQLite with WAL mode,
plus a JSON mirror. A record is seeded into restart-time deduplication only after
its CSV row has been written and the checkpoint marks it `committed`.

On restart:

```bash
./run.sh
```

completed queries are skipped and incomplete work is retried. Do not delete the
checkpoint unless starting a new job is intentional.

## 6. Failure handling

Website and Maps failures are classified instead of being silently converted to
success:

- `HTTP_BLOCKED`, `CAPTCHA_DETECTED`, `JS_REQUIRED`, `TIMEOUT`, and `UNKNOWN`
  are transient;
- `DNS_FAILURE`, `CONNECTION_REFUSED`, `NOT_FOUND`, and `TLS_ERROR` are strong
  dead-site signals; and
- a Maps bot challenge raises a retryable `ZeroListingsError` rather than
  marking the query complete.

The full operational procedure—server update, background execution, tmux,
VNC, output paths, and troubleshooting—is in `README.md`.

## 7. Server controller

`server.sh` is a thin operational wrapper around the Python launcher. It keeps
operator actions separate from scraping logic and provides short, repeatable
commands:

```text
setup  → create .venv, install dependencies, create local config
update → stop-check, fast-forward main, refresh dependencies
config → edit config.local.yaml
run    → start the live job in tmux (nohup fallback)
demo   → run the offline pipeline
status → inspect the managed process
logs   → follow server-console.log
stop   → send a graceful stop request
```

`config.local.yaml`, `.env`, `.abgms.pid`, and `server-console.log` are ignored
by Git. The update command refuses to pull over local tracked code changes and
never overwrites the operator's local configuration or output directory.

## 8. Development rules

When adding a new output field:

1. add it to `OUTPUT_COLUMNS`;
2. add a real producer or leave it out;
3. map it through `normalize_listing()` and `Pipeline._enrich()` if needed;
4. update both CSV and XLSX tests; and
5. add a regression test for its missing and populated cases.

Do not add a placeholder column merely because Google Maps exposes a surface.
Do not put browser calls in pure transformation modules. Run the checks from the
repository root:

```bash
python -m pytest -q
python -m compileall -q scraper
git diff --check
```
