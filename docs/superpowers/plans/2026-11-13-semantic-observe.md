# M1 Semantic Observe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Explicitly evaluate saved local candidates and display evidence-backed relationship/difference proposals, without changing delivery.

**Architecture:** Store owns immutable observations and comparison receipts. A pure evidence module constructs bounded, redacted references and validates classifications; a CLI-only evaluator consumes frozen saved candidates, reuses the existing model resolver/transport with scoped accounting, and persists its results. Page reads never evaluate.

**Tech Stack:** Python standard library, SQLite, existing urllib model transport, server-rendered HTML, unittest and existing browser gates. No new runtime dependencies.

**Spec:** `docs/superpowers/specs/2026-11-12-source-expansion-event-updates-design.md` (approved; builder decisions below implement stage 4 only).

## Global Constraints

- Only off/observe; no apply, grouping, notification suppression, favorite migration or production effects.
- Normal dry runs, reads and page renders never invoke models.
- `stories evaluate --observation ID` records a bounded local evaluation; `--allow-model` explicitly authorizes potentially billable semantic work and requires saved `comparison=manual` and activated observe settings.
- Maximum 5 saved pairs per job, independently of retrieval's 20-candidate ceiling. No arbitrary pair endpoint or all-pairs traversal.
- Evidence: 12 excerpts per side, each at most 240 characters, source text examined at most 20,000 characters; request at most 8,000 characters. IDs reference saved excerpts, not model-authored quotes or URLs. Model differences are new_fact/changed_claim/viewpoint, with one reference on each side, at most 5.
- Duplicate semantics require complete, nontruncated article material and fully represented evidence; partial input, absent differences, bad references or unknown chronology for progress yield uncertain. All semantic labels remain proposals, not truth assertions.
- Model attempts are charged before transport; output/optional reasoning use max_tokens=1200, no automatic comparison retry, timeout 20s, bounded response 1MiB. Cache identity includes provider endpoint/model and immutable evidence/extraction/prompt versions, never credentials.
- Comparison daily/hourly calls default 10/3, daily reserved tokens 50,000. Preserve 20 daily/5 hourly overall calls and 100,000 overall tokens for other work; overall daily token ceiling default 1,000,000. Estimates use UTF-8 input byte count plus completion ceiling, not claimed exact token counts. Measured provider totals are separate; unknown attempts keep their reservation.
- All new settings come from config schema and existing CLI writer. No new web write path for billable work.
- Baseline: main 44ab35f, 1004 tests passed on merged main; focused preflight 48 passed. Worktree is externally managed and must be preserved.

## Review Focus

- Concurrent comparison and summary calls must share overall reservation gates; retries/unknown outcomes cannot create free spend.
- A saved proposal can point to a legacy/incomplete baseline; references must never invent missing originals.
- Long multilingual evidence, facts secrets and prompt-injection text must remain bounded, escaped and data-only.
- Disable/re-enable/config changes during comparison may retain paid accounting but cannot present stale results as active proposals.
- Read-only old SQLite databases must not migrate, and write migration must be transactional and preserve original history.

### Task 1: Evidence Contract

**Files:** Create `src/newspipe/story_evidence.py`, `tests/test_story_evidence.py`.

**Interfaces:** `comparison_input(current: dict, previous: dict) -> dict`, `validate_comparison(data: dict, evidence: dict) -> dict`, `local_comparison(proposal: dict, evidence: dict) -> dict`. Input observations use Store's public redacted contract; output has relation, confidence, reason, differences, evidence and algorithm version.

- [ ] Write tests for bounded excerpts, immutable hashes, exact ID citations, duplicate incompleteness, progress chronology, opposing viewpoints, unrelated/uncertain, invalid model structures and unclassified local changes.
- [ ] Run `env PYTHONPATH=src:tests /tmp/newspipe-20261113-mYQhbG/venv/bin/python -m unittest test_story_evidence`; Expected: fail for missing contract.
- [ ] Implement pure evidence construction/validation. No transport, storage, generated summary or synthesized URLs.
- [ ] Run the same tests plus `test_story_matching`; Expected: all pass.
- [ ] Commit `feat: add evidence-backed comparison contracts`.

### Task 2: Scoped Model Spend

**Files:** Modify `src/newspipe/llm.py`, `src/newspipe/state.py`, `src/newspipe/config.py`; extend `tests/test_model_budget.py`, `tests/test_story_settings.py`.

**Interfaces:** `chat_json(..., spend_scope: dict | None=None, retry_empty: bool=True, response_limit: int | None=None)`; ChatResult adds `accounting: dict`. Store adds atomic model reservation/settlement counters; scopes consume existing overall call budget and overall token budget.

- [ ] Write tests for scope ceilings/reserved summary headroom, concurrent spends, cache endpoint isolation, usage measured/unknown/reasoning and disabled retries, response cap, schema defaults and numeric validation.
- [ ] Run scoped budget/settings tests; Expected: failures for new signatures/settings/accounting.
- [ ] Implement shared reservation/settlement under existing usage lock. Cache hits bypass transport and spend; never include credentials in identity or error payload.
- [ ] Run `test_model_budget test_story_settings test_news_pipeline`; Expected: all pass.
- [ ] Commit `feat: bound and account semantic model spend`.

### Task 3: Explicit Saved-Candidate Jobs

**Files:** Create `src/newspipe/story_compare.py`, `tests/test_story_compare.py`; modify Store/Ledger and CLI registration/parser/handler.

**Interfaces:** `evaluate(store, cfg, observation_id, *, allow_model=False, limit=5, now=None) -> dict`; Store `comparison_candidates(ID, limit=5)` returns latest valid frozen receipt/pairs, `record_comparison(receipt)`, `comparisons(ID, after=0, limit=20)` returns immutable evaluation history.

- [ ] Write tests for no-model default/dry rejection, explicit opt-in, candidate cap, no arbitrary/legacy pairs, cache reuse, settings changes during transport, immutable receipts/read purity and v1 transactional migration.
- [ ] Run `test_story_compare`; Expected: failures for missing evaluator/contracts.
- [ ] Implement bounded CLI job outside HTTP/collection paths, pair-local fallbacks and Store-owned migration. Persist job config/activation/baseline/input versions, classification and cost diagnostics; stale jobs are not active proposals.
- [ ] Run `test_story_compare test_story_contracts test_story_state test_story_collection`; Expected: all pass.
- [ ] Commit `feat: evaluate frozen story candidates explicitly`.

### Task 4: Presentation And Offline Acceptance

**Files:** Modify `src/newspipe/webstories.py`, README; extend web/browser tests and create offline fixtures/acceptance report.

**Interfaces:** Detail reads Store comparisons with UI limit 5; relation chips and optional difference details show side-by-side original excerpts and baseline/source links, uncertainty and measured/estimated/cache cost distinctions.

- [ ] Write web tests for relation/difference/cost/uncertainty, escaped evidence and no model calls. Offline fixtures cover multilingual duplicates, progress, opposing viewpoints, vague titles, URL collisions and late arrivals; report actual fixture outcomes, not live accuracy.
- [ ] Run focused tests; Expected: failures for missing display.
- [ ] Implement compact display and schema help; explain explicit potentially billable command, no suppression and retrieval recall limits.
- [ ] Run all mandatory gates: `NEWSPIPE_BROWSER_TESTS=1 NEWSPIPE_REQUIRE_JS_TESTS=1 ... python -m unittest discover -s tests`, `python3 scripts/scan_secrets.py`, `git diff --check`, synthetic doctor/card budget. Expected: zero failures/skips/secrets and valid card budget.
- [ ] Commit `feat: present comparison evidence and acceptance`.
- [ ] One fresh whole-branch review; reproduce/fix Important/Critical with RED/GREEN and complete suite. Commit fixes/acceptance, push feature branch, verify remote SHA. No main merge inferred.
