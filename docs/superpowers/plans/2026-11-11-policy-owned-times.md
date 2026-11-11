# Policy-Owned Times Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Independent policy sending clocks and read-only source bindings.
**Architecture:** Policy owns stable schedule-key/clock mappings; pure resolution
feeds scheduler and preview. Explicit atomic CLI migration preserves legacy
behavior. SSR dialogs and schema-backed action translation remain the only UI.
**Tech Stack:** Python unittest, SSR HTML, native JavaScript, Playwright.
**Spec:** `docs/superpowers/specs/2026-11-11-policy-owned-times-design.md`

## Global Constraints
- No live config/provider/credential/send/state-ack/deployment/merge/push changes.
- Schema authority stays in config.describe_schema; default dry-run and hashes.
- Legacy clock and override behavior must not silently change.
- Stable schedule keys preserve completion/receipt identities.

## Review Focus
- Duplicate clocks, empty schedules, stale hashes and malformed time rows.
- Migration retains zero/false/empty overrides, disabled sources, unknown keys.
- Completed/unfinished deliveries cannot be resent by migration.
- Source draft and newly saved policy remain usable across dialog switching.
- No-JS PRG preserves added rows and validation failures.

### Task 1: Policy Clocks And Explicit Migration
**Files:** policy.py, edit.py, cli.py, service.py, pipeline.py,
notification_view.py, config.py; tests/test_policy_times.py.
**Interfaces:** `policy.sending_times(cfg, values)` returns stable key -> clock;
`policy.notification_datetime(cfg, source, key, now)` checks sending clocks;
`edit.migrate_notifications(news_dir, base_hash=None, dry_run=False)` atomically
materializes legacy policies and source overrides, with a change report.
- [x] Write independent-time, scheduling, migration, conflict, compatibility,
  completion and mutation-boundary tests; run and observe RED.
- [x] Implement model, CLI migration and schedule/preview integration.
- [x] Run targeted unittest suite; observe GREEN; commit.

### Task 2: Managed Policy Dialogs And Read-Only Source Bindings
**Files:** actions.py, workbench.py, webassets.py, webdrafts.py, inbound.py,
style.py; tests/test_workbench.py, test_notification_contracts.py,
test_browser_e2e.py, test_webeditors.py, preview_workbench.py.
**Interfaces:** schema `times` control translates repeated time/key rows to
mapping. Source binding contains only policy; retained old overrides require
explicit migration. Source -> policy -> cached source dialog flow uses current
native dialog/cache and refreshes only policy choices/read-only descriptions.
- [x] Add SSR/action/browser tests for read-only policy details and multi-time
  editing, no-JS rows, migration UI and draft retention; observe RED.
- [x] Implement controls, clear labels and policy links/refresh.
- [x] Update obsolete override-interaction tests to enforce the new boundary;
  run targeted and browser suite; observe GREEN; commit.

### Task 3: Acceptance
**Files:** README.md and acceptance document.
**Interfaces:** whole-branch diff from 65abe3a; guarded preview only.
- [x] Run full unittest/browser gates, secret scan and diff check.
- [x] One fresh read-only reviewer; Important/Critical fixes each RED -> GREEN.
- [x] Inspect desktop/mobile preview and record results; commit documentation.
- [x] Restart guarded preview; leave branch unmerged/unpushed.
