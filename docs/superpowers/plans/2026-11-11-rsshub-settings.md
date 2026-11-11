# RSSHub Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans inline with one final fresh reviewer. Steps use checkbox syntax.

**Goal:** Make optional RSSHub configuration and read-only diagnosis usable from CLI and the workbench.
**Architecture:** A focused `rssconnect.py` owns endpoint normalization/resolution/probing. Config, CLI and existing atomic edit paths remain authoritative; SSR forms translate through actions.py.
**Tech Stack:** Python 3.11-3.13, unittest, existing SSR/vanilla JavaScript, offline Playwright.
**Spec:** `docs/superpowers/specs/2026-11-11-rsshub-settings-design.md`.

## Global Constraints

- No production providers/credentials, sends/acks/WS/deployment, merge or push; restart only offline preview.
- No new runtime dependencies or second write/renderer/schema authority.
- Native execution, TDD, stage commits, one final scoped reviewer. Preserve dirty values and hashes, existing origin/token/actions gates and no-JS PRG.

## Verification

UNIT: `NEWSPIPE_REQUIRE_JS_TESTS=1 uv run --python 3.13 --no-project --with-editable '.[all]' python -m unittest discover -b -s tests`

BROWSER: `NEWSPIPE_BROWSER_TESTS=1 uv run --python 3.13 --no-project --with-editable '.[browser-test]' python -m unittest discover -b -s tests -p test_browser_e2e.py`

## Review Focus

- Unused invalid environment URL must not break direct feeds or unrelated adapters.
- Reverse-proxy path prefixes and URL normalization must match actual fetch/probe targets.
- Probe must remain read-only and guarded, including no-JS refresh and failed transport.
- Raw upstream errors, bodies or credential/query URLs must not enter feedback, audit or contracts.
- Saved endpoint changes must not silently rebase sibling drafts; probes never acknowledge edits.

## Task 1: Configuration and Runtime Resolution

**Files:** create `rssconnect.py`, `tests/test_rsshub_settings.py`; modify `config.py`, `edit.py`, `cli.py`, `pipeline.py`, `adapters/rsshub.py`.
**Interfaces:** `rssconnect.base_url(value)` normalizes/validates; `effective(configured)` returns `(url, origin)`; `feed_url(entry, configured)` bypasses the hub for absolute HTTP(S); `describe(cfg)` is network-free. `Config.rsshub` is a validated mapping; `rsshub show/set` use existing envelopes and atomic source-file hashes.

- [ ] Add failing tests for config/env/default precedence, malformed values without disclosure, prefix/trailing slash joins, absolute bypass with invalid unused env, dynamic fetch resolution after endpoint changes, dry-run/atomic/hash behavior and sibling block preservation.
- [ ] Run `test_rsshub_settings.py`. Expected: missing settings/resolution/CLI tests fail.
- [ ] Implement normalization, config/schema, dedicated `edit.set_rsshub`, CLI show/set and runtime forwarding at both adapter call sites; retain legacy adapter name.
- [ ] Run UNIT. Expected: all enabled tests pass. Commit `feat: configure optional RSSHub endpoints through the CLI`.

## Task 2: Read-Only Probe and Diagnostics

**Files:** modify `rssconnect.py`, `cli.py`, `view.py`, `actions.py`, `adapters/rsshub.py`; create `tests/test_rsshub_probe.py`.
**Interfaces:** `rssconnect.probe(configured, feed=None)` returns the standard `rsshub.probe` envelope; `_probe_request(url)` is the only bounded probe transport. `describe(cfg)` supplies `rsshub` metadata to doctor/view; new whitelisted action `rsshub-probe` delegates to CLI.

- [ ] Add failing local-HTTP tests for successful reachability, valid empty/nonempty RSS, auth/404/5xx/network/invalid/unsupported/oversized responses, secret-free output, no retries and unchanged files/state. Assert doctor/view make no network calls and only enabled relative sources cause dependency warnings.
- [ ] Run probe suite. Expected: absent probe/classification fails.
- [ ] Implement bounded direct transport and safe classifications, CLI probe, network-free metadata, guarded action translation, and explicit runtime rejection of unsupported feed roots rather than quiet-empty success.
- [ ] Run UNIT. Expected: enabled tests pass. Commit `feat: add guarded read-only RSSHub diagnostics`.

## Task 3: Workbench and Deployment Guidance

**Files:** modify `workbench.py`, `webassets.py`, `style.py`, `inbound.py`, `webdrafts.py` if required, `tests/preview_workbench.py`, browser/SSR tests, `README.md`, `compose.yaml`, `AGENTS.md`.
**Interfaces:** schema-driven settings form saves through `rsshub-save`; probe submitters use `rsshub-probe`. Probe feedback has a separate read-only path: no draft acknowledgment/hash updates/read reconciliation. Settings metadata is a code-owned replaceable read region, the editor is not.

- [ ] Add failing guarded browser/HTTP tests for save/dry-run/inheritance, candidate connection and full/relative feed tests, unchanged files/draft/hash after probes, visible error feedback, stale save conflict, no-JS PRG/refresh and all POST gates. Guard the probe transport in every offline fixture; only designated synthetic URLs get test responses.
- [ ] Run targeted browser/HTTP tests. Expected: missing settings/probe flow fails.
- [ ] Render address/origin/dependencies and schema fields, historical directory copy, generic RSS label; handle probe feedback and no-JS retained forms; update container/environment and agent documentation.
- [ ] Run UNIT and BROWSER. Expected: all enabled tests pass. Commit `feat: expose RSSHub settings and connection tests in the workbench`.

## Final Acceptance

- [ ] Fresh read-only review of `916ca00..HEAD`; one TDD fix pass for Important/Critical findings.
- [ ] Python 3.11/3.12 UNIT and integrated 3.13 with `.[all,browser-test]` and both required flags; expected zero integrated skips.
- [ ] Secret scan and diff check clean; commit acceptance notes and restart/open guarded offline preview.
