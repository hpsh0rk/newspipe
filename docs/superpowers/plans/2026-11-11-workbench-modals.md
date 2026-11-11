# Focused Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans inline, TDD, stage commits and one final fresh review.

**Goal:** Replace permanent forms with unified progressive dialogs and reduce information density.
**Architecture:** `webui.py` renders code-owned panel/link/dialog/help primitives. `workbench.py` renders schema-backed contents, `inbound.py` assembles explicit query/PRG context, and the existing asset owns transient dialog/draft behavior. All writes remain CLI-backed.
**Tech Stack:** Python 3.11+, unittest, vanilla JavaScript/native dialog/popover, offline Chromium/WebKit.
**Spec:** `docs/superpowers/specs/2026-11-11-workbench-modals-design.md`.

## Global Constraints

- No new runtime dependencies, schema, writer, client renderer or persistent drafts.
- Preserve default dry-run, all POST gates, original sibling hashes and no-JS PRG.
- No live credentials/providers/sends/WS/acks/deployment/merge/push; only offline preview restart.

## Verification

UNIT: `NEWSPIPE_REQUIRE_JS_TESTS=1 uv run --python 3.13 --no-project --with-editable '.[all]' python -m unittest discover -b -s tests`
INTEGRATED: `NEWSPIPE_REQUIRE_JS_TESTS=1 NEWSPIPE_BROWSER_TESTS=1 uv run --python 3.13 --no-project --with-editable '.[all,browser-test]' python -m unittest discover -b -s tests`

## Review Focus

- Failed/unsafe editor GET cannot discard a dirty draft, navigate or introduce active content.
- Confirmed saves with intervening edits must stay open; no close/rebase on preview/probe/conflict.
- Dirty sibling editors remain reopenable with original values/hash after settings or policy saves.
- No-JS PRG preserves editor kind, nested controls and raw inputs without POST replay.
- Help dismissal/focus/touch/viewport edges cannot swallow dialog Escape or hide critical errors.

## Task 1: Progressive Dialog Editors

**Files:** create `src/newspipe/webui.py`, `tests/test_webeditors.py`; modify `workbench.py`, `webassets.py`, `style.py`, `inbound.py`, SSR/browser tests.
**Interfaces:** `editor_link(href, label, primary=False)`; `editor_panel(kind, title, body, route, key)` produces a code-owned `data-editor-panel` with stable draft key; `editor_dialog()` produces one shared shell. Links target existing explicit queries. JS imports only validated panel content and retains dirty panels in process/page memory.

- [ ] Write SSR/browser RED tests: normal pages lack editor forms; create/edit/copy opens a dialog without page navigation; correct values; close/cancel/Escape/busy/focus; dirty reopening and sibling hash; visible failed/unsafe GET; real save closes, dry/preview/probe/error stays; no-JS save/PRG stays in the requested editor.
- [ ] Run targeted tests. Expected: missing panels/dialogs and permanent forms fail.
- [ ] Implement primitives, explicit rendering, guarded import/lifecycle and PRG global context; preserve old tests' business assertions while adapting intentional UI lifecycle changes.
- [ ] Run UNIT and browser suite, expected green. Commit `feat: unify workbench editing in progressive dialogs`.

## Task 2: Focused Summaries and Help

**Files:** modify `webui.py`, `workbench.py`, `webassets.py`, `style.py`, `view.py`, `README.md`, browser/SSR tests.
**Interfaces:** `help_tip(text, ident, label)` escapes a focus/touch-capable help widget; schema help uses existing IDs. Summary strips are read regions separate from cached editors; their updates never alter editor hashes.

- [ ] Write RED tests for tooltip hover/focus/pinned touch/Escape, edge fit, visible inline errors; compact ordinary pages and advanced policy fields; clear dry/save labels; source/global/RSSHub summary updates preserve cached drafts.
- [ ] Run targeted tests. Expected: missing tips/density/labels fail.
- [ ] Render focused summaries and folded fields, enhance positioned help and save labels, move secondary operational explanations into help without hiding confirmations/errors.
- [ ] Run INTEGRATED, expected all enabled tests pass. Commit `feat: focus workbench summaries and contextual help`.

## Final Acceptance

- [ ] One fresh read-only review of `a93f48d..HEAD`; one TDD fix pass for Important/Critical issues.
- [ ] Python 3.11/3.12 UNIT and 3.13 INTEGRATED, zero integrated skips; secret/diff scans.
- [ ] Inspect desktop/mobile screenshots; document acceptance, commit and restart/open guarded offline preview. Keep the branch unmerged/unpushed.
