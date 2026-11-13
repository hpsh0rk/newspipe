"""Annotated offline contract fixtures, not a benchmark of any real model."""
from datetime import datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from unittest.mock import patch

from newspipe import config, llm, state, story_compare
from newspipe.observations import prepare_observation
from newspipe.story_matching import rank_candidates
from test_model_budget import Response
from test_observations import article, TEXT


def cases():
    old = article(pub_ts=10)
    changed = article(source_text=TEXT + " The vote is now postponed until Friday.", pub_ts=20)
    return [
        ("exact_reprint", "duplicate", old, old, "duplicate"),
        ("translated_copy", "duplicate", old, article(title="工具版本二已获批准", source_text="委员会在公开审查后批准工具版本二。发行版新增修复流程，并保留原有回执。" * 8, pub_ts=20), "duplicate"),
        ("later_development", "progress", old, changed, "progress"),
        ("opposing_views", "viewpoint", old, article(source_text=TEXT.replace("approved", "opposed"), pub_ts=20), "viewpoint"),
        ("vague_titles", "unrelated", article(title="Update", source_text="Gardens and cooking.", source_text_complete=False),
         article(title="Update", source_text="Satellites and orbit.", source_text_complete=False), "unrelated"),
        ("shared_url_commentary", "unrelated", article(title="Vote", content_kind="post", source_text="I oppose this decision.", source_text_complete=False),
         article(title="Performance", content_kind="post", source_text="Latency measurements improved.", source_text_complete=False), "unrelated"),
        ("late_old_publication", "uncertain", old, {**changed, "pub_ts": 5}, "progress"),
        ("partial_duplicate", "uncertain", old, article(source_text=TEXT + " ", source_text_complete=False, pub_ts=20), "duplicate"),
    ]


def report():
    started = monotonic()
    rows = []
    totals = {"false_duplicate_proposals": 0, "missed_duplicate_associations": 0, "uncertain": 0,
              "model_attempts": 0, "estimated_tokens": 0, "measured_tokens": 0, "unknown_tokens": 0}
    for name, expected, old_item, new_item, model_relation in cases():
        with TemporaryDirectory() as tmp:
            news = Path(tmp)
            (news / "sources.yaml").write_text("chat: oc_test\nslots: {}\nsources: {}\nstories: {mode: observe, comparison: manual}\n")
            store, cfg = state.Store(news), config.load(news)
            at = datetime(2026, 11, 13, 12)
            for source, item in (("first", old_item), ("second", new_item)):
                draft = prepare_observation(source, "rsshub", item)
                snapshot = store.observation_capture_snapshot([draft], at=at, window_days=14, candidate_limit=5, scan_limit=500)
                store.record_observations([draft], at=at, mode="observe", config_revision=cfg.story_revision,
                                         current_revision=cfg.story_revision, expected_activation_id=snapshot.activation_id,
                                         snapshot=snapshot, proposals=rank_candidates(draft, list(snapshot.candidates), limit=5), baselines={})
            answer = {"relation": model_relation, "differences": [] if model_relation in {"unrelated", "uncertain"}
                      else [{"kind": "viewpoint" if model_relation == "viewpoint" else "changed_claim" if model_relation == "duplicate" else "new_fact", "current": "c1", "previous": "p1"}]}
            response = Response({"choices": [{"message": {"content": json.dumps(answer)}}],
                                 "usage": {"prompt_tokens": 200, "completion_tokens": 100, "total_tokens": 300}})
            ref = llm.ModelRef("fixture", "offline", "https://model.invalid/v1", "fixture-key", "test")
            with patch.object(llm, "resolve_model", return_value=ref), patch.object(llm, "_open_model", return_value=response):
                result = story_compare.evaluate(store, cfg, draft.observation_id, allow_model=True, limit=1, now=at)
            relation = result["pairs"][0]["result"]["relation"] if result["pairs"] else "no_candidate"
            rows.append({"case": name, "expected": expected, "result": relation, "latency_ms": result["latency_ms"]})
            totals["false_duplicate_proposals"] += relation == "duplicate" and expected != "duplicate"
            totals["missed_duplicate_associations"] += expected == "duplicate" and relation != "duplicate"
            totals["uncertain"] += relation == "uncertain"
            for key in ("estimated_tokens", "measured_tokens", "unknown_tokens"):
                totals[key] += result["accounting"][key]
            totals["model_attempts"] += result["accounting"]["attempts"]
    return {"fixture_count": len(rows), "results": rows, **totals, "wall_ms": round((monotonic() - started) * 1000, 3),
            "real_model_calls": 0, "note": "Canned model responses test evidence/budget gates, not model accuracy. No suppression or actual merges."}


if __name__ == "__main__":
    print(json.dumps(report(), ensure_ascii=False, indent=2))
