# Advance B2B GMS

A **clean-room, most-capable Google Maps B2B lead scraper** in Python. It
captures business listings *and* turns them into sellable, pre-qualified leads
by extracting reviews and deriving a **lead-quality score**, a **pitch hook**,
social profiles, ownership signals, and ad-spend intent — all offline, all free.

> **Important:** this repository is a self-contained, isolated project. It does
> **not** copy, import, or link any code from any other repository, and it does
> not modify any other repo. It shares only general domain knowledge about
> Google Maps lead scraping, reimplemented from scratch as original code.

---

## What makes it the most capable

| Capability | What it gives you |
|------------|-------------------|
| **Review-grounded lead scoring** | `sentiment_score`, `review_keywords`, `lead_score` (0–100), `pitch_hook`, `top_review` — prioritize and personalize outreach for free |
| **Grid scraping** | Tiles a region into km-sized cells to bypass Google's ~120-results/search cap |
| **Polygon / geolocation search** | Paste a GeoJSON polygon (from geojson.io) and search only inside it |
| **Popular-times + lead signals** | Traffic histogram, `owner`, `owner_posts`, `can_claim`, `is_spending_on_ads`, `competitors`, `gas_prices`, `about` |
| **Social profile detection** | Domain-anchored detection for 9 platforms — a Facebook URL can never land in Instagram |
| **Email extraction + verification** | 5+ sources, obfuscation decoding, optional MX + SMTP verification (off by default) |
| **Decision-maker enrichment** | Optional pass to extract name + title (CEO/Founder/Owner) |
| **Crash-safe resume** | SQLite checkpoint (WAL) + atomic CSV with fsync — survive reboot/Ctrl-C/tmux drop |
| **Smart dedup** | `kgmid`-first identity ladder; multi-location chains are never merged |
| **Anti-block resilience** | HTTP-first enrichment → Playwright escalation; "blocked" is never "dead" |

Everything runs **free and self-hosted** — no paid APIs, no paid proxies. Runs
comfortably on an Oracle Cloud *Ampere A1 Always-Free* instance (4 OCPU / 24 GB).
See [`docs/OCI.md`](docs/OCI.md) for the deploy guide.

---

## Quick start

Requires Python 3.9+.

```bash
# One-command setup (Ubuntu): apt deps -> venv -> pip -> Playwright Chromium
bash setup.sh

# Or manual setup:
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

### Demo mode (offline, no browser — proves the add-on)

```bash
python -m scraper.main --config config.yaml --demo
```

Output lands in `output/<client_name>/leads.csv` (plus `leads.xlsx`).

### Live mode (real Google Maps collection)

Edit `config.yaml` to set your `queries`, then:

```bash
python -m scraper.main --config config.yaml
```

### REST API + Web UI

```bash
python -m scraper.main --config config.yaml --serve
# Open http://localhost:8000  (auto OpenAPI docs at /docs)
```

Endpoints: `POST /api/v1/jobs`, `GET /api/v1/jobs`, `GET/DELETE /api/v1/jobs/{id}`,
`GET /api/v1/jobs/{id}/download`.

---

## Configuration

One `config.yaml` drives everything. Secrets live in `.env` and are referenced
as `${VAR}`. See `config.yaml` for a fully-commented template covering queries,
maps (zoom, limits), reviews, enrichment, runtime workers, grid, geo/polygon,
proxy, and filters.

**Filters** support AND/OR/NOT with operators `= != > < >= <= in notin contains`:

```yaml
filters:
  include_all:
    - { city: "Dallas" }
    - { reviews: 15, op: ">=" }
  exclude_any:
    - { website_status: "DEAD" }
```

## A note on responsible use

This tool is intended for **research and lead generation within applicable
law**. Respect each site's Terms of Service and privacy expectations. CAPTCHAs
are **never bypassed** programmatically — they are detected, classified, and
preserved so you can solve them manually if you choose. Do not scrape personal
or sensitive data without authorization.

## Tests

```bash
python -m pytest tests/ -q        # 95 pure-logic tests, no browser required
```

Core logic — config validation, normalization, dedup, filters, email extraction,
review parsing + scoring, geo, checkpoint, export, and the demo pipeline — is
fully covered by headless unit tests.

## Project layout

```
scraper/
├── main.py          # CLI + --serve entrypoints
├── config.py        # pydantic config + validation
├── models.py        # output schema + website-status taxonomy
├── pipeline.py      # collect -> dedup -> filter -> enrich -> score -> export
├── maps/            # collector (Playwright), parsing, reviews, geo (grid/polygon)
├── websites/        # fetcher (HTTP->Playwright), crawler, enricher, tech_detect
├── email/           # extraction + MX/SMTP verification
├── signals/         # social + business-signal + decision-maker detection
├── filters/         # config-driven filter engine
├── dedup/           # kgmid-first identity resolver
├── checkpoint/      # resumable SQLite state
├── validation/      # per-record quality gate
├── export/          # atomic CSV/XLSX + JSON summary
├── analysis/        # review-quality lead scoring add-on
└── server/          # FastAPI REST + Web UI
docs/                 # ARCHITECTURE.md + OCI.md + responsible-use notes
tests/                # pytest suite
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design and how
this project stays independent.
