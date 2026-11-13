# Google News Source Acceptance

## Delivered Boundary

Native `google_news` searches use the fixed `https://news.google.com/rss/search` endpoint. No RSSHub, Horizon, SDK or credentials are required. Query and locale parameters are URL-encoded data; redirects, Google article decoding and publisher-page fetching are disabled. Each request has a 20-second socket timeout and 2 MiB wire/decoded response bound. Searches run sequentially under the existing shared source-worker deadline and bounded transport exits.

The code-owned descriptor supplies the source dialog and CLI validation:

- `settings.queries`: empty default; collection requires 1..25 nonblank strings, at most 1024 characters each. Trim and exact deduplication preserve case/search semantics. ASCII/C1 controls and invalid Unicode surrogates are rejected before transport. Dialog input is one query per line; commas and Chinese enumeration commas remain query text.
- `settings.language`: default `zh-CN`; a bounded language token, not a strict article-language filter. `settings.region`: default `CN`, two uppercase ASCII letters. Optional advanced `settings.edition` must use the same region prefix. Ordinary editions are derived from region/primary language, with explicit simplified/traditional Chinese mappings. Accepted syntax does not prove that Google serves every locale combination.
- `settings.lookback_days`: integer 1..90, default 7. Each request adds `when:Nd`; explicit unquoted `when:/after:/before:` is rejected rather than silently combined. Only double-quoted phrases hide literal operators; apostrophes in names are not phrase boundaries.
- `settings.max_results`: integer 1..100 per query, default 50. Local filtering applies lookback and finite nonnegative `since` to dated records; equal timestamps remain eligible. Known dates sort newest first, unknown dates remain unknown and last in provider order. Future `since` never creates a negative request window.

These are rolling, finite search results, not exhaustive history. Every completed query reports `window_limited=true`, no watermark and `window_kind=search_window` or `result_cap`. Integration tests verify the actual Store cursor remains unchanged, including apparently non-full/empty responses. Successful sibling searches survive query failures; errors and exported diagnostics are categorical, not provider/query/exception echoes.

## Identity And Evidence

Identity is the versioned full hash of the feed's native GUID, with the original permalink as fallback. A shared headline or publisher homepage cannot merge native articles. The same article found through multiple queries keeps one identity and observation lineage. The observation partition is the stable Google News corpus; query/locale/limit diagnostic keys remain discovery metadata, not article identity. Changing search settings does not create a new original version; meaningful article changes still do. Existing RSS identities are not migrated, rewritten or aliased; changing an existing source's adapter is not a historical deduplication migration.

The aggregator permalink remains the article link. RSS/Atom publisher name/homepage are separately typed attribution, not an author assertion, original article link or linked-fetch target. Publication dates and bounded original excerpts remain evidence; no collection-time dates are fabricated. `content_kind=article`, `source_text_complete=false`: an excerpt cannot qualify for confident complete-article copy suppression.

Native-ID retries use the existing idempotent queue, with no additional AI calls. This stage does not enable semantic cross-source suppression, event-history append delivery, viewpoint/delta analysis or knowledge ingestion.

## Interaction And Safety

The existing create/edit dialogs show queries and locale controls; edition and bounds remain under advanced controls with hover/focus/tap help. RSS-only feeds/HTTP2 are hidden and not submitted for Google News. There is no new permanent settings tab or source-specific policy-parameter override. Sources select an already managed notification policy and default to disabled on creation.

Validation, preview, patch and copy use existing CLI handlers, source revisions, optimistic hashes and origin/token/actions gates. Validation and configuration rendering never collect. Failed validation focuses the relevant control and preserves edits; no-JS POSTs return opaque one-use drafts. Adapter round trips restore raw multiline text, including leading blank lines, from bounded 32 KiB snapshots. Snapshot fields remain presentation state, never additional CLI write parameters. Restored text is escaped and security controls retain freshly rendered values. Literal credentials accidentally pasted into another adapter's reference field stay cleared.

Multiline list controls may use the full snapshot budget rather than the old 16 KiB scalar-control budget: all 25 maximum-length ASCII queries can validate/save through HTTP. The total serialized adapter snapshot remains limited to 32 KiB and the URL-encoded request to 64 KiB. Large Unicode/punctuation queries or additional inactive drafts may hit those byte limits first; reduce the form payload or use CLI configuration. A snapshot-limit error does not disconnect the no-JS error page: direct raw edits remain escaped and editable, but an over-budget inactive snapshot is not retained.

## Known Limits

Google News controls result coverage, retention, ranking and latency. Increasing the rolling window or local cap cannot guarantee all articles, historical replay or recovery after a long outage. No per-query persistent cursor or conditional HTTP cache is added. Unknown-date records are retained rather than guessed. Queries share the source acquisition timeout; this is not an overall collection/AI/send deadline or a permission sandbox.

Offline fixtures prove boundary behavior and UI flows, not live reachability, locale support or provider query interpretation. No real provider/model/Feishu call, source activation, production configuration edit, runner/WS start, send or knowledge write occurred. Live acceptance requires a separate instruction with desired queries and locale; it must not imply exhaustive coverage from a successful response.

## Verification

Task 1: 160 focused source/contract/evidence/worker tests passed; the required full JavaScript/browser gate passed 988 tests with zero skips. Task 2 initially exposed comma splitting, missing textarea/draft restoration, missing CLI edition-region validation and misleading pagination wording. Browser traces additionally exposed an incorrect test precondition and a reproducible no-JS inactive multiline draft loss. The tests now require the specific validation error, and raw snapshots restore without losing leading blank lines. Apostrophe/window-operator regressions also failed before the grammar fix. Post-fix focused source/form/draft tests: 39 passed; both new real-browser tests passed across their six engine/viewport/no-JS scenarios.

Pre-review full acceptance: `NEWSPIPE_BROWSER_TESTS=1 NEWSPIPE_REQUIRE_JS_TESTS=1 PYTHONPATH=src python -m unittest discover -s tests`: **999 tests passed, zero skips, 274.239 seconds**. The suite includes frozen notification payload/retry, original callback, queue-ID and favorite-receipt regressions. Secret scan: **223 files, zero findings**; `git diff --check`: clean.

Synthetic doctor used four sources, including a disabled Google News source: **187/200 card elements**, 13 headroom. Chromium screenshots were inspected at 1440/360 px, including advanced controls; no horizontal document overflow, unexpected JavaScript errors or escaped/provider requests were observed. These are offline fixture results, not live or deployment claims.

One fresh whole-branch read-only review found two Important issues: multiline draft scalar limits/no-JS recovery and query-dependent observation identity. Both were reproduced in failing real-HTTP/Store regressions and fixed in one pass. The review's minor acquisition wording issue was re-graded as Important because every completed search gave contradictory pagination guidance; real acquisition regressions failed before that fix. No second reviewer or live operation was used; no minor finding was deferred.

Final fix-pass verification: **94 focused tests passed**, including the new real-HTTP/Store/acquisition regressions and Chromium/WebKit scenarios. The mandatory full JavaScript/browser gate passed **1003 tests, zero skips, 281.152 seconds**. Final secret scan: **223 files, zero findings**; whitespace check: clean. Post-fix offline doctor/screenshots repeated the four-source, **187/200-element** card budget with no escaped browser requests or provider effects. Both implementation tasks and the review fixes are committed separately; the stage commits include `5bc3230` and `733f1b9`.

## Main Integration

Developed on `feat/google-news-source`, based on `main` at `fdf73c4`. The user subsequently authorized merge and push; the feature was merged into `main` as `964e2a6`. The externally managed feature worktree is preserved.

The first two full runs on merged main each failed the WebKit no-JS group-list flow (1003 tests, one failure per run). Ten isolated repetitions passed, so they were not treated as proof of a fix. Traces showed native navigation snapshots intercepting clicks; browser CSSOM assertions then confirmed that `prefers-reduced-motion` still left cross-document navigation set to `auto` in both engines. The reduced-motion rule now explicitly sets navigation to `none`, with no sleeps, forced clicks, retries or weakened assertions. The new browser assertion observed RED before the fix, then passed together with the original failing test and its preceding pagination scenario.

Final merged-main verification: required JavaScript/browser gates **1004 tests passed, zero skips, 249.244 seconds**. Secret scan: **223 files, zero findings**; `git diff --check`: clean. No production configuration, source activation, provider/model/Feishu call or knowledge write was performed by the integration workflow.
