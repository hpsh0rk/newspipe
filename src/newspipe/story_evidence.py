"""Pure bounded evidence and citation validation. Labels are proposals, not truth."""
from __future__ import annotations

VERSION = "evidence-pairs-v1"
RELATIONS = frozenset({"duplicate", "progress", "viewpoint", "unrelated", "uncertain"})
DIFFERENCES = frozenset({"new_fact", "changed_claim", "viewpoint"})


def _side(row, prefix):
    evidence = row["evidence"]
    excerpts = []
    represented = not (evidence.get("text_truncated") or evidence.get("facts_truncated"))
    for field, count in (("title", 1), ("text", 10), ("facts_text", 1)):
        text = evidence.get(field, "")
        if field == "facts_text" and text == "{}":
            continue
        represented = represented and len(text) <= count * 240
        for offset in range(0, min(len(text), count * 240), 240):
            excerpts.append({"id": prefix + str(len(excerpts)), "field": field,
                             "offset": offset, "text": text[offset:offset + 240]})
    return {key: row.get(key) for key in ("observation_id", "version_hash", "extractor_version", "source", "kind", "pub_ts")} | {
        "text_complete": bool(evidence.get("text_complete")), "fully_represented": bool(represented),
        "text_length": len(evidence.get("text", "")), "excerpts": excerpts}


def comparison_input(current: dict, previous: dict) -> dict:
    """Use only Store's public redacted evidence, never raw observations or model URLs."""
    return {"evidence_version": VERSION, "current": _side(current, "c"), "previous": _side(previous, "p")}


def _result(evidence, relation="uncertain", reason="invalid_model_evidence", differences=(), confidence="uncertain"):
    return {"relation": relation, "confidence": confidence, "reason": reason,
            "differences": list(differences), "evidence": evidence, "algorithm_version": VERSION}


def _citation(side, excerpt):
    return {"observation_id": side["observation_id"], **excerpt}


def validate_comparison(data: dict, evidence: dict) -> dict:
    if (not isinstance(data, dict) or set(data) != {"relation", "differences"}
            or not isinstance(data["relation"], str) or data["relation"] not in RELATIONS
            or not isinstance(data["differences"], list) or len(data["differences"]) > 5):
        return _result(evidence)
    relation = data["relation"]
    current, previous = evidence["current"], evidence["previous"]
    differences = []
    a = {r["id"]: r for r in current["excerpts"]}
    b = {r["id"]: r for r in previous["excerpts"]}
    for row in data["differences"]:
        if (not isinstance(row, dict) or set(row) != {"kind", "current", "previous"}
                or not all(isinstance(row[k], str) for k in row)
                or row["kind"] not in DIFFERENCES or row["current"] not in a or row["previous"] not in b):
            return _result(evidence)
        differences.append({"kind": row["kind"], "current": _citation(current, a[row["current"]]),
                            "previous": _citation(previous, b[row["previous"]])})
    if relation in {"unrelated", "uncertain"}:
        return _result(evidence, relation, "model_" + relation) if not differences else _result(evidence)
    if not differences:
        return _result(evidence, reason="missing_citations")
    if relation == "duplicate" and any(side["kind"] != "article" or not side["text_complete"]
                                       or not side["fully_represented"] or side["text_length"] < 160
                                       for side in (current, previous)):
        return _result(evidence, reason="incomplete_duplicate_evidence")
    if relation == "progress" and (current["pub_ts"] is None or previous["pub_ts"] is None
                                   or current["pub_ts"] < previous["pub_ts"]):
        return _result(evidence, reason="chronology_unverified")
    if relation == "viewpoint" and not any(r["kind"] == "viewpoint" for r in differences):
        return _result(evidence, reason="missing_viewpoint_citations")
    return _result(evidence, relation, "model_cited_proposal", differences, "cited_proposal")


def local_comparison(proposal: dict, evidence: dict) -> dict:
    if proposal["relation"] == "exact_copy":
        return _result(evidence, "duplicate", "complete_evidence_hash_equal", confidence="exact_evidence")
    differences = []
    if proposal["relation"] == "unclassified_change":
        for field in proposal.get("evidence_fields", []):
            field = "facts_text" if field == "facts_json" else field
            a = [r for r in evidence["current"]["excerpts"] if r["field"] == field]
            b = [r for r in evidence["previous"]["excerpts"] if r["field"] == field]
            for current, previous in zip(a, b):
                if current["text"] != previous["text"]:
                    differences.append({"kind": "unclassified", "current": _citation(evidence["current"], current),
                                        "previous": _citation(evidence["previous"], previous)})
                    if len(differences) == 5:
                        break
            if len(differences) == 5:
                break
    return _result(evidence, reason="local_change_unclassified" if proposal["relation"] == "unclassified_change"
                   else "local_candidate_only", differences=differences)
