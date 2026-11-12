# Source Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a compatible, diagnosable acquisition contract that safely supports
new source families without losing pending content or changing delivery policy.

**Architecture:** A focused source-contract module normalizes legacy list
results and new partition-aware results. The existing pipeline remains the
orchestrator, Store remains the persistence boundary, and config.py composes
code-owned adapter metadata as the sole public schema. Acquisition attempts
and successful content watermarks are separate clocks.

**Tech Stack:** Python >=3.11, standard-library dataclasses/typing/unittest,
existing PyYAML and file locking. No new runtime dependency.

**Spec:** `docs/superpowers/specs/2026-11-12-source-expansion-event-updates-design.md`

**Status:** Complete on 2026-11-12. Final acceptance: 767 tests on both Python
3.11 and 3.14 with mandatory browser/JS coverage and no skips. See
`docs/source-foundation-acceptance.md` for review fixes and the remaining scope.

## Global Constraints

- No MCP or Skill integration and no Horizon runtime dependency.
- No tenants, external database service, generic workflow engine or framework rewrite.
- config.py remains the sole public schema/value source; adapters do not render forms.
- No page render, view query or card callback performs collection or model calls.
- Normal dry-run never writes state, calls models or sends cards. Offline
  acceptance must also deny reads of real credentials and all live providers.
- No production configuration changes, second runner/WS, account login, real sends,
  paid collection or knowledge writes during implementation/acceptance.
- Existing native IDs, pushed records, pending queues, callback IDs and favorites
  are not rewritten. New optional capabilities cannot break custom adapters.
- No new network/AI work under the global writer lock. Existing long-held lock
  behavior is not copied into a new collection-result commit boundary.
- Every task has its own red/green verification and commit. Final acceptance runs
  the full unittest suite, browser/JS gates, secret scan and diff validation.

## Review Focus

- Partial subscription success must retain good items without skipping unread
  content from failed subscriptions: Task 2 mixed-result/cursor tests.
- A timeout must not erase acquisition throttling or mark content completed:
  Task 3 attempt-clock/error/restart tests.
- Unknown timestamps, equal timestamps and legacy IDs must not silently become
  new content or invent publication dates: Task 1 identity/time tests.
- A new adapter setting must survive edit/copy/check without being reset by a
  missing form value: Task 4 descriptor/current-value round-trip tests.
- Provider diagnostics and malformed custom results must not leak credentials,
  become quiet success or mutate frozen sends: Tasks 1, 2 and 5 safety tests.

## File Responsibilities

- Create `src/newspipe/source_contract.py`: item/result/report shape, stable new
  native identity helper and safe public diagnostics. No I/O or configuration.
- Create `src/newspipe/source_registry.py`: code-owned adapter descriptions and
  field declarations, with a compatibility description for undeclared adapters.
- Modify `src/newspipe/pipeline.py`: acquisition-result conversion and orchestration;
  preserve plan-before-enrich, acquisition-only and no-fetch notification paths.
- Modify `src/newspipe/state.py`: durable attempt timestamps, source-owned state
  APIs and optional safe collection report storage. Never expose private paths.
- Modify `src/newspipe/scheduler.py`: throttle from attempted acquisition time,
  falling back to the old cursor timestamp when no new record exists.
- Modify `src/newspipe/config.py`: compose metadata and current values/validation.
- Modify `src/newspipe/view.py`: expose safe additive collection diagnostics through
  the existing builder; do not add another dense navigation tab.
- Create focused tests in `tests/test_source_contract.py`,
  `tests/test_source_collection.py` and `tests/test_source_registry.py`.
- Extend `tests/test_notifications.py`, `tests/test_news_pipeline.py`,
  `tests/test_service.py` and `tests/test_view.py` only where behavior crosses their boundary.

## Task 1: Pure Acquisition And Item Contracts

**Files:** Create `source_contract.py` and `tests/test_source_contract.py`.

**Interfaces:**
- Produce `SubscriptionReport(key: str, status: str, fetched: int = 0,
  code: str = "", watermark: float | None = None)`; status is success, empty or
  failure. Public code is a code-owned category, never arbitrary provider text.
- Produce `FetchResult(items: list[dict], reports: tuple[SubscriptionReport, ...],
  reported: bool = True)`
  with computed status success/empty/partial_failure/failure and a safe dictionary
  summary containing counts and report keys/codes, never raw items/credentials.
- Produce `normalize_fetch(value, *, source: str, adapter: str) -> FetchResult`.
  Legacy list/generator results are converted once with reported=False, without
  changing ext_id or dropping unknown item fields. Dictionaries/strings and invalid result shapes
  are rejected with an actionable contract error, not interpreted as item lists.
- Produce `native_identity(adapter: str, native_id: str, *, partition: str = "")
  -> str` using a canonical UTF-8 identity tuple and full SHA-256 digest. It is
  used only by future upgraded adapters, never to rewrite legacy records.
- New-contract timestamps are finite epoch values or explicitly unknown; boolean,
  NaN/infinite and negative values are invalid. Legacy missing/zero publication
  timestamps remain unknown and do not acquire a fabricated publication time.

- [x] Write tests for all four aggregate statuses, legacy item preservation,
  generator single consumption, malformed outputs and JSON-safe diagnostics.
- [x] Write identity tests for differing long IDs with the same 120-character
  prefix, differing partitions, stable repeated inputs and non-ASCII native IDs.
- [x] Write timestamp tests covering unknown/zero and invalid boolean/NaN values.
- [x] Run `python -m unittest discover -s tests -p test_source_contract.py -v`;
  confirm failures are missing contract behavior rather than fixture mistakes.
- [x] Implement the pure module; use no network, SDK imports or Store access.
- [x] Rerun the focused tests and existing item/pipeline tests, then commit
  `feat: add compatible source acquisition contracts`.

## Task 2: Pipeline Result Integration And Safe Watermarks

**Files:** Modify `pipeline.py`; create `test_source_collection.py`; extend
`test_notifications.py` and `test_news_pipeline.py` as needed.

**Interfaces:**
- Consume Task 1 `normalize_fetch` and `FetchResult` at both legacy `_process`
  and `collect_source` acquisition entrypoints. notify_source remains no-fetch.
- Add optional safe `SourceRun.collection` metadata; omit it from serialized
  legacy runs when absent so existing consumers keep their old result shape.
- New reported partial failures retain and durably queue successful items, but
  do not advance the global content watermark. This conservative retry is the
  initial partition-safe policy; per-subscription cursors belong to source-family
  upgrades and must not be assumed to exist in this foundation.
- All-reported failure produces visible acquisition failure with no empty-success
  heartbeat. Frozen remote operations retain their identities/payloads and can
  still follow existing recovery rules; collection failure cannot erase them.
- Complete new-contract results may advance only to a finite safe watermark no
  earlier than the saved cursor and no later than the acquisition clock. Legacy
  complete-list watermark behavior is preserved until that adapter is upgraded.
- A cursor is committed only after good content is durable. Recovery after a
  local write interruption uses existing queue idempotency, never marks a send.
- Snapshot acquisition inputs under a short Store lock, fetch outside that lock,
  and commit idempotently under a short lock. A concurrent completed collection
  cannot have its watermark rolled back by a slower or partial result.

- [x] Add mixed-feed success/failure tests: one good item remains pending, the
  prior cursor remains unchanged, and diagnostics report partial failure.
- [x] Add all-failed, empty-success, malformed-output and safe-diagnostic tests,
  including a provider error containing an authorization-like value.
- [x] Add tests that dry-run writes nothing and notification does not fetch,
  normalize or call a model when consuming existing pending content.
- [x] Add a frozen-send recovery test: new collection reports cannot modify its
  saved target, item membership, payload, send UUID or reserved sequence.
- [x] Add lock-scope and interleaved-result tests: transport runs outside the
  writer lock, while commit never moves an already advanced cursor backwards.
- [x] Run `python -m unittest discover -s tests -p test_source_collection.py -v`
  and watch the new assertions fail before production edits.
- [x] Implement conversion once at the acquisition boundary, retaining current
  filter/dedup/delivery ordering and original items through queue persistence.
- [x] Rerun focused source/notification/reliability tests, then commit
  `feat: preserve partial source results and safe collection cursors`.

## Task 3: Separate Acquisition Attempts From Content Cursors

**Files:** Modify `state.py`, `scheduler.py`, `pipeline.py`; extend source,
notification, service and pipeline tests.

**Interfaces:**
- Produce `Store.record_collection_attempt(source: str, *, at: datetime) -> None`
  and `Store.collection_attempt_age_min(source: str, now: datetime) -> float | None`.
  The latter falls back to cursor_age_min for existing deployments.
- Automatic polling uses attempt age to honor interval_min after errors/partial
  results. Manual force retains its existing explicit throttle bypass; neither
  force nor dry-run bypasses notification gates or begins a model/send operation.
- Record real attempts before transport. Establishing an unforced first-poll
  baseline does not fetch history or create a fabricated transport attempt.
- Existing cursor records remain the incremental-content boundary; attempt
  timestamps survive restart and do not alter pushed IDs or pending membership.

- [x] Test failed and partial automatic collection followed by a second tick
  inside the interval: no second transport call, cursor unchanged, pending retained.
- [x] Test retry after the interval, manual force, dry-run, restart and missing
  new attempt records with legacy timestamp fallback.
- [x] Test an advancing injected clock and timezone-aware/naive fixtures using
  the same execution-clock conventions as current clock/scheduler tests.
- [x] Run focused tests and verify the missing attempt-clock failures.
- [x] Implement Store-owned atomic timestamps and scheduler integration; do not
  add network work under the writer lock or use heartbeat writes as the clock.
- [x] Rerun source, clock, notification and service tests, then commit
  `fix: separate collection throttling from successful content cursors`.

## Task 4: Code-Owned Adapter Descriptions And Settings Metadata

**Files:** Create `source_registry.py` and `test_source_registry.py`; modify
`config.py`; extend config/page tests only for the additive metadata contract.

**Interfaces:**
- Produce `AdapterDescriptor(name: str, label: str, capabilities: tuple[str, ...],
  fields: tuple[dict, ...] = (), optional_dependencies: tuple[str, ...] = ())`.
- Produce `describe_adapter(name: str) -> AdapterDescriptor`, using a code-owned
  registry for built-ins and an explicit legacy-compatible descriptor for custom
  adapter modules. Enumeration/description does not import optional SDKs,
  resolve credentials, fetch samples or execute adapter code.
- Add `adapter_descriptors` metadata to config.describe_schema without removing
  existing fields/adapters. Declared fields use existing schema path/type/default conventions;
  config.source_values and validation compose them from source extras.
- No duplicate conflicting paths, invalid field types, required hidden defaults
  or secret literal values. Credential fields declare environment/reference names.
- This task provides metadata only; source-specific dialogs/settings are delivered
  with their upgraded source families, not a new placeholder plugin editor.

- [x] Test built-in labels/capabilities and custom adapter compatibility without
  optional dependencies or network/credential access.
- [x] Test conflicting descriptor paths/types and every declared field's current
  value, including false, zero, empty lists and preserved unrecognized legacy extras.
- [x] Test load/edit/copy/check round trips with a synthetic code-owned descriptor;
  preserve CLI config's authoritative validation, atomic write and optimistic hash.
  Until adapter-specific editors are delivered, metadata-only custom settings must
  not be represented as supported editable controls; do not claim web acceptance
  from these CLI round trips.
- [x] Run `python -m unittest discover -s tests -p test_source_registry.py -v`
  and verify missing metadata/round-trip assertions fail.
- [x] Implement the registry/schema composition; keep web field definitions owned
  by config.py and do not modify user configuration or trigger discovery calls.
- [x] Rerun schema/config/page/action tests, then commit
  `feat: describe source capabilities through the configuration contract`.

## Task 5: Diagnostics Contract And Foundation Acceptance

**Files:** Modify `view.py`, `README.md`; extend `test_view.py`,
`test_source_collection.py` and offline browser fixtures only if needed.

**Interfaces:**
- Safe collection diagnostics appear through the shared view builder and source
  heartbeat detail. View/page loading is read-only and does not fetch a source.
- Preserve existing status categories when no new reported data exists. Present
  partial failure as partial failure, not quiet success or complete outage.
- A short README section documents legacy result compatibility, structured result
  reports, partial-result watermark behavior and separate attempt throttling.

- [x] Add view tests proving counts/codes are visible while raw provider bodies,
  feed credentials, authentication headers and private state paths are absent.
- [x] Add regression tests for reporting persistence after a later throttle tick,
  and no real provider/model/send access in the fixture preview.
- [x] Run focused tests, implement additive report persistence/presentation and
  concise documentation, then rerun the same tests.
- [x] Run `NEWSPIPE_REQUIRE_JS_TESTS=1 NEWSPIPE_BROWSER_TESTS=1 python -m unittest discover -s tests`.
  Success means all discovered tests pass with no JS/browser coverage skip.
- [x] Run `python3 scripts/scan_secrets.py` and `git diff --check`; both must exit 0.
- [x] Request a fresh read-only review of the foundation branch; fix actual
  contract/cursor/identity regressions and rerun affected tests plus the full suite.
- [x] Commit `feat: expose safe source collection diagnostics` and report this
  foundation as complete, not the entire source-expansion design. Do not merge/push.

## Remaining Subprojects

This is the first independently testable subproject, not a claim that all source
families or story updates are implemented. The approved original sequence stays:

1. Source foundation: this plan.
2. Story observations, exact-copy/local candidate preview and legacy baseline rules.
3. RSS/HN/Reddit upgrades plus GitHub, OSSInsight, Google News, GDELT and Telegram;
   separate source-family commits with real settings dialogs and offline fixtures.
4. Budgeted semantic observe jobs and evidence-backed progress/viewpoint differences.
5. Explicit apply mode, scoped delivery completion, undo and append/history presentation.
6. Optional X/Apify and OpenBB connectors with honest provider/dependency status.
7. Whole-feature review and acceptance of immutable callbacks/favorites and budgets.

Each remaining subsystem gets a focused executable plan after its upstream
interfaces exist; it is not silently dropped or replaced with MCP/Skill work.

## Execution Handoff

Recommended execution is native, sequential implementation in the current
session, followed by an independent whole-foundation review. The pipeline,
cursor and scheduler changes share invariants, so keeping their implementation
context together avoids repeated integration work. A subagent-driven task/review
sequence is also supported if the user prefers that method.
