# Source Worker Deadlines Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Terminate over-budget source acquisition without losing completed subscription results or moving a failed source's cursor.

**Architecture:** A short-lived acquisition subprocess imports only the requested adapter and receives its settings over stdin. Bounded JSONL results cross stdout; all state, model, channel and inbound work stays in the parent. RSS/Reddit yield subscription results as they finish, while legacy adapters retain their list/generator contract through a single result frame.

**Tech Stack:** Python 3.11+, standard-library subprocess/process groups, JSON, ContextVar and unittest; existing browser gates.

**Spec:** `docs/superpowers/specs/2026-11-12-source-expansion-event-updates-design.md`, Source Capability Contract (bounded worker jobs) and Builder Decisions (numeric bounds and worker implementation).

## Global Constraints

- Single-user system; no Horizon, MCP/Skill, extra service runner/WS, live provider/model/send or production configuration changes.
- Preserve native identities, completed receipts, pending entries, frozen deliveries and favorites. Workers never receive a Store, service configuration or knowledge-writing capability.
- Default acquisition budget 90 seconds per source; `fetch.timeout_seconds` accepts integer 1..600. This is not a deadline for filtering/enrichment/delivery or the whole multi-source round.
- Process startup, import, acquisition, parsing and worker normalization share one monotonic budget. Termination gets a separate bounded 2-second cleanup allowance; no hard real-time OS scheduling guarantee.
- Input at most 1 MiB, result stream at most 16 MiB and 100 completed partition frames. Stop on output overflow; preserve previous complete frames with an incomplete failure diagnostic.
- No raw exception/provider text or child stdout/stderr logging enters diagnostics. Settings travel over stdin, never argv or persistent temporary files; child output is reserved for the protocol.
- Retain completed partition evidence on timeout/crash/invalid or interrupted trailing frame; add a categorical worker failure and withhold the global cursor. No automatic immediate retries or historical replay.
- Acquire outside writer locks and outside callback/SSR/view paths. Parent-owned state writes and delivery semantics stay unchanged.
- Existing custom adapters execute in a subprocess with the same settings and environment; list/generator output remains supported. In-process monkeypatches/non-importable callables are not a production adapter interface.
- Manual edits use apply_patch. Commit each task, mandatory JS/browser gates and secret scan before completion.

## Review Focus

- A completed feed followed by a stalled feed: completed evidence survives; the cursor stays unchanged and retries remain idempotent.
- A worker exits without a terminal receipt or with a truncated frame: never report empty success or accept a partial JSON object.
- A noisy/crashing adapter: stderr, prints and exception secrets cannot leak; input/output remain bounded.
- Child descendants holding pipes after leader exit: timeout kills the process group, not just the direct child; cleanup remains bounded.
- Different concurrent caller budgets: ContextVar scope restores on every exit and never changes another acquisition's budget.

---

### Task 1: Bounded Acquisition Worker

**Files:**
- Create: `src/newspipe/source_worker.py`
- Create: `tests/source_worker_fixture.py`, `tests/test_source_worker.py`

**Interfaces:**
- Produces: `source_worker.Adapter(module: str).fetch(cfg=None, since=None) -> FetchResult`.
- Produces: `source_worker.limit(seconds)` context manager, scoped default 90 seconds; validation rejects bool, nonfinite or nonpositive budgets.
- Worker consumes: an importable module with `fetch(cfg=None, since=None)` and optional `fetch_partitions(cfg=None, since=None)` yielding FetchResult.
- Worker produces: independently validated result frames and an explicit terminal receipt; exceptions become existing categorical codes.

- [ ] **Step 1: Write tests.** Use importable offline fixture adapters to pin legacy identities and generator results, successful-empty, explicit configuration failure, partial evidence before timeout, CPU stalls, crash/truncated terminal, stdout/stderr secret scrubbing, oversized output and descendants. Assert consumer-visible results and subprocess disappearance, not mock calls.
- [ ] **Step 2: Run RED.** `python -m unittest discover -s tests -p 'test_source_worker.py'` fails because the worker API is absent.
- [ ] **Step 3: Implement worker.** Use subprocess stdin/stdout, no shell, `start_new_session=True` and monotonic communicate timeout. Kill the process group on timeout, reap within cleanup allowance, and close pipes on all paths. Child output is redirected at OS descriptor level except a dedicated bounded protocol writer. Normalize frames before emitting and validate them again in the parent; no eval/pickle.
- [ ] **Step 4: Run GREEN.** Worker tests pass, then mandatory full suite passes with zero skips.
- [ ] **Step 5: Commit.** `feat: add bounded source acquisition workers`.

### Task 2: Pipeline, Subscription Streaming And Schema Integration

**Files:**
- Modify: `src/newspipe/pipeline.py`, `src/newspipe/config.py`
- Modify: `src/newspipe/adapters/rsshub.py`, `src/newspipe/adapters/reddit.py`
- Test: `tests/test_source_worker.py`, `tests/test_source_identity_upgrade.py`, `tests/test_rsshub_settings.py`
- Document: `docs/source-worker-deadlines-acceptance.md`, `README.md`, `AGENTS.md`

**Interfaces:**
- Consumes: Task 1 Adapter and limit scope. Pipeline `_adapter(name)` returns the source worker proxy; existing unit seams can still supply an adapter object.
- Produces: schema-owned `FetchCfg.timeout_seconds`, current value via `source_values`, round-trip via source CLI/forms and read-only config summary.
- Produces: RSS/Reddit `fetch_partitions(cfg=None, since=None)` yielding one existing FetchResult per validated subscription; public `fetch` aggregates these results unchanged.

- [ ] **Step 1: Write tests.** Pin configuration default/range and create/edit/copy round trips. Use real importable workers and offline fixtures through collect and legacy run to verify timeout diagnostics, preserved cursor/queue/frozen payload/favorites, retry idempotence, dry-run/no view/notify acquisition and acquisition outside locks. Old transport-mocked adapter integration tests explicitly substitute the direct adapter at the existing `_adapter` boundary; actual process behavior has separate real tests.
- [ ] **Step 2: Run RED.** Worker integration tests fail against unbounded pipeline and missing setting/partition interface.
- [ ] **Step 3: Wire the boundary.** Replace only `_adapter` implementation and scope `_acquire`; no parent state transitions move into the worker. Extract RSS/Reddit partition generators without changing item identities, report watermarks, validation or ordering. Add advanced schema field with clear per-source/shared-subscription budget help.
- [ ] **Step 4: Run GREEN.** Focused source/worker/config tests and mandatory full suite pass with zero skips. Run secret scan, diff check and network-denied synthetic doctor through integration tests.
- [ ] **Step 5: Commit.** `feat: enforce per-source acquisition deadlines`.

## Final Acceptance

- One fresh read-only whole-stage review and one RED/GREEN fix pass for user-impacting findings, no per-task agents or re-review.
- Rerun mandatory full suite on final committed tree, secret scan and whitespace checks. Record output/cleanup/legacy adapter limits honestly.
- Keep this new stage on its branch until integration is selected. The current request authorizes pushing the previously finished source-evidence stage, not future commits or production activation.
- Native GitHub collection is the next independently verifiable source-family stage; semantic comparisons and event append remain later.
