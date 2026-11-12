# Group Selection And Acquisition UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace manual group IDs and acquisition IDs with discoverable, safe controls while preserving configuration behavior.

**Architecture:** An optional joined-group capability lives behind the configuration-scoped channel and a read-only CLI/action. SSR renders native group/time controls; the existing same-origin asset enhances pagination, filtering, dialogs and conditional acquisition fields. Acquisition edits reuse `global set` with a scoped slots-only form.

**Tech Stack:** Python 3.11+, standard library, PyYAML, server-rendered HTML, native JavaScript, unittest, Node 24 and Playwright Chromium/WebKit.

**Spec:** `docs/superpowers/specs/2026-11-12-group-picker-acquisition-ux-design.md`

## Global Constraints

- Keep SSR, native dialogs, schema-driven fields and progressive enhancement.
- No new frontend framework, account system, general rule engine or dependency.
- No automatic group joining, permission grant, discovery, test send or WS.
- No production configuration changes, knowledge writes, deployment or push.
- Do not remove stable IDs from persisted configuration or the existing CLI.
- Keep the established dry-run default, action gates, hashes and atomic writes.
- No directory/draft data in disk or browser storage; provider errors contain no raw bodies, credentials or arbitrary exception text.
- Use only guarded synthetic configuration/groups for acceptance; commit each completed stage.

## Review Focus

- A stale group response arriving after editor switching must not overwrite a newer group selection or hash (Task 2).
- A configured group absent from a partial result must stay selectable, not be called invalid (Task 2).
- Duplicate-clock legacy plans, inactive references and disabled sources must retain their original keys (Task 3).
- Empty/all-removed acquisition drafts and newly added rows must survive no-JS failure/dry-run (Task 3).
- A source draft open while shared acquisition settings are saved must retain inputs and its old hash until explicit synchronization (Task 3).

## Task 1: Optional Joined-Group Capability

**Files:** Create `src/newspipe/chats.py` and `tests/test_chats.py`; modify `channel.py`, `backends/feishu_direct.py`, `backends/feishu_lark_cli.py`, `cli.py`, `actions.py`, `tests/test_feishu_direct.py`, `tests/test_cli_surface.py`, `tests/test_actions.py`, `tests/preview_workbench.py`, `tests/test_browser_fixture.py`, `AGENTS.md` and `README.md`.

**Interfaces:**
- Optional backend `list_chats(*, page_size: int = 100, page_token: str = "") -> dict` returns a provider page.
- `channel.list_chats(*, page_size: int = 100, page_token: str = "") -> dict` forwards only to the active backend; CardChannel stays unchanged.
- `chats.list_joined(channel, *, page_size: int = 100, page_token: str = "") -> dict` validates/normalizes the page to `{"groups": [{"chat_id": str, "name": str, "external": bool}], "has_more": bool, "page_token": str}`.
- `chats.DirectoryError` carries controlled message, existing envelope code and actionable hint; no raw provider data.
- CLI `chats list [--page-size 100] [--page-token TOKEN] --json` and web action `chat-list` share this capability and report `changed: false`.

- [ ] **Step 1: Write failing backend/contract tests.** Assert tenant-auth GET `/open-apis/im/v1/chats`, explicit bot identity for external CLI, runtime-bound backend isolation, empty success, duplicate IDs, malformed items/cursors, unsupported custom channel, missing credentials/permission/bot capability, oversize responses and no secret text in any failure envelope.
- [ ] **Step 2: Run RED.** `uv run --python 3.13 --no-project --with-editable '.[all]' python -m unittest discover -s tests -p test_chats.py -v`; expect absent capability/command failures, with all provider calls mocked.
- [ ] **Step 3: Implement directory validation.** Enforce page size 1-100, cursor at most 2048 characters with no control characters, `oc_[A-Za-z0-9_-]+` chat IDs at most 256 characters, at most the requested number of items and names at most 300 characters, boolean pagination and a next cursor when `has_more`. Fail on malformed pages rather than silently returning empty. Only expose selection fields.
- [ ] **Step 4: Implement bounded backend reads.** Direct channel retains its credentials/token identity, uses a 5-second socket timeout and 1 MiB response cap for directory/auth reads, and performs no automatic request retries for a group load. External CLI retains `_call`'s child environment/application selection, explicitly uses bot identity, has a 10-second process deadline and a 1 MiB combined output cap enforced while reading pipes; kill/reap on timeout/overflow, with no output files. Shared delivery behavior is unchanged. Tests pin safe classification and bounded read/cleanup behavior.
- [ ] **Step 5: Wire CLI/action and offline guards.** Resolve the channel with `runtime.activate`, register the read command in API description and the action whitelist, redact opaque cursor values from action CLI feedback. The offline fixture supplies deterministic group pages and guards both real direct and external CLI directory transports. Update contract documentation without claiming live acceptance.
- [ ] **Step 6: Run GREEN and regressions.** Run `test_chats.py`, `test_feishu_direct.py`, `test_cli_surface.py`, `test_actions.py`, `test_browser_fixture.py` and `test_ports.py` by unittest discovery; all must pass. Run secret scan and staged whitespace check, then commit `feat: list joined groups through the configured sending channel`.

## Task 2: Native Group Selector And Read-Only PRG

**Files:** Modify `src/newspipe/workbench.py`, `webassets.py`, `inbound.py`, `webdrafts.py`, `tests/preview_workbench.py`, `tests/test_workbench.py`, `tests/test_workbench_javascript.py`, `tests/test_browser_e2e.py`; create `tests/test_chat_web.py`.

**Interfaces:**
- Group selector region `[data-chat-picker]` owns one authoritative `chat` control, search text, cursor, load/refresh/more buttons and a bounded page collection.
- `notifications_body(..., page)` accepts `page["chat_directory"]` as the read envelope and `page["chat_groups"]` as already retained groups; both are process-memory/SSR data only.
- `chat-list` posts from the global form using `formaction`; no-JS PRG restores it as `global-save` with `global=1`, retaining the original draft/hash and directory feedback.

- [ ] **Step 1: Write failing SSR/web tests.** Assert no network on page/view load; configured ID survives absent/partial results and errors; duplicate names remain distinguishable; load result stays read-only; origin/token/actions-off rejection occurs before directory calls; full no-JS draft survives load and failed load; refreshing a PRG URL makes no second provider call.
- [ ] **Step 2: Run RED.** Run `test_chat_web.py` and `test_workbench.py`; expect missing selector/PRG behavior, never real Feishu traffic.
- [ ] **Step 3: Implement SSR native controls and PRG.** Show a native group select and explicit load/refresh; show available pages and honest incomplete-list search copy. Render advanced manual entry as an alternative mode with only one submitted chat value; no-JS mode switching submits through the same read action without lookup unless an explicit load is requested. Retain group pages in bounded one-use draft memory (maximum 1000 entries, with clear restart guidance at the cap), never trusting client-supplied names as validation evidence. Keep current target as a fallback option. Read feedback is not save/validation feedback.
- [ ] **Step 4: Write failing JS/browser cases.** Pin filtering without losing selection, more-page merging/deduplication, query retention, refresh/error recovery, busy duplicate-click suppression, stale response after close/reopen, edits during load, hostile group text, keyboard and mobile touch use, no hash/read refresh after load.
- [ ] **Step 5: Implement enhancement.** Add `chat-list` to safe markup/action handling; treat it like a read-only probe, not a successful save. Keep per-editor generation guards, local in-memory pages and selected target. Filter fetched pages only and expose remaining pages. Keep fallback and select synchronized, excluding inactive controls from submission. Preserve all other drafts/revisions. Reuse status/error primitives and native focus behavior.
- [ ] **Step 6: Run GREEN and commit.** Run `test_chat_web.py`, `test_workbench.py`, required Node tests and both browser engines for new cases. Check secrets and staged whitespace, then commit `feat: select notification groups by name with safe read-only lookup`.

## Task 3: Acquisition Time Editor And Contextual Source Form

**Files:** Modify `src/newspipe/config.py`, `workbench.py`, `webassets.py`, `actions.py`, `inbound.py`, `webdrafts.py`, `view.py`, `tests/test_workbench.py`, `tests/test_actions.py`, `tests/test_workbench_javascript.py`, `tests/test_browser_e2e.py`; create `tests/test_acquisition_web.py`.

**Interfaces:**
- `/config?acquisition=1` opens `editor_panel("acquisition", ..., "acquisition")`; source and notification pages link to this canonical URL.
- The acquisition form uses `global-save`, hidden `global_scope=acquisition` and `slots_present=1`, hidden `slot_id` rows and native `slot_time` inputs. Its body contains only `slots`; notifications global-save contains only chat/gates.
- `sources_body(..., page)` accepts complete `page["slot_rows"]` reconstructed before generic draft restoration. PRG routes acquisition drafts back to `/config?acquisition=1`.
- A read region `acquisition` refreshes acquisition summaries; source clock controls refreshed from it keep selected stable keys and do not update a cached source draft hash.

- [ ] **Step 1: Write failing action/reference/PRG tests.** Assert existing IDs survive clock edits; new blank IDs get collision-free stable keys; all-removed marker emits `{}`; notification settings cannot submit slots and acquisition scope cannot submit chat/gates; stale hashes/referenced deletion reject atomically; duplicate clocks and unknown source fields/state survive; added/removed rows survive no-JS errors/dry-runs.
- [ ] **Step 2: Run RED.** Run `test_acquisition_web.py`, `test_actions.py` and `test_workbench.py`; expect missing scoped editor/action behavior.
- [ ] **Step 3: Implement hidden-key editing and ownership move.** Translate new keys using `at_HHMM` with numeric collision suffixes, reserving every submitted existing key before assignment. Preserve IDs and duplicate clocks; never rename/deduplicate. Remove acquisition controls from notification global settings. Render existing rows without spare blanks; an empty state has Add. Referenced rows cannot be removed via UI; CLI remains authority. Show active, inactive/disabled and legacy references distinctly, with explicit migration link and warning for shared legacy sending effects.
- [ ] **Step 4: Implement complete acquisition PRG.** Reconstruct all time rows (including zero) before rendering, retain scope and original base hash, route failed/dry-run forms to the correct source-page editor. No-JS Add buttons submit `editor_intent=add-slot` or `add-time`; Remove submits `remove-slot:INDEX` or `remove-time:INDEX`. After all normal POST gates, `_do_action` accepts only these intents for the corresponding scoped global-save/policy-save form, validates row/index bounds, edits the process-memory draft and returns PRG without dispatching a writer/provider. Reject unknown intents; cap time rows at 100. The same draft-only branch can switch the group fallback mode (`chat-list`, `editor_intent=manual-chat`/`select-chat`) without a lookup. Verify one-use refresh and no persistence outside process memory.
- [ ] **Step 5: Write failing conditional-field/browser cases.** Assert `slot` shows clocks only, `poll` interval only, `manual` neither; switching back restores draft values; omitted inactive patch fields preserve stored values. Pin accessible multi-clock selection, equal-clock disambiguation, new time focus/add/remove, retained source drafts across acquisition save, and no false hash sync. Pin zero/unlimited explanation and 24-hour labels on desktop/mobile.
- [ ] **Step 6: Implement contextual fields and compact styling.** Config metadata owns trigger labels/applicability. Replace acquisition multiple-select with native clock checkboxes; scope JS selectors and update drafts only on actual edits. No-JS controls remain visible with applicability hints. Compact boolean rows, help alignment and time row spacing. Remove spare rows from existing policy times while preserving an initial editable row for a new digest policy and explicit no-JS Add. Keep dry-run default/button semantics; no default submission writes.
- [ ] **Step 7: Run GREEN and commit.** Run new acquisition tests, actions/workbench/schema tests, required Node tests, both browser engines and existing policy time/migration tests. Check secrets and staged whitespace, then commit `feat: manage acquisition clocks without exposing internal schedule IDs`.

## Task 4: Integrated Acceptance And Fresh Review

**Files:** Add `docs/superpowers/specs/2026-11-12-group-picker-acquisition-ux-acceptance.md`; update documentation/test fixes in their owning files only.

- [ ] **Step 1: Verify complete spec coverage.** Walk source/global/policy/acquisition create/edit dialogs, source filters, group selection/fallback/load errors, no-JS PRG, unsaved drafts and time-reference consequences. Record observable outcomes and any real Feishu acceptance still deferred.
- [ ] **Step 2: Run all gates.** `NEWSPIPE_REQUIRE_JS_TESTS=1 NEWSPIPE_BROWSER_TESTS=1 uv run --python 3.13 --no-project --with-editable '.[all,browser-test]' python -m unittest discover -b -s tests`; require zero failures and no skipped required JS/browser tests. Run `python3 scripts/scan_secrets.py` and `git diff --check`.
- [ ] **Step 3: Fresh read-only branch review.** One independent reviewer checks changes against spec/plan, with special focus on identity, bounded reads, mutation gates and draft/hash/reference preservation. No implementation delegation. Fix important findings with regression tests and rerun affected plus integrated gates.
- [ ] **Step 4: Record acceptance and commit.** Write test counts, browser engines, guarded-preview checks and deferred live checks; commit `test: verify group selection and acquisition UX end to end`.
- [ ] **Step 5: Restart guarded preview only.** Verify old preview PID/command/port before stopping; start `tests/preview_workbench.py` with the existing isolated uv environment, inspect desktop/mobile and return the current local URL. Leave production runners alone. Report commits and residual limitations; do not merge or push without a new request.

## Self-Review

The four tasks cover the approved spec's directory boundary, group interaction,
ownership move, related form cleanup and acceptance stages. Each task has a
RED/GREEN boundary and commit. The five Review Focus cases have owning tests;
cross-task method, action, scope and page-data names are consistent. No product
code or dependencies are changed before the plan execution handoff is approved.
