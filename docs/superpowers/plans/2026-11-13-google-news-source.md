# Google News Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user configure Google News searches through the existing source dialog without composing RSS URLs, installing RSSHub or supplying credentials.

**Architecture:** A native `google_news` adapter constructs one fixed-host RSS request per search and projects the existing bounded feed parser's evidence into FetchResult. The source registry remains the schema authority; workers, CLI writers, Store and notification policies retain their existing responsibilities. This implements the Google News slice of the already approved source-expansion design, not a new comparison or delivery system.

**Tech Stack:** Python standard library, existing `net.request` and `feedparse.parse`, unittest, existing JavaScript and Chromium/WebKit acceptance gates.

**Spec:** `docs/superpowers/specs/2026-11-12-source-expansion-event-updates-design.md`, source capability/coverage, stage 3.

## Global Constraints

- No Horizon runtime or copied scraper, MCP/Skill integration, new SDK, paid/model/send calls, production edits or source activation.
- Continue in the existing host-managed worktree on `feat/google-news-source`, based on `main` at `fdf73c4`. Preserve this worktree. The request to push the completed main does not authorize merging or pushing this new stage.
- Only request `https://news.google.com/rss/search`; construct parameters with `urlencode`. No redirects, Google article decoding, publisher-page fetches or private/local endpoint selection. Socket timeout 20 seconds, response bound 2 MiB, sequential searches within the existing source-worker deadline and transport retry bound.
- `settings.queries`: empty default, 1..25 nonblank queries required at collection, at most 1024 characters each, no control characters. Deduplicate exact trimmed queries; do not lowercase or rewrite search semantics.
- `settings.language`: default `zh-CN`, at most 35 characters matching `[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*`; `settings.region`: default `CN`, two uppercase ASCII letters. `settings.edition`: optional empty-default advanced override of `REGION:language`, at most 38 characters using the same language-token syntax; its region must match the selected region. Derive the ordinary edition from the lowercase primary language, with `zh-CN`/`zh-SG`/`zh-Hans` -> `zh-Hans`, `zh-TW`/`zh-HK`/`zh-Hant` -> `zh-Hant`; do not claim arbitrary locale combinations are supported by Google.
- `settings.lookback_days`: default 7, integer 1..90; add `when:Nd` to the query. This is an upstream search hint, not proof of complete history. Reject conflicting explicit `when:`, `after:` or `before:` query operators with an actionable configuration error instead of silently combining windows.
- `settings.max_results`: default 50, integer 1..100 per query. Parse the bounded feed first, then filter/select locally, newest known publication first and unknown dates last in provider order. Filter dated records against the later of `since` and the configured lookback boundary; retain unknown dates without inventing dates. Accept `since=None` or a finite nonnegative numeric timestamp; invalid types/NaN/infinity fail before transport. Equal-timestamp items remain eligible for native-ID retry safety. The request window does not depend on `since`, so future `since` simply filters dated records and never creates a negative query window.
- Re-run the bounded recent search on collection; report no complete high-water mark. Do not introduce per-query persistent cursors, historical replay or a claim that finite search results cover every article. Explain this limitation independently of a pagination cap.
- Versioned full native identity uses the feed GUID or original permalink fallback, never a headline, publisher homepage or truncated hash. Same native article in two queries retains one native identity; old RSS identities are not migrated or aliased.
- Preserve the aggregator permalink, original bounded excerpt and reported publication date. Keep publisher name/homepage as separately typed attribution, not an article URL, author assertion or complete article body. `content_kind=article`, `source_text_complete=false`; incomplete evidence cannot qualify for exact-copy suppression.
- Configuration renders and ordinary dry-runs never collect. Source creation/edit/copy/preview use existing CLI handlers, hash/revision checks, actions switch, same-origin and token gates. Inactive adapter drafts remain bounded and never enter CLI write parameters.

## Review Focus

- Chinese/quoted/operator queries and locale overrides must encode as query data, never change the host/path or accidentally request another edition.
- A full or capped search response cannot advance a cursor past omitted articles; successful sibling queries survive one query's failure without claiming complete coverage.
- Aggregator and publisher URLs must remain distinguishable; a publisher homepage cannot become the original article or an automatic linked fetch target.
- Missing dates, duplicate native IDs, reused URLs and incomplete excerpts cannot fabricate publication times, collide through truncation or become confident article copies.
- Adapter switches and failed/no-JS saves must preserve Google News edits without sending Google settings to RSS/GitHub or replacing managed notification parameters.

---

### Task 1: Native Search And Evidence Boundary

**Files:** Create `src/newspipe/adapters/google_news.py`, `tests/test_google_news_source.py`; modify `src/newspipe/source_registry.py`, `src/newspipe/feedparse.py`, `src/newspipe/source_contract.py`; extend `tests/test_source_registry.py`, `tests/test_feedparse.py`, `tests/test_source_contract.py`, `tests/test_source_collection.py`.

**Interfaces:** Consume `net.request(url, timeout=20, max_bytes=feedparse.MAX_BODY, max_redirects=0)`, `feedparse.parse(body, base_url=url)`, `native_identity`, `FetchResult`, `SubscriptionReport`, and worker `fetch_partitions(cfg, since)`. Produce `google_news.fetch(cfg=None, since=None) -> FetchResult` and `google_news.fetch_partitions(cfg=None, since=None)`, yielding one completed query frame. Add trailing defaulted `FeedEntry.publisher_name` / `publisher_url` fields so existing parser callers remain compatible.

- [x] Write `GoogleNewsSourceTests` with synthetic RSS/Atom and mocked transport: fixed request host/path, Unicode/quoted query encoding, locale defaults/override validation, lookback conflicts, no credentials or linked requests, invalid configuration rejected before transport.
- [x] Add RED regressions for long native GUIDs and cross-query identity, publisher attribution versus aggregator permalink, unknown/equal dates, full/capped search with no watermark, successful siblings after failure and categorical errors without response/query/exception reflection.
- [x] Run `PYTHONPATH=src python -m unittest discover -s tests -p test_google_news_source.py`; observe failure because the adapter is absent.
- [x] Implement the descriptor and provider module. Extend code-owned string validation with optional `pattern` and `max_length` used by locale fields; preserve existing descriptor defaults. Add bounded RSS `<source>` / Atom `<source>` attribution parsing without changing generic feed identities or author/body semantics.
- [x] Add allowlisted `window_kind` diagnostics (`page_cap`, `search_window`, `result_cap`) alongside existing `window_limited`; every completed Google search sets `window_limited=true`, `watermark=None`, and `search_window` or, when locally clipped, `result_cap`. The existing pipeline checks `window_limited` before its unknown-watermark fallback; integration tests must prove the actual Store cursor does not advance, not merely inspect the adapter report. Unknown/invalid diagnostic values remain rejected, stored unknown keys remain excluded from public export.
- [x] Run source/registry/feed/contract/worker/observation/collection suites with synthetic transports; verify feedparser changes leave existing RSS, HN, Reddit and GitHub behavior intact.
- [x] Commit `feat: add bounded Google News RSS searches`.

### Task 2: Source Dialog And Search Diagnostics

**Files:** Extend `tests/test_adapter_settings_web.py`, `tests/test_browser_e2e.py`, `tests/test_view.py`; modify `src/newspipe/view.py` only where acquisition-window labels require new diagnostic distinctions. Modify schema/form/action modules only if the generic descriptor path is proven insufficient. Update `README.md`; create `docs/google-news-source-acceptance.md`.

**Interfaces:** Consume Task 1 descriptor through existing `config.source_fields`, form translation and inactive-draft snapshots. Google News hides irrelevant `feeds` / `http2`; query and localization controls render through the same schema as CLI validation. Existing old GitHub diagnostics without `window_kind` retain their pagination wording.

- [x] Write/observe RED tests for Google News create/dry/patch/copy/preview/reopen with queries and locales, invalid/blank numeric values, source revision conflicts, unknown legacy extras and non-writing no-JS refresh.
- [x] Add a read-only acquisition diagnostic test distinguishing bounded search history from GitHub pagination, including empty and failed searches. No runtime source lookup or re-collection during rendering.
- [x] Add Chromium/WebKit scenarios at 360/1440 px: choose Google News, enter queries, validation preserves edits, dry-run does not write, confirmed save creates a disabled collect-only source, reopen/edit/copy retain values. Add no-JS Google News -> RSS -> Google News round-trip coverage.
- [x] Keep primary fields concise, with edition/limits in advanced controls and existing hover/focus help. Do not add another permanent settings tab or source-level notification overrides.
- [x] Run web/action/draft/view/JavaScript suites and the new browser scenarios. Document examples, finite-window/no-cursor behavior, publisher versus aggregator links, no full-body fetch and the separate live-acceptance requirement.
- [x] Commit `feat: expose Google News searches and window diagnostics`.

### Final Acceptance

- [x] Run full unittest discovery with mandatory JavaScript/browser gates, secret scan and `git diff --check`, once at a time in the isolated worktree. Record counts and any intermittent automation failures honestly.
- [x] Run synthetic doctor/card-budget checks without real credentials, provider/model requests, runner/WS or sends. Verify frozen payloads, original callbacks, queue IDs and favorite receipts are unchanged.
- [x] Obtain one fresh whole-branch read-only review using the existing native execution approach; fix Important/Critical findings after reproduced RED/GREEN and document any deferred minor finding. Do not request another reviewer merely to replace an unfavorable result.
- [x] Record measured offline acceptance and final commit. Leave the new stage committed but unmerged/unpushed until separately instructed.

## Approval Boundary

The parent design is already approved. This plan selects Google News as the next independently testable source family because it reuses existing RSS infrastructure and needs no new credentials. Numeric bounds, locale defaults and the conservative rolling-search strategy above are builder proposals. Confirm this concrete stage plan before implementation; OSSInsight, GDELT, Telegram, richer HN/Reddit modes and budgeted semantic differences remain separate subsequent plans.
