# CI and Browser Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make CI and real-browser acceptance repeatable without touching a live deployment.
**Architecture:** Extend existing unittest gates, add an optional pinned Playwright test extra and a context-managed offline HTTP fixture, and keep all UI writes on the existing action/CLI path.
**Tech Stack:** Python 3.11/3.12/3.13, Node 24, Playwright 1.65.0 Chromium/WebKit, GitHub Actions.
**Spec:** `docs/superpowers/specs/2026-11-11-ci-browser-acceptance-design.md` and the existing notification workbench design.

## Global Constraints

- Use the existing fix/reliability-contracts checkout; commit each stage, no merge/push/deployment.
- No live credentials, notifications, IM connections, model/fetch calls, knowledge writes or real queue acknowledgements.
- Browser tooling is optional and test-only; no production build chain or new runtime dependency.
- CI must fail for missing required Node/browser tooling rather than silently skip.
- Preserve CLI/action contracts, origin/token gates, one-WS ownership and uncertain-send protection.

## Review Focus

- Missing Node/Playwright/engine binaries must fail their dedicated CI gate.
- Browser saves with stale hashes or uncertain responses must not discard edits or retry automatically.
- Real native validation and keyboard focus must work for collapsed/hidden controls and grouped slots.
- No-JS failed submissions must retain the editor, raw candidate values and current policy.
- Test servers/contexts must remain isolated from real credentials, services and network destinations.

### Task 1: Mandatory CI Coverage

**Files:** Modify `.github/workflows/ci.yml`, `tests/test_workbench_javascript.py`; create `tests/test_ci_contracts.py`; include this spec/plan.
**Interfaces:** `NEWSPIPE_REQUIRE_JS_TESTS=1` makes missing Node fail; normal Python-only usage can still skip the optional JS tests.

- [x] Add tests for all-branch trigger, full Python matrix, explicit Node and fail-not-skip behavior; run and observe missing gates fail RED.
- [x] Add Node 24, Python 3.13, branch-push triggers and the required-JS flag; verify CI contracts and JS behavior, expected PASS.
- [x] Run full unittest suite, secret scan and diff check; commit `ci: enforce Python and JavaScript acceptance gates`.

### Task 2: Real Browser Acceptance

**Files:** Modify `pyproject.toml`, `.gitignore`, `tests/preview_workbench.py`, `.github/workflows/ci.yml`; create `tests/test_browser_e2e.py`, `tests/test_browser_fixture.py`; correct existing workbench UI files only for reproduced failures.
**Interfaces:** `offline_workbench()` yields news directory, URL and temporary Store; `NEWSPIPE_BROWSER_TESTS=1` enables mandatory Chromium/WebKit tests; `test-results/browser` holds failure artifacts. Default unit runs skip browser tests deliberately; CI has a separate mandatory browser job.

- [x] Add fixture lifecycle/isolation and mandatory browser-job tests; observe missing fixture/job fail RED, then implement the fixture and optional dependency/job.
- [x] Add and execute real-engine responsive/keyboard/reduced-motion/preview/save/conflict/network/pending-edit/no-JS scenarios. Watch any concrete UI defects fail before correcting their root cause.
- [x] Run the complete browser matrix, unit suite, scanner and diff checks; commit `test: add isolated Chromium and WebKit workbench acceptance`.

### Task 3: Operational Acceptance Record

**Files:** Create `docs/live-feishu-acceptance.md`; update README.md, AGENTS.md and this plan.
**Interfaces:** Document exact local/CI test commands and live acceptance stop conditions; no automatic live probe or outcome-reconciliation writer.

- [x] Record browser evidence, limits and a dedicated-app/exclusive-owner live checklist; verify documented commands against the implemented test gates.
- [x] Dispatch one fresh scoped whole-change reviewer; correct Important findings with RED/GREEN. Run Python 3.11/3.12/3.13 suites, real browser matrix, scanner and diff checks, expected PASS.
- [x] Commit `docs: record browser acceptance and controlled Feishu validation`; report commits and outstanding live acceptance honestly.
