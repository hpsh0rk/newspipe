"""Read-only effective notification contract, including candidate configuration.

Only config and public Store reads are used. Preview never enters a workflow
lock, migrates ledgers, fetches, enriches, acknowledges or sends.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import yaml

from newspipe import config, delivery, edit, policy, state
from newspipe.errors import ConfigError


def _rule(cfg, source):
    resolved = policy.resolve(cfg, source)
    if resolved is not None:
        return asdict(resolved)
    return {"policy_id": None, "values": {"mode": "legacy", "form": source.deliver.form,
            "quiet_hours": source.deliver.quiet_hours, "max_cards_per_day": source.deliver.max_cards_per_day,
            "min_gap_min": source.deliver.min_gap_min, "bypass_source_limits": source.deliver.priority == "high"},
            "origins": {"scope": "legacy:global-budget"}}


def snapshot(news_dir: Path, *, source: str | None = None, candidate: dict | None = None,
             now: datetime | None = None) -> dict:
    news_dir = Path(news_dir)
    now = now or datetime.now()
    before = config.load(news_dir)
    after = before
    if candidate is not None:
        if not isinstance(candidate, dict) or set(candidate) - {"sources", "policies", "notifications", "chat", "slots"}:
            raise ConfigError("预览候选只能包含 sources、policies、notifications、chat、slots")
        raw = yaml.safe_load((news_dir / "sources.yaml").read_text(encoding="utf-8"))
        for key, value in candidate.items():
            if key == "sources":
                raw[key] = edit.merge_patch(raw.get(key), value)
            elif key == "policies" and isinstance(value, dict):
                policies = dict(raw.get(key) or {})
                for name, body in value.items():
                    if body is None:
                        policies.pop(name, None)
                    else:
                        policies[name] = body
                raw[key] = policies
            elif value is None:
                raw.pop(key, None)
            else:
                raw[key] = value
        after = config.load(news_dir, sources_text=yaml.safe_dump(raw))
    if source and source not in after.sources:
        raise ConfigError(f"未知信源 {source!r}，请用 source list 查看")
    store = state.Store(news_dir)
    day = now.date().isoformat()
    rows = []
    for name, scfg in after.sources.items():
        if source and source != name:
            continue
        current = before.sources.get(name)
        rule = _rule(after, scfg)
        effective = policy.effective_source(after, scfg)
        cap = delivery.card_cap(effective)
        items = store.peek_pending(name, max(cap, 1) * 2)
        unfinished = store.unfinished_batch(name)
        plan = delivery.plan(items, effective, store, digest=day, slot="preview", now=now, cfg=after)
        reason = plan.reason
        if not scfg.enabled:
            reason = "disabled"
        elif rule["values"]["mode"] == "digest":
            from newspipe.service import SLOT_TOLERANCE, slot_datetime
            due = [s for s in rule["values"]["slots"] if slot_datetime(after, s, now) <= now <=
                   slot_datetime(after, s, now) + SLOT_TOLERANCE]
            if not due:
                reason = "waiting_slot"
            elif all(store.notification_slot_complete(day, name, s) for s in due) and not unfinished:
                reason = "slot_complete"
        if unfinished:
            started = unfinished.get("send_started_at") or unfinished.get("remote_attempt_started_at")
            if unfinished.get("delivery_effect_done"):
                reason = "receipt_repair"
            elif started and now.timestamp() - datetime.fromisoformat(started).timestamp() >= 3600:
                reason = "send_result_unknown"
            elif int(unfinished.get("notification_attempts") or 0) >= 3:
                reason = "retry_exhausted"
        consequence = "collect mode consumes pending locally; switching back will not resend consumed items" if (
            rule["values"]["mode"] == "collect") else "pending retained until gates and notification schedule permit delivery"
        if current and (current.notify is None) != (scfg.notify is None):
            consequence += "; legacy budgets are global, policy budgets are per-source; conversion/unbind does not reset usage"
        if unfinished:
            consequence += "; unfinished remote identity/target stay pinned, no force-resend or receipt invention"
        receipt = {key: unfinished.get(key) for key in (
            "delivery_id", "delivery_chat", "card_id", "message_id", "delivery_at",
            "send_started_at", "remote_attempt_started_at", "notification_attempts", "notification_retry_after")} if unfinished else None
        rows.append({"source": name, "enabled": scfg.enabled, "before": _rule(before, current) if current else None,
                     "after": rule, "pending": store.pending_count(name), "reason": reason,
                     "plan": plan.as_dict(), "budget": store.source_budget_state(day, name),
                     "legacy_budget_scope": scfg.notify is None, "unfinished": receipt, "consequence": consequence})
    return {"contract_version": 1, "date": day, "time_zone": str(state.TZ or "local"),
            "chat": after.chat, "slots": after.slots, "hash": edit.file_hash(news_dir / "sources.yaml"),
            "notifications": asdict(after.notifications) if after.notifications is not None else None,
            "global_budget": store.budget_state(day), "sources": rows,
            "policies": {name: {"values": template.values(), "bound_sources": [s.name for s in after.sources.values()
                          if s.notify and s.notify.policy == name]} for name, template in after.policies.items()}}
