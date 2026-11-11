# Notification Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate acquisition from reusable notification policies and provide a clear, single-user source/notification workbench without changing existing sources implicitly.

**Architecture:** Keep configuration validation in config.py, pure policy resolution in policy.py, durable accounting in state.py and one delivery/recovery path in pipeline.py. CLI/edit remain the only writers; server-rendered pages use local progressive enhancement rather than a SPA.

**Tech Stack:** Python 3.11+, PyYAML, unittest, Python SSR, local vanilla JavaScript and CSS.

**Spec:** docs/superpowers/specs/2026-11-11-notification-workbench-design.md

## Global Constraints

- One user, one existing oc_ destination; no accounts, arbitrary rule language, automatic migration or external assets.
- Preserve legacy semantics unless explicitly configured global gates constrain them.
- Global pause/quiet/cap/gap are hard gates; source exceptions never bypass them.
- Notification operations include confirmed card updates and are idempotent by operation ID.
- All writes use validated CLI handlers, atomic YAML edits, dry-run/base-hash and the Result envelope.
- Preserve actions switch, same origin and process token; no real sends, credential probes, queue acknowledgements or extra IM connections in verification.
- Unknown remote results beyond the idempotency horizon remain blocked; never invent a receipt or force a new send identity.
- Commit each tested stage. Final suite: Python 3.11/3.12/3.13, secret scanner, diff check and temporary-config doctor/browser checks.

## Review Focus

- Duplicate YAML keys, false/zero/empty overrides and malformed windows must fail or retain their exact meaning (Task 1).
- Historical incomplete source attribution and interrupted remote success must not reset budgets or resend (Task 2).
- New content, source disablement and unrelated edits must not reset exhausted delivery attempts (Task 2).
- Stale hashes, adapter extension keys and collapsed advanced settings must not lose edits or unrelated YAML (Task 3).
- Network-uncertain writes, cross-origin JSON requests and hostile labels must not cause retries, unsafe rendering or token bypass (Tasks 3/4).

---

### Task 1: Policy Configuration and Resolution

**Files:** Create src/newspipe/policy.py and tests/test_policies.py; modify src/newspipe/config.py.

**Interfaces:** Produces NotificationPolicy, NotificationGlobals, NotifyBinding; resolve(cfg, source) -> EffectiveNotification | None with values/origins; effective_source(cfg, source) -> SourceConfig for the existing delivery implementation. Config gains policies/notifications; SourceConfig gains notify. describe_schema exposes policy/global field metadata separately from legacy source fields.

- [x] Write tests for defaults, explicit overrides (zero/empty/false), unresolved references, digest slots, nonfinite/negative limits, duplicate keys, quiet/time ranges, empty registries and legacy non-migration.
- [x] Run `uv run --no-project --with PyYAML --with pycryptodome python -m unittest discover -s tests -p test_policies.py`; expect failures because policies are not parsed/resolved.
- [x] Implement strict parsing and pure resolution; preserve absent-vs-explicit overrides and existing adapter extras. Defaults: immediate/card, no slots/quiet, 12 operations, gap 0, bypass false. Require stable ASCII IDs; validate all effective bindings and all references including disabled sources.
- [x] Run focused tests then full suite/scanner/diff check; expect all passing.
- [x] Commit `feat: add reusable notification policy configuration`.

### Task 2: Collection, Notification and Accounting

**Files:** Modify src/newspipe/pipeline.py, delivery.py, state.py, service.py; create focused notification scheduling tests.

**Interfaces:** Consumes Task 1 resolution. Produces collect_source(cfg, store, name, *, now, force, dry) -> SourceRun and notify_source(cfg, store, name, *, now, slot, bypass_clock, dry) -> SourceRun; source_budget_state(date, source) and source-attributed note_card(..., source=...). Service progress tracks acquisition separately from notification; Store owns completion receipts used by both CLI and service without lock inversion.

- [ ] Write tests for poll/digest separation, pause while collecting, immediate pending without fetch, collect-local consumption, separate source counters under global limits, and legacy behavior.
- [ ] Run focused tests; expect new entrypoints/accounting behavior missing.
- [ ] Extract shared delivery of pending/unfinished work without duplicating render/send/recovery. Persist acquisition only after pending is durable. Use sequenced immediate-card batches, stable append batches and existing horizon protection.
- [ ] Write/run failing fault tests: remote success receipt repair while paused/disabled, incomplete historical attribution fails closed, digest catch-up bounded retries, immediate attempts survive new content/config edits.
- [ ] Implement per-source/global idempotent ledgers, hard global gates and independent service progress (15-minute digest catch-up, 3 attempts, >=60 seconds apart, one new immediate operation/source/tick).
- [ ] Run focused/full suite/scanner/temporary doctor/diff check; expect passing, no sends.
- [ ] Commit `feat: separate source collection from notification delivery`.

### Task 3: Validated Editing and Action Contracts

**Files:** Modify src/newspipe/edit.py, yamlblocks.py, cli.py, actions.py, inbound.py; create tests/test_notification_contracts.py.

**Interfaces:** Consumes Tasks 1/2. Produces source patch, policy list/show/set/remove, global set, read-only notification preview, collect/notify/protected retry CLI commands and matching code-whitelisted Web translations. JSON HTTP actions return the same Result; HTML returns only to allowlisted own-page routes.

- [ ] Write failing tests for recursive patch preservation/removal of inherited overrides, referenced policy/slot rejection, empty mappings, optimistic concurrency and dry-run changed semantics.
- [ ] Implement atomic target-block edits with candidate config validation; retain source set replacement semantics. Return affected-source effective changes for policy edits.
- [ ] Write failing tests for preview zero network/model/writes, separated manual collect/notify, protected retry, JSON origin/token/actions gates and own-page redirects.
- [ ] Implement CLI/CONTRACT/parser handlers first, then actions as argv translation only and HTTP result negotiation.
- [ ] Run focused/full suite/scanner/diff check; expect passing.
- [ ] Commit `feat: expose notification editing and preview contracts`.

### Task 4: Source and Notification Workbench

**Files:** Modify src/newspipe/view.py, style.py, inbound.py; create focused workbench renderer/assets module(s); update specs/design/DESIGN_SYSTEM.md and view/config/history tests.

**Interfaces:** Consumes Task 3 action/read contracts and Task 1 schema. Produces /notifications, shared five-route navigation, grouped source editor with inherit/custom controls, policy/global forms, read-only effective previews and local /assets/workbench.js.

- [ ] Write failing SSR tests for navigation, empty states, search/filter/deep links, policy binding/clone/edit, effective origins/pending reasons and no-JS core forms.
- [ ] Implement shared shell and focused renderers preserving blue/slate design and escaped labels. No automatic source migration/activation or setup sends.
- [ ] Write failing asset/security tests allowing only the code-owned same-origin script, JSON envelope feedback, safe text updates and no persistence/external resources.
- [ ] Implement progressive form enhancement: pending state/duplicate prevention, errors retain inputs/open sections/scroll, safe result rendering, current hashes after writes, explicit conflict reconciliation, unsaved navigation warning and destructive confirmations.
- [ ] Run focused/full suite/scanner/diff check; expect passing.
- [ ] Commit `feat: rebuild source and notification workbench`.

### Task 5: Acceptance, Documentation and Review

**Files:** Update README.md, AGENTS.md, relevant docs and regression tests; corrections limited to approved scope.

**Interfaces:** Consumes all earlier public contracts. Produces verified acceptance and truthful operational documentation.

- [ ] Run a temporary-directory browser service (inbound none, actions enabled, no real network effects); verify 360/768/desktop, keyboard focus, no horizontal page overflow, reduced motion, no-JS forms, previews and failed/stale submissions retaining edits.
- [ ] Document configuration examples, legacy conversion/unbind effects, budget scopes, manual operation differences, unresolved send investigation and route/action contracts.
- [ ] Run full suite on Python 3.11/3.12/3.13, scanner, temporary-config doctor and diff check; expect passing.
- [ ] Dispatch one fresh whole-feature reviewer per executing-plans; fix important findings with RED/GREEN and rerun full verification. Do not deploy/merge/push.
- [ ] Commit `docs: verify and document notification workbench` and report stage commits, verification and real-runtime limitations.

## Execution Record

Native execution continues in the existing fix/reliability-contracts checkout, as in the preceding reliability stages. The user approved the authoritative design and explicitly requested implementation; no redundant design/plan approval is introduced. This plan was self-reviewed for specification coverage and shared interface consistency before execution.
