# Existing Source Evidence Quality Acceptance

## Delivered Scope

- RSS 2.0 and namespaced Atom parsing share one bounded evidence parser. Inline HTML/XHTML, entities, alternate permalinks and XML base are parsed without collecting linked articles.
- Original text/markup are retained separately from short display summaries. Text/markup/title limits are 20,000/40,000/500 characters with explicit clipping. Inline feed content alone is not proof of a complete article.
- HN keeps its original post permalink separate from a referenced article, original discussion text, native ID and engagement metadata. Supplied authors are retained as bounded source facts for public evidence; metrics/timestamps do not change content fingerprints.
- Reddit keeps native post identities, original material, reported publication/update dates and categorical per-community failures instead of silently returning quiet.
- All three adapters return FetchResult with successful-empty, success, partial-failure or total-failure outcomes. Credentials/permission/rate limits/invalid data/transport limits are categorical; no provider body or raw exception is exported.
- Acquisition is limited to 100 subscriptions per source, 1000 entries per feed, 2 MiB of wire and decoded bytes per response and 5 redirects per exit, with the existing two-exit transport bound. Adapter-level immediate retry loops are removed; normal scheduled/manual retries remain available. Both transports close redirect responses without draining their bodies and cap raw chunks before bounded gzip/deflate decoding. Unknown/incomplete encodings fail explicitly; source requests ask for identity encoding. HTTP/2 does not inherit unrelated environment proxies.
- Full RSS GUIDs no longer collide through truncation. Only long v2 IDs carry their strictly validated old 120-character prefix, used against unversioned legacy completed/pending IDs, never against another new item in the batch. All new RSS items/receipts carry the v2 generation marker, so a new exactly-120-character native ID cannot be mistaken for an old truncation. New completion records never register a truncated alias as another completed ID.
- Alias-only historical completions become explicit missing-material migration baselines, without rewriting old originals or claiming duplicate/progress classification. Frozen deliveries and favorite intent remain unchanged.

## Offline Acceptance

Fixtures use temporary configuration/state and synthetic transport only. Tests cover HTML/XHTML, date/identity edge cases, bounded bodies/redirects/XML declarations, raw evidence, model-free fingerprint stability, partial acquisition, restart/retry, unchanged old queue bytes, frozen payloads and favorites. Synthetic doctor stubs credential availability and denies network access; views do not collect.

```sh
NEWSPIPE_REQUIRE_JS_TESTS=1 NEWSPIPE_BROWSER_TESTS=1 python -m unittest discover -s tests
python3 scripts/scan_secrets.py
git diff --check
```

No live source/model/Feishu calls, paid collection, knowledge writes, production configuration changes or extra runner/WS are part of this acceptance. Backend parsing support does not assert current provider/IP availability.

2026-11-13 implementation verification: 893 tests passed with mandatory JavaScript, Chromium and WebKit gates and zero skips. Secret scan and whitespace checks passed. Synthetic doctor and frozen-delivery/favorite preservation are included in the offline integration tests.

## Independent Review

One fresh read-only review found eight user-impacting gaps: mixed-length GUID aliases, decompression allocation, redirect-body draining, child XML bases, per-item whole-ledger copies, lost attribution, technical-title text loss and configuration-status drift. Attribution/title/status findings were initially Minor but regraded Important because they erase evidence or misdirect recovery. Each received reproduced failing tests and fixes; no second reviewer was dispatched.

The fix pass also covers new exactly-120-character completion/pending IDs across runs, gzip member/truncation handling, HTTP/2 explicit proxy selection and stripping caller-supplied credential headers on cross-origin redirects in both transports. Author attribution is a source fact, not an engagement metric. Plain RSS title character data is preserved literally; Atom typed HTML/XHTML retains its declared extraction semantics.

Review rulings: live provider/IP availability remains unverified because no live access was authorized; richer modes/new sources/semantic append are deliberately later stages (the overall goal is not finished); rollback to truncating RSS remains unsupported without migration because it cannot interpret new full completion identities. No deferred minor findings remain in this stage.

2026-11-13 final fix verification: 905 tests passed, zero skips, with JavaScript and both browser engines mandatory; secret scan and whitespace checks clean.

## Deliberate Limits And Follow-Ups

- Generic RSS remains unknown-kind, HN/Reddit remain posts, all with completeness false. This stage improves evidence honesty, not confident article-copy matching or semantic event understanding.
- Partial results withhold the global cursor, rather than introducing persisted per-subscription cursors. Successful siblings can be fetched again; queue/history retries are idempotent. Content already missing from a finite upstream feed/search window cannot be recovered by this cursor strategy.
- HN remains front-page/30 hits/last 24 hours; Reddit remains subreddit top/day/25 posts. Conditional fetching, additional selections/user feeds/comments, authenticated access and new families are follow-up work.
- Unknown HN publication dates remain unknown and are retained rather than fabricated or classified as old. Network timeouts are socket budgets, not absolute overall job deadlines; overall worker deadlines remain follow-up work.
- Same-date RSS article edits with no upstream update marker cannot be detected through publication-time filtering alone. Atom update times aid selection but are not a guarantee that providers report every edit.
- A legacy truncated RSS identity cannot establish which full GUID was historically consumed. All matching full candidates avoid replay and receive an uncertain observation baseline; a pre-baseline update may be missed.
- Do not roll back to the old truncating RSS adapter without an explicit identity migration: it cannot interpret new full completion IDs and can replay/merge content incorrectly.
- No comparison-model token use, automatic suppression, apply/undo, appended event notifications, automatic deletion or history rebuild is introduced. Immutable history still needs deployment backups/capacity monitoring.
