# Policy-Owned Times And Read-Only Source Bindings

## Intent
The user approved independent policy sending times and sources that select a
managed policy rather than override its parameters. A policy edit affects all
its bound sources, never another policy. Keep the SSR/schema-driven dialogs.

## Approach
Global sending-time references perpetuate the ambiguity; merely hiding source
overrides conceals actual behavior. Use policy-owned `times` mappings of stable
internal schedule keys to HH:MM clocks. Keys are not editable user-facing IDs;
they preserve completed schedules and unfinished delivery identities. Global
`slots` remain acquisition-only after an explicit compatibility migration.

Legacy configurations remain readable and behave unchanged. An atomic CLI
`notification migrate` freezes referenced clocks into each policy and moves
each source's effective legacy overrides into a new independently editable
policy. Preserve zero, false, empty values, unknown source keys, receipts and
all state. Dry-run reports the exact policy/source changes; stale hashes reject
the entire operation. Until migration, clearly label legacy dependencies; do
not silently clear or introduce overrides through source saves.

## Interaction
Policy dialogs allow multiple native time inputs, adding/removing rows, with
no two-choice limit. Hide inactive time controls outside digest mode. Explain
the exact bound sources affected before save. Source dialogs offer a policy
dropdown, read-only parameter display, create/edit/manage links, and no override
inputs. Changing a dropdown updates the displayed policy. Drafts survive
switching between source and policy dialogs; show how to resume a source draft.
Legacy delivery settings are not another editable override while bound.

## Constraints
All mutations use the existing CLI handlers, action whitelist, same-origin
token gates, default dry-run and optimistic hash checks. Do not touch live
configuration, providers, scheduler, credentials, sends, knowledge receipts,
deployment, merge or push. Use only guarded synthetic preview. Commit stages.
