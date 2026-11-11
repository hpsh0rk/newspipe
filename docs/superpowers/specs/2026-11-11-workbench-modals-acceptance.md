# Focused Workbench Acceptance

## Delivered

- Source/policy create, edit and copy, plus global/RSSHub settings, use one progressive native dialog. Ordinary pages contain summaries and lists, not permanent editors.
- Dialog close/cancel/Escape preserves dirty drafts only within the current page. Busy editors cannot close. Preview/probe/dry-run/error/conflict keep the editor open; a confirmed save closes only when its submitted revision is unchanged.
- Dirty sibling drafts retain their own values and original hashes. Create/copy forms never silently become overwrites. No-JS PRG retains the editor kind, raw controls and optimistic hash without replaying POSTs.
- Secondary explanations use inline native popover buttons with hover, focus and click behavior. Advanced policy fields are folded, not removed. Errors, delete consequences and receipt conditions remain visible. Validation/save button labels also work without JavaScript.
- Shared inert tag/attribute checks reject resource-loading markup and uncontrolled/clobbered forms before importing editors or read regions. POST enhancement prevents native navigation before accessing form properties.

## Verification

Final verification after the safety fix:

- Python 3.13 integrated: 650 tests, all passed, zero skips. Chromium and WebKit enabled.
- Python 3.11 and 3.12: 650 tests each, passed; 35 browser tests intentionally disabled in these unit-only runs. Node-based JavaScript tests required and executed.
- One independent read-only branch review found two Important import-boundary issues. Both were reproduced in Chromium/WebKit, fixed in one pass and verified RED to GREEN. No Critical or deferred Minor findings.
- Real-browser cases cover normal loading/save, unsafe loading, retained drafts/hash, pending saves, no-JS PRG, native validation, tooltip focus/touch/escape, read refresh and desktop/mobile action geometry.
- Synthetic 360px/1440px screenshots inspected. No horizontal overflow or external/provider calls during guarded preview capture.
- Secret scan and `git diff --check` passed. No new runtime dependencies or alternate configuration writer.

## Boundaries

Only code, tests and documentation were changed. No production configuration, credentials, provider calls, sends, long connections, acknowledgments or deployment were used. The local preview uses temporary synthetic data and denies provider effects. Branch `feat/workbench-modals` is retained without merge or push.

Stage commits: `5203a9a` (dialogs), `18dbff3` (focused summaries/help), `003f06b` (inert imports and controlled submits).
