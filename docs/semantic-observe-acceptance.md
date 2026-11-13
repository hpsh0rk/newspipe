# M1 Semantic Observe Acceptance

## Scope And Boundaries

M1 adds explicit saved-candidate evaluation and read-only relationship/difference presentation. It does not group stories, suppress notifications, alter delivery ownership, append remote cards or migrate favorites/queues. Existing off/observe activation and immutable originals remain authoritative.

`stories evaluate --observation ID` is an explicit local write job. Optional `--allow-model` requires saved manual-comparison permission and activated observe settings. Each job handles at most five already-saved valid pairs, not arbitrary pairs or all-pairs traversal. Normal collection, dry runs, view/CLI reads and page renders never call the comparison model. Dry-run evaluation is rejected before effects.

The model can only return five relationship labels and at most five pairs of IDs from bounded redacted evidence (12 excerpts per side, 240 characters each). The validator reconstructs quotes from the original input; it never trusts model-authored quotes, URLs or summaries. Incomplete/truncated duplicate material, invalid citations, title-only/whitespace evidence, incompatible difference categories and unverified progress chronology become uncertain. Facts omitted/redacted by the safe projection are explicitly incomplete. Semantic conclusions remain proposals, not fact checking; cited claims can still be false.

Attempts reserve call and token envelopes before transport. Comparison defaults are 10 daily/3 hourly calls and 50,000 daily reserved tokens, within the overall model gate and its summary headroom. UTF-8 byte-based estimates, provider-measured totals, unknown reservations, cache hits and budget fallback counts are separate. Completion usage includes reasoning rather than double-counting it. Comparison requests have no implicit retries, a 20-second socket-inactivity timeout and a 1MiB response cap. Model redirects are rejected rather than extending endpoint authorization; deeply nested model objects are rejected safely (depth 32/node 10,000 structural ceilings). Cache identity includes immutable input/extractor/prompt versions and provider endpoint/model, never credentials. Old endpoint-unqualified model receipts are not reused; legacy token-less call ledgers retain a conservative spend envelope.

SQLite comparison history upgrades v1 to v2 only during a transactional write. Reads never migrate or change permissions. Interrupted migration/insertion rolls back. Configuration changes during execution stop additional pairs, preserve accounting and mark the receipt inactive; reads disclose that historical status. Paid requests can outlive an interrupted process; unknown reservation remains even when no comparison receipt was saved.

## Offline Fixture Results

Command: `env PYTHONPATH=src:tests <test-python> tests/story_comparison_fixtures.py`.

These eight annotated fixtures use **canned model responses**, not an actual model. They measure retrieval/validation/accounting wiring, not real-world semantic accuracy. Real model calls, provider charges, actual grouping and suppression: **zero**.

| Case | Observed Result |
| --- | --- |
| Identical complete reprint | Local duplicate proposal; no model |
| Pure English/Chinese translated copy | No lexical candidate; missed association |
| Later development | Cited progress proposal |
| Opposing viewpoints | Cited viewpoint proposal |
| Vague shared titles | No candidate; no unsupported relation |
| Shared URL with distinct commentary | No candidate; no unsupported relation |
| Late arrival with older publication | Uncertain; progress claim rejected |
| Incomplete apparent duplicate | Uncertain; duplicate claim rejected |

Observed in the final v2 fixture run: **0 false duplicate proposals, 1 missed duplicate association, 2 uncertain results, 4 stubbed model attempts**. Simulated provider totals: **1,200 tokens**, conservative request reservations: **19,130 tokens**, unknown usage: **0**. Individual job preparation/transport-stub latency was **0.018..5.913 ms**, overall fixture execution **213.377 ms**. These are local test timings and simulated usage, not provider latency or a pricing estimate. No general accuracy percentage is claimed.

## Verification

Focused evidence/evaluator/accounting/web acceptance: **55 tests passed**. Chromium/WebKit comparison, settings, keyboard, 360/1440 px and no-JS focused acceptance: **3 tests passed**, covering all their engine/viewport subcases. Browser requests were restricted to the temporary fixture origin, server-side provider/model effects denied, and file bytes unchanged by reads. Comparison evidence layout uses existing responsive two-column/one-column rules.

Mandatory full suite: **1,039 tests passed, zero skips, 260.289 seconds**, with `NEWSPIPE_BROWSER_TESTS=1` and `NEWSPIPE_REQUIRE_JS_TESTS=1`. Secret scan: **231 files, zero findings**. Whitespace check: clean. Synthetic doctor: **187/200 card elements**, 13 headroom, no provider effects. The 360 px expanded difference screenshot was visually inspected; document width remained 360 px, quotes stacked cleanly and no controls/links overflowed.

One fresh whole-branch reviewer independently passed 43 focused offline tests and found four Important issues, no Critical or Minor issues. All four were accepted and reproduced RED before the single fix pass: cross-origin bearer disclosure, unsupported citation/category eligibility, omitted-facts completeness, and deeply nested model JSON escaping. Fixes reject all model redirects, tighten evidence/category gates, expose projection completeness, and bound/categorize model JSON. Post-fix focused suite: **145 tests passed**. Evidence and prompt versions are v2 so dependent paid-result identities and diagnostic records remain reproducible.

Final post-fix mandatory suite: **1,044 tests passed, zero skips, 265.028 seconds**, with required JavaScript and Chromium/WebKit gates. Secret scan: **232 files, zero findings**; whitespace check clean; repeated synthetic doctor **187/200 elements** with no provider effects. Four stage commits and the final security/evidence fix commit are separate. No second reviewer or live provider/model/Feishu calls were used.

Review boundary decisions: no live accuracy/availability/pricing/usage-truthfulness claims (real behavior may differ from fixtures); no live Feishu/collection/favorite/deployment checks (deployment-specific faults remain possible); 20 seconds is an inactivity transport timeout, not an absolute wall deadline (slow continuously responding endpoints may prolong a CLI job; interrupting the CLI is safe for delivery, while its model reservation remains charged).

## Remaining Limits

- Local lexical retrieval misses pure translations without shared terms. No remote embeddings, vector service or all-pairs model bill was added.
- Bounded excerpts can omit a decisive fact; material beyond the evidence window is not treated as full semantic-duplicate proof. Original history remains available for inspection.
- Quotation/reference validation cannot prove that a model correctly understood an event or that a source is true. Proposals require inspection before any future apply mode.
- Comparison jobs are explicit CLI processes, not a scheduled automatic service or a billable web action. Reads cannot start or rerun them.
- The transport timeout is not an absolute job deadline. A continuously responding endpoint may take longer than 20 seconds; no background job watchdog is claimed.
- History is append-only; no new retention/deletion policy, progress delivery or correction/undo workflow is part of M1.
- The provider's token usage may be absent or unreliable. Unknown outcomes retain conservative reservations; no currency estimate is possible without provider pricing.
