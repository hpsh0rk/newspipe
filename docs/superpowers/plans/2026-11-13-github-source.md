# GitHub Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collect public GitHub user activity and repository releases through the existing bounded acquisition pipeline, with usable schema-driven configuration.

**Architecture:** A native adapter owns provider parsing only and emits completed subscription frames. The existing worker, Store, CLI writers and notification policies retain their responsibilities. Add allowlisted acquisition diagnostics and schema-driven adapter controls rather than a second provider UI/writer.

**Tech Stack:** Python standard library, existing HTTP transport, unittest, existing local JavaScript and Playwright gates.

**Spec:** `docs/superpowers/specs/2026-11-12-source-expansion-event-updates-design.md`, source capability/coverage and stage 3.

## Global Constraints

- No Horizon runtime, MCP/Skill connectors, tenants, new SDK, real provider/model/send calls or production changes.
- No merge/push is implied by continuing development; retain the existing externally managed worktree.
- Public endpoints only: fixed HTTPS `api.github.com`, no redirects, 2 MiB/page, 20-second socket timeout, at most 3 pages/subscription and 100 subscriptions/source; existing worker budget is authoritative.
- `settings.users` and `settings.repositories` are separate string lists; either/both may be set. `settings.max_pages` defaults to 1, range 1..3. Each page requests 100 records.
- Optional `settings.token_env` names an environment variable, not a secret. Explicit missing/invalid credentials fail, never silently fall back to anonymous. Blank means anonymous public API.
- Native identity is versioned and based on provider event/release ID plus type, never repository URL. Preserve publication versus update dates, original evidence and typed references. Private events and draft releases are not collected.
- A full last permitted page is visibly window-limited and has no high-water mark. A failed subscription does not advance the source cursor; successful siblings survive. No claim of complete historical recovery.
- No collection from page render, preview, callback or ordinary dry-run. Configuration writes use existing CLI handlers with origin/token/hash gates.

## Review Focus

- Distinct activity and releases sharing a repo or tag URL retain identity; missing event permalinks must not invent one.
- Malformed nested provider facts, private records, draft releases and unknown event types cannot create false original evidence.
- Quota/reset/auth diagnostics contain only allowlisted primitives, including empty and failed responses; no header/body/token reflection.
- A failure after page 1 discards the incomplete subscription, retains completed siblings and cannot move the cursor.
- Switching adapter, failed validation, copy and no-JS draft round trips retain chosen parameters without submitting another adapter's defaults.

---

### Task 1: Native Provider Boundary

**Files:** Create `src/newspipe/adapters/github.py`, `tests/test_github_source.py`; modify `source_registry.py`, `source_contract.py`, `net.py`, `pipeline.py`, `tests/test_source_collection.py`; test existing source/worker/transport suites.

**Interfaces:** Consumes `net.request`, `FetchResult`, `SubscriptionReport`, worker `fetch_partitions(cfg, since)`. Produces importable GitHub `fetch`/`fetch_partitions`, declarative settings and optional safe `SubscriptionReport.diagnostics`. Extend `net.request` with opt-in response-header capture without changing byte return values or old callers.

- [ ] Write failing tests for public releases/activity identity/evidence, same-time since, malformed/private/draft data, empty success, all/partial failure, quota categorization, credential references, bounded pagination and request targets.
- [ ] Observe RED using `python -m unittest discover -s tests -p test_github_source.py`; fail because native adapter is absent.
- [ ] Add code-owned descriptor and generic numeric/list pattern validation. Implement sequential public API pages; reject redirect/link-derived request targets. Preserve release Markdown as source evidence and separately project bounded activity facts (unknown kinds explicitly incomplete).
- [ ] Add/observe RED then GREEN for allowlisted diagnostics in normalize/summary/worker, and response headers captured only on successful bounded transport; discard unexpected fields from stored public summaries.
- [ ] Run GitHub, contract, registry, worker, transport and evidence tests; verify no outbound real requests.
- [ ] Commit `feat: add bounded public GitHub source`.

### Task 2: Schema-Driven Adapter Dialogs

**Files:** Modify `config.py`, `source_registry.py`, `actions.py`, `workbench.py`, `webassets.py`, `webdrafts.py`, `inbound.py`, `view.py`; create `tests/test_adapter_settings_web.py`; extend `tests/test_browser_e2e.py`; document `docs/github-source-acceptance.md` and README.

**Interfaces:** Consumes descriptor fields from Task 1. Produces `config.source_fields(adapter, schema=None)` used by both form rendering and form translation. Adapter-specific controls are grouped/disabled when not selected; a draft-only refresh supports no-JS.

- [ ] Write/observe failing tests: selected descriptor fields render and reach CLI set/patch/copy/preview; false/zero/empty and unknown extras preserved; literal tokens and invalid subscription syntax/ranges rejected before writing.
- [ ] Compose source fields from the authoritative descriptor; add a focused adapter-parameters group, descriptor labels/help, selected-adapter submission and draft-only refresh with no state/network effects.
- [ ] Add real Chromium/WebKit, mobile and no-JS scenarios: choose GitHub, retain invalid draft, dry-validate then create disabled collect-only source, reopen/edit/copy parameters, switch adapter without foreign parameters.
- [ ] Run adapter web/action/draft suites and browser gates. Document finite windows, quota/auth behavior, optional token reference and exact live-acceptance limits.
- [ ] Commit `feat: expose adapter settings in source dialogs`.

### Task 3: Response Disconnects

**Files:** Modify `src/newspipe/inbound.py`; create `tests/test_inbound_disconnect.py`.

**Interfaces:** Independent of Task 1/2; response writes only, not business exception handling.

- [ ] Reproduce expected peer disconnect during header/body writes for HTML/JSON/static/redirect responses; unrelated I/O failures must still surface.
- [ ] Suppress only `BrokenPipeError` and `ConnectionResetError` at the response transport boundary, without retrying a completed write action or labeling it a service failure.
- [ ] Run inbound/callback/action suites, commit `fix: handle disconnected HTTP clients quietly`.

### Final Acceptance

- [ ] Run complete unittest discovery with required JS/browser gates enabled, secret scan and `git diff --check` in the isolated worktree.
- [ ] Run synthetic doctor/card budget checks with no production credentials or runner.
- [ ] Obtain one fresh whole-branch review; fix Important/Critical findings with observed RED/GREEN, record any deferred minors.
- [ ] Record measured acceptance results, leave branch committed and unmerged/unpushed pending a separate instruction.
