"""Bounded lexical proposals; nothing here approves membership or delivery dedup."""
from __future__ import annotations

import re
from newspipe.observations import ObservationDraft

MATCHER_VERSION = "local-relations-v1"
MAX_TEXT_CHARACTERS = 20000
MAX_TERMS = 256
MIN_SIMILARITY = 0.35


def candidate_terms(draft: ObservationDraft) -> tuple[str, ...]:
    text = (draft.evidence.title + "\n" + draft.evidence.text + "\n" + draft.evidence.facts_json)[:MAX_TEXT_CHARACTERS].casefold()
    terms = {}
    for match in re.finditer(r"[a-z][a-z0-9_]*|[\u3400-\u9fff]+", text):
        word = match.group()
        values = (word[n:n + 2] for n in range(len(word) - 1)) if "\u3400" <= word[0] <= "\u9fff" else (word,)
        for term in values:
            terms.setdefault(term, None)
            if len(terms) >= MAX_TERMS:
                return tuple(terms)
    return tuple(terms)


def propose_relation(current: ObservationDraft, previous: ObservationDraft) -> dict:
    a, b = set(candidate_terms(current)), set(candidate_terms(previous))
    score = len(a & b) / len(a | b) if a or b else 0.0
    relation, reason, fields = "unrelated", "insufficient_similarity", []
    if current.identity_hash == previous.identity_hash:
        if current.version_hash == previous.version_hash:
            reason = "same_version"
        else:
            relation, reason = "unclassified_change", "same_native_new_evidence"
            if current.original_json and previous.original_json:
                fields = [key for key in ("title", "text", "facts_json", "references", "permalink", "text_complete")
                          if getattr(current.evidence, key) != getattr(previous.evidence, key)]
            else:
                # Snapshot evidence is bounded; the full version hash proves only a material change.
                fields = ["source_material"]
                reason = "same_native_changed_material_details_unverified"
    elif current.copy_hash and current.copy_hash == previous.copy_hash:
        relation, reason, score = "exact_copy", "complete_article_evidence_equal", 1.0
        fields = ["title", "text", "facts_json", "references"]
    elif score >= MIN_SIMILARITY and (current.kind == previous.kind or "unknown" in {current.kind, previous.kind}):
        relation, reason = "candidate", "bounded_lexical_similarity_only"
    return {"relation": relation, "current_id": current.observation_id,
            "previous_id": previous.observation_id, "score": round(score, 6),
            "reason": reason, "evidence_fields": fields, "matcher_version": MATCHER_VERSION}


def rank_candidates(draft: ObservationDraft, candidates: list[ObservationDraft], *, limit: int) -> list[dict]:
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError("Candidate limit must be 1..20.")
    relations = {candidate.observation_id: propose_relation(draft, candidate) for candidate in candidates}
    return sorted((row for row in relations.values() if row["relation"] != "unrelated"),
                  key=lambda row: (-row["score"], row["previous_id"]))[:limit]
