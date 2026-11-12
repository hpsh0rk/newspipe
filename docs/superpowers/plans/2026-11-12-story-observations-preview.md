# Story Observations And Local Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve original source versions and expose exact-copy/local candidate
proposals without suppressing notifications, rewriting history or spending tokens.

**Architecture:** Pure observation extraction and matching sit outside acquisition
and delivery. A focused local SQLite ledger is owned by Store and preserves
immutable versions, mutable last-seen indexes and prospective mode frontiers.
The pipeline records normalized originals before filtering/enrichment; existing
pending, pushed, delivery and favorite paths retain their current semantics.
Versioned CLI/read contracts and a compact drill-down on the existing items page
expose proposals, not approved cross-source story membership.

**Tech Stack:** Python >=3.11, standard-library sqlite3/dataclasses/hashlib,
existing PyYAML and file locking; server-rendered HTML with existing progressive
enhancement. No new runtime dependency or external database service.

**Spec:** `docs/superpowers/specs/2026-11-12-source-expansion-event-updates-design.md`

**Status:** Planned; implementation awaits review of this focused plan. The user
has selected native sequential execution and a commit for every stage.

## Global Constraints

- No Horizon runtime dependency, MCP/Skill connector integration or new sources
  in this subproject. No tenants, workflow engine or framework rewrite.
- This stage supports off and observe only. Existing deployments default to off;
  apply is rejected explicitly until scoped completion/recovery/undo are built.
- No automatic suppression, cross-source membership merge, queue migration,
  retrospective backfill, historic resend or remote delivery completion.
- No production configuration changes, account login, real credential access,
  provider/model/Feishu calls, paid collection, knowledge writes or second runner/WS.
- Normal dry-run never writes state or calls models/sends. Read routes never
  collect, compare with a model, activate settings, initialize/migrate SQLite,
  rebuild indexes or update last-seen timestamps.
- config.py is the sole public settings schema/value/validation boundary.
  Page writes use actions.py's allowlist and the existing CLI mutation handler,
  with actions/origin/token/hash checks and no second YAML writer.
- Preserve ext_id values/types, pending membership, pushed receipts, card callback
  IDs, frozen payloads/targets/sequences and acknowledged favorite evidence.
- Immutable observations are never automatically deleted. Candidate retention
  limits retrieval recall, not source history or favorite evidence.
- No network/model work or full-history baseline scanning under the writer lock.
  Prepare hashes/text outside it; commit locally and atomically under a short lock.
- Each task gets RED/GREEN tests and a commit. No merge or push in this stage.

## Builder Decisions And Bounds

These are engineering choices, not additional user-specified requirements.

- Settings live in sources.yaml under `stories`: mode=off, window_days=14,
  candidate_limit=5, scan_limit=500. Mode accepts off/observe; window_days 1..90,
  candidate_limit 1..20 and scan_limit 50..2000, with candidate_limit <= scan_limit.
- Pure extraction uses version `source-text-v1`: NFC and normalized line endings,
  not case folding, punctuation deletion, boilerplate removal or rewritten URLs.
  Title, original source text, typed references and explicit structured facts are
  meaningful; AI fields, crawl/publication timestamps and engagement counters
  do not create content versions by themselves. Dates/numbers in text/facts stay.
- Missing content_kind is unknown. Do not infer article identity from an adapter
  name or a shared URL. Article/post/comment/release/activity/snapshot/unknown
  are distinct, and unknown kinds get candidates only, never a confident copy.
- Exact-copy proposals initially require two explicit article observations with
  identical complete normalized title/text/facts and at least 160 non-whitespace
  body characters. Completeness requires `source_text_complete: true` on an
  explicit `source_text`; length alone and legacy summary fields do not prove it.
  Typed referenced URLs must agree; each publisher's own permalink is provenance,
  not a requirement for equal URLs across publishers. Same-URL observations with
  different commentary remain separate.
- URL matching lowercases scheme/host and normalizes default ports. Remove only
  the enumerated tracking keys utm_*, fbclid and gclid; preserve other query bytes,
  repeated parameter order, paths, versions, nonempty fragments and URL roles.
  It is pure identity comparison, not redirect resolution or network access.
- Candidate-only lexical retrieval uses up to 20,000 source characters and 256
  distinct Latin-word/CJK-bigram terms. Jaccard >=0.35 retrieves a candidate,
  not a factual relation or merge. Return at most candidate_limit candidates from
  at most scan_limit recent observations; disclose truncation and matching bounds.
  Retrieval uses ingestion time, retaining publication time separately for late arrivals.
- Same-native-identity version lookup is not limited by the cross-source window.
  A source-text change is labelled unclassified change, not progress/viewpoint/
  correction until evidence-backed classification is delivered in a later stage.
- Original JSON is retained privately and in full. Public contracts allowlist
  evidence fields and bounded text, never arbitrary extras/headers/provider errors.
  URL credentials and query values are not exported in diagnostics; redacted URLs
  are labelled and not offered as misleading original-version links.
- Mode activation occurs on the first real collection using the saved settings
  revision, atomically before its observations. Record revision, mode and sequence
  frontier; do not reclassify pre-frontier observations or old pending entries.
  Reads show saved versus last activated mode when these differ.
- Use separate monotonic observation and capture sequences: a version gets one
  immutable observation sequence; every accepted collection gets a capture receipt.
  Frontiers use capture sequence and a new activation ID, so repeat versions and
  off->observe->off->observe transitions cannot reuse an earlier activation.
- A stale observe task still persists its original versions, but its receipt says
  `stale_settings`/`stale_history` and contains no relation proposals. It cannot
  activate old settings. A task started in off does not retroactively acquire
  observe semantics. Only a later real observation can get a new eligible receipt.
- Preview reads saved capture proposals and their recorded settings/index bounds;
  changing current settings does not rerun classification of earlier observations.

## Review Focus

- The same native ID changes after an earlier successful notification: retain a
  new original version without rewriting pushed/pending/callback/favorite records.
  Pin with Task 3 version-after-completion and byte-preservation tests.
- Two social/activity/release entries share a URL or a vague title: they remain
  separate; a local similarity score cannot become a confident merge.
  Pin with Task 1 typed-URL and weak-evidence fixtures.
- A completed legacy ID has only generated/truncated/missing material: establish
  an honest silent baseline, never call the first refetch duplicate or progress.
  Pin with Task 2 original-material availability tests.
- A slower collection uses settings from before an off/observe transition: it
  must not restore an older frontier or silently migrate queued/frozen content.
  Pin with Task 3 interleaved-revision and mode-transition tests.
- SQLite is missing, locked, corrupt or a write is interrupted: distinguish
  empty/disabled from unavailable; no read-side repair, quiet empty view or cursor
  advancement before observation durability. Pin with Tasks 2, 3 and 4 faults.

## File Responsibilities

- Create `src/newspipe/observations.py`: pure version/evidence/identity extraction.
- Create `src/newspipe/story_matching.py`: exact-copy and bounded local candidates.
- Create `src/newspipe/story_state.py`: local SQLite schema/transactions/read-only
  queries; no configuration, network or delivery. Only Store owns its location.
- Modify `state.py`: public observation/frontier/material APIs delegating to ledger.
- Modify `config.py` and `edit.py`: authoritative settings and atomic CLI writes.
- Modify `pipeline.py`: optional observation capture at both acquisition boundaries;
  never add capture to notify_source, callbacks or delivery recovery-only paths.
- Create `story_capture.py`: prepare snapshots/matching outside the writer lock and
  revalidate settings at commit; Store itself never reads configuration.
- Modify `cli.py`: settings, observations and preview contract/argument handlers.
- Create `webstories.py`; modify `view.py`, `inbound.py`, `actions.py` and only the
  needed existing workbench enhancement: compact settings/preview/detail drill-down.
- Create focused observation/ledger/matching/integration/CLI/web tests; extend
  offline browser fixtures and safety gates only at the boundaries actually changed.

## Task 1: Pure Original Versions And Conservative Matching

**Files:** `observations.py`, `story_matching.py`, `tests/test_observations.py`,
`tests/test_story_matching.py`.

**Interfaces:**
- Frozen `SourceReference(role: str, url: str)` and `SourceEvidence(title: str,
  text: str, facts_json: str, references: tuple[SourceReference, ...],
  permalink: str, text_complete: bool)`; JSON strings and tuples prevent nested
  mutation, not just reassignment of the dataclass.
- Frozen `ObservationDraft`: observation_id/identity_hash/version_hash/source/
  adapter/partition/kind/extractor_version are str; native_id is str | int;
  evidence is SourceEvidence; original_json is full JSON text; pub_ts/observed_ts
  are float | None. Original JSON retains native values and their types.
- `prepare_observation(source: str, adapter: str, item: dict) -> ObservationDraft`:
  use full SHA-256 canonical UTF-8 tuples; integer comparison keys match the
  foundation. Partition comes only from explicit `subscription`; kind comes only
  from `content_kind`; text prefers `source_text`, otherwise original `summary`.
  `facts` and `referenced_urls` supply structured facts and role/url references;
  `original_url`/`url` supply the permalink. Missing optional fields stay unknown;
  malformed explicit fields raise SourceContractError, never stringify a body.
  Version hashes include kind/title/text/facts/canonical references and permalink,
  excluding timestamp/engagement/AI fields; cross-source copy comparison excludes
  native identity and the publisher's own permalink, but retains typed references.
- `canonical_reference(url: str, role: str) -> str` is pure and type-sensitive.
- `propose_relation(current: ObservationDraft, previous: ObservationDraft) -> dict`:
  exact_copy, candidate, unclassified_change or unrelated; return relation,
  current_id, previous_id, score, reason, evidence_fields and matcher_version.
  Same-native text/fact changes are unclassified_change, not semantic differences.
- `candidate_terms(draft: ObservationDraft) -> tuple[str, ...]` and
  `rank_candidates(draft: ObservationDraft, candidates: list[ObservationDraft], *,
  limit: int) -> list[dict]` honor the bounds; ties sort by observation ID.

- [ ] Add `test_complete_evidence_required_for_exact_copy` and
  `test_candidate_cap_is_not_an_exact_hash_cap` with these assertions:

  ```python
  self.assertEqual(propose_relation(full_a, full_b)["relation"], "exact_copy")
  self.assertNotEqual(propose_relation(summary_a, summary_b)["relation"], "exact_copy")
  self.assertNotEqual(propose_relation(full_a, tail_changed)["relation"], "exact_copy")
  self.assertLessEqual(len(candidate_terms(full_a)), 256)
  ```

- [ ] Write tests for integer 0/long/Unicode identities, source partitions,
  immutable originals, unknown/zero dates and metric/AI-only changes.
- [ ] Write tests for explicit fact/version changes, significant punctuation,
  whitespace/code indentation and text beyond the candidate-character limit.
- [ ] Write fixtures for identical articles, shared social URLs, distinct GitHub
  activity/releases, snapshot metrics, vague titles, unknown kinds and late dates.
- [ ] Run `python -m unittest discover -s tests -p 'test_observations.py' -v` and
  `python -m unittest discover -s tests -p 'test_story_matching.py' -v`.
  Expected: missing pure interfaces fail, not provider/dependency access.
- [ ] Implement pure extraction/matching; no Store, imports of providers or I/O.
- [ ] Rerun both commands. Expected: all pass, exact hashes use complete evidence
  while local candidates remain bounded and non-authoritative.
- [ ] Commit `feat: define immutable source observations and conservative matching`.

## Task 2: Store-Owned Immutable Ledger And Honest Legacy Baselines

**Files:** `story_state.py`, `state.py`, `tests/test_story_state.py`.

**Interfaces:**
- Frozen `CaptureSnapshot` in story_state.py: activation_id: str | None,
  capture_sequence: int, latest_by_identity: dict[str, ObservationDraft],
  candidates: tuple[ObservationDraft, ...], bounds: dict and limitations: tuple[str, ...].
  Its nested mappings are defensive copies; never expose a live mutable index.
- `Store.observation_capture_snapshot(drafts: list[ObservationDraft], *,
  at: datetime, window_days: int, candidate_limit: int, scan_limit: int)
  -> CaptureSnapshot`: read-only indexed lookup of same-native latest versions and
  at most scan_limit recent observations shared by this collection. Full candidate
  hashing/ranking happens outside the writer lock. Return exact scanned counts,
  as-of capture sequence and whether the cap excludes additional candidates.
- `Store.record_observations(drafts: list[ObservationDraft], *, at: datetime,
  mode: str, config_revision: str, current_revision: str,
  expected_activation_id: str | None, snapshot: CaptureSnapshot,
  proposals: list[dict], baselines: dict) -> dict`:
  one SQLite transaction inserts immutable versions, updates last-seen/latest
  indices and stores a capture/proposal receipt. Check current_revision and the
  activation ID; if either is stale, retain originals but no proposals/activation.
  If a relevant same-native latest ID changed since the snapshot, mark that
  receipt stale_history rather than comparing against the wrong predecessor.
  Adding unrelated observations does not invalidate a bounded as-of proposal.
  Return contract_version, status, capture_sequence, activation_id, inserted,
  repeated and proposal_count; no delivery receipts or filesystem paths.
- `Store.observations(*, after: int = 0, limit: int = 50, source: str = '') -> dict`
  and `Store.observation(observation_id: str) -> dict | None`: read-only, versioned,
  paginated by observation sequence, allowlisted; unknown IDs differ from storage
  failures. Limits are 1..100; after must be >=0. Detail caps exported text at
  20,000 characters and discloses truncation independently from source completeness.
- `Store.story_preview(*, observation_id: str = '', after: int = 0,
  limit: int = 20) -> dict`: saved receipts ordered by capture sequence, limit
  1..100. Return contract_version, receipts, next_after, has_more and availability;
  receipts expose proposals, baselines, recorded bounds and limitations. This is
  not a dynamic matching or baseline-repair endpoint.
- `Store.story_status() -> dict`: saved ledger mode/frontier/counts/availability;
  nonexistent ledger returns not_initialized, not an invented successful history.
  Keys are contract_version, availability, activated_mode, activated_revision,
  activation_id, frontier_sequence, observation_count and capture_count. Missing
  ledger has null activation fields and zero counts; storage faults remain errors.
- `Store.original_observation_baselines(source: str, native_ids: set[str]) -> dict`:
  retrieve comparable source-owned batch originals outside the writer lock.
  Never use *_zh/model receipts as original text. Incompatible or missing material
  returns an explicit availability reason and silent migration-baseline warning.
  Keys use identity_key; values have completed: bool, availability: str and
  draft: ObservationDraft | None. Genuine title plus original summary/source_text
  can be a limited baseline; pushed-title snippets, generated-only or explicitly
  truncated originals cannot. No assumption that a limited snapshot is full text.
- SQLite transactions preserve source/version uniqueness and last-seen indices;
  reader connections use mode=ro, not implicit database/schema creation. Use DELETE
  journal and a <=250ms busy timeout to avoid read-side WAL/shm writes and long lock
  stalls. Raise StoryStorageError with unavailable/locked/corrupt/version codes,
  never raw SQLite exception text; missing storage is only not_initialized.

- [ ] Add `test_repeated_version_keeps_original_and_advances_capture` and
  `test_unknown_id_is_not_storage_failure` with these assertions:

  ```python
  self.assertEqual(first_draft.observation_id, repeated_draft.observation_id)
  self.assertEqual(persisted_original_json_after, persisted_original_json_before)
  self.assertGreater(second_receipt["capture_sequence"], first_receipt["capture_sequence"])
  self.assertIsNone(store.observation("ob_missing"))
  with self.assertRaises(StoryStorageError):
      corrupt_store.observation("ob_missing")
  ```

- [ ] Write tests for repeated/changed/late observations, restart, stable sequences,
  cross-source provenance and unchanged observation bytes when only metrics change.
- [ ] Write completed-legacy fixtures with genuine v2 original title/summary,
  generated-only text, insufficient/truncated material and missing batches. Assert
  first refetch baseline is silent/uncertain, later versions compare to that baseline.
- [ ] Write interruption, uniqueness race, locked/corrupt database and read-only
  tests: no partial observation transaction and no repair/write during queries.
- [ ] Run `python -m unittest discover -s tests -p 'test_story_state.py' -v`.
  Expected: missing ledger behavior fails before production changes.
- [ ] Implement the focused ledger and Store delegation; never expose its file
  paths, delete historic observations or scan baselines while holding the writer lock.
- [ ] Rerun that command. Expected: all pass with byte-identical legacy pending,
  pushed, batches and favorites; read calls create no files.
- [ ] Commit `feat: persist source observation history with legacy-safe baselines`.

## Task 3: Prospective Observe Mode At Acquisition Boundaries

**Files:** `config.py`, `edit.py`, `cli.py`, `pipeline.py`,
`story_capture.py`, `tests/test_story_collection.py`, `tests/test_story_settings.py`.

**Interfaces:**
- `StorySettings` lives in config.py with the specified defaults/bounds and
  `parse(raw)` / `values()`; Config gains stories: StorySettings and
  story_revision: str. Revision is SHA-256 of canonical settings values, independent
  of source edits, YAML comments and key order. Activation IDs, not hashes alone,
  distinguish returns to previously used settings.
- `config.story_settings_snapshot(news_dir: Path, *, sources_text: str | None = None)
  -> tuple[StorySettings, str]` parses just the settings from one YAML read; no
  adapter, model, service or credential resolution. It uses the same parser as load.
- `describe_schema()` adds story_fields; values and validation come from that same
  declaration. `edit.set_story_settings(news_dir, body, *, base_hash=None,
  dry_run=False)` only edits the stories block through the existing atomic handler.
- CLI `stories settings` and `stories set --from-json ... [--dry-run] [--base-hash]`
  declare contracts/examples/exit codes. No CLI settings write collects or creates
  observation history; activation is reported as awaiting real collection.
- Initial off mode leaves legacy acquisition unchanged and does not initialize the ledger.
  Once a ledger exists, a matching off collection records only its activation
  transition, preserving prior evidence; it does not record new drafts/proposals.
  Observe captures normalized originals at _process and collect_source before
  filter/dedup/enrich; no new suppression or enrichment work is introduced.
- `story_capture.begin_capture(store, *, dry: bool) -> CaptureContext | None`
  runs before transport on a real eligible collection; dry returns None.
  CaptureContext holds settings: StorySettings, config_revision: str and
  activation_id: str | None, read without creating storage or resolving credentials.
- `story_capture.prepare_capture(context: CaptureContext | None, store, scfg,
  items: list[dict], *, at: datetime) -> PreparedCapture | None` runs after
  transport, outside the writer lock. Prepare drafts, legacy material, indexed
  candidates and proposals using the earlier context. PreparedCapture holds
  context, snapshot, drafts, baselines, proposals and at; None means no state work.
- `story_capture.commit_capture(store, capture: PreparedCapture) -> dict` runs
  inside the same writer lock as edit.set_story_settings. Read current settings
  revision through story_settings_snapshot, then delegate to record_observations.
  Pass context.activation_id as expected_activation_id, not the newer ID read
  during candidate retrieval. A relevant before/during-transport transition is
  stale even if the saved settings later return to the same semantic hash.
  Stale receipts remain visible and never restore earlier mode/frontier values.
  Do not initialize storage for off tasks or activate on dry/baseline/throttled/
  recovery-only/total-failure paths. Successful-empty/partial collections may activate.
- Observation durability precedes cursor advancement. An observation write error
  retains successful pending content, leaves the cursor unchanged and records a
  safe visible failure. Retries are idempotent and reuse ordinary attempt throttling.
  The writer lock does not make SQLite + pending JSON atomic: ledger success before
  a queue failure is retried idempotently; a queue success before a ledger failure
  retains pending without advancing cursors. Existing frozen recovery can continue.
  `SourceRun.observations: dict | None` carries the categorical capture receipt/error;
  do not stuff it into the separate validated source acquisition report contract.

- [ ] Add `test_stale_observe_task_cannot_reactivate_old_mode` and
  `test_ledger_failure_keeps_pending_but_not_cursor` with these assertions:

  ```python
  self.assertEqual(receipt["status"], "stale_settings")
  self.assertEqual(receipt["proposal_count"], 0)
  self.assertEqual(store.story_status()["activation_id"], active_before)
  self.assertEqual(store.cursor(source), cursor_before)
  self.assertEqual(store.peek_pending(source), successful_pending)
  ```

- [ ] Write off/default/dry/no-fetch notification tests, plus raw-original capture
  when filters discard an item or enrichment changes its generated display fields.
- [ ] Write same-ID-after-delivery, metric-only, partial fetch, pending interruption,
  failed ledger commit and frozen-send/favorite byte-preservation tests.
- [ ] Write off->observe->off->observe, restart, pre-existing queue and interleaved
  settings revision tests. Assert immutable frontiers and no retrospective grouping.
- [ ] Run `python -m unittest discover -s tests -p 'test_story_collection.py' -v`
  and `python -m unittest discover -s tests -p 'test_story_settings.py' -v`.
  Expected: absent optional capture/transition/settings behavior fails safely offline.
- [ ] Implement settings/CLI/capture integration. Reject apply and invalid/unknown
  fields with actionable validation errors; preserve false/zero/hash round trips.
- [ ] Rerun those tests plus source-contract/collection/notification/reliability tests.
  Expected: all pass; observe spends zero comparison tokens and does not suppress sends.
- [ ] Commit `feat: observe original source versions without changing notification semantics`.

## Task 4: Read Contracts And Compact Preview Drill-Down

**Files:** `cli.py`, `view.py`, `webstories.py`, `inbound.py`, `actions.py`,
`tests/test_story_contracts.py`, `tests/test_story_web.py`, offline preview fixture.

**Interfaces:**
- CLI `observations list [--source] [--after] [--limit]`, `observations show <id>`
  and `stories preview [--observation ID] [--after] [--limit]` read saved data only.
  Preview returns versioned evidence/proposals/bounds, not a network collection job.
- These delegate to Task 2's observations/observation/story_preview, with unchanged
  pagination units and limits. Synchronize CONTRACT, SUBCOMMANDS, parser and handler.
  Missing ledger is an explicit empty not_initialized result; corrupt/locked storage
  is an error, unknown detail ID is not_found, invalid pagination is validation error.
- `/view` adds only lightweight story status/counts/frontier/mode, not raw observations.
- Existing `/items` gains one compact preview entry; `/items?story_preview=1` and
  `/items?observation=<ID>` provide bounded proposals/version evidence with pagination.
  No new dense navigation tab, model button, merge button or notification claim.
- `/items?story_settings=1` uses the existing editor/modal pattern and story_fields.
  `story-settings` is a code-owned action delegating to stories set; no-JS works and
  the actions/origin/token/hash gates remain mandatory. Advanced bounds are collapsed.
- Exact copies say proposal only; text changes say unclassified, not progress or
  disagreement. Unknown baselines and redacted/unavailable source links are visible.

- [ ] Add `test_preview_uses_saved_bounds_without_writing` and
  `test_actions_disabled_hides_and_rejects_settings` with these assertions:

  ```python
  self.assertEqual(preview["receipts"][0]["bounds"]["scan_limit"], 500)
  self.assertEqual(files_after, files_before)
  self.assertNotIn('action="/api/actions/story-settings"', disabled_html)
  self.assertEqual(disabled_post.status, 404)
  ```

- [ ] Write CLI describe/parser/envelope/pagination tests and missing-versus-corrupt
  ledger tests. Deny adapters/providers/credentials/models throughout read operations.
- [ ] Write HTML escape/secret-URL/auth-header/private-path tests and byte snapshots
  proving all preview/detail requests are read-only, including missing storage.
- [ ] Write settings-modal/no-JS/hash-conflict/actions-disabled/Origin/token tests;
  saving must not collect, compare, initialize observations or send anything.
- [ ] Run `python -m unittest discover -s tests -p 'test_story_contracts.py' -v`
  and `python -m unittest discover -s tests -p 'test_story_web.py' -v`.
  Expected: missing public contracts/UI fail, no real provider is contacted.
- [ ] Implement bounded read contracts, truthful drill-down and the single settings
  action. Preserve existing items/callback/favorite navigation and contract fields.
- [ ] Rerun both commands plus view/actions/browser-fixture/workbench JS tests.
  Expected: all pass and previews/settings do not alter delivery state.
- [ ] Commit `feat: preview local content relations with original-version evidence`.

## Task 5: Review, Browser Acceptance And Scope Documentation

**Files:** focused browser fixtures/tests, README.md, AGENTS.md and
`docs/story-observations-preview-acceptance.md`; only actual review fixes in product code.

- [ ] Add offline fixtures for copies, same-ID changes, shared-URL commentary,
  late arrivals and missing legacy material, without changing real configuration.
- [ ] Run browser coverage for desktop/mobile, Chromium/WebKit, keyboard and no-JS:
  settings modal, preview pagination/detail, warnings and unchanged ordinary items UI.
  Expected: no blocked real provider/model/credential/send call escapes the fixture.
- [ ] Run `NEWSPIPE_REQUIRE_JS_TESTS=1 NEWSPIPE_BROWSER_TESTS=1 python -m unittest discover -s tests`.
  Expected: all discovered tests pass with required JS/browser gates, no coverage skip.
- [ ] Request one fresh read-only whole-stage review focused on the five Review
  Focus cases; reproduce real findings with RED/GREEN fixes, rerun affected/full suites.
- [ ] Run `python3 scripts/scan_secrets.py`, `git diff --check` and synthetic
  `doctor --json` with credentials stubbed/network denied. Expected: clean/OK;
  card budget unchanged because no card rendering is added in this stage.
- [ ] Document local bounds, storage growth/no deletion, baseline uncertainty,
  prospective modes and candidate-not-merge semantics; do not claim AI accuracy,
  live-provider availability, source migration or notification suppression.
- [ ] Commit `test: verify immutable observations and local preview end to end`.

## Remaining Subprojects

After this independently testable stage: source-family upgrades/new sources,
budgeted semantic observation/classification, explicit apply with scoped completion
and undo, then append-only progress/viewpoint/correction delivery and history.
X/Apify and OpenBB remain optional later connectors. MCP/Skill remains deferred.

## Execution Handoff

Preserve native sequential execution: these tasks share observation identities,
Store durability and mode frontiers, so one implementation context is appropriate.
Review this focused plan before implementation; each task receives its own commit.

## Self-Review

- Spec stage 2 maps to Tasks 1..4; Task 5 verifies its offline/UI safety. Source
  families, semantic classifications, apply/suppression/scoped delivery, undo and
  append presentation are intentionally deferred, not incomplete claims in this stage.
- The five Review Focus cases have owning tests, including storage faults, stale
  activation, full-versus-limited originals and preservation after completion.
- Store retrieval and saved preview APIs now have distinct responsibilities and
  pagination units. Preparation/hashing/baseline scans stay outside the writer lock;
  settings/frontier validation and durability happen inside it.
- Original versions and capture receipts have separate sequences. Public contracts
  expose bounded allowlisted evidence, never raw original_json or private paths.
