# Workbench Interaction Repairs

The user approved the fixes after a browser walkthrough: consistent buttons,
click-then-confirm deletion, accurate health, reconciled lists, editor navigation
and visible action feedback. This extends the approved notification workbench,
not its domain model or deployment.

## Decisions and Alternatives

- Use one local HTML dialog for source/policy deletion, with a read-only server
  confirmation page as the no-JS fallback. A permanent checkbox plus browser
  confirm was rejected because it crowds every row and asks twice. Browser
  confirm alone was considered but cannot consistently present the target and
  reference/retention consequences.
- Confirm shows the target label/ID, retention consequences and policy bindings.
  Cancel/ESC performs no write and restores focus. Pending blocks duplicate
  confirmation. Referenced policies explain why deletion is blocked.
- Both flows POST through the same action/CLI with exactly `confirm_delete=1`,
  the current rendered hash and process token. GET never writes; action switch,
  same-origin/token gates and reference checks remain authoritative.
- Buttons use the same inline-flex geometry and alignment: at least 36px tall
  on desktop and 44px on narrow screens. Card actions wrap without stretching.
  Section titles have no inherited top margin inside heading toolbars.
- Source health uses the view contract's label/tone/staleness, independent of
  enablement. Never-run is neutral; collected is recognized and eligible for
  the existing acquisition staleness window. Delivery failures remain danger.
- Successful writes reconcile read regions from the existing server-rendered
  page, avoiding a second client-side source/policy renderer. Only clean read
  regions are replaced; editors/dirty controls are preserved. Newly rendered
  inline controls carry a fresh hash; stale editors are never silently rebased.
- Feedback appears at the operation context and is brought into view with the
  minimum needed scrolling, never an unconditional jump to the page header.
  Preview/dry/error do not acknowledge drafts or trigger writes/retries.
- Source edit/copy links land at their open editor. Unknown IDs never become
  implicit create forms. Actual native/no-JS workflows remain supported.

## Boundaries and Acceptance

No real credentials/providers, sends, WS clients, queue acks, deployment or
remote push. No dependencies, runtime build, routing engine or private-state
contract changes. Use isolated fixtures; leave the user's preview data intact
until the explicit preview restart at completion.

Regression coverage must measure actual browser button geometry, confirmation
cancel/ESC/keyboard/success/blocked/stale/no-JS, consecutive inline operations,
removal/copy visibility, dirty-editor preservation, feedback visibility and
navigation landing. Both Chromium/WebKit and 360/768/1440px remain required.
Each stage is tested and committed; one fresh scoped review follows the work.
