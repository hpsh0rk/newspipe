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
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from newspipe import channel, dedup, delivery, enrich as enrich_mod, filter as filter_mod
from newspipe import events, render, scheduler, state
from newspipe.errors import ConfigError, DeliveryError
from newspipe.state import now_iso, today


def _record_event(store: state.Store, event_type: str, *, payload: dict[str, Any],
                  source: str, slot: str, digest: str, now: datetime | None = None) -> None:
    """记一条事件。**事件写失败不能让投递失败**（卡已经发出去了），但也不许静默。

    契约里 `--dry` 零副作用：调用方负责不要在没有副作用时调它。
    """
    try:
        events.append(store.news_dir, event_type, payload=payload, source=source,
                      slot=slot, digest=digest, ts=now)
    except Exception as exc:                             # noqa: BLE001
        print(f"⚠️ 事件写入失败（{event_type} {source}-{slot}）："
              f"{type(exc).__name__}: {exc}"[:300])

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
    existing = store.read_batch(digest, scfg.name, slot)
    is_append = bool(existing and existing.get("card_id"))
    if existing:
        batch = dict(existing)
    else:
        batch = {"digest": digest, "slot": slot, "source": scfg.name, "adapter": scfg.adapter,
                 "form": scfg.deliver.form, "title": scfg.card_title, "chat_id": cfg.chat,
                 "created_at": now_iso(), "card_id": None, "message_id": None, "seq": 0,
                 "view": "list", "items": [], "archived": [], "overflow": 0}
    # 回调 payload 里要带批次路径，而卡片是 write_batch 之前渲染的 ⇒ 这里先钉死
    batch["batch"] = store.batch_rel(digest, scfg.name, slot)
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
    run.new = len(fresh)

    cap = delivery.card_cap(scfg)
    pending = [] if dry else store.take_pending(scfg.name, max(cap, 1) * 2)
    candidates = _merge(pending, fresh)
    plan = delivery.plan(candidates, scfg, store, digest=digest, slot=slot, now=now)
    run.planned, run.queued = len(plan.items), len(plan.queued)

    if dry:
        run.status, run.card = "dry", "none"
        run.note = f"plan={plan.reason}"
        return run

    if plan.reason != "ok":
        queued = _queue(store, scfg.name, plan.queued)
        run.status, run.queued = plan.reason, queued
        store.heartbeat(scfg.name, status=plan.reason, digest=digest, fetched=run.fetched,
                        kept=run.kept, new=run.new, queued=queued)
        if mode == "poll" and plan.reason not in NO_CURSOR_ADVANCE:
            store.set_cursor(scfg.name, _watermark(since, kept, raw, now))
        return run

    plan.items, run.enrich = _enrich_new(cfg, store, scfg, plan.items, now)

    batch, is_append, excess = _prepare_batch(cfg, store, scfg, digest, slot, plan.items, cap)
    batch["overflow"] = max(int(batch.get("overflow") or 0), plan.overflow, len(excess))

    if not scfg.sends_card:
        # 不发卡的源必须在元素预算**之前**返回：它根本不渲染卡片，拿 200 元素去卡它是纯误伤
        # （实测 reddit_painpoints 40 条 = 565 元素被判 overflow，批次永远写不出来 ⇒ 下游
        # 痛点提炼 job 天天读到空原料，且心跳显示"故障"，看起来像采集坏了）。
        store.write_batch(batch)
        store.record_pushed(scfg.name, batch["items"], digest)
        run.queued = _queue(store, scfg.name, [*plan.overflow_items, *excess])
        run.status, run.card = "state_only", "none"
        store.heartbeat(scfg.name, status="ok", digest=digest, fetched=run.fetched, kept=run.kept,
                        new=run.new, planned=run.planned, queued=run.queued, card="none",
                        enrich=run.enrich, note="只落 state")
        _advance(store, scfg, mode, since, kept, raw, now)
        return run

    try:
        card = render.render(batch, getattr(cfg, "hooks", None))
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
        if is_append and batch.get("card_id"):
            seq = int(batch.get("seq") or 1) + 1
            if not channel.update_entity(str(batch["card_id"]), seq, card):
                raise DeliveryError("卡片实体更新返回失败")
            batch["seq"] = seq
            run.card = "updated"
        else:
            card_id = channel.create_entity(card)
            batch["card_id"] = card_id
            batch["message_id"] = channel.send_card(cfg.chat, card_id)
            batch["seq"] = 1
            run.card = "sent"
    except Exception as exc:
        queued = _queue(store, scfg.name, batch["items"])
        run.status, run.note, run.queued = "send_failed", f"{type(exc).__name__}: {exc}"[:200], queued
        store.heartbeat(scfg.name, status="send_failed", digest=digest, note=run.note, queued=queued)
        _record_event(store, "failed", payload={"status": "send_failed", "error": run.note,
                                                "queued": queued},
                      source=scfg.name, slot=slot, digest=digest, now=now)
        return run

    store.write_batch(batch)
    # 语义 = "已投递"：只记真正上了卡的那些（batch["items"]），装不下顺延的不算已投递
    store.record_pushed(scfg.name, batch["items"], digest)
    run.queued = _queue(store, scfg.name, [*plan.overflow_items, *excess])
    store.note_card(digest, at=now)
    run.status = "ok"
    _record_event(store, "delivered",
                  payload={"card": run.card, "card_id": batch.get("card_id"),
                           "message_id": batch.get("message_id"), "chat": cfg.chat,
                           "items": len(batch.get("items") or []), "queued": run.queued,
                           "title": (batch.get("card") or {}).get("title") or batch.get("title")},
                  source=scfg.name, slot=slot, digest=digest, now=now)
    degraded = int((run.enrich or {}).get("degraded") or 0)
    if degraded:
        _record_event(store, "degraded", payload={"count": degraded, "enrich": run.enrich},
                      source=scfg.name, slot=slot, digest=digest, now=now)
    store.heartbeat(scfg.name, status="ok", digest=digest, fetched=run.fetched, kept=run.kept,
                    new=run.new, planned=run.planned, queued=run.queued, card=run.card,
                    card_id=batch.get("card_id"), enrich=run.enrich)
    _advance(store, scfg, mode, since, kept, raw, now)
    return run


def _advance(store: state.Store, scfg: Any, mode: str, since: float | None,
             kept: list[dict], raw: list[dict], now: datetime) -> None:
    if mode == "poll":
        store.set_cursor(scfg.name, _watermark(since, kept, raw, now), at=now)


def run_slot(cfg: Any, store: state.Store, slot: str, *, dry: bool = False,
             only: str | None = None, now: datetime | None = None) -> list[SourceRun]:
    now = now or datetime.now()
    # 批次日期必须来自**注入的时钟**：用 `today()`（墙钟）会让「回放/测试给了一个时刻」与
    # 「批次落在哪一天」分叉——批次写到今天、调用方按注入日期去读，读到的永远是 None，
    # 而心跳显示 ok。顺带把打扰预算的日期键也钉在同一个时钟上。
    digest = now.strftime("%Y-%m-%d")
    out: list[SourceRun] = []
    for scfg in scheduler.slot_sources(cfg, slot):
        if only and scfg.name != only:
            continue
        out.append(_process(cfg, store, scfg, digest=digest, slot=slot, dry=dry,
                            now=now, mode="slot"))
    return out


def run_poll(cfg: Any, store: state.Store, *, dry: bool = False, only: str | None = None,
             force: bool = False, now: datetime | None = None) -> list[SourceRun]:
    now = now or datetime.now()
    digest = now.strftime("%Y-%m-%d")                       # 同上：别一半墙钟一半注入时钟
    out: list[SourceRun] = []
    for decision in scheduler.poll_decisions(cfg, store, now, force=force, only=only):
        scfg = cfg.sources[decision.source]
        if not decision.run:
            if not dry:
                store.heartbeat(scfg.name, status=decision.reason, digest=digest)
            out.append(SourceRun(source=scfg.name, adapter=scfg.adapter, status=decision.reason))
            continue
        out.append(_process(cfg, store, scfg, digest=digest, slot="alert", dry=dry,
                            now=now, mode="poll", force=force))
    return out


def run_source(cfg: Any, store: state.Store, name: str, *, slot: str | None = None,
               dry: bool = False, force: bool = True, now: datetime | None = None) -> list[SourceRun]:
    """手工调试单源：按该源的 trigger 走对应分支（poll 源 force 跳过节流）。"""
    scfg = cfg.sources.get(name)
    if scfg is None:
        raise ConfigError(f"未知信源：{name}（可选：{', '.join(sorted(cfg.sources))}）")
    now = now or datetime.now()
    if scfg.fetch.trigger == "poll":
        return run_poll(cfg, store, dry=dry, only=name, force=force, now=now)
    use = slot or (scfg.fetch.slots[0] if scfg.fetch.slots else "manual")
    return run_slot(cfg, store, use, dry=dry, only=name, now=now)


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
