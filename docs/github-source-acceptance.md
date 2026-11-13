# GitHub Source Acceptance

## Delivered Boundary

- Native `github` adapter, no SDK/Horizon/RSSHub dependency. Public user events (`/users/{name}/events/public`) and public repository releases (`/repos/{owner}/{repo}/releases`). Includes prereleases, excludes drafts/private events.
- `settings.users`, `settings.repositories`, optional `settings.token_env`, `settings.max_pages` (default 1, integer 1..3) are code-owned descriptor fields. Source dialogs and action translation compose them from the same schema; edit/copy/dry/preview preserve values and unknown legacy extras.
- Both subscription lists may be used, at most 100 combined entries before case-insensitive deduplication. Empty lists alone are an explicit acquisition configuration failure, not an empty-success response.
- A token reference resolves only the named process environment variable. Blank is anonymous. Missing/invalid explicit credentials fail before transport, without another-account or anonymous fallback. Load the variable into the service environment explicitly; this adapter does not independently open dotenv or keychain files.
- Fixed `https://api.github.com` request paths constructed from validated names. No provider Link-header following, redirects, linked-article fetching or remote credentials in URLs. Each page has a 20-second socket timeout and 2 MiB wire/decoded bound; existing two-exit transport retry and source-worker deadline remain authoritative. Token-authenticated repository collection first makes one bounded repository metadata request and requires explicit public visibility; private/unknown visibility is refused before release notes are requested.
- At most 100 records/page, 1..3 pages/subscription, sequential requests. A full last allowed page is `window_limited`, with no source cursor advancement; successful sibling subscriptions are retained after a failure. A failed later page discards that incomplete subscription, not the completed siblings.
- Errors expose categorical codes only. Optional quota/auth/window diagnostics have an explicit primitive allowlist, survive the worker/state/view contracts and also work for empty/failed responses. Quota metadata describes the last completed request/error, not a continuously refreshed account dashboard.

## Identity And Evidence

Event/release identity is a versioned full hash of GitHub native ID plus record type, never a repository URL. Event IDs and release IDs cannot collide. Repository case variants are one subscription; changing the mode/source does not migrate or rewrite an old RSS identity.

Release Markdown remains bounded original material, with publication and update times distinct. Equal-timestamp entries are accepted for native-ID retry safety. Unknown dates stay unknown. Mutable reactions/download counters do not alter meaningful evidence versions. Release bodies are only release notes, never a claim of complete linked-document content.

Activities retain actor/repository/type and bounded related issue/PR/release/comment/review/commit facts under recognized event-field contracts. They remain incomplete evidence. Unknown event types retain identity/type with no payload interpretation, even if a payload resembles a known comment or release. Nested draft releases are excluded and unknown nested release visibility fails. An activity without an independent provider permalink exposes the repository entry point and `permalink_available=false`, never a fabricated event link. Current activity support is not full comment/discussion expansion.

Observation capture and conservative relationship previews are unchanged. This stage does not introduce semantic suppression, event-history revisions, delta notifications or knowledge ingestion.

## Interaction And Safety

Selecting GitHub reveals its own subscription parameters and hides irrelevant RSS feeds/HTTP2. JavaScript switches controls locally; without JavaScript, “更新订阅参数” only refreshes an opaque one-use draft. It neither writes configuration nor collects. Inactive adapter edits survive round trips through bounded 32 KiB raw draft snapshots; those snapshots are not CLI parameters and cannot bypass configuration validation. A no-JS adapter change must refresh before save. Failed validation preserves ordinary edits; mistaken literal secrets in credential-reference fields are rejected before CLI argv creation and cleared from restored server drafts. Policy settings remain managed in their existing policy dialogs.

Source writes still use the CLI handlers, validation/dry-run, origin/token gates, optimistic hashes and source revisions. Browser/mobile acceptance uses synthetic data and denies providers, credentials, model calls and sends.

## Known Limits And Live Acceptance

Public Events API is a bounded provider-maintained recent-history window (not a durable event stream), with upstream latency. Polling/extra pages cannot promise recovery after content expires. Releases are paginated in provider order; pages are not stopped early by a publication timestamp. Events are immutable activity records; later edits are not promised as updates to an old event payload.

No per-subscription persistent cursor or conditional HTTP cache is added. Partial/window-limited retries may refetch successful records, while existing queues remain idempotent. Source-worker limits are not an overall round/AI/send deadline, permission sandbox or hard real-time guarantee.

Fixtures prove request construction, parsing, security, preservation and browser flows. No live GitHub/IP/token acceptance, paid call, production source activation, deployment change or send occurred. Live acceptance needs a separate explicit request and the user's desired subscriptions/account.

## Verification

Task 1: 115 focused GitHub/contract/registry/worker/evidence/collection/transport tests passed. Task 2: 84 form/JS/new-browser tests passed. Task 3: 74 response/inbound/action/callback tests passed. Browser scenarios include Chromium/WebKit, 360/1440 px, no-JS creation and repeated adapter switching.

One fresh whole-branch read-only review found four must-fix issues: authenticated private repository releases, unknown event payload interpretation, nested draft releases and no-JS inactive draft loss. All four were reproduced in failing regressions and repaired in one fix pass; no minor findings were deferred. Post-fix targeted tests passed (33 source/form tests plus the real-browser scenarios).

Final required-gate command: `NEWSPIPE_BROWSER_TESTS=1 NEWSPIPE_REQUIRE_JS_TESTS=1 PYTHONPATH=src python -m unittest discover -s tests`: **971 tests passed, zero skips, 260.907 seconds**. Secret scan: **219 files, zero findings**. `git diff --check`: clean. Synthetic doctor: three sources including disabled GitHub, card budget **187/200 elements**, 13 headroom. These are offline acceptance results, not live provider or production deployment claims.

Development is committed on `feat/github-source`, based on `main` at `7c26331`; merge/push and production activation require a separate instruction. Existing RSS IDs and notification completion records are not migrated or replayed.
