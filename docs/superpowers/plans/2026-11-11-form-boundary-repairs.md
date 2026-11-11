# Form Boundary Repairs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this approved repair inline, task-by-task.

**Goal:** Prevent ambiguous numeric clearing and unconfirmed Web deletions.

**Architecture:** Forms translate into the existing CLI handlers. Source patches distinguish missing fields from explicit numeric clearing; custom notification overrides require a value. Web deletion confirmation is enforced before invoking the unchanged CLI.

**Tech Stack:** Python 3.11-3.13, unittest, server-rendered HTML, vanilla JavaScript, offline Playwright Chromium/WebKit.

**Spec:** `docs/superpowers/specs/2026-11-11-notification-workbench-design.md`, Compatibility and Editing Contracts; the user approved these three reproduced defects for repair.

## Global Constraints

- No real credentials, providers, notifications, WS clients, queue acknowledgements, deployment or remote push.
- Preserve action whitelist, origin/token/actions gates, CLI contracts, unknown source fields and unrelated blocks.
- No additional runtime dependencies or frontend build chain.
- Commit each verified stage; continue on the existing `fix/reliability-contracts` branch.

## Review Focus

- No uncovered classes identified: tests below cover blank/missing/zero, preview/save, dry/conflict, confirmation tampering, and JS/no-JS submissions.
- Review the interaction between submitted defaults, preserved advanced fields, and explicit inheritance deliberately.

## Verification Commands

`UNIT`: `NEWSPIPE_REQUIRE_JS_TESTS=1 uv run --python 3.13 --no-project --with-editable '.[all]' python -m unittest discover -b -s tests`

`BROWSER`: `NEWSPIPE_BROWSER_TESTS=1 uv run --python 3.13 --no-project --with-editable '.[browser-test]' python -m unittest discover -b -s tests -p test_browser_e2e.py`

`TARGET`: `uv run --python 3.13 --no-project --with-editable '.[all]' python -m unittest discover -b -s tests -p test_notification_contracts.py`

## Task 1: Reject Empty Custom Notification Numbers

**Files:** `src/newspipe/actions.py`, `src/newspipe/workbench.py`, `tests/test_notification_contracts.py`, `tests/test_browser_e2e.py`.

**Interfaces:** `notification_binding(form: dict[str, list[str]]) -> dict | None` remains the common source-save/patch/preview translator. Numeric custom values must not become merge-patch deletion markers.

- [x] Add failing tests: custom `max_cards_per_day` and `min_gap_min` with empty, whitespace or missing values reject before writing (also preview/create); explicit zero persists, explicit inherit removes only its override, empty quiet hours/slots remain meaningful.
- [x] Run TARGET. Expected: new empty-custom tests fail because the current action succeeds.
- [x] In `notification_binding`, reject a coerced `None` in the custom branch with the field path and an explicit inherit hint. Add schema-derived UI guidance for custom numbers; keep server validation authoritative and inherited controls optional.
- [x] Add browser regression: zero save, empty custom rejection with input/selector preserved, then explicit inherit restores template. Both engines, JS and no-JS.
- [x] Run UNIT and BROWSER. Expected: all enabled tests pass, no provider effects.
- [x] Commit `fix: reject empty custom notification budgets`.

## Task 2: Restore Defaults On Explicit Source Numeric Clearing

**Files:** `src/newspipe/actions.py`, `src/newspipe/workbench.py`, `tests/test_notification_contracts.py`, `tests/test_config_page.py`, `tests/test_browser_e2e.py`.

**Interfaces:** `build_source_body(form, schema=None, *, patch=False) -> dict[str, Any]` preserves existing create behavior. `_source_patch_body` opts into patch semantics and remains shared by preview/save.

- [x] Add failing tests: existing numeric values clear to defaults (`fetch.interval_min=0.0`, `filter.min_items=1`, `filter.max_items=13`, `enrich.max_body_chars=6000`, legacy cap=12, legacy gap=0.0). Missing preserves, zero stays explicit, create blank omits. Dry/conflict retain bytes; unknown keys, advanced values and other blocks survive.
- [x] Run TARGET. Expected: clearing tests fail because old keys remain.
- [x] Preserve `None` only for explicit blank numeric fields in patch mode; use patch mode for source preview/save. Update builder docstrings and show blank/default help using each schema field's default, not duplicate constants.
- [x] Add browser regression: clear max_items and interval, preview without writing, save and reload shows defaults. Verify independent advanced values survive. Both engines.
- [x] Run UNIT and BROWSER. Expected: all enabled tests pass, no provider effects.
- [x] Commit `fix: apply explicit numeric clearing in source patches`.

## Task 3: Require Server-Side Web Deletion Confirmation

**Files:** `src/newspipe/actions.py`, `tests/test_notification_contracts.py`, `tests/test_config_page.py`, `tests/test_browser_e2e.py`, `docs/superpowers/specs/2026-11-11-notification-workbench-design.md`.

**Interfaces:** A shared confirmation guard accepts exactly `confirm_delete: ["1"]` before translating source-remove and policy-remove. Direct CLI deletion remains unchanged.

- [x] Add failing action and HTTP tests: absent/empty/false/arbitrary/repeated confirmation rejects and retains bytes; valid confirmation permits dry-run, still enforces hash conflicts and referenced-policy protection, and allows deletion.
- [x] Run TARGET. Expected: unconfirmed deletion tests fail because current handlers delete.
- [x] Add shared confirmation helper; route policy removal through a named argv builder. Update previously confirmed-deletion test inputs, not their assertions.
- [x] Add browser regression: unchecked delete produces no request, confirmed source and unreferenced policy deletion works in JS/no-JS; PRG refresh never replays.
- [x] Record clearing and deletion semantics in the design contract.
- [x] Run UNIT and BROWSER. Expected: all enabled tests pass, no provider effects.
- [x] Commit `fix: enforce Web deletion confirmation on the server`.

## Final Acceptance

- [x] One fresh scoped review of `8cc94ad..HEAD`, then one TDD fix pass for Important/Critical findings if any.
- [x] Repeat UNIT for Python 3.11 and 3.12; run integrated Python 3.13 with `NEWSPIPE_BROWSER_TESTS=1 NEWSPIPE_REQUIRE_JS_TESTS=1` and extras `.[all,browser-test]`. Expected: all enabled tests pass with zero integrated skips.
- [x] Run `python3 scripts/scan_secrets.py` and `git diff --check`. Expected: clean.
- [x] Record acceptance evidence, commit documentation, clean only this plan's scratch workspace, report local commits and live-validation limitations.
