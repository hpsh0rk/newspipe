# Workbench Interactions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans inline, with one final fresh reviewer.

**Goal:** Fix the five concrete interaction defects found in the approved walkthrough.
**Architecture:** Keep CLI writes and SSR rendering authoritative. Add progressive deletion confirmation and replace only clean read regions after writes.
**Tech Stack:** Python 3.11-3.13, unittest, vanilla JavaScript, local CSS, offline Playwright.
**Spec:** `docs/superpowers/specs/2026-11-11-workbench-interactions-design.md`.

## Global Constraints

- No production data, credentials, scheduler, WS, real sends/acks, deployment, merge or push.
- Work in `fix/workbench-interactions`; retain existing safety/unknown-send/atomic-write contracts.
- Use apply_patch, TDD, per-stage commits, one final reviewer; no implementer delegation.
- Preserve dirty values/open sections and no-JS usability; no external assets/build/dependencies.

## Verification

UNIT: `NEWSPIPE_REQUIRE_JS_TESTS=1 uv run --python 3.13 --no-project --with-editable '.[all]' python -m unittest discover -b -s tests`

BROWSER: `NEWSPIPE_BROWSER_TESTS=1 uv run --python 3.13 --no-project --with-editable '.[browser-test]' python -m unittest discover -b -s tests -p test_browser_e2e.py`

## Review Focus

- Dirty controls inside a read region must prevent replacement/rebasing.
- A config change between delete rendering and confirmation must still conflict.
- A failed read after confirmed write must not offer an unsafe retry.
- Server-rendered region insertion must not execute untrusted content/scripts.
- No-JS confirmation GET and refresh must never delete or replay POST.

## Task 1: Correct Source Health Semantics

**Files:** `view.py`, `workbench.py`, `tests/test_view.py`, `tests/test_workbench.py`.
**Interfaces:** `view.build` retains raw status and emits authoritative label/tone/stale. `sources_body` consumes that tone, not a tone derived solely from enabled.

- [ ] Add tests: never => Chinese neutral label, collected => Chinese success; 31h-old slot collection stale=true/warn with aware/naive clocks; send_failed rendered danger, disabled stays explicitly disabled. Recognize shared notification-reason labels without a second vocabulary.
- [ ] Run targeted view/workbench suites. Expected: new labels/staleness/tone tests fail.
- [ ] Add missing collection/never semantics, use shared notification reason labels for fallback, and render the supplied health tone.
- [ ] Run UNIT. Expected: enabled tests pass. Commit `fix: render truthful source health in the workbench`.

## Task 2: Normalize Actions and Add Progressive Delete Confirmation

**Files:** `workbench.py`, `style.py`, `view.py`, `inbound.py`, `webassets.py`, relevant SSR/HTTP/browser tests.
**Interfaces:** `GET /confirm-delete?kind=source|policy&name=<id>` is read-only, fixed-route, code-whitelisted. `delete_confirmation` builds the target metadata from the snapshot. Dialog/form still dispatch source-remove/policy-remove through actions.py.

- [ ] Add failing browser/HTTP tests: no permanent confirmation checkbox; action heights equal within 1px and >=36/44px; click opens dialog with target/retention; cancel and ESC do not POST; explicit confirm posts once; referenced policy blocked; stale hash rejected; no-JS confirmation GET/POST/refresh safe. Actual source edit/copy clicks land in view.
- [ ] Run targeted browser cases/HTTP tests. Expected: geometry/dialog/confirmation route cases fail.
- [ ] Render simple delete links and shared dialog; add read-only confirmation renderer/route with token/hash/hidden exact confirmation, no writes. Update tests that previously clicked the inline checkbox to follow the new approved flow.
- [ ] Normalize button/action/heading styles and source editor/copy anchors. Keep keyboard focus and validation fallback.
- [ ] Run UNIT and BROWSER. Expected: all enabled tests pass. Commit `fix: unify workbench actions and deletion confirmation`.

## Task 3: Reconcile Lists and Present Contextual Feedback

**Files:** `workbench.py`, `view.py`, `webassets.py`, `style.py`, Node/browser tests.
**Interfaces:** SSR marks clean replaceable read regions; the existing GET page renders them with snapshot hashes. Editors stay outside replaceable regions; dirty/busy controls inside regions block replacement.

- [ ] Add failing browser tests: toggle immediately changes status/action and a second row action succeeds; deleted source/policy disappears; copied source/policy appears; policy binding counts update; dirty sibling editor preserves draft/hash; feedback within viewport after save/error/network failure; no automatic write retries.
- [ ] Run targeted tests. Expected: stale lists/hashes and offscreen feedback fail.
- [ ] Read and parse same-origin SSR pages after confirmed writes, replace only known clean regions, keep dirty editors intact, and expose read-failure guidance. Do not insert arbitrary scripts or raw input as HTML.
- [ ] Position feedback at form/card context; minimum necessary scroll replaces unconditional exact-pixel restoration. Preserve draft/version acknowledgement rules and pending protection.
- [ ] Run UNIT and BROWSER. Expected: all enabled tests pass. Commit `fix: reconcile workbench state and contextual action feedback`.

## Final Acceptance

- [ ] Fresh read-only review of `63e60e4..HEAD`; one TDD fix pass for Important/Critical findings.
- [ ] Run Python 3.11/3.12 UNIT equivalents and integrated Python 3.13 `.[all,browser-test]` with both flags. Expected: integrated zero skips.
- [ ] Secret scan/diff check clean; record evidence and commit documentation.
- [ ] Restart only the offline preview with current code, verify pages/geometry, provide its new URL. Preserve production runners; do not push.
