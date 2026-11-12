# Group Selection And Acquisition UX Acceptance

## Scope And Commits

Branch: `feat/group-picker-acquisition-ux`, starting from `d3fcdf4`.
No production configuration, real Feishu lookup/send, long connection,
knowledge write, deployment, merge or push was performed.

- `cbddbcd`: approved design.
- `47f6b3f`: staged execution plan.
- `af36b23`: optional sending-bot joined-group capability.
- `9c45766`: native group selector and read-only draft-preserving lookup.
- `742ebdf`: source-page acquisition clocks and contextual form interactions.

## Observable Outcomes

- Global notification settings explicitly load the configured sending bot's
  joined groups. Name search covers loaded pages only; pagination and the
  1000-group local limit are explicit. Current targets remain selectable even
  before loading or when absent from a partial page. Manual ID is a fallback,
  not a second competing submitted target.
- Group loads do not save/send, acknowledge dirty drafts or advance hashes.
  Errors remain errors rather than empty success; obsolete responses after
  closing/switching editors are discarded. No-JS pagination/mode/filter use
  one-use process-memory PRG, never disk or browser storage.
- Acquisition time management is on the source page. Existing keys remain
  hidden and stable; new keys are collision-free. Existing equal-clock plans,
  disabled/inactive references, legacy notification references, extension
  fields and stored state survive. Referenced rows cannot be removed.
- Shared clock changes update source clock labels but retain unsaved source
  inputs and their old hash. Independently configured notification times do
  not change; legacy shared sending effects are disclosed with migration links.
- Trigger controls show scheduled-clock checkboxes, polling interval or neither
  for manual acquisition. Switching back restores draft values; omitted
  inactive fields do not clear stored configuration. No-JS applicability
  remains visible in field labels/help.
- Source/global/policy/acquisition editors remain native dialogs with SSR
  fallback. Boolean labels/help and time rows align. Existing time schedules
  have no spare rows; Add/Remove only edit drafts. Enter explicitly validates
  even when the visible dry-run checkbox is unchecked. Reference locks survive
  validation and failed saves.

## Verification Evidence

Required integrated command:

```sh
NEWSPIPE_REQUIRE_JS_TESTS=1 NEWSPIPE_BROWSER_TESTS=1 \
  uv run --python 3.13 --no-project --with-editable '.[all,browser-test]' \
  python -m unittest discover -b -s tests
```

- Baseline: 674 tests, zero skips, passed.
- Group-selector stage: 702 tests, zero skips, passed; later local-capacity
  regression passed in its focused suite.
- Acquisition stage: 718 tests, zero skips, passed in 181.933 seconds.
- Final acceptance after all review fixes: 722 tests, zero skips, passed in
  195.119 seconds. Secret scan: 177 files, zero findings; whitespace checks passed.
- Focused acquisition suite: 7 tests; schema/form suite: 24 tests; required
  Node suite: 7 tests. All passed.
- Chromium and WebKit exercised JS and no-JS flows, keyboard Enter, mobile
  controls, independent policy times and retained draft/hash behavior.
- Guarded visual pass produced 20 desktop/mobile screenshots across source,
  global, policy and acquisition pages at 1440/360 px in both engines. No
  horizontal page overflow, page exceptions or blocked provider operations.
- Secret scan at acquisition commit: 176 files, zero findings.
  `git diff --check` and staged whitespace checks passed.

The first integrated acquisition run exposed an obsolete select-specific
assertion. It was changed to verify the intended checkbox controls; the full
suite then passed. Additional observed RED/GREEN regressions cover ID rewriting
of data attributes, root-versus-nested YAML key selection, implicit Enter
activating Remove, and referenced buttons becoming unlocked after validation.

## Review And Limits

One independent fresh-context read-only whole-branch review covered
`d3fcdf4..742ebdf`. It found four Important issues, no Critical/Minor issues,
and no declined-to-judge items. All four were reproduced and fixed in one pass:

- Removed `constructor`/`__proto__` acquisition keys now retain their selected
  fallback option in source drafts; membership checks use own properties.
- Bounded CLI directory reads start in an isolated POSIX process group and
  terminate wrapper descendants on timeout/overflow, not only the wrapper.
- Global validation and conflict responses reconcile replacement one-use
  directory handles. Subsequent More succeeds without changing the selected
  target, unsaved inputs or original configuration hash.
- Unbound scheduled legacy card sources disclose shared sending-time effects,
  including disabled sources. Local-only and inactive poll/manual references
  do not falsely claim scheduled sending effects.

Each finding has an observed RED/GREEN regression. The two browser regressions
passed in Chromium and WebKit; directory and acquisition suites passed 16 and
8 tests respectively. Required Node behavioral tests also passed.
Final integrated verification after the fix pass passed 722/722 tests with
zero skips. No Important findings remain open; no second review was dispatched.

Live Feishu acceptance remains deferred: both configured backends' real app
identity, permission and joined-group responses need an explicitly authorized
application/group check. Offline passing is not evidence of live acceptance.
Group search does not claim to search unfetched provider pages.

Native time inputs may display locale-specific AM/PM (observed in WebKit).
The labels explicitly state 24-hour time, values/persistence are `HH:MM`, and
no editable acquisition or notification time identifier is exposed.

## Execution Decisions

- Kept the approved existing feature checkout rather than creating a second
  worktree. Cost: no filesystem isolation from unrelated simultaneous edits;
  git status was monitored and no unrelated changes were reverted.
- Kept trusted directory-page PRG retention in `webchats.py`, outside the
  already-large HTTP handler. Cost: one focused helper module, with tests.
- Expanded the acquisition stage to fix the actual `webui.py` and
  `yamlblocks.py` boundaries instead of adjusting JS values or YAML ordering
  in tests. Cost: shared-helper regression risk, covered by targeted and full
  suites.
