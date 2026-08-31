# Advance B2B GMS — Google Maps B2B Lead Scraper

A clean-room, production-grade Google Maps B2B lead-generation engine. It
scrapes business listings from Google Maps, enriches each one with deep website
intelligence (emails, social profiles, tech stack, lead signals), verifies
emails natively (MX/SMTP), and scores every lead — outputting a fully-populated
**85-column** XLSX.

## Highlights

- **Deep Maps extraction** — clicks into each result card, waits for the detail
  panel, and extracts ratings, review counts, hours, description, claimed/
  business status, plus code, phone (national + international), and social
  links via layered, drift-resistant selectors.
- **Website intelligence** — multi-page crawl, email extraction (with
  obfuscation decoding + domain filtering), social profile detection, tech
  stack detection (Wappalyzer with regex fallback), and business signs
  (GA4/GTM/Meta Pixel/booking/chat/pricing/financing/…).
- **Native email verification** — MX via `dnspython` and SMTP via `smtplib`,
  no paid external APIs.
- **Lead scoring + hooks** — sentiment score, review keywords, 0–100 lead
  score, and a pitch hook (rule-based, with an *optional* AI personalized hook).
- **Pure CLI / background execution** — no web UI. Driven entirely by
  `config.yaml` + `.env`. Resumes from a durable SQLite checkpoint.
- **VPS-ready** — headless by default, or `maps.headless: false` + `vnc.display`
  to route a visible browser to TightVNC for manual CAPTCHA solving.

## Quick start

```bash
./setup.sh                # apt deps -> venv -> pip -> playwright chromium
cp .env.example .env      # (optional) add API keys / proxy
./run.sh --demo           # offline test — sample records, no browser
./run.sh                  # live scrape (config.yaml + .env)
```

Output lands in `output/<client_name>/leads.xlsx` (+ `leads.csv`, `summary.json`).

## Configuration

Everything lives in **`config.yaml`** (the single control point) and **`.env`**
(secrets). The most important knobs:

| Section | What it does |
|---------|--------------|
| `queries` | Search terms, e.g. `"dentists in Dallas, TX"` |
| `maps.headless` / `vnc.display` | Headed VNC browser for CAPTCHA solving |
| `website.max_pages_per_site` | Bounded crawl depth per business site |
| `enrichment.mx_verify` / `smtp_verify` | Native email verification toggles |
| `ai_hook.*` | Optional AI personalized pitch hook (see below) |
| `filters` | Keep/reject rules (two-pass: Maps fields, then website fields) |

## AI personalized pitch hook (optional, backward-compatible)

The engine can generate a **context-aware, personalized** outreach hook per
business using an LLM. It is completely optional:

1. **AI mode** — set `ai_hook.enabled: true` in `config.yaml` and paste one
   key in `.env`:

   ```bash
   OPENAI_API_KEY=sk-...        # or
   DEEPSEEK_API_KEY=sk-...
   ```

   The engine sends the full available context (name, category, rating,
   reviews, sentiment, keywords, location, social presence, website stack) to
   the LLM and returns a personalized hook.

2. **Rule-based mode** — no key, `enabled: false`, or any LLM failure → the
   existing rule-based hook is used unchanged. Nothing breaks.

The provider/model/key slot all live in `ai_hook:` — no other code changes are
needed to flip modes later.

## Clean console + full logs

The terminal shows structured, easy-to-read progress — how many results a
query returned, which result is being processed (X of N), the business name
and local time, plus a final summary:

```
Advance B2B GMS — Lead Scraper
job: campaign   |   queries: 2   |   started 10:49 PM
──────────────────────────────────────────────────────────────

━━━ [1/20] gyms in Houston, TX ━━━
   found 96 results
        1 of 96   22:35:12   Anytime Fitness - Heights
        2 of 96   22:35:14   LA Fitness
        3 of 96   22:35:16   Planet Fitness
   ↳ collected 3 of 96 · saved 3

┌─ Run complete ──────────────────────────────
   Total collected : 320
   Total saved     : 318
   Duplicates      : 2
   Elapsed         : 12:03
└──────────────────────────────────────────────
```

On a real terminal a live status footer (saved/collected/remaining/ETA) updates
in place under the current query. When output is redirected (tmux log, nohup,
cron), plain lines are emitted instead — no escape-code garbage in the log.

Every warning and error (timeouts, blocked sites, bot challenges, selector
drift) is written to `output/<client>/run.log` instead of spraying the screen,
so the terminal never floods. To suppress progress lines entirely, set
`logging.quiet: true` in `config.yaml`.

## Output schema

85 columns across: identity (kgmid/place_id/cid/name/category), contact
(phone/web/address/coords/plus-code/timezone), maps intelligence
(rating/reviews/hours/description/claimed/status), provenance, website
intelligence (status/emails/social/tech/signals), scoring
(sentiment/keywords/lead-score/pitch-hook/top-review), decision-maker, and
verification (mx/smtp).

## Architecture

```
config.yaml → AppConfig (pydantic) → Pipeline
  → MapsCollector (Playwright: card-click + detail-panel deep extraction)
  → dedup → filters → Enricher (fetch → crawl → email/social/tech/signals)
  → MX/SMTP verify → analysis (sentiment/lead-score/hook) → quality gate
  → atomic CSV → checkpoint → XLSX + summary
```

See `docs/ARCHITECTURE.md` for the full rationale.

## License

Original, self-contained code. Not derived from any other project.
