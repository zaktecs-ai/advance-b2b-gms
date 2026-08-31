# Advance B2B GMS

A standalone, clean-room Google Maps B2B lead scraper. It captures business
listings and, as its headline **free add-on feature**, extracts review text and
derives a **lead-quality score** plus a **pitch hook** for each business — turning
raw listings into personalized, sellable leads.

> **Important:** this repository is a self-contained, isolated project. It does
> **not** copy, import, or link any code from the legacy `Fiverr-Automation` repo,
> and it does not modify that repo in any way. It shares only general domain
> knowledge about Google Maps lead scraping.

---

## The Free High-Quality Feature (spec, ~200 words)

- **Feature:** Review-grounded lead qualification.
- For every business, capture the latest Google-Maps reviews (offline, no paid API)
  and derive three cheap signals per record:
  - `sentiment_score` — a transparent lexicon score (-1..1) of the review text.
  - `review_keywords` — the most frequent meaningful topics mentioned.
  - `lead_score` (0-100) — a single "how qualified is this lead" number combining
    rating, review volume, sentiment and topic strength.
  - `pitch_hook` — an auto-generated, data-grounded opening line for outreach.
- **Why it boosts quality:** sellers can prioritize high-score leads and open with
  a relevant, personal line instead of a generic pitch — dramatically improving
  reply rates and perceived value.
- **Implementation outline:** collect N reviews per business → clean/normalize →
  lexicon sentiment → frequency keyword extraction → weighted composite score →
  write `lead_score`, `pitch_hook` and review columns to CSV/XLSX.
- **Expected impact:** higher conversion on sold leads, zero marginal cost.
- **Cost:** free. No external APIs, no licensing.

---

## Setup

Requires Python 3.9+.

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Usage

Edit `config.yaml` (queries, output dir, review settings), then run:

```bash
# Demo mode (uses built-in sample records; no network, proves the add-on):
PYTHONPATH=src python -m scraper.main --config config.yaml --demo

# Live mode (requires a Playwright/Chrome collector — see ARCHITECTURE.md):
PYTHONPATH=src python -m scraper.main --config config.yaml
```

Output lands in `output/<client_name>/leads.csv` (and `leads.xlsx` if
`openpyxl` is installed).

## Tests

```bash
python -m pytest tests/ -q
```

Core logic (config validation, sentiment, keyword extraction, lead scoring,
pipeline/export) is covered by pure-logic tests — run without a browser.

## Project layout

```
.
├── src/scraper/
│   ├── models.py            # data contract + all output columns
│   ├── config.py            # YAML config + validation
│   ├── pipeline.py          # collect -> analyze -> export
│   ├── main.py              # CLI entrypoint
│   ├── maps/collector.py    # Google Maps collection seam (+ demo provider)
│   ├── analysis/engine.py   # THE FREE ADD-ON: review quality analysis
│   └── export/              # atomic CSV/XLSX writer + demo fixtures
├── tests/                   # pytest suite
├── config.yaml
└── requirements.txt
```

See `docs/ARCHITECTURE.md` for details on how this project stays independent from
the legacy Fiverr-Automation repository.
