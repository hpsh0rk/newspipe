"""Prospective collection integration; prepare comparisons before taking the writer lock."""
from dataclasses import dataclass

from newspipe import config
from newspipe.observations import prepare_observation
from newspipe.source_contract import identity_key
from newspipe.story_matching import propose_relation, rank_candidates
from newspipe.story_state import CaptureSnapshot
from newspipe.story_state import StoryStorageError


def observation_status(store, *, settings, revision):
    """Optional history failure must not hide the saved off switch."""
    try:
        status = store.story_status()
        awaiting = status["activated_revision"] != revision if settings.mode != "off" or status["activated_mode"] else False
    except StoryStorageError as error:
        status = {"contract_version": 1, "availability": "unavailable", "code": error.code,
                  "activated_mode": None, "activated_revision": None, "activation_id": None,
                  "frontier_sequence": None, "observation_count": None, "capture_count": None}
        awaiting = None
    return {**status, "saved_mode": settings.mode, "saved_revision": revision, "awaiting_collection": awaiting}


@dataclass(frozen=True)
class CaptureContext:
    settings: config.StorySettings
    config_revision: str
    activation_id: str | None


@dataclass(frozen=True)
class PreparedCapture:
    context: CaptureContext
    snapshot: CaptureSnapshot
    drafts: tuple
    baselines: dict
    proposals: tuple
    at: object


def begin_capture(store, *, dry, cfg=None):
    if dry:
        return None
    settings = getattr(cfg, "stories", None)
    if settings is None:
        settings, revision = config.story_settings_snapshot(store.news_dir)
    else:
        revision = getattr(cfg, "story_revision", settings.revision)
    if settings.mode == "off" and not store._story_ledger().path.exists():
        return None
    status = store.story_status()
    if settings.mode == "off" and status["activated_mode"] == "off" and status["activated_revision"] == revision:
        return None
    return CaptureContext(settings, revision, status["activation_id"])


def prepare_capture(context, store, scfg, items, *, at):
    if context is None:
        return None
    settings = context.settings
    drafts = tuple(prepare_observation(scfg.name, scfg.adapter, item) for item in items) if settings.mode == "observe" else ()
    if settings.mode == "off":
        status = store.story_status()
        snapshot = CaptureSnapshot(status["activation_id"], status["capture_count"], {}, (), settings.values(), ())
        return PreparedCapture(context, snapshot, (), {}, (), at)
    snapshot = store.observation_capture_snapshot(list(drafts), at=at, window_days=settings.window_days,
                                                candidate_limit=settings.candidate_limit, scan_limit=settings.scan_limit)
    missing = {identity_key(d.native_id) for d in drafts if d.identity_hash not in snapshot.latest_by_identity}
    baselines = store.original_observation_baselines(scfg.name, missing)
    proposals = []
    latest = dict(snapshot.latest_by_identity)
    for draft in drafts:
        previous = latest.get(draft.identity_hash) or baselines.get(identity_key(draft.native_id), {}).get("draft")
        if previous:
            relation = propose_relation(draft, previous)
            if relation["relation"] != "unrelated":
                proposals.append(relation)
        candidates = [c for c in snapshot.candidates if c.identity_hash != draft.identity_hash]
        proposals.extend(rank_candidates(draft, candidates, limit=settings.candidate_limit))
        latest[draft.identity_hash] = draft
    return PreparedCapture(context, snapshot, drafts, baselines, tuple(proposals), at)


def commit_capture(store, capture):
    _, current_revision = config.story_settings_snapshot(store.news_dir)
    return store.record_observations(list(capture.drafts), at=capture.at, mode=capture.context.settings.mode,
                                    config_revision=capture.context.config_revision, current_revision=current_revision,
                                    expected_activation_id=capture.context.activation_id, snapshot=capture.snapshot,
                                    proposals=list(capture.proposals), baselines=capture.baselines)
