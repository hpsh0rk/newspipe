# Reliability Fixes Implementation Plan

> For agentic workers: execute inline with superpowers:executing-plans and test-driven-development. Review the complete change after all stages.

**Goal:** Fix the five confirmed reliability defects and restore the CI secret-scan gate, with one commit per stage.

**Architecture:** Preserve the existing YAML/JSONL contracts and modular monolith. Serialize state-changing workflows, keep pending items until successful completion, persist delivery identity before sending, and use a durable callback inbox for slow side effects. CLI and Web share the same handlers and runtime dependency assembly.

**Tech Stack:** Python 3.11+, standard library, PyYAML, unittest.

**Spec:** The preceding code review and AGENTS.md hard rules.

## Global Constraints

- Never send real cards, start a second IM connection, or consume the user's actual queue during tests.
- Never write a knowledge base; require an explicit receipt to acknowledge favorites.
- Preserve existing configuration/state files and public result envelopes.
- Use apply_patch for edits; commit each tested stage without amending earlier commits.
- Run the full offline suite at each stage; run doctor for card changes using temporary example configuration.

## Review Focus

- Process interruption after remote send but before local persistence must not create another message on retry.
- Concurrent pipeline, callback, and configuration writers must not lose state or reuse an update sequence.
- Old unacknowledged favorites must remain visible and survive pruning; invalid bulk acknowledgements must not partially write.
- Callback retries must reuse durable event identity and must not wait for external APIs or hooks.
- Runtime configuration changes and real model usage must exercise the same contracts as CLI calls.

## Stage 1: State and Delivery Recovery

Files: _atomic.py, state.py, pipeline.py, interaction.py, channel.py, ports.py, both channel backends, regression tests.

- [x] Add failing tests for pending retention, post-send persistence failure, concurrent interactions, and transport idempotency.
- [x] Add reentrant process/thread workflow locks and unique durable temporary writes.
- [x] Peek pending items and remove only successfully processed identities; deduplicate pushed records.
- [x] Persist card/message operation identity before network effects and reserve update sequences before calling the channel.
- [x] Run targeted tests, the full suite, and a temporary-config doctor; commit.

## Stage 2: Favorite Receipt Contract

Files: events.py, cli.py, actions.py, view.py, docs and regression tests.

- [x] Add failing tests for old favorites, prune protection, missing receipts, generic ack bypass, and partial bulk ack.
- [x] Scan all pending favorites, validate acknowledgements before writing, require favorite-specific receipts, and protect unconsumed events from pruning.
- [x] Update CLI/Web contracts and documentation; run the full suite and commit.

## Stage 3: Runtime Assembly

Files: runtime.py, backends/__init__.py, config.py, cli.py, service.py, regression tests.

- [x] Add failing tests for CLI/service channel agreement and hot switching without stale overrides.
- [x] Centralize runtime channel assembly, remove import-time environment mutation, and default to the standalone direct channel.
- [x] Apply runtime changes only after successful construction; run the full suite and commit.

## Stage 4: Bounded Callback Acceptance

Files: callback_jobs.py, inbound.py, interaction.py, service.py, host-integration.md, regression tests.

- [x] Add failing tests for immediate acceptance, event retry deduplication, restart recovery, and worker failure visibility.
- [x] Persist callback envelopes before acknowledgement; run slow effects in a service-owned worker and preserve platform event IDs through both ingress paths.
- [x] Document accepted-versus-completed semantics and hook recovery limits; run the full suite and commit.

## Stage 5: Model Budget and CI Gate

Files: llm.py, state.py, scripts/scan_secrets.py, docs and regression tests.

- [x] Add failing tests for actual hourly usage, budget/cached-result ordering, retry budgets, and scanner behavior.
- [x] Reserve/account every model attempt using the injected clock and enforce input limits; restore a redacting repository secret scanner.
- [x] Run the full suite, scanner, doctor and complete diff review; commit.

## Execution Notes

Ruling: Keep JSONL compatibility rather than introduce a SQLite migration in a bug-fix series. Durable workflow identity plus writer serialization addresses the observed faults without requiring deployment migration. Remote exactly-once guarantees remain bounded by the platform's idempotency retention.

Stage 1 verification: 361 offline tests passed. Doctor projected 187/200 elements for 13 items. Unfinished delivery identities survive across dates; unknown send results beyond the one-hour message idempotency window stop automatic re-sending for reconciliation.

Stage 2 verification: 365 offline tests passed. Queue reads and acknowledgements cover retained history; pruning protects all unconsumed event day files. Generic event ack cannot consume favorites and invalid bulk requests write nothing.

Stage 3 verification: 370 offline tests passed. Runtime channel scopes are thread-local and shared across CLI/pipeline entrypoints. Hot switching clears the prior service channel, and optimistic configuration edits serialize hash checks with writes.

Stage 4 verification: 378 offline tests passed. Callback acceptance does not call slow handlers. Queued jobs survive restarts, execute in acceptance order, and reuse business event identities. Core retries refresh current state rather than undo later actions; interrupted third-party hooks fail closed for manual reconciliation. Service resources close in finally, including unexpected failures.

Stage 5 verification: 395 offline tests passed. Every HTTP attempt is durably reserved before transport, including reasoning retries and interruptions. Cached receipts remain free at limits; concurrent calls share reservations and identical prompts share receipts. Hourly usage rolls across midnight. Input overflow degrades without truncating prompts. Restored scanner passed with zero findings and only emits locations/rules.

Ruling: The scanner covers tracked and non-ignored working-tree files, not Git history or ignored local credentials. This restores the missing CI publication gate without reading the user's real credentials; historical exposures still require credential rotation.
