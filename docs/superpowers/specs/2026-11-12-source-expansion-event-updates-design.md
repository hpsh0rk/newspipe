# Source Expansion And Story Updates

## Status And Intent

The user selected independent newspipe adapters and layered deduplication,
without running or depending on Horizon. They explicitly want later progress
and different viewpoints appended, with the differences visible, rather than
discarded as duplicates or overwriting earlier information.

This document translates that direction into a proposed implementation design.
The detailed design awaits user approval; product implementation has not begun.
The user previously requested a separate commit for each implementation stage.

The goal is less repeated interruption without losing new facts or provenance.
The current deployment is single-user. Retain newspipe's existing acquisition,
notification policies, delivery identities, callbacks and favorite receipts.

## Approaches And Selection

1. Replace newspipe or run Horizon as a content backend. The user rejected
   using Horizon; it also introduces a second configuration/runtime boundary.
2. Copy its scraper and AI pipeline wholesale. This transfers useful source
   coverage but imports its dependencies, batch-first processing and publication
   assumptions. It does not solve newspipe's cross-run notification semantics.
3. Add source capabilities behind newspipe's adapter boundary and independently
   implement exact matching, candidate retrieval and optional AI comparisons.
   The user selected this direction. Reuse compatible MIT code selectively,
   retaining attribution, not the Horizon runtime or configuration model.

## Scope And Non-Goals

- Cover Horizon's ten source families, including improving existing sources.
- Keep paid or authenticated collection optional, disabled until configured.
- Preserve immutable observations, source links and append-only story history.
- Distinguish duplicate facts, new facts, different viewpoints and uncertainty.
- Provide preview/undo before enabling cross-source notification suppression.
- Bound network, comparison indexes/inputs and model expenditure.
- Preserve current schema-generated forms and the single CLI mutation path.
- No tenants, external database service, generic workflow engine or framework
  rewrite. A local index implementation is a builder decision within Store.
- No account login, private-channel access, paid actor execution, real model
  requests, real sends, knowledge writes or production configuration changes
  during development/acceptance without a separate explicit request.
- Independent editable processing profiles, deep background searches, category
  quotas and a source marketplace are follow-up proposals, not this scope.

## Existing Boundaries

Adapters currently return dictionaries through fetch(config, since). Source
extras pass through SourceConfig.adapter_config; config.describe_schema and
source_values are the authoritative form metadata/current-value contract.
HN and Reddit currently have narrower settings and time handling than Horizon.
RSS already supports ordinary RSS/Atom URLs as well as configured RSSHub routes.

dedup.fresh uses source + ext_id and pushed IDs. Pending queues and displayed
items are source-owned; the pipeline must not treat observed content as remotely
delivered. Existing state_only/collect completion records do not prove remote
delivery and cannot satisfy new notification deduplication.

append_card currently appends to a source's daily card and archives rows beyond
its cap. Card updates are charged operations, with a frozen delivery payload,
stable remote identity and increasing sequence. Keep those guarantees intact.
Operational events in events.py are not news stories: use a separate story
domain and contract, never reinterpret delivered/clicked/favorite ledgers.

## Source Capability Contract

Introduce an explicit code-owned adapter descriptor and a normalized fetch
result. Descriptors declare settings, labels, supported collection modes,
optional dependencies and credential *references*, not credential values.
config.py remains the sole public schema/value source, composing descriptors
rather than creating a separate web schema. Adapters do not render forms.

Fetch results distinguish successful empty, success, partial failure and total
failure, with bounded per-subscription diagnostics and a safe high-water mark.
No missing credentials, unsupported mode or inaccessible endpoint becomes an
empty-success response. Scrub provider bodies, URL secrets and exception text.
Honor since consistently; do not advance a watermark beyond failed partitions
and lose their unread content. Multi-subscription collection therefore needs
per-subscription cursors or equivalent retry-safe partitions.

Each normalized item carries stable native identity, source/subscription,
content kind, title, source text, publication/observation times, original
permalink and separately typed referenced URLs. Engagement, comments and trend
metrics are separate metadata, not an undifferentiated source-text blob.

Keep a compatibility adapter for existing fetch functions and legacy items.
New identity rules are versioned; do not truncate native IDs into collisions,
rewrite old IDs or replay old queues on upgrade. Preserve full settings and
credentials references through create/edit/copy/dry-run round trips.
Custom adapters remain usable; lack of new optional capabilities is explicit,
not an import-time failure or automatic fallback to another identity/backend.

All new transports use bounded requests, redirects, response sizes, retries and
concurrency. Feed-supplied linked URLs cannot reach private/loopback services;
explicitly configured local RSSHub addresses remain supported under a separate
trusted configuration boundary. No new SSR/view/callback path does collection.
Long fetches, browser work and AI comparisons run in bounded worker jobs, not
the card callback's three-second path or new global-writer-lock critical sections.

## Source Coverage

| Family | Coverage and constraints |
| --- | --- |
| RSS/Atom | Reuse generic feeds; improve conditional fetching, partitions and diagnostics without requiring RSSHub. |
| Hacker News | Configurable story selection, score/limit, dates and optional discussion; preserve article versus discussion identity. |
| Reddit | Communities/users, supported sorting/windows and optional bounded comments; surface blocking/rate limits instead of hiding failures. |
| GitHub | Public user events and repository releases. Reuse Atom where sufficient; API modes expose optional token/rate-limit status. Distinct events sharing a repository URL stay distinct. |
| OSSInsight | Repository trends by period/language/keywords; model periodic snapshots, not permanent repo-only IDs. Metric-only changes do not force new notifications. |
| Google News | Query/language/region settings translated to RSS collection; preserve aggregator and publisher identity without assuming every redirect can be resolved. |
| GDELT | Bounded DOC article searches with query/language/country/window; limits and observation time versus publication time remain explicit. |
| Telegram | Public web-preview channels only; retain message permalink separately from linked articles and label unavailable history honestly. |
| X | Initially an optional Apify connector for timelines/search and bounded reply expansion where the configured actor supports them. Paid requests require explicit consent/configuration. Browser-cookie collection is a separate future extension, not required for this delivery; no hidden account/provider fallback. |
| OpenBB | Optional SDK and provider-specific watchlists/news/filings capabilities; missing SDK/credentials/subscription produce actionable diagnostics. No provider-universal promise. |

Backend support and online availability are separate acceptance claims. Mocked
tests prove parsing/configuration/security, not that today's provider will allow
the user's account/IP. Live acceptance is separately authorized and documented.

## Observations, Stories And Revisions

An observation is an immutable source item/version. Its version fingerprint
uses source-native identity and meaningful source text/structured facts, not
translated AI output, crawl time or mutable engagement counters. Re-observing
identical content updates a last-seen/provenance index, not a new revision.

Existing completed legacy IDs are not automatically backfilled or replayed.
Reuse comparable original source snapshots from Store-owned batches where they
exist, never translated/generated text as an original baseline. If an old
completed ID has no sufficient original material, its first refetched version
establishes a silent baseline and an explicit migration-baseline warning.
Do not label that version confidently duplicate or changed. Later versions
are compared against that baseline. This deliberately favors no historical
replay; an update that happened before baseline establishment may be missed,
and the preview must disclose that limitation. Preserve legacy ID aliases and
all existing pushed records, pending entries and acknowledged favorites.

A story groups one concrete event, release or incident. Sharing a company,
repository, keyword or broad topic is not enough. A revision is an immutable
addition to that story: initial facts, progress, a viewpoint or a correction.
Late-arriving observations append at ingestion order while retaining their
reported publication time; do not reorder or renumber previously shown history.

Exact copies attach additional source references without creating an interrupting
revision. Distinct useful comments or social commentary are separately evaluated,
even when they link to an already known article. Different version numbers,
dates, quantities, named actors, decisions or contradictions are safety signals
against an automatic merge, not proof of truth or automatic relatedness.

Every classified revision includes story/revision IDs, predecessor/baseline IDs,
classification, evidence-backed differences, source references, confidence and
algorithm/model/prompt versions. Keep source text and evidence for inspection.
Differences separate new facts, changed claims and different viewpoints. Do not
describe one viewpoint as disproving another without supporting evidence.
If materials are insufficient, say so; do not invent an old-versus-new summary.

Candidate indexes and paid-result caches may have bounded windows and rebuildable
retention. That window limits recall, not the immutable story history. No new
automatic deletion of story observations or favorite evidence is authorized;
an explicit future retention policy must expose unavailable baseline material
and preserve references rather than pretending old evidence is still present.

## Layered Comparison And Cost

1. Native identity and conservative type-specific URL rules. Preserve meaningful
   query parameters, article versioning and event identity. Shared URLs alone do
   not merge social commentary, releases, public activity or rolling snapshots.
2. Normalized source-text hashes for exact copies, using versioned extraction
   rules that exclude known boilerplate without deleting meaningful facts.
3. Bounded local retrieval using time/type/entity constraints and lexical or
   fingerprint similarity. These find candidates only, not deletion decisions.
4. Optional model comparison of a small candidate group against relevant previous
   revisions, using bounded source evidence. Return duplicate, progress,
   viewpoint, unrelated or uncertain with validated references and differences.

No all-pairs model traversal and no default full-body prompts. Local multilingual
embeddings can be evaluated later; no remote embedding bill or vector service is
required for the baseline. Matching window, candidate count and evidence limits
are builder decisions, visible in diagnostics and tested for boundedness.

Provide off, observe and apply modes. Existing deployments default to off;
observe records proposed relations without suppressing notifications. Exact
matching can be enabled independently of AI; semantic suppression requires an
explicit apply choice after preview. Uncertain or failed comparisons retain the
item separately with at most a candidate relationship, never a confident merge.

Mode transitions have a durable activation frontier (configuration revision
and observation sequence), not a retrospective rewrite. New observations after
activation use the new mode. Previously queued items retain their old semantics
until an explicit preview-and-confirm pending migration; disclose that old
queue contents have not yet adopted cross-source suppression. Such migration
may only classify/migrate still-undelivered entries, preserves their originals
and native IDs, checks current configuration/queue versions and rejects changes
to an unresolved frozen remote operation. Switching off stops new comparisons/
suppression but does not erase completed dedup receipts or unresolved ownership.
Already displayed separate stories and callback snapshots are not silently
replaced; future related revisions can add links to that existing history.

Reuse model resolution and paid-result caching. Cache comparisons by immutable
inputs/baselines and extraction/prompt/model versions; changes invalidate only
dependent results. Add separate comparison call/token budgets under an overall
model budget so summaries cannot be starved silently. Reserve bounded spend
before transport; record provider usage when available and conservative unknown
usage on timeout. Optional reasoning tokens/retries count toward expenditure.
Show estimate versus measured usage, cache hits and budget fallbacks distinctly.

No model call occurs in a normal dry-run, page render or view query. An explicitly
requested comparison-evaluation job is a billable action, not disguised as a
dry-run. Tests and fixture previews deny all real providers and credentials.

## Notification Scope And Delivery Safety

Story identity can be shared, but notification completion is scoped. Proposed
default scope is destination + bound notification policy ID; legacy unbound
sources stay isolated until explicitly opted in. Sources with different policies
do not silently suppress one another. collect-only completion never creates a
remote notification completion. Changing a target/policy does not replay already
completed source observations; new revisions follow the new effective scope.

The first eligible source operation becomes delivery owner for a story revision
within a scope. Passing acquisition alone, being paused, waiting for a send
clock or failing the delivery gates cannot take ownership. Preserve source-level
and global budgets; this is deduplication, not a new cross-source digest sender.

Persist revision membership, scope, frozen comparison/difference snapshot and
owner operation before the remote attempt. Only confirmed remote success can
commit notification completion and suppress equivalent observations. Other
members remain durable and visibly waiting while an owner is unresolved.
Unknown remote outcome blocks alternative-owner sends; source disable/removal,
policy edits, restart and arriving new facts must not silently free ownership
or mutate the in-flight payload. Recovery reuses the existing identity/sequence
and retry rules; it never fabricates a receipt or automatically forces a resend.

Completion and suppression bookkeeping are idempotent and recoverable after
partial local writes. Suppressed observations have a separate terminal reason,
owner/revision reference and provenance; do not pretend each sent a card. Reject
source/policy deletion when it would orphan an unresolved owned operation, with
an actionable recovery explanation. No direct state edits as a recovery API.

## Append And Difference Presentation

Proposed meaning of append: add a revision to the story timeline and deliver
its delta through the existing effective policy. Do not silently switch the
user's card mode or create one forever-growing card per story.

- append_card adds an update row to the existing source's daily live card, with
  the story title, progress/viewpoint/correction label and concise differences.
- card/digest puts the delta in the next eligible card, not the whole prior
  article again. Cross-day updates use the current normal notification cycle;
  do not automatically rewrite yesterday's cards.
- collect adds history without sending. Paused notifications keep deltas pending.
- Story detail exposes the immutable history, source references and differences
  with bounded pagination. It is a drill-down, not another dense settings tab.

The current event revision and the last revision actually shown in a scope are
different baselines. Coalesce undispatched progress safely: notification deltas
must include all relevant facts/viewpoints since the confirmed shown baseline,
not just the most recently ingested change. Already frozen operations retain
their original baselines; later revisions wait for subsequent delivery.

Keep summaries compact; full evidence/history belongs in detail, not every list
row. Preserve old card callback paths, IDs, read/dismiss states and favorite
payloads. Favorites reference the clicked immutable revision and material; later
updates never rewrite an acknowledged favorite or trigger knowledge ingestion.
Expose stories through versioned CLI/view/read contracts, never private files.
Manual merge undo/split is auditable through CLI and the code-owned actions
allowlist with actions/origin/token/hash validation. Undo cannot retract a past
send or silently resend history; newly arriving content uses corrected grouping.

Test element and byte budgets for delta rows, multi-source links and bounded
history. Archive/page overflow without losing revisions or bypassing budgets.

## Stages And Acceptance

1. Commit design and, after approval, the implementation plan. Add normalized
   adapter/result/settings contracts with compatibility, cursor/identity and
   failure tests. Existing behavior stays unchanged.
2. Add story observations, exact-copy matching and local candidate previews.
   No automatic suppression until scoped completion, recovery and undo tests
   are complete. Verify URL collisions, updated articles, activity events,
   legacy missing-material baselines and explicit mode/queue transitions.
3. Improve RSS/HN/Reddit and add GitHub, OSSInsight, Google News, GDELT and public
   Telegram in separately verifiable source-family commits; deliver their
   schema-driven source dialogs, dependency status and explicit sample previews.
4. Add budgeted semantic observe jobs and evidence-based difference generation.
   Evaluate with fixtures for multilingual copies, progress, contradictory
   viewpoints, vague titles, shared URLs and late arrivals. Report false merges,
   missed duplicates, unclassified rates, latency and measured/estimated spend;
   do not claim an unmeasured accuracy percentage.
5. Add explicit apply mode, scoped revision delivery and append/detail contracts.
   Verify same/cross-policy scopes, collect versus send, pause, coalesced updates,
   unknown outcomes, restart/crash points, corrected grouping and immutable
   callbacks/favorites. Commit the presentation and delivery integration stage.
6. Add X and OpenBB as separately configured optional connectors, with mode and
   provider capability tests. X initially uses an explicit Apify actor, not
   browser-cookie automation. No paid or authenticated live test by default.
7. Fresh review, browser/no-JS/keyboard/mobile acceptance, fixes and final commit.

Every stage has focused tests, diff validation and its own commit. Final product
acceptance runs the complete unittest suite, required browser/JS gates, secret
scan and doctor/card-budget checks in an isolated synthetic configuration.
Do not start a second production runner/WS connection or change production files.
No merge or push of this new branch is authorized by the earlier merge request.

## Builder Decisions And Approval

The builder may choose module/command names, a local Store-owned index format,
numeric bounds and worker implementation while preserving these contracts.
Use standard-library/local dependencies where sufficient; optional capabilities
must not become mandatory imports. Record chosen limits and measured outcomes
in the plan/tests rather than claiming a universal similarity threshold.

User approval is requested for this detailed scope and the proposed notification
scope/presentation semantics above. These are design proposals, not claims that
the user already selected each implementation detail.

Builder-check decisions made by the author, also subject to this approval:
silent legacy baseline with an honest missing-update warning; prospective mode
activation plus explicit old-queue migration; initial X support through Apify
without requiring browser-cookie automation. A fresh independent builder check
identified these boundaries, which are now explicitly specified.
