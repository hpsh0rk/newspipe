# CI and Browser Acceptance Design

## Intent and Scope

The user selected the recommended next steps: make verification repeatable in CI and complete browser acceptance before adding more product features. This extends the already approved workbench acceptance requirements; it does not redesign the product or add a reconciliation writer.

Goals: all branch pushes and PRs run Python 3.11/3.12/3.13 checks; JavaScript behavioral tests cannot silently skip in CI; real Chromium and WebKit exercise the existing server-rendered application at 360px, 768px and desktop widths, with keyboard, reduced motion, no-JS and failed/stale submissions.

Non-goals: no runtime frontend toolchain, live deployment, remote notifications, new IM connections, knowledge-base writes, policy engine, state-store replacement or delivery refactor.

## Implementation Choices

Builder choice: use Python Playwright as an optional test-only extra, pinned to the locally verified version 1.65.0, and keep runtime dependencies unchanged. Node 24 is explicitly installed for the existing lightweight JavaScript tests.

Alternatives considered: continue manual native-browser checks (not repeatable, previously interrupted); add a Node frontend test project (duplicates the existing Python test lifecycle); Python Playwright (selected by the builder because it tests real engines without introducing a frontend build chain).

## CI Gates

All branch pushes and PRs run the Python matrix with Node 24 and an explicit require-JavaScript flag. Missing Node must fail, not skip. A separate browser job installs the test extra plus both engines/dependencies, enables browser tests explicitly and uploads failure screenshots/traces. Ordinary Python-only development may still skip optional browser tests; the dedicated CI job must fail if its required engine or dependency is missing.

## Offline Fixture and Browser Matrix

Reuse the existing preview data through a context-managed fixture: temporary news directory, loopback ephemeral HTTP port, no scheduler, no event receiver, no credentials. Fetch, enrichment/model calls, credential resolution and remote transport methods are blocked or replaced with deterministic fixture behavior. Shutdown closes the server and removes temporary state.

Browser contexts deny non-fixture network requests. Failure artifacts contain only synthetic fixture data and expired process-local form tokens, never production data. Generated artifacts are ignored by Git.

Coverage: all five routes and source/policy editors across three widths and both engines; no horizontal document overflow; labels and keyboard-visible focus; reduced-motion styles; validated preview/save and copied records; local/server validation; optimistic conflicts and network failures retaining edits, scroll and open sections; editing during pending saves; no-JS PRG drafts and core forms.

Assertions concern browser-visible behavior and resulting CLI contracts, not private layout internals. Fix concrete UI defects uncovered by these tests under the existing workbench specification, using RED/GREEN regression evidence.

## Live Feishu Boundary

Provide an operator checklist for a dedicated test application/chat or an explicitly exclusive maintenance window. Reuse the existing inbound owner; never launch a second client merely to test. Record app ownership, runner exclusivity, destination, card operation/receipt and callback results before declaring live acceptance. Do not execute live probes as part of CI or this implementation session. Unknown outcomes remain blocked; a platform-receipt reconciliation writer is separate work.
