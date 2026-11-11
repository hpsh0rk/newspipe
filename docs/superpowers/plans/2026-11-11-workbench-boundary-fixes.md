# Workbench Boundary Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct the four reproduced destination, clock, local-consumption and unsaved-edit defects without expanding product scope.

**Architecture:** Keep existing CLI/action boundaries and durable operation identity. Separate card destination lifetime from operation recovery, share an advancing invocation clock across nested collection/delivery, and track client edits by revision rather than clearing them on any successful response.

**Tech Stack:** Python 3.11+, unittest, server-rendered HTML, local vanilla JavaScript, optional Node for behavioral asset tests.

**Spec:** `docs/superpowers/specs/2026-11-11-notification-workbench-design.md`; four offline-confirmed review findings from 2026-11-11.

## Global Constraints

- One user and one configured destination; no routing engine or database.
- Pending remote work retains its exact identity, payload and destination; no force resend or invented receipts.
- No live credentials, network effects, IM clients, deployment, merge or push during verification.
- All edits stay on the existing `fix/reliability-contracts` checkout; commit every stage.
- Existing global gates, legacy semantics, protected Web actions and supported Python versions remain compatible.

## Review Focus

- Destination changes with both completed and unfinished append cards: preserve old callback deep links and pending recovery, but never append new content to an old destination.
- Collection, lock waits and model processing crossing time boundaries: gates use current invocation time and confirmation uses the full elapsed duration.
- Long operation crossing midnight: daily quotas and receipts use the proper date, independently of the batch/slot identity.
- Collect-only with retained quiet hours: local consumption does not inherit remote-notification gates; minimum items still applies.
- Input changes during a pending successful save: only the submitted revision is acknowledged; new input still warns before navigation.

### Task 1: Destination-Safe Card Lifetime

**Files:** Modify `src/newspipe/pipeline.py`, `src/newspipe/service.py`; test `tests/test_notifications.py`.
**Interfaces:** Keep public signatures; a completed card cannot be reused across destinations. Preserve the previous batch at its existing callback path and reserve a new batch slot when needed.

- [x] Add tests for completed append destination changes, retained old batches, subsequent new-target appends and unfinished original-target retries; run notification tests and observe the destination regression fail.
- [x] Rotate completed destination-mismatched batches before reuse, retaining unfinished work unchanged; rerun tests, expected PASS.
- [x] Run full unittest suite and diff check; commit `fix: isolate notification cards across destination changes`.

### Task 2: Advancing Invocation Clock

**Files:** Create `src/newspipe/clock.py`; modify `src/newspipe/service.py`, `src/newspipe/pipeline.py`; test `tests/test_notifications.py`, `tests/test_clock.py`.
**Interfaces:** An invocation-scoped datetime anchor plus monotonic elapsed time is shared by nested pipeline calls; public `now` injection remains supported. Refresh gates after collection/lock waits and before remote effects; confirmation derives from the same advancing clock.

- [x] Add deterministic elapsed-time tests for slow collection, late confirmation/minimum gap, quiet-window crossing during enrichment, digest expiration, midnight and scope isolation; run them and observe the timing regressions fail.
- [x] Implement the scoped clock and refresh scheduling/delivery/retry checks at effect boundaries; rerun timing and scheduler tests, expected PASS.
- [x] Run full unittest suite and diff check; commit `fix: advance notification clocks through collection and delivery`.

### Task 3: Local Consumption Without Notification Silence

**Files:** Modify `src/newspipe/policy.py`; test `tests/test_notifications.py`, `tests/test_news_pipeline.py`.
**Interfaces:** `plan()` keeps min-items/card-independent local capacity, but notification silence only constrains remote effects for collect-mode sources.

- [x] Add collect-mode tests retaining source/global quiet hours, pause and zero quotas; verify local batches consume pending without enrichment/send, and min-items still defers. Observe the quiet regression fail.
- [x] Exclude local collect consumption from notification silence; run full suite, expected PASS.
- [x] Run diff check; commit `fix: keep notification silence out of local collection`.

### Task 4: Revision-Safe Save Feedback

**Files:** Modify `src/newspipe/webassets.py`; create `tests/test_workbench_javascript.py`; update this plan's execution record.
**Interfaces:** Input/change events increment a per-form revision. Success clears dirty state only for the submitted revision. No-JS behavior and action envelopes remain unchanged.

- [ ] Add Node behavioral tests using the actual code-owned asset and controlled pending responses; test edited/unchanged saves, failed saves and dry previews. Observe the in-flight edit regression fail.
- [ ] Keep new edits dirty after an old request succeeds; run behavioral and SSR tests, expected PASS.
- [ ] Run full suite on Python 3.11/3.12/3.13, secret scan, diff and JS syntax checks; dispatch one fresh scoped whole-change reviewer and correct Important findings with RED/GREEN.
- [ ] Record evidence and remaining browser/live-platform limitations; commit `fix: preserve edits made while configuration saves are pending`.
