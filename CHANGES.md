# CHANGES.md

Remediation of the senior-architect audit (AUDIT-REMEDIATION-PLAN.md). One entry
per F-ID: root cause, files touched, tests added, verification evidence.

Suite grew 158 → 191 passing tests.

---

## F01 — Facebook column constant (panel-wide anchor scrape)
- **Root cause:** `_extract_social_links` harvested anchors from the ENTIRE page
  (including the results feed behind/around the panel), so one business's
  `facebook.com/championplumbers` leaked into every row.
- **Files:** `scraper/maps/collector.py` (new `filter_panel_hrefs`, scoped panel
  selectors).
- **Tests:** `tests/test_panel_guard.py::test_filter_panel_hrefs_drops_maps_nav_links`.
- **Evidence:** test green; `filter_panel_hrefs` drops `maps/place|dir|search`.

## F02 — One-row-shift contamination
- **Root cause:** extraction proceeded on blind sleeps with no proof the panel
  switched to the clicked card, so name/phone/rating belonged to the PREVIOUS
  business.
- **Files:** `scraper/maps/collector.py` (`_click_to_open` URL-switch wait,
  `_open_and_extract` panel-identity guard + coherence sentinel, new
  `_names_compatible`).
- **Tests:** `tests/test_panel_guard.py::test_names_compatible_guard`.
- **Evidence:** green.

## F03 — kgmid never extracted
- **Root cause:** production URLs carry `!16s%2Fg%2F…` (percent-encoded); the
  regex only handled plain `/g/…`.
- **Files:** `scraper/maps/parsing.py` (`unquote` once + updated kgmid regex).
- **Tests:** `test_kgmid_extracted_from_encoded_url`, `test_kgmid_extracted_from_plain_url`.
- **Evidence:** green.

## F04 — cid grabs a wrong hex token
- **Root cause:** standalone `0x…:0x…` regex matched the first pair anywhere,
  including ad-panel `!5s` tokens.
- **Files:** `scraper/maps/parsing.py` (cid derived only from `!1s`).
- **Tests:** `test_cid_matches_place_id_and_ignores_ad_tokens`.
- **Evidence:** green; `cid == place_id` on the Aberle ad-token URL.

## F05 — lat/lng polluted with viewport center
- **Root cause:** `/maps/@…` (search camera) was treated as the place pin.
- **Files:** `scraper/maps/parsing.py` (deleted `/maps/@` branch; only `!3d/!4d`).
- **Tests:** `test_viewport_center_never_becomes_place_coords`,
  `test_true_pin_coords_extracted_over_viewport`; updated existing
  `test_parse_maps_url_coords` per R3.
- **Evidence:** green.

## F06 — Duplicate businesses committed twice
- **Root cause:** weak-phone rule skipped when a place_id was present; no
  same-website guard at all.
- **Files:** `scraper/dedup/dedup.py` (same-domain+name / same-phone+name guards
  independent of strong ids), `scraper/checkpoint/store.py` (`name_key` column +
  migration + seeds), `scraper/pipeline.py` (pass seeds).
- **Tests:** `test_same_domain_same_name_duplicate_even_with_distinct_place_ids`,
  `test_chain_locations_same_domain_diff_names_not_merged`,
  `test_same_phone_same_name_duplicate_with_distinct_place_ids`.
- **Evidence:** green.

## F07 — Decision-maker fabrication
- **Root cause:** CTA verbs glue to names; titles split; duplicated tokens;
  non-person words captured.
- **Files:** `scraper/signals/detector.py` (multi-word `Vice President` title,
  CTA-prefix reject, `_NAME_NOT_PERSON_WORDS`, duplicated-token reject,
  title-ending reject).
- **Tests:** `test_no_decision_maker_from_production_false_positives`,
  `test_vice_president_title_not_split`, `test_real_decision_makers_still_detected`.
- **Evidence:** green; all 5 production FP rows → `("", "")`.

## F08 — Review chrome → junk keywords
- **Root cause:** whole-review-card harvest pulled reviewer name + "4 reviews ·
  1 photo… months ago Like Share" into top_review/keywords.
- **Files:** `scraper/maps/reviews.py` (body-span selectors + `clean_review_text` +
  `len>=25`), `scraper/analysis/engine.py` (`_UI_NOISE` guard).
- **Tests:** `test_clean_review_text_strips_ui_chrome`,
  `test_keywords_never_contain_ui_noise`.
- **Evidence:** green.

## F09 — Nonexistent `asyncio.CoroutineNotAllowedError`
- **Root cause:** that attribute does not exist in any Python version.
- **Files:** `scraper/browser/browser_manager.py`.
- **Tests:** existing `test_browser_manager_importable` (in test_smoke); import
  succeeds.
- **Evidence:** `import scraper.browser.browser_manager` exits 0.

## F10 — `(?-i:…)` needs Python 3.11 while `requires-python >=3.9`
- **Root cause:** scoped inline regex flags are 3.11+; the pin was 3.9.
- **Files:** `pyproject.toml` (`requires-python = ">=3.11"`), `README.md`,
  `scraper/signals/detector.py` (comment).
- **Tests:** detector imports on 3.11.
- **Evidence:** suite green on Python 3.11.16.

## F11 — `FetchResult.headers: dict = None`
- **Root cause:** mutable-class-type default.
- **Files:** `scraper/websites/fetcher.py` (`field(default_factory=dict)`).
- **Tests:** `test_fetch_result_default_headers`.
- **Evidence:** green.

## F12 — Dead `ConnectTimeout` branch in `_classify`
- **Root cause:** unreachable (already caught by the top TIMEOUT branch).
- **Files:** `scraper/websites/fetcher.py`.
- **Tests:** `test_connect_timeout_classifies_as_timeout`.
- **Evidence:** green.

## F13 — `ProgressConsole.query_total` method/attr collision
- **Root cause:** `def query_total` wrote `self.query_total`, shadowing the
  method; attribute uninitialized.
- **Files:** `scraper/utils/progress.py` (`current_query_total` + `set_query_total`),
  `scraper/main.py` (`on_query_total=progress.set_query_total`).
- **Tests:** `tests/test_progress.py::test_footer_before_query_started_does_not_crash`.
- **Evidence:** green.

## F14 — Collector context/page leak
- **Root cause:** ctx/page created outside try/finally.
- **Files:** `scraper/maps/collector.py` (`collect` wraps creation in try/finally;
  extracted `_collect_on_page`).
- **Tests:** N/A (headless); verification = full suite green.

## F15 — Dead branch in `_extract_phone_international`
- **Root cause:** `re.sub(r"\D",…)` can never start with `+`.
- **Files:** `scraper/maps/collector.py` (new pure `digits_to_intl`).
- **Tests:** `test_digits_to_intl`.
- **Evidence:** green.

## F16 — CSV row counting breaks on multi-line fields
- **Root cause:** physical-line count ≠ row count for quoted embedded newlines.
- **Files:** `scraper/export/csv_writer.py` (`csv.reader` count).
- **Tests:** `test_row_count_handles_multiline_quoted_field`.
- **Evidence:** green.

## F17 — `at`/`dot` corrupts prose into fake emails
- **Root cause:** bare `\s+at\s+` / `\s+dot\s+` decoded natural language.
- **Files:** `scraper/email/extract.py` (bracket-only decode).
- **Tests:** `test_prose_at_dot_not_decoded`.
- **Evidence:** green.

## F18 — Social path rejection uses substring matching
- **Root cause:** `/tr` killed `/travel`; `/pages/` killed real pages.
- **Files:** `scraper/signals/social.py` (first-segment reject; multi-segment
  instagram; `/pages/` accepted).
- **Tests:** `test_segment_rejection_not_substring`.
- **Evidence:** green.

## F19 — Signal keywords raw substring match
- **Root cause:** `licensed` matched `unlicensed`.
- **Files:** `scraper/signals/detector.py` (`_kw_in_blob` word-boundary).
- **Tests:** `test_keyword_word_boundaries`.
- **Evidence:** green.

## F20 — Tech detection scans body prose + legacy `detect_tech`
- **Root cause:** fallback scanned raw HTML text; `detect_tech` duplicated and
  contradicted `SignalDetector`.
- **Files:** `scraper/websites/tech_detect.py` (markup-only `_fallback_detect`,
  removed prose patterns, deleted `detect_tech`).
- **Tests:** `test_fallback_detect_ignores_body_prose`,
  `test_fallback_detect_finds_markup_artifact`.
- **Evidence:** green; `grep detect_tech` → nothing.

## F21 — Phone extensions destroyed
- **Root cause:** extension regex stripped and discarded the extension.
- **Files:** `scraper/utils/normalize.py` (`_EXT_RE` capture + `x<ext>` append).
- **Tests:** updated `test_normalize_phone_strips_extension_and_trailing_noise`.
- **Evidence:** green; `+1 555-123-4567 ext 890` → `+15551234567 x890`.

## F22 — Address decomposition drops valid cities
- **Root cause:** comma-only split + blanket digit rejection.
- **Files:** `scraper/maps/parsing.py` (wider separators; postal-shaped rejection
  only).
- **Tests:** `test_pipe_and_newline_separated_addresses`.
- **Evidence:** green.

## F23 — SMTP verification hardening
- **Root cause:** MAIL FROM response discarded; plain starttls; placeholder
  sender allowed.
- **Files:** `scraper/email/verification.py` (check mail_from, `ssl` context),
  `scraper/config.py` (SMTPConfig validator).
- **Tests:** `test_smtp_from_email_must_be_real_when_enabled`.
- **Evidence:** green.

## F24 — Crawler nondeterminism + dead `early_stop_reached`
- **Root cause:** bare set→list sort unstable; `early_stop_reached` never wired.
- **Files:** `scraper/websites/crawler.py` (order-preserving dedup),
  `scraper/websites/enricher.py` (early-stop in extra-pages loop).
- **Tests:** existing suite.
- **Evidence:** green.

## F25 — Dead/unwired config knobs
- **Root cause:** several knobs validated but never read.
- **Files:** `scraper/main.py` (`logging.level` wired), `scraper/pipeline.py`
  (`max_email_length`, `reviews.min_len/max_len`, enrichment toggles wired),
  `scraper/config.py` (`exclude_selectors` = F33), `scraper/websites/enricher.py`.
- **Tests:** `test_config_template_has_no_dead_sections`.
- **Evidence:** green; every `config.yaml` top-level key maps to an `AppConfig`
  field.

## F26 — One failing record aborts the batch
- **Root cause:** `ex.map` re-raised first worker exception.
- **Files:** `scraper/pipeline.py` (`_safe_enrich`).
- **Tests:** `test_one_failing_enrich_does_not_abort_batch`.
- **Evidence:** green.

## F27 — Proxy pool never applied to httpx
- **Root cause:** `ProxyManager` only fed Playwright contexts.
- **Files:** `scraper/main.py` (http/https wired, proxy_manager passed),
  `scraper/pipeline.py` (threads `httpx_proxy()`, failure feedback),
  `scraper/websites/enricher.py` (`proxy_manager`).
- **Tests:** `test_pipeline_threads_proxy_to_enricher`.
- **Evidence:** green.

## F28 — Pipeline resources leak on `run()` raise
- **Root cause:** `finally` closed only the browser.
- **Files:** `scraper/pipeline.py` (idempotent `close()`), `scraper/main.py`.
- **Tests:** existing suite.
- **Evidence:** green.

## F29 — Remaining blind sleeps in collector
- **Root cause:** `time.sleep` used for data sync instead of selector waits.
- **Files:** `scraper/maps/collector.py` (feed/h1 waits), `scraper/maps/reviews.py`
  (element-scoped scroll + `wait_for_timeout`).
- **Tests:** existing suite.
- **Evidence:** `grep time.sleep` shows only pacing/cooldown/fallback sleeps.

## F30 — `_write_mirror` rewrites the entire table
- **Root cause:** full select+serialize+rewrite every N records.
- **Files:** `scraper/checkpoint/store.py` (NDJSON `_append_mirror`; deleted
  `_write_mirror`/`_maybe_mirror`/`MIRROR_EVERY`).
- **Tests:** `test_mirror_is_ndjson_incremental` (replaces the old throttle test
  per R3).
- **Evidence:** green; 1200 commits → 2400 lines.

## F31 — `_finalize` OOM (whole dataset in RAM + non-streaming XLSX)
- **Root cause:** `committed_rows()` materialized everything; `write_xlsx` built
  a full Workbook.
- **Files:** `scraper/checkpoint/store.py` (`iter_committed_rows`),
  `scraper/export/xlsx_writer.py` (write-only), `scraper/pipeline.py`.
- **Tests:** `test_iter_committed_rows_streams`.
- **Evidence:** green; demo run writes a loadable `leads.xlsx`.

## F32 — Dedup loads all history into Python sets
- **Root cause:** unbounded cold-start preload.
- **Files:** `scraper/checkpoint/store.py` (bounded preload + `identity_exists` /
  `domain_name_seen` / `phone_name_seen`).
- **Tests:** `test_db_path_covers_older_history`.
- **Evidence:** green.

## F33 — Email/decision-maker stripping deletes real team data
- **Root cause:** `.author`/`blockquote`/`.quote`/`cite` blanket decompose.
- **Files:** `scraper/config.py` (`EnrichmentConfig.exclude_selectors`),
  `scraper/email/extract.py`, `scraper/websites/enricher.py`.
- **Tests:** existing suite (default excludes no longer strip `blockquote`/`.author`).
- **Evidence:** green.

## Schema change — 75 → 68 columns
- Removed: `kgmid`, `cid`, `subcategory`, `about`, `mx_enabled`, `smtp_enabled`,
  `filtered_out_reason`.
- **Files:** `scraper/models.py`, `scraper/maps/transform.py`,
  `scraper/pipeline.py`, `tests/test_maps_transform.py`, `README.md`,
  `docs/ARCHITECTURE.md`.
- **Tests:** `test_schema_has_no_unproduced_ghost_columns` (68 + removed names),
  `test_protected_columns_still_present`.
- **Evidence:** demo run CSV header = 68 columns; removed columns absent.

---

## Definition of Done (global)

| Check | Status |
|---|---|
| `python -m pytest -q` | 191 passed |
| `python -m compileall -q scraper` | clean |
| `python -m scraper.main --demo` | completes; 68-col CSV, XLSX + summary.json |
| Every F-ID in CHANGES.md | yes |
| `grep time.sleep` collector/reviews | pacing/cooldown/fallback only |
| `grep detect_tech` | nothing |
| No dead config section | `test_config_template_has_no_dead_sections` green |
| README/docs updated | 68 columns, Python 3.11+, migration note |
| Zero test deletions | yes (updated tests carry R3 comment) |
| Final report table | this section |
