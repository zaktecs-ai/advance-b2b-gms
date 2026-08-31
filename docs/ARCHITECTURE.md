# Architecture

Advance B2B GMS is a **clean-room, standalone** Google Maps B2B lead scraper.
It is designed for resilience, correctness, and low cost, and it reimplements
well-known scraping capabilities (resumable checkpoints, identity dedup,
review extraction, grid tiling) as **original Python code**.

## Independence statement

This repository does not import, vendor, or link any code from any other
project. It shares only *general domain knowledge* that is common to the
Google-Maps-scraping problem space (e.g. "deduplicate by a stable business id",
"tile a region to exceed the ~120 results/search cap"). Every module here was
written from scratch for this project.

## High-level flow

```
config.yaml ──► load_config() ──► AppConfig (pydantic-validated)
                                        │
                                        ▼
              Pipeline.run()  ──► for each query ──► MapsCollector.collect()
                                        │                  │  (Playwright:
                                        │                  │   card-click +
                                        │                  │   detail-panel
                                        │                  │   deep extraction)
                                        ▼
   normalize_listing → dedup → pre-filter → Enricher (fetch → crawl → emails/social/tech/signals/decision-maker)
                                        │
                                        ▼
              MX/SMTP verify → analysis (sentiment/lead-score/hook)
                                        │                  │  (optional LLM
                                        │                  │   personalized hook)
                                        ▼
              post-filter → quality gate → atomic CSV → checkpoint → XLSX + summary
```

## The collector (the fix)

The collector owns browser interaction and raw field reads; deterministic
normalization is delegated to `scraper/maps/transform.py`. The current
`MapsCollector`:

1. Navigates to the search URL (`hl`/`gl`/region forced).
2. Dismisses the EU consent wall if present.
3. Detects bot challenges (fails closed, retryable — never marked "done").
4. Scrolls the results feed (`div[role="feed"]`).
5. For each listing, **clicks the card in-place** (SPA flow) and waits for the
   detail panel to hydrate (`h1` present), then extracts each field through
   PRIMARY → ALTERNATE → regex fallback selectors:
   - name, category, address (raw; decomposed by the pure transform boundary)
   - phone (+ international), website, plus code
   - rating, review count (rating block → aria → regex fallback)
   - hours (table `eK4R0e` buttons), status, claimed status, description
   - social links (Facebook/IG/LinkedIn/YouTube/X/TikTok/Pinterest/GitHub/Snap)
   - coords / place_id / cid / kgmid from the live URL

## Website enrichment

`Enricher` fetches the homepage (HTTP-first via `httpx`), crawls a bounded set
of relevant internal pages (contact/about/services), then:

- extracts emails (mailto → JSON-LD → inline scripts → visible text, with
  obfuscation decoding + domain filtering)
- detects social profiles (domain-anchored, per-platform)
- detects tech stack (Wappalyzer preferred, regex fallback)
- runs signal detectors (GA4/GTM/Meta Pixel/booking/chat + business keywords)
- optionally extracts a decision-maker name/title from the fetched about/team context

## Output contract

`models.OUTPUT_COLUMNS` is the single source of truth for CSV, XLSX, and
checkpoint export. It contains 75 producer-backed fields; unsupported Maps
surfaces such as popular times, competitors, ownership posts, ad-spend flags,
gas prices, featured questions, rating buckets, and timezone are intentionally
not represented. Every missing value is applied at the transformation/export
boundary, never by a fake producer inside the collector or detector.

## Email verification (native, no paid APIs)

- **MX** — `dnspython` lookup, cached per-domain (TTL, size-capped).
- **SMTP** — `smtplib` RCPT TO probe with MX-preference-ordered host failover
  and explicit statuses (Verified / Invalid / Catch-All / Inconclusive /
  Connection Failed). Uncertainty is never collapsed into false certainty.

Both are OFF by default (`enrichment.mx_verify` / `smtp_verify`).

## Lead scoring + AI hooks

`analysis/engine.py` produces sentiment (−1..1 lexicon), review keywords,
0–100 lead score, a rule-based pitch hook, and the top review.

`analysis/llm_hooks.py` adds an **optional** AI personalized hook: when
`ai_hook.enabled` is true and an API key is present in `.env`, the full
context is sent to OpenAI/DeepSeek; otherwise the rule-based hook is used
(a seamless, backward-compatible fallback).

## VNC / headed mode

`BrowserManager` routes a visible browser to a TightVNC display by exporting
`DISPLAY` + `XAUTHORITY` into the inherited environment — so an operator can
watch and solve a CAPTCHA manually. Set `maps.headless: false` and
`vnc.display`.

## Resilience

- **Durable checkpoint** (SQLite WAL + JSON mirror): queries and records resume
  across crashes; only COMMITTED records seed dedup on restart.
- **Atomic CSV** (flush + fsync per row) with malformed-tail recovery.
- **Browser recycling** every N queries to keep long VPS jobs healthy.
- **Identity dedup ladder**: kgmid → place_id → (domain+city) → phone →
  (name+city); weak signals never merge distinct strong-id listings.
- **Rich failure taxonomy**: HTTP_BLOCKED / CAPTCHA / JS_REQUIRED / TIMEOUT are
  transient and never misclassified as DEAD.
