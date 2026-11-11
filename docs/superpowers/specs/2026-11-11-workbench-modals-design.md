# Focused Workbench Editors

User approved the unified approach: pages show summaries/lists; source and policy create/edit/copy, global settings and RSSHub settings open in dialogs. Secondary explanations use help icons with hover, focus and touch activation. Critical errors, explicit confirmations and save outcomes stay visible.

## Approach

Keep the existing SSR, schema authority, CLI actions and local JavaScript. A code-owned editor panel is rendered only for explicit editor queries. Links remain usable without JS; JS imports the same escaped panel into one native dialog, with no second client renderer or writer. A drawer would contradict the requested popup; separate editing pages remain only the progressive fallback.

## Interaction

Normal pages contain no permanent editor form. Editor headings distinguish create/edit/copy. Dialogs have a close control, a scrolling body and accessible focus/keyboard behavior. Closing a dirty dialog confirms intent and retains its DOM draft for the current page only. Switching editors preserves dirty sibling drafts and their original hashes. Leaving/reloading still warns. No nested dialogs or persistent browser storage.

Only a confirmed non-dry save of the unchanged submitted revision closes the editor and refreshes summaries/lists. Preview, dry-run, probes, validation/conflict/transport failures and edits made while saving keep it open. Busy dialogs cannot close. Create forms never turn silently into overwrites. Reopening a saved clean editor reads fresh SSR; reopening a dirty editor restores its existing draft/hash.

GET editor loading is code-owned, same-origin, whitelisted to /config and /notifications. Validate imported content/actions; missing, unsafe or failed responses preserve the current editor and show a visible error. No automatic navigation or write retries. No-JS PRG reconstructs the correct explicit editor and preserves drafts/hash on preview, dry-run or failure; refresh cannot repeat POSTs.

## Information Hierarchy

Global and RSSHub settings become short summary strips with settings links. Lists lead with name, essential behavior, status and primary edit action. Technical limits/identifiers and schema help are secondary. Policy editors show name/mode/schedule first, with advanced delivery/limits folded. Source filtering/processing/legacy appearance remain folded. Save buttons clearly reflect validation versus actual save while retaining default dry-run and all backend gates.

Help uses code-owned escaped markup; hover/focus open, click can pin, Escape dismisses help before a dialog. Mobile/edge positioning must not clip content or overflow. No-JS focus/click fallback remains available. Inline validation errors must not be hidden as tips. Dangerous confirmations remain explicit.

## Boundaries

Python 3.11+, no new runtime dependencies, no new schema/writer/client renderer. Existing origin/token/actions gates and optimistic hashes remain. No production credentials, providers, sends, WS, acknowledgments, deployment, merge or push. Only guarded offline preview is restarted. Implement inline with stage commits and one final fresh read-only reviewer.
