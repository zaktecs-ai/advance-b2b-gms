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
              Pipeline.run()  ──► for each query ──► Collector.collect()
                                        │                  │  (Playwright live,
                                        │                  │   or Demo provider)
                                        ▼
                          normalize + polygon filter
                                        │
                                        ▼
                          IdentityResolver.is_duplicate()  ◄── seeded from checkpoint
                                        │
                                        ▼
                          pre-enrichment filters (pass 1)
                                        │
                                        ▼
                          Website enricher (HTTP-first → Playwright escalation)
                          + email extraction + tech detection + social signals
                                        │
                                        ▼
                          review analysis (sentiment / keywords / lead score)
                                        │
                                        ▼
                          post-enrichment filters (pass 2)
                                        │
                                        ▼
                          quality gate ──► atomic CSV append ──► checkpoint commit
```

## Key design decisions

### 1. `kgmid`-first identity dedup

Google's Knowledge Graph Machine ID (`kgmid`) is the authoritative, never-null
key. The dedup ladder is:

1. `kgmid`
2. `place_id`
3. `(canonical domain + city)`
4. `normalized phone`
5. `(name key + city)`

Weak signals (`domain+city`, `phone`) never merge two listings that carry a
distinct `kgmid`/`place_id` — that would silently drop a real multi-location
chain. The weak-signal seen-sets are fed *only* by records lacking a strong id.

### 2. Resumable, crash-safe checkpoint

SQLite in WAL mode (`synchronous=NORMAL`) tracks queries, per-record stage, and
committed row offsets. A JSON mirror + `.backup` are maintained. On restart,
dedup seen-sets are re-seeded from **committed** records only, so an in-flight
record is never mistaken for a duplicate.

### 3. Atomic append-safe CSV

Each row is written, flushed, and `fsync`'d *before* the checkpoint advances.
On open, a malformed trailing row (from a partial write) is trimmed. This
guarantees no lost committed rows and no corrupted trailing line.

### 4. HTTP-first enrichment → Playwright escalation

Cheap `httpx` GET by default; escalate to a browser only when a page is
JS-required/blocked/incomplete. A rich failure taxonomy
(`HTTP_BLOCKED`, `CAPTCHA_DETECTED`, `JS_REQUIRED`, `DNS_FAILURE`, `TIMEOUT`,
`TLS_ERROR`, `CONNECTION_REFUSED`, `NOT_FOUND`, `UNKNOWN`) ensures "blocked" is
**never** conflated with "dead" — a LIVE-but-uncrawled record is preserved.

### 5. Review extraction (RPC-first)

Reviews come from `google.com/maps/rpc/listugcposts` (browser-session RPC) with
a DOM-scroll fallback. The review-quality lead-scoring add-on derives
`sentiment_score` (transparent lexicon), `review_keywords` (frequency,
stopword-filtered), `lead_score` (0–100 composite), `pitch_hook`, and
`top_review` — all offline, no paid APIs.

### 6. Grid + polygon search

A bounding box is tiled into km-sized cells (lat-adjusted longitude step); one
search runs per cell to exceed the ~120 results/search cap. GeoJSON polygons
(`geojson.io`) filter results to a user-drawn area via pure ray-casting
point-in-polygon.

## Memory discipline (12 GB VM budget)

For a 200k-record run on a 12 GB VM:

- Never retain full page history — parse and discard.
- Bounded caches (`DNSCache` is size-capped + TTL).
- Deterministic query ordering + bounded parallelism so checkpoints stay
  correct and memory stays flat.

## Extensibility

- **Custom signals** — add to `config.yaml` `signals:` without editing code.
- **Proxy seam** — `ProxyManager` supports HTTP/HTTPS/SOCKS5, round-robin or
  random, off by default.
- **Collector seam** — `Collector.collect()` is the interface; swap the live
  Playwright implementation for any other provider.
- **Export seam** — CSV is canonical; XLSX and JSON summary are additive.
