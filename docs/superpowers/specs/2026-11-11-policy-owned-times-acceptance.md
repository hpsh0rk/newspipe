# Policy-Owned Times Acceptance

## Delivered
- Policies own stable schedule-key/HH:MM mappings. Shared source bindings follow
  their policy, not another policy or the acquisition clock registry.
- Source dialogs select a policy and display schema-derived, read-only managed
  parameters. They do not submit notification override or inactive delivery
  inputs. Create/edit/manage links and resumable page-local drafts remain native
  progressive dialogs.
- Multiple time rows are editable; inactive timing controls are disabled. Time
  controls match the existing input styling and remain usable at 360px width.
- `notification migrate` previews and atomically preserves legacy clocks, source
  override values, disabled sources, unknown source fields, schedule-completion
  keys, queues, budgets and unfinished remote identities. New CLI slot-based
  policy writes are materialized into owned clocks.
- Source-version synchronization is explicit, compares the full raw source
  fingerprint, and uses a stable editor URL rather than a one-use PRG handle.

## Review And Corrections
One fresh read-only reviewer checked `65abe3a..3242b84`. Two Important findings
were reproduced and corrected with RED -> GREEN tests: legal legacy equal-clock
keys could not migrate, and no-JS migration previews truncated the report.
There were no Critical findings, deferred minors or declined-to-judge items.

Acceptance additionally reproduced and corrected the new time-input height,
one-use source draft URL and multi-source YAML-alias dependency ordering.
Existing equal-clock keys are preserved on reads, migration and unchanged
schedule edits; newly introduced duplicates are rejected by the write handler.
This deliberately retains old multiple-schedule behavior, not a merged schedule.

## Verification
Final integrated gate: Python 3.13, `NEWSPIPE_REQUIRE_JS_TESTS=1` and
`NEWSPIPE_BROWSER_TESTS=1`, 674 tests, zero skips, 150.387 seconds, GREEN.
Chromium and WebKit execute the actual workbench asset and guarded HTTP writes.
`python3 scripts/scan_secrets.py` and `git diff --check` are clean.
Guarded Chromium/WebKit screenshots inspected source/policy/list pages at
1440px and 360px: no horizontal overflow, no JavaScript errors, and no provider
or credential calls. Browser screenshots are temporary acceptance artifacts,
not repository assets.

## Safety
All edits stayed on the feature branch. No live configuration, credentials,
providers, sends, scheduler, knowledge writes/receipts, deployment, merge or
push was performed. Existing real configuration is not automatically migrated;
the user must preview and explicitly apply the migration through CLI/UI.
