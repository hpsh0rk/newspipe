# Source Evidence Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve trustworthy original material from existing RSS/Atom, HN and Reddit sources and expose retry-safe subscription failures before adding semantic comparisons.

**Architecture:** A pure feed parser produces bounded source evidence; optional bounded HTTP transport protects acquisition without changing existing callers. Built-in adapters return the existing FetchResult contract. RSS full native IDs carry narrowly validated legacy aliases, used only to recognize old completed/pending items; Store treats alias-only legacy completions as uncertain baselines.

**Tech Stack:** Python 3.11+, standard-library XML/HTML/date parsing, existing optional HTTP/2 transport, unittest and Playwright.

**Spec:** `docs/superpowers/specs/2026-11-12-source-expansion-event-updates-design.md`

## Global Constraints

- Single-user newspipe; no Horizon runtime, MCP/Skill, extra runner, live collection/model/send or production configuration changes.
- Preserve old pushed IDs, pending entries, frozen payloads, callbacks and favorites; never rewrite them on upgrade.
- Original source material is captured before filtering/enrichment. Unknown publication times stay unknown; updated time is not publication time.
- No automatic suppression, semantic classification, paid comparisons, apply/undo or historical replay in this stage.
- No feed-supplied article URLs are fetched. Explicitly configured feeds/local RSSHub remain trusted acquisition inputs.
- Acquisition runs outside writer locks. Partial results keep the existing global cursor unchanged; successful siblings remain durable and retry idempotently.
- New feed requests: at most 2 MiB decoded bytes, 5 redirects, existing two-exit attempt bound; at most 100 subscriptions and 1000 entries per feed.
- Source text at most 20,000 characters; title 500; raw markup 40,000. Explicit clipping flags; content/encoded fields alone never prove full article text.
- No new settings or source-family modes in this stage. Conditional fetching, authenticated modes and richer HN/Reddit selection remain separate follow-ups.
- Manual edits use apply_patch. Commit each task; full JS/browser gates and secret scan before completion.

## Review Focus

- A subscription fails while another succeeds: successful content survives, failure is visible, cursor does not advance.
- Two RSS GUIDs share their first 120 characters: both new items remain distinct, while old completed/pending aliases do not replay.
- Invalid or timezone-less dates and recently edited old posts: never fabricate a publication date; compare updated time only for incremental selection.
- Huge/compressed responses, redirect loops, XML entities, malformed entries: bounded failure without provider body/URL secrets in diagnostics.
- Feed descriptions, XHTML, HN links and social posts: preserve useful evidence without claiming complete articles or classifying metric changes as content changes.

---

### Task 1: Bounded Feed Parsing And Transport

**Files:**
- Create: `src/newspipe/feedparse.py`
- Modify: `src/newspipe/net.py`, `src/newspipe/rssconnect.py`
- Test: `tests/test_feedparse.py`, `tests/test_bounded_transport.py`, `tests/test_rsshub_probe.py`

**Interfaces:**
- Consumes: existing `net.request(url, ..., http2=False)` and RSSHub URL resolution.
- Produces: `net.request(..., max_bytes=None, max_redirects=None)`; limits are opt-in and terminal across exit retries.
- Produces: `feedparse.parse(body: bytes, *, base_url: str = "") -> tuple[FeedEntry, ...]`; FeedEntry contains native_id, title, permalink, source_text, source_markup, text_truncated, published_ts, updated_ts, author, body_field and typed references.
- Produces: `feedparse.FeedError(reason)` with invalid_feed, unsupported_feed and too_large reasons. RSSHub probe accepts RSS 2.0 and namespaced Atom, still rejects RDF and is read-only.

- [ ] **Step 1: Write failing tests.** Pin RSS encoded text over 300 characters, HTML entities/scripts, Atom XHTML/link selection/xml:base, unknown dates, old publication/new update, missing identities, DTD/entity rejection, 1001-entry failure and truncation. Test bounded HTTP/1 and HTTP/2 bodies/redirects and response closure, with limit failures not retried via proxy. Replace obsolete Atom-unsupported probe expectations with RDF and add valid Atom probe counts.
- [ ] **Step 2: Run RED.** `python -m unittest discover -s tests -p 'test_feedparse.py'` and the bounded transport file must fail because these interfaces/behaviors do not exist.
- [ ] **Step 3: Implement the interfaces.** Use pure XML parsing and bounded HTML text extraction; preserve raw markup separately and require explicit timezone for dates. Keep probe's 1 MiB transport limit and its no-retry behavior. Do not add linked-page collection.
- [ ] **Step 4: Run GREEN.** Focused parser/transport/probe tests pass, then `NEWSPIPE_REQUIRE_JS_TESTS=1 NEWSPIPE_BROWSER_TESTS=1 python -m unittest discover -s tests` passes with zero skips.
- [ ] **Step 5: Commit.** `feat: add bounded RSS and Atom evidence parsing`.

### Task 2: Evidence-Producing Built-In Adapters And Safe Legacy Identity Recognition

**Files:**
- Modify: `src/newspipe/adapters/rsshub.py`, `src/newspipe/adapters/hn.py`, `src/newspipe/adapters/reddit.py`
- Modify: `src/newspipe/source_contract.py`, `src/newspipe/source_registry.py`, `src/newspipe/dedup.py`, `src/newspipe/pipeline.py`, `src/newspipe/state.py`, `src/newspipe/story_capture.py`
- Test: `tests/test_source_evidence.py`, `tests/test_source_identity_upgrade.py`, existing RSS runtime/settings/probe tests
- Document: `docs/source-evidence-quality-acceptance.md`, `README.md`, `AGENTS.md`

**Interfaces:**
- Consumes: Task 1 parser/transport; existing FetchResult(items, reports), SubscriptionReport and observation evidence fields.
- Produces: built-in fetch(cfg=None, since=None) -> FetchResult with categorical per-subscription success/empty/failure reports. All requests use Task 1 transport bounds; defaults and existing short IDs stay unchanged.
- Produces: `source_contract.item_identity_keys(item) -> set[str]`, primary identity plus optional RSS v2 legacy prefix; alias is accepted only when `identity_version == "rss-full-v2"`, full ext_id exceeds 120 characters, and `legacy_ext_id == ext_id[:120]`.
- Produces: Store.original_observation_baselines(source, native_ids, *, aliases=None) recognizes alias-only completions as missing-material baselines, without rewriting old evidence or converting it to new identity.
- RSS source text is retained rather than its short display summary; generic feed kind remains unknown and completeness false. HN/Reddit are posts, not complete linked articles. Publication and update dates, native evidence and separately typed linked references are retained; engagement stays metadata.

- [ ] **Step 1: Write failing tests.** Test complete/empty/partial/total result categories for each adapter; 401/403/429/invalid data must not be quiet or leak errors. Pin since filtering, recent edits, timezone-less dates, HN original discussion versus linked article, metric-only fingerprint stability, raw text/truncation and no linked requests. Pin no-feeds/configuration failure and 101-subscription refusal before network.
- [ ] **Step 2: Run RED.** New evidence tests fail against legacy list returns, lost original text/dates and silent Reddit failures. Identity tests fail for colliding long GUIDs and absent aliases.
- [ ] **Step 3: Implement adapters and identity recognition.** Deduplicate identical configured subscriptions, retain healthy siblings, and return safe reports. Keep partial global cursor withholding as the equivalent retry-safe boundary for this stage; no per-partition persisted cursors. Queue/dedup compare incoming aliases to existing exact IDs but do not insert aliases as new completion keys; two newly observed full IDs sharing a prefix must remain separate.
- [ ] **Step 4: Add offline integration acceptance.** Use real adapters with mocked bounded HTTP: partial collection, restart/retry, original observation recording, completed/pending legacy aliases and uncertain baseline receipt; assert no old payload/favorite rewrites or new model/send calls. Existing create/edit/copy configuration round trips and network-free views remain green. Update adapter capability metadata and document limitations, including finite upstream history, no conditional fetching and unsupported rollback to the truncating RSS adapter.
- [ ] **Step 5: Run GREEN.** New evidence/identity tests and existing RSS/source/story tests pass; full mandatory JS/browser suite has zero skips. Run secret scan, whitespace checks and synthetic doctor (no credentials/provider access).
- [ ] **Step 6: Commit.** `feat: preserve source evidence and report subscription failures`.

## Final Acceptance

- One fresh read-only whole-stage review, with one RED/GREEN fix pass for user-impacting findings.
- Rerun the full mandatory suite on the final committed tree; report live provider availability as unverified.
- Keep the new stage on its development branch until integration is explicitly selected. The previously completed observation stage is independently authorized for local main integration; no production observation-mode activation is authorized.
