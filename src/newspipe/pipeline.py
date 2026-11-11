"""编排面 —— 唯一知道全序的地方。

    fetch → filter → dedup → （顺延队列）→ delivery.plan → enrich → render → channel → state

两条不变量在这里落地（顺序不可换）：

1. **`plan()` 必须先于 `enrich()`**：先裁决"这一批展示哪几条、预算与元素上限截掉哪些"，
   再只对留下的条目调模型。反了就是先花钱再挑。
2. **`record_pushed` 只在投递成功后调用**：pushed-ids 的语义是"已投递"，不是"已看到"。
   所以发送失败、元素超限、被预算挡下的条目都不记 pushed —— 它们要么进了 pending 队列，
   要么下一轮被重新取回，不会丢。发卡失败时**不新建卡堆叠**（state 里有 card_id 就只更新实体）。

第三条：**`--dry` 零副作用**——不写游标、不写心跳、不写批次、不调模型。历史教训：alert 的
基线分支曾在 dry 下也写游标，一次探查就把基线吃掉，之后再跑就分不清"基线"与"真轮询"。
"""
from __future__ import annotations

import importlib
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from newspipe import channel, dedup, delivery, enrich as enrich_mod, filter as filter_mod
from newspipe import events, policy, render, runtime, scheduler, state
from newspipe.errors import ConfigError, DeliveryError
from newspipe.state import now_iso, today


def _record_event(store: state.Store, event_type: str, *, payload: dict[str, Any],
                  source: str, slot: str, digest: str, now: datetime | None = None,
                  event_id: str = "") -> bool:
    """记一条事件。**事件写失败不能让投递失败**（卡已经发出去了），但也不许静默。

    契约里 `--dry` 零副作用：调用方负责不要在没有副作用时调它。
    """
    try:
        events.append(store.news_dir, event_type, payload=payload, source=source,
                      slot=slot, digest=digest, ts=now, event_id=event_id)
        return True
    except Exception as exc:                             # noqa: BLE001
        print(f"⚠️ 事件写入失败（{event_type} {source}-{slot}）："
              f"{type(exc).__name__}: {exc}"[:300])
        return False

ID_PREFIX = {"append_card": "a", "card": "n", "state_only": "s"}
# 这些状态不推进游标：条目还没被消费（下轮要重取），推进了就等于丢掉
NO_CURSOR_ADVANCE = {"config_missing", "error", "below_min_items", "send_failed", "overflow"}


@dataclass
class SourceRun:
    source: str
    adapter: str = ""
    status: str = "ok"
    fetched: int = 0
    kept: int = 0
    new: int = 0
    planned: int = 0
    queued: int = 0
    card: str = "none"        # sent | updated | none
    enrich: dict[str, int] = field(default_factory=dict)
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        out = {"source": self.source, "adapter": self.adapter, "status": self.status,
               "fetched": self.fetched, "kept": self.kept, "new": self.new,
               "planned": self.planned, "queued": self.queued, "card": self.card}
        if self.enrich:
            out["enrich"] = self.enrich
        if self.note:
            out["note"] = self.note
        return out


# ── 小工具 ────────────────────────────────────────────────────────────────────
def _adapter(name: str) -> Any:
    return importlib.import_module(f"newspipe.adapters.{name}")


def _merge(first: list[dict], second: list[dict]) -> list[dict]:
    """按 ext_id 去重合并（first 优先，保序）——顺延队列在旧条目在前的语义下先进先出。"""
    seen: set[str] = set()
    out: list[dict] = []
    for it in [*first, *second]:
        key = str(it.get("ext_id") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _queued_ids(store: state.Store, source: str) -> set[str]:
    path = store.root / "pending" / f"{source}.jsonl"
    if not path.is_file():
        return set()
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.add(str(json.loads(line).get("ext_id") or ""))
        except ValueError:
            continue
    return out


def _queue(store: state.Store, source: str, items: list[dict]) -> int:
    """进显式顺延队列（幂等：已在队列里的不重复入队）。

    同时排除**已投递**的条目：队列的语义是"还没发出去"，把已投递的条目排进来会让它下轮
    重新参与裁决、把批次撑大、再触发超限再排队——一个自我放大的死循环（2026-10-03 实测：
    aihot 已发 13 条的批次被重排进 17 条，243 元素 > 200，卡再也发不出去）。
    """
    if not items:
        return 0
    known = _queued_ids(store, source) | store.pushed_ids(source)
    todo = [it for it in items if str(it.get("ext_id") or "") not in known]
    return store.append_pending(source, todo) if todo else 0


def _watermark(since: float | None, kept: list[dict], raw: list[dict], now: datetime) -> float:
    for pool in (kept, raw):
        stamps = [float(it["pub_ts"]) for it in pool if it.get("pub_ts")]
        if stamps:
            return max(stamps)
    return now.timestamp()


def _assign_ids(batch: dict) -> None:
    prefix = ID_PREFIX.get(str(batch.get("form") or "card"), "n")
    used = {str(i.get("id") or "") for i in batch.get("items") or []}
    n = 0
    for it in batch.get("items") or []:
        if not it.get("id"):
            n += 1
            while f"{prefix}{n:02d}" in used:
                n += 1
            it["id"] = f"{prefix}{n:02d}"
            used.add(it["id"])
        it.setdefault("status", "unread")
        it.setdefault("updated_at", None)


def _prepare_batch(cfg: Any, store: state.Store, scfg: Any, digest: str, slot: str,
                   items: list[dict], cap: int) -> tuple[dict, bool, list[dict]]:
    """当日同 (source, slot) 已有带 card_id 的批次 → 追加进同一张卡（不新建卡堆叠）。

    返回 `(batch, is_append, excess)`：`excess` 是**装不下**的条目（已投递的前 cap 条必须留在
    卡上，多出来的顺延下一批）。不返回它们就会把批次撑到元素上限之外，整张卡被飞书拒收。
    """
    existing = store.unfinished_batch(scfg.name) or store.read_batch(digest, scfg.name, slot)
    if (existing and not existing.get("delivery_pending") and scfg.sends_card
            and (existing.get("delivery_chat") or existing.get("chat_id")) != cfg.chat):
        # Old cards retain their callback paths; a different destination owns a different card.
        slot = f"{slot}-to-{hashlib.sha256(cfg.chat.encode()).hexdigest()[:16]}"
        existing = store.read_batch(digest, scfg.name, slot)
        if existing and (existing.get("delivery_chat") or existing.get("chat_id")) != cfg.chat:
            raise DeliveryError("通知目标与已有卡片目标不一致，拒绝复用卡片")
    is_append = bool(existing and existing.get("message_id"))
    if existing:
        batch = dict(existing)
        if existing.get("delivery_pending"):
            # Freeze an unfinished operation's payload/form/target as well as its identity.
            return batch, is_append, []
    else:
        batch = {"digest": digest, "slot": slot, "source": scfg.name, "adapter": scfg.adapter,
                 "form": scfg.deliver.form, "title": scfg.card_title, "chat_id": cfg.chat,
                 "created_at": now_iso(), "card_id": None, "message_id": None, "seq": 0,
                 "view": "list", "items": [], "archived": [], "overflow": 0}
    # 回调 payload 里要带批次路径，而卡片是 write_batch 之前渲染的 ⇒ 这里先钉死
    batch["batch"] = store.batch_rel(batch["digest"], scfg.name, batch["slot"])
    merged = list(batch.get("items") or [])
    known = {str(i.get("ext_id") or "") for i in merged}
    for it in items:
        key = str(it.get("ext_id") or "")
        if key and key not in known:
            merged.append(it)
            known.add(key)
    if scfg.deliver.form == "append_card" and len(merged) > cap:
        # 滚动窗口：卡上只留最新 cap 条，更早的进 archived（state 不丢，只是不渲染）
        batch["archived"] = [*(batch.get("archived") or []), *merged[:-cap]]
        merged = merged[-cap:]
        batch["overflow"] = len(batch["archived"])
    excess: list[dict] = []
    if len(merged) > cap:
        # 同 (source, slot) 重跑：卡上已发的前 cap 条留着（它们已经在卡片实体里），
        # 装不下的顺延到下一批——由下一次运行（通常是下一个槽位）的 take_pending 取走。
        excess = merged[cap:]
        merged = merged[:cap]
        batch["overflow"] = max(int(batch.get("overflow") or 0), len(excess))
    batch["items"] = merged
    batch["title"] = scfg.card_title
    batch["form"] = scfg.deliver.form
    batch["updated_at"] = now_iso()
    _assign_ids(batch)
    return batch, is_append, excess


def _enrich_new(cfg: Any, store: state.Store, scfg: Any, items: list[dict],
                now: datetime, *, retry_degraded: bool = False) -> tuple[list[dict], dict[str, int]]:
    """只加工没加工过的条目（回执机制让重复加工很便宜，但没必要真去调）。

    `retry_degraded=True` 时连 `enrich_state="degraded"` 的也重来：修完模型/预算问题后
    需要它，否则失败过的条目会被当成"已处理"永远跳过。
    """
    todo = [it for it in items
            if not it.get("enrich_state")
            or (retry_degraded and it.get("enrich_state") == "degraded")]
    if not todo or scfg.enrich.summary == "off":
        for it in items:
            it.setdefault("enrich_state", "off")
        return items, {}
    done = enrich_mod.enrich(todo, scfg, store, cfg.models, cfg.budget(), now=now)
    by_key = {str(d.get("ext_id") or ""): d for d in done}
    out = [by_key.get(str(it.get("ext_id") or ""), it) for it in items]
    counts: dict[str, int] = {}
    for it in out:
        key = str(it.get("enrich_state") or "?")
        counts[key] = counts.get(key, 0) + 1
    return out, counts


# ── 主流程 ────────────────────────────────────────────────────────────────────
def _process(cfg: Any, store: state.Store, scfg: Any, *, digest: str, slot: str,
             dry: bool, now: datetime, mode: str, force: bool = False) -> SourceRun:
    if dry:
        return _process_locked(cfg, store, scfg, digest=digest, slot=slot, dry=dry,
                               now=now, mode=mode, force=force)
    with store.transaction():
        return _process_locked(cfg, store, scfg, digest=digest, slot=slot, dry=dry,
                               now=now, mode=mode, force=force)


def _process_locked(cfg: Any, store: state.Store, scfg: Any, *, digest: str, slot: str,
                    dry: bool, now: datetime, mode: str, force: bool = False) -> SourceRun:
    run = SourceRun(source=scfg.name, adapter=scfg.adapter)
    since = store.cursor(scfg.name) if mode == "poll" else None
    unfinished = None if dry else store.unfinished_batch(scfg.name)
    if unfinished and "notification_mode" in unfinished and not unfinished.get("delivery_effect_done"):
        # Unbinding cannot turn a protected notification into an unbounded legacy send.
        collect_source(cfg, store, scfg.name, dry=dry, force=force, now=now)
        return notify_source(cfg, store, scfg.name, dry=dry, slot=slot if mode == "slot" else None, now=now)
    if unfinished and unfinished.get("delivery_effect_done"):
        # A confirmed remote success needs only local repair, even if fetching/budgets now fail.
        for key, value in (unfinished.get("delivery_run") or {}).items():
            setattr(run, key, value)
        recovered = _complete_delivery(store, unfinished, run)
        if recovered.status != "ok" or (unfinished["digest"], unfinished["slot"]) == (digest, slot):
            return recovered
        # Repairing yesterday must not consume today's only scheduled collection.
        run = SourceRun(source=scfg.name, adapter=scfg.adapter)
        since = store.cursor(scfg.name) if mode == "poll" else None
    if mode == "poll" and since is None and not force and not dry:
        # 首轮只建基线：否则会把全部历史条目当新内容推给用户
        store.set_cursor(scfg.name, now.timestamp(), at=now)
        run.status = "baseline"
        store.heartbeat(scfg.name, status="baseline", digest=digest, note="首轮建基线，不发卡")
        return run

    try:
        raw = _adapter(scfg.adapter).fetch(scfg.adapter_config(), since=since)
    except ConfigError as exc:
        run.status, run.note = "config_missing", str(exc)[:200]
        if not dry:
            store.heartbeat(scfg.name, status="config_missing", digest=digest, note=run.note)
        return run
    except Exception as exc:
        run.status, run.note = "error", f"{type(exc).__name__}: {exc}"[:200]
        if not dry:
            store.heartbeat(scfg.name, status="error", digest=digest, note=run.note)
        return run

    run.fetched = len(raw)
    kept = filter_mod.apply(raw, scfg)
    run.kept = len(kept)
    fresh = dedup.fresh(kept, store, scfg.name)
    unfinished = None if dry else store.unfinished_batch(scfg.name)
    if unfinished:
        fresh = _merge(list(unfinished.get("items") or []), fresh)
    run.new = len(fresh)

    cap = delivery.card_cap(scfg)
    pending = [] if dry else dedup.fresh(store.peek_pending(scfg.name, max(cap, 1) * 2), store, scfg.name)
    candidates = _merge(pending, fresh)
    return _deliver_candidates(cfg, store, scfg, run, candidates, digest=digest, slot=slot,
                               dry=dry, now=now, mode=mode, since=since, kept=kept, raw=raw)


def _deliver_candidates(cfg: Any, store: state.Store, scfg: Any, run: SourceRun,
                        candidates: list[dict], *, digest: str, slot: str, dry: bool,
                        now: datetime, mode: str, since: float | None = None,
                        kept: list[dict] | None = None, raw: list[dict] | None = None,
                        scheduled_slot: bool = False) -> SourceRun:
    kept, raw = kept or [], raw or []
    operation_clock = time.monotonic()
    cap = delivery.card_cap(scfg)
    plan = delivery.plan(candidates, scfg, store, digest=digest, slot=slot, now=now, cfg=cfg)
    run.planned, run.queued = len(plan.items), len(plan.queued)

    if dry:
        run.status, run.card = "dry", "none"
        run.note = f"plan={plan.reason}"
        return run

    if plan.reason != "ok":
        _queue(store, scfg.name, candidates)
        queued = store.pending_count(scfg.name)
        run.status, run.queued = plan.reason, queued
        store.heartbeat(scfg.name, status=plan.reason, digest=digest, fetched=run.fetched,
                        kept=run.kept, new=run.new, queued=queued)
        if mode == "poll" and plan.reason not in NO_CURSOR_ADVANCE:
            store.set_cursor(scfg.name, _watermark(since, kept, raw, now))
        return run

    # Persist every candidate first, and enrich only the final display set after merging.
    _queue(store, scfg.name, candidates)
    batch, is_append, excess = _prepare_batch(cfg, store, scfg, digest, slot, plan.items, cap)
    if scfg.sends_card:
        if not batch.get("delivery_pending"):
            if scfg.notify is not None:
                batch["notification_mode"] = policy.resolve(cfg, scfg).values["mode"]
                batch["notification_scheduled"] = scheduled_slot
                batch["notification_slot"] = slot
            else:
                batch.pop("notification_mode", None)
                batch.pop("notification_scheduled", None)
                batch.pop("notification_slot", None)
        _begin_delivery(batch, cfg, digest, now)
        if scfg.notify is not None or "notification_mode" in batch:
            batch["notification_attempts"] = int(batch.get("notification_attempts") or 0) + 1
            batch["notification_retry_after"] = now.timestamp() + 60
            store.write_batch(batch)
    pinned = batch.get("delivery_payload")
    if not pinned and (scfg.notify is None or scfg.sends_card):
        batch["items"], run.enrich = _enrich_new(cfg, store, scfg, batch["items"], now)
    if not pinned:
        batch["overflow"] = max(int(batch.get("overflow") or 0), plan.overflow, len(excess))

    if not scfg.sends_card:
        # 不发卡的源必须在元素预算**之前**返回：它根本不渲染卡片，拿 200 元素去卡它是纯误伤
        # （实测 reddit_painpoints 40 条 = 565 元素被判 overflow，批次永远写不出来 ⇒ 下游
        # 痛点提炼 job 天天读到空原料，且心跳显示"故障"，看起来像采集坏了）。
        store.write_batch(batch)
        store.record_pushed(scfg.name, batch["items"], digest)
        store.remove_pending(scfg.name, store.pushed_ids(scfg.name))
        run.queued = store.pending_count(scfg.name)
        run.status, run.card = "state_only", "none"
        store.heartbeat(scfg.name, status="ok", digest=digest, fetched=run.fetched, kept=run.kept,
                        new=run.new, planned=run.planned, queued=run.queued, card="none",
                        enrich=run.enrich, note="只落 state")
        _advance(store, scfg, mode, since, kept, raw, now)
        return run

    try:
        card = pinned or render.render(batch, getattr(cfg, "hooks", None))
        render.assert_within_limit(card, what=f"{scfg.name}-{slot}")
    except ValueError as exc:
        # 元素超限（飞书 11310）：整批顺延，不记 pushed —— 这正是 v1 静默失败的那个坑
        queued = _queue(store, scfg.name, batch["items"])
        run.status, run.note, run.queued = "overflow", str(exc)[:200], queued
        store.heartbeat(scfg.name, status="overflow", digest=digest, note=run.note, queued=queued)
        _record_event(store, "failed", payload={"status": "overflow", "error": run.note,
                                                "queued": queued},
                      source=scfg.name, slot=slot, digest=digest, now=now)
        return run

    try:
        _begin_delivery(batch, cfg, digest, now)
        batch["delivery_run"] = {key: getattr(run, key) for key in ("fetched", "kept", "new", "planned", "enrich")}
        if mode == "poll":
            batch["delivery_cursor"] = _watermark(since, kept, raw, now)
        else:
            batch.pop("delivery_cursor", None)
        batch["delivery_pending"] = True
        batch["delivery_effect_done"] = False
        batch["delivery_payload"] = card
        if scfg.notify is not None or "notification_mode" in batch or batch.get("card_id"):
            store.write_batch(batch)
        if is_append and batch.get("card_id"):
            seq = int(batch.get("seq") or 1) + 1
            # Reserve the sequence durably: a lost response cannot make us reuse it.
            batch["seq"] = seq
            batch.setdefault("remote_attempt_started_at", now.isoformat())
            store.write_batch(batch)
            if not channel.update_entity(str(batch["card_id"]), seq, card):
                raise DeliveryError("卡片实体更新返回失败")
            run.card = "updated"
        else:
            if not batch.get("card_id"):
                batch["card_id"] = channel.create_entity(card)
                batch["seq"] = 1
            else:
                batch["seq"] = int(batch.get("seq") or 1) + 1
                store.write_batch(batch)
                if not channel.update_entity(str(batch["card_id"]), batch["seq"], card):
                    raise DeliveryError("未发送卡片实体更新返回失败")
            batch.setdefault("send_uuid", str(uuid.uuid4()))
            started = batch.get("send_started_at")
            if started and now.timestamp() - datetime.fromisoformat(started).timestamp() >= 3600:
                raise DeliveryError("发送结果未确认且已超过平台幂等窗口；请核对消息并修复批次回执，禁止自动重发")
            batch.setdefault("send_started_at", now.isoformat())
            batch.setdefault("remote_attempt_started_at", now.isoformat())
            store.write_batch(batch)
            batch["message_id"] = channel.send_card(batch["delivery_chat"], str(batch["card_id"]),
                                                    idempotency_key=batch["send_uuid"])
            run.card = "sent"
        batch["delivery_effect_done"] = True
        confirmed = now + timedelta(seconds=max(0, time.monotonic() - operation_clock))
        batch["delivery_confirmed_at"] = confirmed.isoformat()
        batch["delivery_confirmation_date"] = confirmed.date().isoformat()
        batch["delivery_card"] = run.card
        store.write_batch(batch)
    except Exception as exc:
        _queue(store, scfg.name, batch["items"])
        queued = store.pending_count(scfg.name)
        run.status, run.note, run.queued = "send_failed", f"{type(exc).__name__}: {exc}"[:200], queued
        store.heartbeat(scfg.name, status="send_failed", digest=digest, note=run.note, queued=queued)
        _record_event(store, "failed", payload={"status": "send_failed", "error": run.note,
                                                "queued": queued},
                      source=scfg.name, slot=slot, digest=digest, now=now)
        return run

    return _complete_delivery(store, batch, run)


def _begin_delivery(batch: dict, cfg: Any, digest: str, now: datetime) -> None:
    if not batch.get("delivery_pending"):
        if batch.get("message_id"):
            batch.pop("send_started_at", None)
            batch.pop("send_uuid", None)
        batch.pop("remote_attempt_started_at", None)
        batch.pop("delivery_payload", None)
        batch.pop("delivery_confirmed_at", None)
        batch.pop("delivery_confirmation_date", None)
        batch.update(delivery_id=uuid.uuid4().hex, delivery_at=now.isoformat(),
                     delivery_date=digest, delivery_chat=cfg.chat, notification_attempts=0)
    batch.setdefault("delivery_id", uuid.uuid4().hex)
    batch.setdefault("delivery_at", now.isoformat())
    batch.setdefault("delivery_date", digest)
    batch.setdefault("delivery_chat", cfg.chat)
    batch["delivery_pending"] = True
    batch["delivery_effect_done"] = False


def _complete_delivery(store: state.Store, batch: dict, run: SourceRun) -> SourceRun:
    source, digest, slot = batch["source"], batch["digest"], batch["slot"]
    operation_id = batch["delivery_id"]
    delivered_at = datetime.fromisoformat(batch.get("delivery_confirmed_at") or batch["delivery_at"])
    run.card = batch["delivery_card"]
    store.record_pushed(source, batch["items"], digest)
    store.remove_pending(source, store.pushed_ids(source))
    run.queued = store.pending_count(source)
    store.note_card(batch.get("delivery_confirmation_date") or batch["delivery_date"], at=delivered_at, operation_id=operation_id, source=source)
    run.status = "ok"
    recorded = _record_event(store, "delivered",
                  payload={"card": run.card, "card_id": batch.get("card_id"),
                           "operation_id": operation_id,
                           "message_id": batch.get("message_id"), "chat": batch["delivery_chat"],
                           "items": len(batch.get("items") or []), "queued": run.queued,
                           "title": (batch.get("card") or {}).get("title") or batch.get("title")},
                  source=source, slot=slot, digest=digest, now=delivered_at,
                  event_id=f"ev_delivery_{operation_id}")
    degraded = int((run.enrich or {}).get("degraded") or 0)
    if degraded:
        recorded = _record_event(store, "degraded", payload={"count": degraded, "enrich": run.enrich},
                                source=source, slot=slot, digest=digest, now=delivered_at,
                                event_id=f"ev_delivery_{operation_id}_degraded") and recorded
    if not recorded:
        run.status, run.note = "error", "投递已确认，事件台账未完成；下轮只修复本地回执"
        store.heartbeat(source, status=run.status, digest=digest, note=run.note)
        return run
    store.heartbeat(source, status="ok", digest=digest, fetched=run.fetched, kept=run.kept,
                    new=run.new, planned=run.planned, queued=run.queued, card=run.card,
                    card_id=batch.get("card_id"), enrich=run.enrich)
    if "delivery_cursor" in batch:
        current = store.cursor(source)
        store.set_cursor(source, max(float(current or 0), batch["delivery_cursor"]), at=delivered_at)
    if batch.get("notification_scheduled"):
        store.complete_notification_slot(batch["delivery_date"], source,
                                         batch.get("notification_slot") or slot, status="ok")
    batch["delivery_pending"] = False
    store.write_batch(batch)
    return run


def _advance(store: state.Store, scfg: Any, mode: str, since: float | None,
             kept: list[dict], raw: list[dict], now: datetime) -> None:
    if mode == "poll":
        store.set_cursor(scfg.name, _watermark(since, kept, raw, now), at=now)


@runtime.scoped
def run_slot(cfg: Any, store: state.Store, slot: str, *, dry: bool = False,
             only: str | None = None, now: datetime | None = None,
             collect_only: bool = False) -> list[SourceRun]:
    now = now or datetime.now()
    # 批次日期必须来自**注入的时钟**：用 `today()`（墙钟）会让「回放/测试给了一个时刻」与
    # 「批次落在哪一天」分叉——批次写到今天、调用方按注入日期去读，读到的永远是 None，
    # 而心跳显示 ok。顺带把打扰预算的日期键也钉在同一个时钟上。
    digest = now.strftime("%Y-%m-%d")
    out: list[SourceRun] = []
    for scfg in scheduler.slot_sources(cfg, slot):
        if only and scfg.name != only:
            continue
        if scfg.notify is not None:
            out.append(collect_source(cfg, store, scfg.name, dry=dry, now=now, force=True))
            if not collect_only:
                out.append(notify_source(cfg, store, scfg.name, dry=dry, now=now, slot=slot))
            continue
        out.append(_process(cfg, store, scfg, digest=digest, slot=slot, dry=dry,
                            now=now, mode="slot"))
    return out


@runtime.scoped
def run_poll(cfg: Any, store: state.Store, *, dry: bool = False, only: str | None = None,
             force: bool = False, now: datetime | None = None,
             collect_only: bool = False) -> list[SourceRun]:
    now = now or datetime.now()
    digest = now.strftime("%Y-%m-%d")                       # 同上：别一半墙钟一半注入时钟
    out: list[SourceRun] = []
    for decision in scheduler.poll_decisions(cfg, store, now, force=force, only=only):
        scfg = cfg.sources[decision.source]
        if not decision.run:
            if not dry:
                store.heartbeat(scfg.name, status=decision.reason, digest=digest)
            out.append(SourceRun(source=scfg.name, adapter=scfg.adapter, status=decision.reason))
            if scfg.notify is not None and not collect_only:
                out.append(notify_source(cfg, store, scfg.name, dry=dry, now=now))
            continue
        if scfg.notify is not None:
            out.append(collect_source(cfg, store, scfg.name, dry=dry, now=now, force=force))
            if not collect_only:
                out.append(notify_source(cfg, store, scfg.name, dry=dry, now=now))
        else:
            out.append(_process(cfg, store, scfg, digest=digest, slot="alert", dry=dry,
                                now=now, mode="poll", force=force))
    return out


@runtime.scoped
def run_source(cfg: Any, store: state.Store, name: str, *, slot: str | None = None,
               dry: bool = False, force: bool = True, now: datetime | None = None) -> list[SourceRun]:
    """手工调试单源：按该源的 trigger 走对应分支（poll 源 force 跳过节流）。"""
    scfg = cfg.sources.get(name)
    if scfg is None:
        raise ConfigError(f"未知信源：{name}（可选：{', '.join(sorted(cfg.sources))}）")
    now = now or datetime.now()
    if scfg.notify is not None:
        return [collect_source(cfg, store, name, dry=dry, force=force, now=now),
                notify_source(cfg, store, name, dry=dry, slot=slot, now=now)]
    if scfg.fetch.trigger == "poll":
        return run_poll(cfg, store, dry=dry, only=name, force=force, now=now)
    use = slot or (scfg.fetch.slots[0] if scfg.fetch.slots else "manual")
    return run_slot(cfg, store, use, dry=dry, only=name, now=now)


def _source(cfg: Any, name: str) -> Any:
    if name not in cfg.sources:
        raise ConfigError(f"未知信源 {name!r}；先用 source list 查看可选 ID")
    return cfg.sources[name]


@runtime.scoped
def collect_source(cfg: Any, store: state.Store, name: str, *, now: datetime | None = None,
                   force: bool = True, dry: bool = False) -> SourceRun:
    """Collect durably, never remotely send or enrich; manual force skips only baseline/throttle."""
    scfg = _source(cfg, name)
    now = now or datetime.now()
    digest = now.date().isoformat()
    run = SourceRun(source=name, adapter=scfg.adapter)
    if not scfg.enabled:
        run.status = "disabled"
        return run
    from contextlib import nullcontext
    with nullcontext() if dry else store.transaction():
        since = store.cursor(name) if scfg.fetch.trigger == "poll" else None
        if scfg.fetch.trigger == "poll" and not force and not dry:
            if since is None:
                store.set_cursor(name, now.timestamp(), at=now)
                run.status = "baseline"
                store.heartbeat(name, status=run.status, digest=digest, note="首轮建基线，不发卡")
                return run
            age = store.cursor_age_min(name, now)
            if age is not None and age < scfg.fetch.interval_min:
                run.status = "throttled"
                return run
        try:
            raw = _adapter(scfg.adapter).fetch(scfg.adapter_config(), since=since)
            kept = filter_mod.apply(raw, scfg)
            fresh = dedup.fresh(kept, store, name)
        except Exception as exc:
            run.status = "config_missing" if isinstance(exc, ConfigError) else "error"
            run.note = f"{type(exc).__name__}: {exc}"[:200]
            if not dry:
                store.heartbeat(name, status=run.status, digest=digest, note=run.note)
                _record_event(store, "failed", payload={"status": run.status, "error": run.note},
                              source=name, slot="collect", digest=digest, now=now)
            return run
        run.fetched, run.kept, run.new = len(raw), len(kept), len(fresh)
        if dry:
            run.status, run.note = "dry", "只预览采集；不写游标、队列或批次"
            return run
        _queue(store, name, fresh)
        # Cursor commits after durable pending, even when delivery is paused or fails later.
        store.set_cursor(name, max(float(since or 0), _watermark(since, kept, raw, now)), at=now)
        run.status, run.queued = "collected", store.pending_count(name)
        store.heartbeat(name, status=run.status, digest=digest, fetched=run.fetched,
                        kept=run.kept, new=run.new, queued=run.queued, card="none")
        rule = policy.resolve(cfg, scfg)
        if rule is not None and rule.values["mode"] == "collect":
            return notify_source(cfg, store, name, now=now)
        return run


@runtime.scoped
def notify_source(cfg: Any, store: state.Store, name: str, *, now: datetime | None = None,
                  slot: str | None = None, bypass_clock: bool = False,
                  dry: bool = False) -> SourceRun:
    """Deliver existing pending only; explicit clock bypass never bypasses delivery gates."""
    scfg = _source(cfg, name)
    now = now or datetime.now()
    digest = now.date().isoformat()
    run = SourceRun(source=name, adapter=scfg.adapter)
    from contextlib import nullcontext
    with nullcontext() if dry else store.transaction():
        unfinished = store.unfinished_batch(name)
        if unfinished and unfinished.get("delivery_effect_done") and not dry:
            for key, value in (unfinished.get("delivery_run") or {}).items():
                setattr(run, key, value)
            return _complete_delivery(store, unfinished, run)
        if not scfg.enabled:
            run.status = "disabled"
            return run
        rule = policy.resolve(cfg, scfg)
        effective = policy.effective_source(cfg, scfg)
        mode = rule.values["mode"] if rule else "immediate"
        if mode == "digest" and not bypass_clock:
            from newspipe.service import SLOT_TOLERANCE, slot_datetime
            eligible = [s for s in rule.values["slots"] if (slot is None or s == slot)
                        and slot_datetime(cfg, s, now) <= now <= slot_datetime(cfg, s, now) + SLOT_TOLERANCE]
            if not eligible:
                run.status, run.queued = "waiting_slot", store.pending_count(name)
                return run
            remaining = [s for s in eligible if not store.notification_slot_complete(digest, name, s)]
            if not remaining and not unfinished:
                run.status, run.queued = "slot_complete", store.pending_count(name)
                return run
            slot = remaining[0] if remaining else eligible[0]
        if unfinished:
            started = unfinished.get("remote_attempt_started_at") or unfinished.get("send_started_at")
            if started and now.timestamp() - datetime.fromisoformat(started).timestamp() >= 3600:
                run.status = "send_result_unknown"
                run.note = (f"结果未确认且已超幂等窗口；核对平台回执：目标 {unfinished.get('delivery_chat')}, "
                            f"卡片 {unfinished.get('card_id')}, 操作 {unfinished.get('delivery_id')}, 时间 {started}；禁止自动重发")
                return run
            if not dry and (scfg.notify is not None or "notification_mode" in unfinished):
                if int(unfinished.get("notification_attempts") or 0) >= 3:
                    run.status, run.note = "retry_exhausted", "已尝试三次；仅可在原身份安全窗口内显式重试，或核对平台回执"
                    return run
                if float(unfinished.get("notification_retry_after") or 0) > now.timestamp():
                    run.status = "retry_wait"
                    return run
            if mode == "collect":
                run.status, run.note = "send_failed", "未完成的远端操作保留原身份；先核对回执，不能改为本地消费"
                return run
            effective = replace(effective, deliver=replace(effective.deliver, form=unfinished["form"]))
        cap = delivery.card_cap(effective)
        candidates = dedup.fresh(store.peek_pending(name, max(cap, 1) * 2), store, name)
        if unfinished:
            candidates = _merge(list(unfinished.get("items") or []), candidates)
        if not candidates:
            run.status = "nothing_new"
            if not dry and mode == "digest" and not bypass_clock:
                store.complete_notification_slot(digest, name, slot, status=run.status)
            return run
        if not dry and effective.sends_card and rule:
            store.ensure_source_accounting(digest)
        if unfinished:
            use = unfinished["slot"]
        elif mode == "collect" or (mode == "immediate" and effective.deliver.form == "card"):
            use = "preview" if dry else store.reserve_notification_slot(digest, name,
                                            prefix="collected" if mode == "collect" else "notify")
        elif mode == "immediate":
            use = "live"
        else:
            use = slot or "manual-notify"
        run.new = len(candidates)
        try:
            outcome = _deliver_candidates(cfg, store, effective, run, candidates, digest=digest, slot=use,
                                         dry=dry, now=now, mode="notify",
                                         scheduled_slot=mode == "digest" and not bypass_clock)
            if (not dry and mode == "digest" and not bypass_clock and
                    outcome.status not in {"error", "config_missing", "send_failed", "overflow"}):
                store.complete_notification_slot(digest, name, slot, status=outcome.status)
            return outcome
        except Exception as exc:
            run.status, run.note = "error", f"{type(exc).__name__}: {exc}"[:200]
            if not dry:
                store.heartbeat(name, status=run.status, digest=digest, note=run.note,
                                queued=store.pending_count(name))
                _record_event(store, "failed", payload={"status": run.status, "error": run.note},
                              source=name, slot=use, digest=digest, now=now)
            return run


def stats(store: state.Store, cfg: Any) -> list[dict[str, Any]]:
    """运行态一览（供 cli --status 与 WebUI 复用）。"""
    rows = []
    for scfg in cfg.sources.values():
        status = store.status(scfg.name)
        cursor = store.cursor(scfg.name)
        rows.append({
            "source": scfg.name, "adapter": scfg.adapter, "enabled": scfg.enabled,
            "trigger": scfg.fetch.trigger, "form": scfg.deliver.form,
            "priority": scfg.deliver.priority, "summary": scfg.enrich.summary,
            "status": status.get("status") or "—", "ts": status.get("ts") or "",
            "fetched": status.get("fetched"), "planned": status.get("planned"),
            "card": status.get("card"), "note": status.get("note") or "",
            "cursor": datetime.fromtimestamp(cursor).isoformat(timespec="minutes") if cursor else "",
            "pending": store.pending_count(scfg.name),
        })
    return rows
