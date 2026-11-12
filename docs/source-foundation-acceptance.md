# Source Foundation Acceptance

Completed on 2026-11-12 on `feat/source-expansion-event-updates`.
This is only the five-task source foundation, not the whole source expansion.

## Delivered Boundaries

- Legacy list/generator adapters retain native IDs, unknown fields and unknown dates.
- Structured results distinguish empty, success, partial failure and total failure.
- Successful content is durable before safe cursor advancement; partial results do
  not advance the global watermark. Collection transport runs outside the writer lock.
- Real acquisition attempts throttle independently of successful content cursors,
  survive restart, and preserve legacy fallback and manual force semantics.
- Code-owned adapter metadata composes configuration validation/current values.
  Metadata-only parameters are not presented as supported web editing controls.
- Safe bounded collection diagnostics survive notification/throttle heartbeats;
  newer legacy success leaves an ordering tombstone instead of reviving old errors.

## Verification

Final mandatory command (isolated virtualenvs, editable project plus browser/crypto
test dependencies; no production dependency or configuration installation):

```sh
NEWSPIPE_REQUIRE_JS_TESTS=1 NEWSPIPE_BROWSER_TESTS=1 python -m unittest discover -s tests
```

- Python 3.11: 767 tests, 192.425 seconds, OK, no skips.
- Python 3.14: 767 tests, 190.163 seconds, OK, no skips.
- Final fix-focused regression: 313 tests passed.
- Secret scan and `git diff --check` passed.
- Synthetic `doctor --json`: OK, 187/200 card elements, 13 headroom;
  credentials were stubbed and network access denied.

A fresh read-only review identified integer-ID loss, malformed result/UTF-8
failures escaping diagnostics, and stale diagnostic resurrection. Each received
a reproduced failing test and a passing fix. An initially unjudged recovery case
was reproduced and fixed: local receipt repair after partial acquisition cannot
complete the slot without retrying its failed subscriptions. Confirmed remote
sends still retain their identity/payload and are never repeated by that repair.

No live provider/model/Feishu calls, knowledge writes, account login, production
configuration changes, second runner/WS, merge or push were performed.

## Remaining Scope

Story observations and candidate preview are next. Existing RSS/HN/Reddit upgrades,
new source families, budgeted semantic comparisons, scoped suppression/undo and
append-only event differences remain separate planned subprojects. Live provider
availability is unverified. No Horizon runtime or MCP/Skill integration was added.
