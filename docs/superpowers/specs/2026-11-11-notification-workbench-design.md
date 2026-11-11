# Notification Workbench Design

## Intent and Chosen Approach

The user currently has one account and wants flexible source management and notification controls with a clearer, smoother interface. They selected reusable notification policies with sources bound to those policies. Flexibility belongs in these independent responsibilities, not in speculative multi-tenancy.

Alternatives considered:
- Global defaults plus independent source settings: shorter implementation, but shared behavior must be edited repeatedly and drifts across sources.
- Reusable policies plus source bindings: selected by the user; shared edits affect all bound sources, with explicit limited overrides.
- Arbitrary rule engine or multi-user subscriptions: rejected for this release because neither the requirements nor the current deployment needs their execution, authentication and isolation complexity.

This is a product change, not a cosmetic reskin. Collection and notification are currently coupled, the daily delivery counter is global despite living in source configuration, and the Web editor replaces entire source blocks. The implementation must correct those semantics rather than merely rename fields.

## Goals and Non-Goals

Goals:
- Independently configure acquisition, filtering/enrichment, notification timing and the existing destination.
- Reuse editable policies, clone them, assign them to sources, and override individual notification fields explicitly.
- Show the effective rule, its origin, affected sources and a read-only preview before applying a change.
- Make source CRUD and policy CRUD usable on desktop and mobile with keyboard navigation and clear success/failure feedback.
- Retain existing state, delivery recovery, callback security and CLI contracts.

Non-goals:
- No accounts, authentication product, per-user subscriptions, multi-target routing or shared multi-tenant state.
- No arbitrary expressions, policy nesting, event scripting, weekday calendars, time zones per policy or new outbound channels.
- No cross-source digest merging: one source still owns one card/batch. A shared policy is a reusable configuration, not a shared message or quota bucket.
- No remote feed discovery claims, automatic knowledge-base writing, real deployment changes or automatic sending during setup.

## Domain and Configuration

Configuration stays in sources.yaml so sources, references, slots and policy edits can be validated in one atomic transaction. No database or extra policy configuration file is required.

Existing top-level chat remains the only destination. The UI calls it "Notification destination"; it still accepts only the existing oc_ chat ID format. Keeping destination separate permits a later routing feature without inventing user records now.

New optional top-level sections:
- notifications: paused, quiet_hours, max_cards_per_day, min_gap_min. These are hard global delivery gates when explicitly configured, including for legacy sources. Absence preserves existing behavior. Zero daily limit means no delivery, not unlimited; zero gap and empty quiet hours disable their respective gates.
- policies: mapping keyed by stable policy ID. Each has a display label, mode, slots, form, quiet_hours, max_cards_per_day, min_gap_min and bypass_source_limits.

Policy fields:
- mode: immediate, digest or collect. Immediate sends pending items on a service tick as gates allow; digest sends at selected named slots; collect materializes browsable local batches without sending.
- slots: required nonempty list of existing slot IDs only for digest. Slots always use the service's existing local time zone; the UI shows that zone and the actual times, not just IDs.
- form: card or append_card. Digest card uses a source/date/slot batch. Immediate card creates a new batch/card for each eligible notification operation, with a persisted source/date batch sequence; it must not accidentally reuse a full daily card and strand remaining pending items. Immediate append_card updates a stable source/live daily batch with the existing rolling window. Digest append_card updates a stable source/date/slot batch. Collect uses state_only internally.
- quiet_hours: empty or a validated HH:MM-HH:MM window, including across midnight. Invalid formats/ranges are rejected, never treated as an empty window.
- max_cards_per_day: nonnegative per-source delivery-operation limit; min_gap_min: nonnegative per-source minimum spacing.
- bypass_source_limits: false by default. When explicitly true, bypasses only that source's quiet hours, daily limit and spacing. Never bypasses global gates, policy timing, source enablement, card limits or min_items. The UI labels this an exception rather than "high priority" and displays its exact effect.

Each source may have notify.policy and notify.overrides. Overrides can contain only mode, slots, form, quiet_hours, max_cards_per_day, min_gap_min and bypass_source_limits. Unknown keys and unresolved policy IDs are rejected. There is one inheritance level: policy followed by explicitly present source overrides. An empty string or zero is an intentional override; an absent key inherits. There is no inheritance from another policy.

An empty sources mapping is valid and renders an actionable empty state; deleting the last source must not require leaving a broken placeholder behind. An empty slots mapping is valid only when no acquisition or notification policy references slots. Existing missing/invalid required configuration still reports errors rather than silently constructing a deployment.

The default values for newly created policies are immediate, no slots, card, empty quiet hours, 12 operations per source per day, zero gap and bypass_source_limits=false. Presets are UI starting values, not hidden immutable policies:
- Morning/evening digest: digest at the existing am/pm slots when present; otherwise require explicit slot selection.
- Continuous updates: immediate with append_card, 10-minute per-source gap and a 23:00-07:30 quiet window.
- Collect only: collect. The user must save a named policy before a source can bind to it.

No policy is automatically created or assigned to a user's existing configuration. New-source setup prompts for a policy; old sources remain explicitly labeled "Legacy inline strategy" until the user converts them.

## Effective Behavior and Limits

Order of responsibility:
1. Source enabled and acquisition schedule determine what is fetched.
2. Source filters determine what is durably collected; enrichment waits until items are selected for a batch.
3. Policy plus explicit source overrides determine notification timing and local gates.
4. Global pause, quiet hours, total daily operations and total spacing constrain every remote delivery, regardless of local exceptions.
5. Minimum items, card element/byte budgets and delivery recovery still apply.

Counters count confirmed delivery operations, including append-card updates, preserving the current meaning of note_card. The UI says "Notification operations" and explains that updating an existing card also consumes budget. Failed remote effects do not consume budget; confirmed effects with incomplete local bookkeeping do, once, by delivery operation ID. New per-source counters are additive to the existing global ledger and retain idempotent completion repair.

For the first use of per-source accounting on a pre-existing day, derive the known source counts and last-operation time from that day's confirmed delivered events under the workflow lock, then persist the new accounting version. The global total is never reset. If confirmed global operations lack attributable retained events, display the historical attribution as incomplete; do not fabricate a per-source count. Policy-bound remote delivery with enforced local budgets fails closed for that incomplete day, with a next-day/receipt-investigation hint; previews show this before conversion. The global hard cap remains authoritative. Subsequent completion records source identity with every operation, even when the source still uses a legacy policy, so changing a binding cannot reset its known local count.

Limits on a shared policy are independent for each source; the global limit is shared across sources. Policy changes do not reset counters. Changing a destination, policy or form cannot redirect or recreate an already pending remote operation: repair uses its persisted target and identity first.

Pause suppresses new remote sends/updates but does not stop acquisition or local completion repair. Global limits do not prevent collect-mode local batches. Source disablement stops both new collection and new notification for that source, retaining queued content. Remote effects already confirmed may finish their local receipts even while paused/disabled.

## Collection and Notification Scheduling

New policy-bound sources use two separate phases:
- Collect: adapter fetch, filter and dedup, durable pending write, then acquisition cursor/attempt timestamp update. Advance only after collected items are safely persisted. This phase does not send, enrich or mark items pushed.
- Notify: load pending/unfinished work, resolve current policy, run gates, then reuse the existing batch/enrichment/render/send/completion workflow. It does not fetch or advance the acquisition cursor.

The first unforced polling pass still establishes a baseline without replaying all historical feed items. Existing acquisition schedules remain slot/poll/manual. Pausing notifications or entering a quiet window does not suppress collection for policy-bound sources; the legacy branch preserves its previous poll behavior.

Service tick order is collection slots, due polling collection, then due notifications. A notification slot can therefore use content collected in the same tick. Reuse the 15-minute slot catch-up window and bounded three-attempt retry semantics; persist separate acquisition and notification progress so successful acquisition is not rerun after a send failure.

Digest timing is tracked per source/date/slot. No content or blocked gates are intentional outcomes for that slot, not infinite retries. Deferred items remain pending for the next configured slot. A transient delivery failure retries only that source within the window. Configuration changes do not repeat already completed slots. If a slot is moved after completion, it does not send again that date.

Immediate policies are checked every service tick, even when acquisition was throttled. Once a quiet window or minimum gap ends, pending content can send without another fetch. Catch-up preserves oldest-first order; one service tick attempts at most one new delivery operation per source rather than draining the whole backlog into many messages.

Transient immediate delivery failures use the same three-attempt limit with at least one minute between attempts. Exhausted or unknown-result work stays visibly pending and requires an explicit retry or receipt investigation; new content must not reset an unfinished operation's retry budget. Existing uncertain-send idempotency-horizon protection remains in force. Changing an unrelated policy setting cannot reset that protection.

An explicit pending retry may reset the bounded attempt counter only when reusing the existing operation identity within the supported idempotency horizon or for a positively known pre-send failure. Never clear send_started_at/send_uuid or create a new identity to evade that protection. This release does not implement a remote-outcome reconciliation writer: after an unknown send exceeds the horizon, show the source, destination, card ID, operation ID, time and actionable platform-receipt investigation hint, without credentials, and keep it blocked. Do not provide a force-resend button or advise editing private state to bypass protection. Verified reconciliation is a separate reliability feature; the UI must not claim this unresolved outcome is automatically repairable.

Collect mode creates local batches at acquisition time and marks only materialized items consumed, with no network send. It uses the existing state_only behavior and card-independent item capacity. The UI must distinguish collected, waiting-to-notify and successfully notified content.

Changing between digest/immediate/collect does not discard pending content. A change to collect materializes pending locally on the next eligible processing tick; returning to a sending mode does not resend those consumed local batches. The before/after preview must state this consequence. A manual acquisition source may still notify previously collected pending content automatically under an immediate/digest policy; "manual" describes acquisition, not notification.

Manual operations are explicit:
- "Collect now" fetches once, preserving the selected policy; it does not force a send.
- "Notify pending now" consumes existing pending only. It may bypass the digest clock after an explicit confirmation, but never bypasses pause, global gates, local limits or element limits.
- Existing run/source and run/slot semantics remain unchanged for legacy sources. For policy-bound sources they collect and evaluate normal notification eligibility, not force sends. New CLI actions expose separate collect/notify entrypoints.
- Read-only preview uses candidate configuration, pending counts and current budget state; no network, model, state writes, acknowledgements or send side effects. It does not promise future new content or exact future API success.

## Compatibility and Editing Contracts

Sources without notify.policy keep legacy fetch/deliver semantics and public commands. Legacy inline budgets continue using the old global counter and high-priority behavior unless explicit new global gates constrain delivery. The interface states this scope clearly rather than pretending the old value was per-source.

When a user converts a legacy source, display before/after behavior and require confirmation. Map its delivery form and schedule into a new or selected policy. If its old budget was globally scoped, explain that the new local budget is per-source and recommend an explicit global cap; do not silently reinterpret it. Original legacy fields can remain stored for rollback but do not participate in policy-bound delivery. Unbind reverts to those fields, with a visible preview.

Policies are identified by stable ASCII IDs. Display labels can change; IDs are not renamed in place. Copy creates a new ID. Delete is rejected while any source references the policy; the user must rebind first. Policy saves report all referencing sources, including disabled sources and overriding sources, with effective changes.

Source edits must preserve unknown adapter-specific keys, existing advanced values, card settings and absent-versus-explicit overrides. Add a source patch CLI contract for editing existing blocks; the Web form translates only provided edits into that contract, using the current file hash. Existing source set keeps its replace semantics. A failed candidate validation or optimistic-hash conflict writes nothing. Explain any loss of comments inside replaced blocks; unrelated blocks remain byte-preserved.

Global chat/slots/notifications and policies need validated CLI handlers before their Web forms exist. Slot removal/rename fails if acquisition or notification references remain. Slot time ranges, quiet-hour windows, nonfinite/negative numbers, duplicate IDs and unresolved references fail before writing. Global edits preserve unrelated source/policy blocks.

All new commands support the existing Result envelope, --json, --dry-run for writes, --base-hash, accurate changed and actionable hints. Actions remain code-whitelisted. Preview and describe expose effective values, origins and pending reasons rather than reading private state from a host.

## Frontend and Interaction

Keep Python server-rendered pages and add a small local vanilla-JavaScript enhancement layer, with no frontend build chain, external CDN, third-party telemetry or runtime font downloads. Reuse the existing blue/slate visual language; improve hierarchy and density rather than inventing an unrelated brand. Update the old zero-script design/test contract deliberately: only a same-origin, code-owned static asset is allowed, not arbitrary inline scripts or external resources. No JS is required to read pages or submit core forms.

Navigation: Overview, Sources, Notifications, Items, Operations. Keep existing /, /config, /items, /ops and batch deep links. /config remains the Sources route; add /notifications for policies/global controls. Sources use query-string deep links for create/edit/copy; Notifications uses query-string links for policy editing. A single shared page shell owns navigation and responsive layout.

Sources:
- Search/filter by name, enabled state, adapter and policy; rows show acquisition cadence, assigned/effective policy, status and pending count.
- Primary action is "Add source". Start with source name, adapter/feed addresses and policy; filtering, processing and adapter-specific settings are grouped sections rather than one long undifferentiated form.
- RSS/RSSHub setup supports direct addresses and the existing tested local catalog. A source copy preserves configuration but creates a new ID; no automatic activation or sending.
- Each field has visible help and validation feedback. Advanced sections preserve values when collapsed. Policy overrides have an explicit inherit/custom selector rather than storing flattened effective values.

Notifications:
- Global pause and hard gates are distinct from policy templates; show current total budget consumption and destination/time zone.
- Policy list shows label, plain-language rule and bound-source count; create/copy/edit/delete are explicit.
- Editing shows an effective-rule summary and affected-source list. Save validates first; preview does not alter service settings. A policy update cannot send a card as a hidden setup side effect.

Overview focuses on service health, recently collected/notified content, waiting work and actionable warnings such as restart-required resources. Items and Operations retain their existing contracts; Operations distinguishes collection from pending notification and favorite ingestion receipts.

Interaction requirements:
- JS enhances forms with pending states, prevention of duplicate clicks, local validation and result presentation using the same CLI envelope. Preserve inputs, scroll and open sections on validation/network errors.
- Use Accept: application/json for enhanced actions; normal HTML submissions retain POST/Redirect/GET to their own page, not always the Overview.
- Update client-side text with textContent, not untrusted HTML. All server-rendered input remains escaped. No hidden automatic retries of writes after uncertain network outcomes.
- Save result distinguishes preview/no change/applied/failed/restart required. Successful writes refresh hashes and affected read contracts. Conflict feedback keeps the user's edits and requires refresh/reconciliation, never auto-overwrites.
- Keyboard-visible focus, labeled controls, inline error associations and an aria-live result region. Destructive actions require confirmation. Focus moves to the first invalid input or result summary when appropriate.
- Responsive layouts at 360px, 768px and desktop; no page-level horizontal scrolling. Motion is restrained and honors prefers-reduced-motion; content never depends on animation to become visible.
- Unsaved edits warn before internal navigation. Do not persist drafts, credentials or tokens in localStorage.

## Architecture and Security

- config.py remains the authoritative parsing/validation/schema facade; a focused policy module owns resolution and effective-rule construction.
- pipeline.py orchestrates reusable collection and delivery stages; focused helpers must not grow a second send/recovery implementation.
- service.py owns due-work selection and independent progress; state.py owns durable pending, acquisition receipts and budget ledgers.
- CLI/edit own validation, serialization, atomic writes and optimistic concurrency. actions.py only translates forms; HTTP never writes YAML itself.
- Separate page shell, source/policy form rendering and local assets from the existing large view.py where useful. The view contract builder remains the shared read source.
- Preserve the actions-enabled switch, same-origin validation and process token on every new action, including JSON-enhanced requests. Same-origin static assets contain no credentials or state tokens. No arbitrary return URLs or file paths from forms.
- Runtime listener, credential and permission changes remain restart-required; policy/source/slot changes reload through the existing last-known-good mechanism.
- Preserve single-WS ownership, durable callback deduplication, explicit failure events and favorite receipt rules. This project never writes the knowledge base.

## Delivery Stages and Acceptance

Commit after each tested stage:
1. Policy/global configuration, resolution, validation, schema and legacy compatibility.
2. Collection/notification separation, scheduling, per-source/global accounting and interrupted delivery recovery.
3. Validated CLI edits, previews, source patching and action translations.
4. Responsive page shell and source/notification workbench with progressive enhancements.
5. Browser/offline contract verification, documentation and independent review corrections.

Before final completion, run the full unittest suite on Python 3.11/3.12/3.13, scan_secrets.py and git diff --check. For card-related changes run doctor against temporary configuration with fake credentials and no real send. Browser verification uses a temporary news directory, inbound mode none, localhost with actions enabled and mocked/no network effects.

Critical acceptance examples:
- Poll every 10 minutes but notify only at am/pm; sending at am reads pending and never refetches.
- Two sources share a policy but have separate local counts; the global cap still bounds their combined operations.
- Pause/quiet hours continue collection; resume immediate notifications without an extra fetch.
- A legacy source remains behaviorally unchanged after merely opening the new UI.
- Editing an unrelated field preserves unknown source keys and explicit inheritance states.
- Save failure/conflict retains form input; a cross-origin or missing-token JSON action cannot mutate state.
- A crash after remote success repairs local budgets/events once without a duplicate message.
- Disabled sources, missed slots, manual sources, collect-only sources and empty pending queues have distinct truthful states.

## Decisions Left to Implementation

Exact Python helper signatures, extraction boundaries, CSS spacing, icon shapes and filenames are implementation choices. The designer owns no new user-management model, rule language, provider or deployment step. Minor choices may be settled and documented during implementation; changes to scheduling, budget scope or compatibility guarantees require updating this design.

## Design Check Record

The independent builder check identified two material ambiguities, both settled within the chosen scope:
- Immediate card creates separate cards; append_card alone reuses the daily live card. This prevents a full default card from stranding the rest of the day's content.
- Unknown remote-outcome reconciliation is not added to this workbench release. Protected retry and truthful blocked-state guidance are included; a verified reconciliation writer is not. A UI form cannot safely invent evidence of remote success.

Additional designer decisions: empty source registries are supported; acquisition and notification progress are separate; partial historical budget attribution fails closed rather than inventing usage; existing sources are never auto-migrated. These are proposals in this design, not assumptions about an already approved implementation.
