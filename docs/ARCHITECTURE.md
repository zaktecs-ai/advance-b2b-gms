# Architecture

## Independence from the legacy Fiverr-Automation repo

This project is a **clean-room, standalone** codebase. Per its specification it:

- Contains **no files** from `zaktecs-ai/Fiverr-Automation`.
- **Imports nothing** from it (`requirements.txt` lists only PyYAML, pytest, and
  optionally openpyxl).
- **Never pushes to / never modifies** the legacy repo.

It shares only general domain vocabulary (Google Maps fields such as
`business_name`, `rating`, `review_count`, `place_id`) that any Google-Maps lead
scraper uses. All implementation is original to this repo.

## Data flow

```
 config.yaml ──> Config (validated)
      │
      ▼
 Collector (maps/collector.py)
      │   yields Business records (with raw review text)
      ▼
 Analysis (analysis/engine.py)   <-- the free add-on
      │   sentiment_score, review_keywords, lead_score, pitch_hook
      ▼
 Export (export/writer.py)  -> output/<client>/leads.csv (+ xlsx)
```

## Key design decisions

- **Review-quality add-on is the differentiator.** Raw listings are cheap; leads
  that are *qualified and personalized* are valuable. The add-on runs entirely
  offline and adds `lead_score`, `pitch_hook`, `top_review`, `review_keywords`
  and `sentiment_score` columns at zero marginal cost.
- **Collector is a seam, not a monolith.** `Collector.collect()` defines the
  interface. A `--demo` provider yields realistic fixtures so the pipeline and
  the add-on are testable and demonstrable file-to-file without Google. A live
  Playwright collector can be dropped in behind the same interface.
- **Atomic append-safe export.** Each row is flushed and fsync'd before the caller
  advances checkpoint state, so a crash never corrupts a committed row.
- **Config-as-contract.** One YAML file; every risky knob range-checked with a
  clear error that names the key, value, and allowed range.
- **Deterministic pure functions.** Sentiment, keyword extraction and scoring are
  pure functions, isolated in `analysis/engine.py` for unit testing.

## Free add-on scoring model (0–100)

| Component | Weight | Notes |
|---|---|---|
| Rating | up to 50 | high >=4.5, neutral >=3.5, low otherwise |
| Review volume | up to 25 | scaled to a 500-review cap |
| Sentiment | up to 20 | lexicons; negatives clamped to 0 for "qualified" |
| Topic match | up to 5 | presence of high-signal topics (service, pricing, financing...) |
