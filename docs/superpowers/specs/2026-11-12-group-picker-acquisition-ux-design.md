# Group Selection And Acquisition UX

## Intent And Approved Scope

The user approved the proposed four-part scope: select a notification group by
name from the sending bot's joined groups; hide acquisition schedule IDs;
manage acquisition schedules on the source page rather than notification
settings; and show source acquisition controls relevant to the chosen trigger.
The purpose is to reduce manual input and make acquisition, notification and
shared-setting effects understandable for the current single-user deployment.

The user approved this implementation design on 2026-11-12 and requested
execution. Product implementation follows the corresponding staged plan.

## Approaches

1. Keep manual chat IDs and editable acquisition IDs, adding explanatory text.
   This is cheapest but retains both causes of confusion and more reading.
2. List all groups joined by the human user and turn acquisition clocks into
   reference IDs. This looks simpler but can select unsendable destinations
   and breaks stable source/scheduler references when clocks change.
3. Use the configured sending bot's joined-group directory, retain hidden
   stable acquisition keys, and separate acquisition from notification UI.
   The user selected this approach because it removes unnecessary input
   without changing configuration ownership or delivery identities.

## Goals And Non-Goals

- Group selection supports names, search, pagination, refresh and clear errors.
- Users edit acquisition clocks, not internal keys, and see affected sources.
- Keep current behavior, source references, retries and historical state intact.
- Keep SSR, native dialogs, schema-driven fields and progressive enhancement.
- No new frontend framework, account system, general rule engine or dependency.
- No automatic group joining, permission grant, discovery, test send or WS.
- No production configuration changes, knowledge writes, deployment or push.
- Do not remove stable IDs from persisted configuration or the existing CLI.

## Group Directory Boundary

Add an explicitly invoked, read-only CLI operation for joined groups and expose
it through a code-owned action. Its exact command/action names are a builder
decision. It uses the same configuration-scoped channel instance as delivery,
including service-bound transports, instead of resolving a different default
application or reading a human user's credentials.

Direct Feishu uses tenant authentication and the joined-chats API
`GET /open-apis/im/v1/chats`. The external CLI backend reuses its existing child
environment/application configuration and explicitly requests bot identity.
Never fall back to a different backend or user identity. If a custom backend
cannot provide this optional capability, return an actionable unsupported
result; do not widen the required CardChannel protocol and break custom senders.

Return only the fields needed for selection (chat ID, group name and optional
external-group indicator), plus pagination state. Do not return member lists,
messages, tokens, provider bodies or arbitrary provider exceptions. Validate
the response shape, pagination state and chat IDs. Deduplicate repeated IDs,
escape names, and distinguish empty success from missing credentials, missing
`im:chat:read`, bot capability, transport or malformed-response failures.

Each explicit action reads a bounded page with bounded network/response budgets;
no unbounded automatic pagination or silent truncation. The selector clearly
offers loading the next page when more results exist. Search filters the
groups fetched so far and states when more groups remain, rather than claiming
that an unfetched group does not exist. Numerical budgets are left to the
builder and must be tested. Only use a provider search endpoint if its results
are proven restricted to joined groups; joined-group enumeration is sufficient.

The CLI uses the normal result envelope (`changed: false`) and API description.
The web action uses the existing actions switch, same-origin check and process
token even though it is read-only. Page/view loading never calls Feishu.

## Group Selection Interaction

Global notification settings start with a group-name selector and explicit
load/refresh control. Search and selection are keyboard- and touch-accessible.
Use native controls where possible; do not introduce a bespoke combobox unless
necessary. Explain that groups belong to the sending bot, not the human user.
The configured target remains available before directory loading, after errors,
on other pages and when absent from the fetched list, with an honest
name-unavailable/not-in-loaded-results label. Do not call it invalid merely
because a paginated list did not contain it.

Selecting a row updates only the unsaved `chat` draft. Refresh/search/pagination
must not change the selected chat, save configuration, advance the optimistic
hash or clear unrelated edits. Keep chat ID details and manual entry in an
advanced fallback with exactly one authoritative submitted chat value. Failed
loads offer retry, permission guidance and the fallback without an empty-success
message. Do not persist directory data or drafts in browser storage or disk.
Discard obsolete async results when their editor/request context has changed.

No-JS users can explicitly load a page through the same read action and choose
from server-rendered options. PRG retains the complete original draft and
directory result in bounded one-use process memory. Refreshing the result page
does not repeat the provider call. Group loads never count as saved/validated
configuration. Real saving continues through `global set`, not new web writers.

## Acquisition Schedule Ownership And Interaction

Move the acquisition schedule summary/editor to `/config`, with a clear
"acquisition times" entry point from the source editor. Notification settings
contain only the group and global notification gates. Leave a compact link to
acquisition settings where it aids discovery, rather than duplicate editors.
Source acquisition summaries and options display clocks rather than `am/pm`.

Acquisition rows have one native time input, add/remove controls, and a hidden
stable key. Existing keys survive clock changes. New rows obtain collision-free
internal keys in the server-side action translator; they are not based on a
rename of existing clocks. Do not render spare empty rows on an existing
schedule; an empty schedule has a useful empty state and an add action.
Use 24-hour HH:MM labels, with an explicit 24-hour hint for native time controls
whose presentation can depend on the browser/OS locale.

Each existing row shows the count and accessible details of affected sources,
including disabled sources. Show any legacy notification policies/source
overrides that still reference it. Editing such clocks preserves existing
legacy semantics and warns that legacy sending times are also affected, with a
link to explicit notification migration. Do not silently migrate or freeze
legacy behavior as a side effect of editing acquisition settings.

Removing a referenced row is visibly explained as unavailable, and authoritative
CLI validation still rejects referenced removal or tampering. Unreferenced rows
can be removed. Preserve legal legacy equal-clock entries as separate stable
plans: disambiguate them with affected-source details or neutral plan numbering,
not editable internal IDs. New equal-clock rows need not be prohibited unless
existing configuration validation requires it; never deduplicate legacy keys.

The acquisition form submits only `slots` through the existing `global set`
handler, not the target or notification gates. Retain full added/removed time
rows and stable keys after no-JS validation failures and dry runs, including an
all-removed form. Use an explicit form scope to restore the correct editor on
`/config`. Default dry-run, optimistic hashes and atomic reference validation
remain unchanged. Save success refreshes acquisition summaries/options without
discarding existing source drafts or falsely syncing their configuration hash.

## Related Form Cleanup

Source trigger choices use plain labels: scheduled acquisition, periodic
acquisition and manual acquisition. Scheduled acquisition shows clock choices,
periodic acquisition shows only its interval, and manual acquisition shows
neither. Avoid a native multiple-select that requires Ctrl/Command knowledge;
use accessible clock checkboxes or an equivalent native selection pattern.

Hidden inactive controls retain their values in the draft and do not silently
clear persisted configuration. Changing trigger changes the active presentation,
not unrelated fields. Without JS, show the controls with clear applicability
labels so the form remains usable. Field metadata remains owned by config.py.

Compact boolean rows and align labels/help icons with their controls. Put
repeated explanations in accessible help, but keep consequences such as shared
updates, no-save results and zero-versus-unlimited visible when actionable.
Keep the established dry-run default. Make check/preview/save button labels and
feedback consistent across source, policy, global and acquisition dialogs; do
not redesign the mutation safety flow or make a default submission write.
Apply the same time-row spacing and spare-row cleanup to policy dialogs where
appropriate, retaining their independent ownership and full draft behavior.

## Verification And Stages

1. Design/plan commit, then read-only group capability with mocked backend and
   CLI/security tests.
2. Group selector and no-JS/async/draft acceptance, committed separately.
3. Acquisition ownership presentation, hidden keys and source form cleanup,
   with reference-preservation and no-JS tests, committed separately.
4. Fresh read-only review, desktop/mobile keyboard/touch checks in Chromium and
   WebKit using guarded synthetic groups/configuration; fixes in a final commit.

Run the full unittest suite with required JS and browser gates, secret scan and
`git diff --check`. Extend the offline fixture to deny all real directory
transports/credentials as well as sends; synthetic groups must never read the
user's real Feishu data. Do not claim real Feishu acceptance from offline tests.
Restart only the guarded local preview after verifying its process identity.
No automatic merge/push is authorized for this new work.
