# Optional RSSHub Acceptance

Date: 2026-11-11. Branch: `feat/rsshub-settings`.
Code scope: `916ca00..22903e4`. No merge, push or production deployment.

## Delivered

- RSSHub is optional: absolute HTTP(S) feeds bypass it; relative routes use explicit `rsshub.base_url`, then `RSSHub_BASE`, then the local default. Resolution is dynamic and preserves reverse-proxy prefixes.
- CLI show/set/probe retain the existing envelopes. Endpoint edits reuse atomic writes, dry-run and file hashes; probes use a bounded direct request and sanitized diagnostic output.
- Workbench metadata shows the effective address, origin and enabled dependent sources without contacting the network. Schema-owned settings save through the same CLI handler.
- Connection and feed tests are explicit, guarded and read-only. Success, rejection and transport failure never acknowledge drafts, update hashes or refresh write-related read regions. No-JS PRG preserves the submitted draft for one response and cannot replay a probe on refresh.
- Catalog verification is historical, not a claim about the current instance. Container documentation distinguishes container localhost, host addresses and remote endpoints; RSSHub is not automatically discovered or deployed.

## Review Fixes

One fresh read-only reviewer covered `916ca00..e3cb85c` and found two Important runtime issues. Both were reproduced RED-to-GREEN in one fix pass:

- Inferred source names now use parsed hostnames or paths, excluding URL query values. Synthetic successful items and authentication failures verify that inferred labels, pipeline results, event errors and view status metadata do not disclose those values. Editable source inputs intentionally remain lossless.
- Malformed/unsupported XML is recorded per feed rather than aborting healthy siblings. Tests cover both orders, Atom, HTML and broken XML. Partial failures emit safe warning logs; all-feed failure remains an explicit pipeline/event error, not quiet empty success.

No Critical or Minor findings, declined judgments or deferred review findings remain from this review. Live Feishu/RSSHub deployment was not tested.

## Verification

Python 3.11 and 3.12: each 634 discovered, 609 executed, 25 intentionally opt-in browser methods skipped; all executed tests passed.

```sh
NEWSPIPE_REQUIRE_JS_TESTS=1 uv run --python 3.11 --no-project --with-editable '.[all]' python -m unittest discover -b -s tests
NEWSPIPE_REQUIRE_JS_TESTS=1 uv run --python 3.12 --no-project --with-editable '.[all]' python -m unittest discover -b -s tests
```

Python 3.13 integrated: 634/634 passed, zero skips, including 25 browser methods exercising Chromium and WebKit. Responsive tests cover 360, 768 and 1440px. Desktop/mobile screenshots were inspected, with no horizontal overflow.

```sh
NEWSPIPE_REQUIRE_JS_TESTS=1 NEWSPIPE_BROWSER_TESTS=1 uv run --python 3.13 --no-project --with-editable '.[all,browser-test]' python -m unittest discover -b -s tests
python3 scripts/scan_secrets.py
git diff --check
```

Secret and whitespace scans passed. All fixtures use temporary synthetic data; both browser and server guards block providers, credentials, model calls and remote sends, including the new probe transport. Only designated synthetic probe targets receive test responses.

## Execution Boundary

Continued in the existing dedicated feature branch and checkout, avoiding another workspace approval round. This leaves shared-checkout coordination risk; no conflicting edits occurred. Production configuration, runners and host integrations were left untouched. Only the guarded offline preview is restarted for visual inspection.
