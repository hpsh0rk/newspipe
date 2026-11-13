# Source Worker Deadlines Acceptance

## Delivered

- Pipeline acquisition executes in a short-lived importable adapter subprocess. All production collect/run/scheduler paths share `_acquire`; notification, views and callbacks do not gain collection work.
- `fetch.timeout_seconds` defaults to 90, accepts integers 1..600, and is an advanced schema-owned field with current-value/create/edit/copy/dry-run preservation. Subscriptions in one source share this budget; AI and sending do not.
- Startup/import/fetch/parse/worker normalization share a monotonic deadline. On expiration, the process group receives termination and cleanup has one shared 2-second allowance, not two successive waits.
- RSS/Reddit emit completed subscription results independently. Previously completed partitions survive timeout/crash/output overflow; the current incomplete partition is not accepted. A categorical failure withholds the global cursor and retains retry-idempotent queue entries.
- RSS retains its global publication order via a declared, validated protocol ordering enum. HN/Reddit and legacy identities are not rewritten.
- Input is at most 1 MiB, total output 16 MiB and 100 completed partition frames, enforced by both result producer and decoder. Settings use stdin, not argv or persistent scratch. SafeDumper/SafeLoader preserve existing config YAML types (including integer keys, dates, datetime, binary and sets), without arbitrary object construction. Output JSON is normalized in the child and revalidated in the parent; no pickle/eval. A terminal receipt is required for complete success.
- Child stdout/stderr are discarded at descriptor level except a dedicated bounded protocol writer. Provider/exception text is never exported as diagnostics. Workers receive adapter settings, not a Store, model/channel/service or inbound configuration.
- Existing list/generator adapters remain supported in importable trusted modules. Process-local patches/callables are not an adapter deployment interface. This is termination isolation, not a permissions or memory sandbox.
- Enabled manual legacy sources without a notification binding now dispatch through the same `_process` instead of silently returning an empty successful run list. Disabled/dry/delivery gates remain in force.

## Offline Verification

Tests use importable synthetic adapters, temporary state and an ephemeral loopback HTTP fixture, never live providers/models/Feishu. Coverage includes successful empty, native integer identities, list/generator compatibility, CPU stalls, completed-feed-then-stall, stream overflow, process crashes, inherited-pipe descendants, credential-text scrubbing, shared cleanup allowance and independent thread budgets.

Pipeline acceptance covers partial timeout/cursor preservation, restart/retry, dry-run/favorite preservation, legacy acquisition failures and schema/CLI/form round trips. Existing tests preserve frozen delivery, callback/favorite and writer-lock invariants; transport-mocked adapter tests deliberately substitute the direct adapter at the existing `_adapter` seam. Separate integration tests exercise the actual subprocess and real RSS parser against loopback HTTP.

```sh
NEWSPIPE_REQUIRE_JS_TESTS=1 NEWSPIPE_BROWSER_TESTS=1 python -m unittest discover -s tests
python3 scripts/scan_secrets.py
git diff --check
```

No production activation, extra service runner/WS, paid collection, model comparison or knowledge write is part of this stage.

## Independent Review

One fresh read-only whole-stage review found manual legacy no-op dispatch, loss of accepted custom configuration types through JSON stdin, and missing parent-side partition-count validation. The first two were Important. The defensive frame-count gap was initially Minor but regraded Important because an independently validated protocol must also bound parent decoding/state work when a producer is malformed or version-mismatched. All three were reproduced with failing tests and fixed in one pass; no re-review or deferred minor findings.

Legacy manual dispatch uses the existing process/state/delivery path, not a second write path. Typed input uses the project's existing safe YAML dependency rather than a configuration migration or an arbitrary-object codec. Genuine legacy adapters without `fetch_partitions` are tested for both lists and generators.

Review set-asides stand: POSIX-only deployment, no sandbox/OS real-time guarantee, parent/AI/delivery/whole-round deadlines excluded, per-subscription cursors/conditional fetching/finite-window recovery later, and live/production/provider/model/send/knowledge operations require separate authorization. These are limits, not claims that the broader source-expansion/event-update goal is finished.

## Limits

- Deadline termination is best effort under OS scheduling; process creation/kernel operations are not a hard real-time guarantee. Parent bounded protocol decoding/state commits are not worker execution. Cleanup never waits indefinitely for an uninterruptible kernel task.
- Supported deployment targets are macOS/Linux (POSIX process groups). This is not a Windows process-tree implementation.
- Custom adapters remain trusted code inheriting the caller's environment and permissions. They must not write state, spawn detached services or depend on parent memory; no sandbox or memory quota is claimed.
- A partition interrupted before its result frame is lost from this attempt, not marked consumed. Earlier complete frames are retained; finite upstream windows can still lose unseen content. No persisted per-subscription cursor or conditional requests are added.
- Direct library `adapters.*.fetch` calls remain unbounded unless the caller supplies its own execution boundary. Read-only RSSHub probes retain their separately documented socket timeout.
- No global multi-source round deadline, AI/delivery deadline, semantic suppression, event differences or appended notifications are introduced. Native GitHub remains the next source-family stage.
- Live source/IP availability and deployment resource sizing remain unverified. Existing legacy RSS identity ambiguity/rollback restrictions still apply.
