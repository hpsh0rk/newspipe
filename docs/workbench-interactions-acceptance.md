# Workbench Interaction Acceptance

Date: 2026-11-11. Branch: `fix/workbench-interactions`.
Scope: `63e60e4..1688d03`; no merge, push or production deployment.

## Repairs

- Source health uses the authoritative label, tone and staleness, rather than treating every enabled source as healthy.
- Source/policy deletion uses one explicit confirmation dialog. Without JavaScript, a read-only confirmation page precedes the same guarded POST. Cancel, Escape and refresh cannot delete or replay a write.
- Buttons have consistent 36px desktop / 44px narrow-screen minimum heights; action rows no longer stretch links. Edit/copy links land at the open editor.
- Confirmed writes refresh code-owned SSR read regions. Dirty editors and dirty/busy regional controls retain their values and versions. Feedback stays near the action and is brought into view.
- Read failures do not turn a confirmed write into an unknown outcome or automatically retry it. Active content in replacement regions is rejected.
- Final review found detached legacy flush feedback and a contradictory first-policy empty state. Both have reproducing Chromium/WebKit tests and were fixed in one pass.

## Verification

Python 3.11 and 3.12: each 605 discovered, 583 executed, 22 deliberately opt-in browser tests skipped; all executed tests passed.

```sh
NEWSPIPE_REQUIRE_JS_TESTS=1 uv run --python 3.11 --no-project --with-editable '.[all]' python -m unittest discover -b -s tests
NEWSPIPE_REQUIRE_JS_TESTS=1 uv run --python 3.12 --no-project --with-editable '.[all]' python -m unittest discover -b -s tests
```

Python 3.13 integrated: 605/605 passed, zero skips, including 22 browser methods with Chromium and WebKit. Responsive acceptance covers 360, 768 and 1440px; JS and no-JS confirmation are exercised.

```sh
NEWSPIPE_REQUIRE_JS_TESTS=1 NEWSPIPE_BROWSER_TESTS=1 uv run --python 3.13 --no-project --with-editable '.[all,browser-test]' python -m unittest discover -b -s tests
python3 scripts/scan_secrets.py
git diff --check
```

Secret scan and whitespace checks passed. Screenshots were also inspected at desktop, tablet and narrow-screen sizes. Tests use temporary synthetic data and preserve browser/server provider guards; no real notification, WS connection, queue acknowledgment or knowledge-base write was performed.

## Decisions

- Continue on a dedicated repair branch in the user's existing checkout, without another workspace/design approval round. The checkout remains intact and is not merged or pushed.
- Refresh escaped SSR regions rather than maintaining a second client-side renderer. This keeps one rendering authority; regions containing drafts may intentionally require a manual refresh after those drafts are resolved.
- Re-grade the first-policy empty-state contradiction as Important: a successful creation must not simultaneously claim that no policy exists. The additional test and moving its existing markup into the same region address that onboarding failure.

One fresh read-only reviewer covered the repair range, with no declined judgments. Its findings were verified RED-to-GREEN and the complete suite rerun; no deferred findings remain from this review. Live Feishu deployment is not part of this acceptance; see `docs/live-feishu-acceptance.md` for that separate boundary.
