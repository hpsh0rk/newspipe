"""资讯管线 v2 测试。

覆盖四类容易静默失败的地方（这些是 v1 用血的教训换来的）：

1. **配置层**：四轴解析、YAML 1.1 的 `off` → 布尔陷阱、未知适配器/槽位必须报错而不是静默忽略；
2. **投递裁决**：静默窗口（含跨午夜）、优先级越过预算、打扰预算、最小间隔、攒批门槛、
   元素上限截断与顺延——顺序错了就是"先花钱再挑"；
3. **不变量**：`plan()` 先于 `enrich()`；`record_pushed` 只在投递成功后写；`--dry` 零副作用；
   发卡失败不新建卡堆叠；
4. **卡片预算**：行结构改了必须重算 200 元素上限（本文件里的实测数字与 config.CARD_MAX_ITEMS 同源）。

全程离线：适配器与模型调用都被替换，不碰网络、不真发卡。
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"          # 未安装时也能跑：src 布局回退
for _p in (str(SRC_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from newspipe import (  # noqa: E402
    channel,
    config,
    delivery,
    enrich,
    events,
    filter as filter_mod,
    interaction,
    llm,
    pipeline,
    render,
    scheduler,
    state,
)
from newspipe.errors import ConfigError  # noqa: E402

NOW = datetime(2026, 10, 3, 10, 0, 0)


def _src(**kw) -> config.SourceConfig:
    """构造一个信源配置（默认：槽位触发、发卡、不加工）。"""
    fetch = config.FetchCfg(trigger=kw.get("trigger", "slot"),
                            slots=tuple(kw.get("slots", ("am",))),
                            interval_min=kw.get("interval_min", 0.0))
    return config.SourceConfig(
        name=kw.get("name", "src"),
        adapter=kw.get("adapter", "aihot"),
        enabled=kw.get("enabled", True),
        tier=kw.get("tier", "T2"),
        first_party=False,
        fetch=fetch,
        filter=config.FilterCfg(min_items=kw.get("min_items", 1),
                                max_items=kw.get("max_items", config.CARD_MAX_ITEMS),
                                include_keywords=tuple(kw.get("include_keywords", ())),
                                exclude_keywords=tuple(kw.get("exclude_keywords", ())),
                                rank=kw.get("rank", "none")),
        enrich=config.EnrichCfg(summary=kw.get("summary", "off"),
                                fetch_body=kw.get("fetch_body", False),
                                translate_body=kw.get("translate_body", False)),
        deliver=config.DeliverCfg(form=kw.get("form", "card"),
                                  priority=kw.get("priority", "normal"),
                                  quiet_hours=kw.get("quiet_hours", ""),
                                  max_cards_per_day=kw.get("max_cards_per_day", 12),
                                  min_gap_min=kw.get("min_gap_min", 0.0)),
        card={"title": kw.get("title", "测试卡")},
        extra=dict(kw.get("extra", {})),
    )


def _item(n: int, **kw) -> dict:
    return {"ext_id": kw.get("ext_id", f"e{n}"), "title": kw.get("title", f"Title {n}"),
            "url": kw.get("url", f"https://example.com/{n}"), "original_url": "",
            "source": kw.get("source", "Test"), "summary": kw.get("summary", ""),
            "category": "", "score": kw.get("score", 100 - n), "pub_ts": kw.get("pub_ts", 0)}


def _items(n: int, **kw) -> list[dict]:
    return [_item(i, **kw) for i in range(1, n + 1)]


def _write_registry(tmp: Path, sources: dict, *, chat: str = "oc_test_chat",
                    slots: dict | None = None) -> Path:
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "sources.yaml").write_text(yaml.safe_dump(
        {"chat": chat, "slots": slots or {"am": "08:15", "noon": "12:35", "pm": "18:35"},
         "sources": sources}, allow_unicode=True), encoding="utf-8")
    (tmp / "models.yaml").write_text(yaml.safe_dump(
        {"default": "host", "capabilities": {"summarize": {"model": "host"}},
         "budget": {"max_calls_per_day": 100, "max_calls_per_hour": 20}}, allow_unicode=True),
        encoding="utf-8")
    return tmp


class ConfigTests(unittest.TestCase):
    def test_four_axes_expand(self) -> None:
        with TemporaryDirectory() as tmp:
            news = _write_registry(Path(tmp), {"s1": {
                "adapter": "rsshub", "tier": "T1", "feeds": ["/twitter/user/x"],
                "fetch": {"trigger": "poll", "interval_min": 30},
                "filter": {"min_items": 2, "max_items": 4, "include_keywords": ["ai"]},
                "enrich": {"summary": "post", "translate_body": True},
                "deliver": {"form": "append_card", "priority": "high",
                            "quiet_hours": "23:30-07:30",
                            "budget": {"max_cards_per_day": 5, "min_gap_min": 10}}}})
            cfg = config.load(news)
            s = cfg.sources["s1"]
            self.assertEqual(s.fetch.trigger, "poll")
            self.assertEqual(s.fetch.interval_min, 30)
            self.assertEqual(s.filter.max_items, 4)
            self.assertEqual(s.filter.include_keywords, ("ai",))
            self.assertEqual(s.enrich.summary, "post")
            self.assertTrue(s.enrich.translate_body)
            self.assertEqual(s.deliver.form, "append_card")
            self.assertEqual(s.deliver.priority, "high")
            self.assertEqual(s.deliver.max_cards_per_day, 5)
            self.assertEqual(s.deliver.min_gap_min, 10)
            # extra 原样透传给适配器
            self.assertEqual(s.adapter_config()["feeds"], ["/twitter/user/x"])
            self.assertTrue(s.sends_card)

    def test_yaml_off_is_not_boolean(self) -> None:
        """YAML 1.1 把裸 `off` 解析成 False —— 必须归一化成字符串 "off"。"""
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "sources.yaml"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "chat: oc_x\nslots:\n  am: '08:15'\nsources:\n  s:\n    adapter: aihot\n"
                "    fetch: {trigger: slot, slots: [am]}\n    enrich:\n      summary: off\n",
                encoding="utf-8")
            (Path(tmp) / "models.yaml").write_text("default: host\n", encoding="utf-8")
            cfg = config.load(Path(tmp))
            self.assertEqual(cfg.sources["s"].enrich.summary, "off")

    def test_unknown_adapter_and_slot_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            news = _write_registry(Path(tmp), {"s": {"adapter": "nope",
                                                     "fetch": {"trigger": "slot", "slots": ["am"]}}})
            with self.assertRaises(ConfigError):
                config.load(news)
        with TemporaryDirectory() as tmp:
            news = _write_registry(Path(tmp), {"s": {"adapter": "aihot",
                                                     "fetch": {"trigger": "slot", "slots": ["nope"]}}})
            with self.assertRaises(ConfigError):
                config.load(news)

    def test_bad_enum_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            news = _write_registry(Path(tmp), {"s": {
                "adapter": "aihot", "fetch": {"trigger": "slot", "slots": ["am"]},
                "deliver": {"form": "carrier_pigeon"}}})
            with self.assertRaises(ConfigError):
                config.load(news)

    def test_chat_must_be_chat_id(self) -> None:
        with TemporaryDirectory() as tmp:
            news = _write_registry(Path(tmp), {"s": {"adapter": "aihot",
                                                     "fetch": {"trigger": "slot", "slots": ["am"]}}},
                                   chat="not-a-chat")
            with self.assertRaises(ConfigError):
                config.load(news)

    def test_slot_and_poll_selection(self) -> None:
        with TemporaryDirectory() as tmp:
            news = _write_registry(Path(tmp), {
                "a": {"adapter": "aihot", "fetch": {"trigger": "slot", "slots": ["am", "pm"]}},
                "b": {"adapter": "rsshub", "fetch": {"trigger": "poll", "interval_min": 30}},
                "c": {"adapter": "aihot", "enabled": False,
                      "fetch": {"trigger": "slot", "slots": ["am"]}}})
            cfg = config.load(news)
            self.assertEqual([s.name for s in cfg.slot_sources("am")], ["a"])
            self.assertEqual([s.name for s in cfg.slot_sources("noon")], [])
            self.assertEqual([s.name for s in cfg.poll_sources()], ["b"])

    def test_live_registry_is_valid(self) -> None:
        """真实配置必须能加载，且发卡源的条数上限不超过元素预算。

        配置目录来源：环境变量 `NEWSPIPE_LIVE_CONFIG`（部署时指向真实配置目录）
        → 否则用随包示例 `examples/news/`。这样这条断言在独立仓库里依然有效，
        不会因为找不到宿主目录而红。
        """
        live = os.environ.get("NEWSPIPE_LIVE_CONFIG")
        news_dir = Path(live) if live else (REPO_ROOT / "examples" / "news")
        cfg = config.load(news_dir)
        self.assertTrue(cfg.chat.startswith("oc_"))
        self.assertTrue(cfg.sources, "配置里至少要有一个信源")
        for src in cfg.sources.values():
            if src.sends_card:
                self.assertLessEqual(src.filter.max_items, config.CARD_MAX_ITEMS,
                                     f"{src.name}: max_items 超过卡片元素预算")


class DeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.store = state.Store(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_quiet_hours_window(self) -> None:
        self.assertTrue(delivery.in_quiet_hours("23:30-07:30", datetime(2026, 10, 3, 23, 45)))
        self.assertTrue(delivery.in_quiet_hours("23:30-07:30", datetime(2026, 10, 3, 3, 0)))
        self.assertFalse(delivery.in_quiet_hours("23:30-07:30", datetime(2026, 10, 3, 12, 0)))
        self.assertTrue(delivery.in_quiet_hours("12:00-14:00", datetime(2026, 10, 3, 12, 30)))
        self.assertFalse(delivery.in_quiet_hours("", datetime(2026, 10, 3, 3, 0)))
        self.assertFalse(delivery.in_quiet_hours("垃圾", datetime(2026, 10, 3, 3, 0)))

    def test_high_priority_bypasses_quiet_and_budget(self) -> None:
        scfg = _src(priority="high", quiet_hours="23:30-07:30", max_cards_per_day=0)
        plan = delivery.plan(_items(3), scfg, self.store, digest="2026-10-03", slot="alert",
                             now=datetime(2026, 10, 3, 23, 50))
        self.assertEqual(plan.reason, "ok")
        self.assertEqual(len(plan.items), 3)

    def test_normal_priority_defers_in_quiet_hours(self) -> None:
        scfg = _src(quiet_hours="23:30-07:30")
        plan = delivery.plan(_items(3), scfg, self.store, digest="2026-10-03", slot="alert",
                             now=datetime(2026, 10, 3, 23, 50))
        self.assertEqual(plan.reason, "quiet_hours")
        self.assertTrue(plan.deferred)
        self.assertEqual(len(plan.deferred_items), 3)

    def test_budget_exceeded_defers(self) -> None:
        scfg = _src(max_cards_per_day=1)
        self.store.note_card("2026-10-03")
        plan = delivery.plan(_items(3), scfg, self.store, digest="2026-10-03", slot="alert",
                             now=NOW)
        self.assertEqual(plan.reason, "budget_exceeded")

    def test_min_gap_defers(self) -> None:
        scfg = _src(min_gap_min=30)
        self.store.note_card("2026-10-03", at=NOW)
        plan = delivery.plan(_items(3), scfg, self.store, digest="2026-10-03", slot="alert",
                             now=NOW + timedelta(minutes=5))
        self.assertEqual(plan.reason, "min_gap")
        self.assertEqual(len(plan.deferred_items), 3)

    def test_below_min_items_does_not_queue(self) -> None:
        scfg = _src(min_items=3)
        plan = delivery.plan(_items(2), scfg, self.store, digest="2026-10-03", slot="am", now=NOW)
        self.assertEqual(plan.reason, "below_min_items")
        self.assertEqual(plan.queued, [])   # 留在原地，下轮重取自然累积

    def test_cap_and_overflow(self) -> None:
        scfg = _src(max_items=2)
        plan = delivery.plan(_items(5), scfg, self.store, digest="2026-10-03", slot="am", now=NOW)
        self.assertEqual(plan.reason, "ok")
        self.assertEqual(len(plan.items), 2)
        self.assertEqual(plan.overflow, 3)
        self.assertEqual(len(plan.overflow_items), 3)
        self.assertEqual([i["ext_id"] for i in plan.overflow_items], ["e3", "e4", "e5"])

    def test_card_cap_clamped_but_state_only_not(self) -> None:
        self.assertEqual(delivery.card_cap(_src(max_items=99)), config.CARD_MAX_ITEMS)
        self.assertEqual(delivery.card_cap(_src(max_items=99, form="state_only")), 99)

    def test_empty_is_nothing_new(self) -> None:
        plan = delivery.plan([], _src(), self.store, digest="2026-10-03", slot="am", now=NOW)
        self.assertEqual(plan.reason, "nothing_new")
        self.assertFalse(plan.will_deliver)


class StateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.news = Path(self._tmp.name)
        self.store = state.Store(self.news)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_batch_roundtrip(self) -> None:
        batch = {"digest": "2026-10-03", "source": "s", "slot": "am", "items": [_item(1)],
                 "card_id": "c1", "seq": 1, "view": "list"}
        path = self.store.write_batch(batch)
        self.assertEqual(batch["batch"], "state/batches/2026-10-03/s-am.json")
        again = self.store.read_batch("2026-10-03", "s", "am")
        self.assertIsNotNone(again)
        self.assertEqual(again["card_id"], "c1")
        found = self.store.load_batch_by_rel("state/batches/2026-10-03/s-am.json")
        self.assertEqual(found[0].resolve(), path.resolve())   # /var vs /private/var 软链

    def test_load_batch_by_rel_rejects_traversal(self) -> None:
        self.assertIsNone(self.store.load_batch_by_rel("../../../etc/passwd"))
        self.assertIsNone(self.store.load_batch_by_rel(""))

    def test_pushed_and_cursor(self) -> None:
        self.store.record_pushed("s", _items(2), "2026-10-03")
        self.assertEqual(self.store.pushed_ids("s"), {"e1", "e2"})
        self.assertIsNone(self.store.cursor("s"))
        self.store.set_cursor("s", 1000.0, at=NOW)
        self.assertEqual(self.store.cursor("s"), 1000.0)
        age = self.store.cursor_age_min("s", NOW + timedelta(minutes=3))
        self.assertAlmostEqual(age, 3.0, places=1)
        # naive 调用方（pipeline 用 datetime.now()）不能因为时区相减失败而静默返回 None
        self.assertAlmostEqual(self.store.cursor_age_min("s", NOW.replace(tzinfo=None)
                                                        + timedelta(minutes=4)), 4.0, places=1)

    def test_pending_queue_order_and_take(self) -> None:
        self.store.append_pending("s", _items(3))
        self.assertEqual(self.store.pending_count("s"), 3)
        head = self.store.take_pending("s", 2)
        self.assertEqual([i["ext_id"] for i in head], ["e1", "e2"])
        self.assertEqual(self.store.pending_count("s"), 1)

    def test_budget_note_card(self) -> None:
        self.assertEqual(self.store.budget_state("2026-10-03")["cards"], 0)
        self.store.note_card("2026-10-03")
        st = self.store.budget_state("2026-10-03")
        self.assertEqual(st["cards"], 1)
        self.assertTrue(st["last_card_ts"])

    def test_receipt_and_usage(self) -> None:
        key = self.store.receipt_key("v1", "m", "payload")
        self.assertIsNone(self.store.receipt(key))
        self.store.save_receipt(key, {"data": {"title_zh": "x"}})
        self.assertEqual(self.store.receipt(key)["data"]["title_zh"], "x")
        self.store.add_usage("2026-10-03", calls=2, chars=10)
        usage = self.store.usage("2026-10-03")
        self.assertEqual(usage["calls"], 2)

    def test_migrate_legacy(self) -> None:
        (self.news / "pushed-ids").mkdir(parents=True)
        (self.news / "pushed-ids" / "s.jsonl").write_text('{"ext_id": "old1"}\n', encoding="utf-8")
        (self.news / "cursors").mkdir()
        (self.news / "cursors" / "s.json").write_text('{"last_ts": 123.0}', encoding="utf-8")
        result = self.store.migrate_legacy()
        self.assertGreaterEqual(result.get("pushed", 0), 1)
        self.assertEqual(self.store.pushed_ids("s"), {"old1"})
        self.assertEqual(self.store.cursor("s"), 123.0)


class RenderTests(unittest.TestCase):
    def _batch(self, n: int, *, view="list") -> dict:
        items = _items(n)
        for i, it in enumerate(items, 1):
            it["id"] = f"n{i:02d}"
            it["status"] = "unread"
        return {"digest": "2026-10-03", "slot": "am", "source": "s", "title": "测试卡",
                "batch": "state/batches/2026-10-03/s-am.json", "items": items, "view": view,
                "card_id": "c1", "seq": 1}

    def test_element_budget_matches_card_max_items(self) -> None:
        """行结构变了必须重算：CARD_MAX_ITEMS 条刚好在 200 元素内，再多一条就超。"""
        card = render.list_card(self._batch(config.CARD_MAX_ITEMS))
        n = render.count_elements(card)
        self.assertLessEqual(n, render.CARD_ELEMENT_LIMIT)
        over = render.count_elements(render.list_card(self._batch(config.CARD_MAX_ITEMS + 1)))
        self.assertGreater(over, render.CARD_ELEMENT_LIMIT)
        self.assertLess(len(render.card_json(card).encode()), 30000)

    def test_assert_within_limit_raises_with_numbers(self) -> None:
        card = render.list_card(self._batch(config.CARD_MAX_ITEMS + 1))
        with self.assertRaises(ValueError) as ctx:
            render.assert_within_limit(card, what="test")
        self.assertIn("11310", str(ctx.exception))

    def test_row_actions(self) -> None:
        card = render.list_card(self._batch(1))
        row = card["body"]["elements"][0]
        self.assertEqual(row["tag"], "interactive_container")
        self.assertEqual(row["behaviors"][0]["value"]["news_action"], "open_detail")
        self.assertEqual(row["behaviors"][0]["value"]["domain"], "news")
        # 行内按钮：🔗 是 open_url（不占回调），⭐/🚫 是 callback
        buttons = row["elements"][0]["columns"][1]["elements"]
        self.assertEqual(buttons[0]["behaviors"][0]["type"], "open_url")
        self.assertEqual(buttons[1]["behaviors"][0]["value"]["news_action"], "wiki")
        self.assertEqual(buttons[2]["behaviors"][0]["value"]["news_action"], "dismiss")

    def test_detail_card_has_back_and_four_buttons(self) -> None:
        batch = self._batch(3, view={"item": "n02"})
        card = render.render(batch)
        self.assertEqual(card["header"]["title"]["content"], "📰 条目详情")
        flat = json.dumps(card, ensure_ascii=False)
        for label in ("查看原文", "入库", "不感兴趣", "返回列表"):
            self.assertIn(label, flat)
        self.assertEqual(render.count_elements(card), 23)

    def test_render_falls_back_to_list_for_unknown_item(self) -> None:
        card = render.render(self._batch(2, view={"item": "n99"}))
        self.assertEqual(card["header"]["title"]["content"], "📰 2026-10-03 测试卡 · 早间")

    def test_display_prefers_chinese(self) -> None:
        it = _item(1)
        it.update({"title": "English title", "title_zh": "中文标题",
                   "summary": "english", "summary_zh": "中文摘要"})
        self.assertEqual(render.display_title(it), "中文标题")
        self.assertEqual(render.display_summary(it), "中文摘要")


class PipelineTests(unittest.TestCase):
    """端到端（离线）：适配器与飞书通道都被替换。"""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.news = _write_registry(Path(self._tmp.name), {"s": {
            "adapter": "aihot", "fetch": {"trigger": "slot", "slots": ["am"]},
            "filter": {"min_items": 1, "max_items": 5},
            "deliver": {"form": "card", "budget": {"max_cards_per_day": 12}}}})
        self.cfg = config.load(self.news)
        self.store = state.Store(self.news)
        self.items = _items(3)
        self.sent: list[dict] = []
        self.updated: list[int] = []
        self._patches = [
            patch.object(pipeline, "_adapter", lambda name: _FakeAdapter(self.items)),
            patch.object(channel, "create_entity", lambda card: "card_1"),
            patch.object(channel, "send_card", lambda chat, cid: (self.sent.append(
                {"chat": chat, "card_id": cid}) or "om_1")),
            patch.object(channel, "update_entity", lambda cid, seq, card: (
                self.updated.append(seq) or True)),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()

    def test_run_sends_card_records_state_and_pushes(self) -> None:
        runs = pipeline.run_slot(self.cfg, self.store, "am", now=NOW)
        self.assertEqual([r.status for r in runs], ["ok"])
        self.assertEqual(runs[0].card, "sent")
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0]["chat"], "oc_test_chat")
        self.assertEqual(self.store.pushed_ids("s"), {"e1", "e2", "e3"})
        batch = self.store.read_batch("2026-10-03", "s", "am")
        self.assertEqual(batch["card_id"], "card_1")
        self.assertEqual(batch["message_id"], "om_1")
        self.assertEqual(batch["seq"], 1)
        self.assertEqual([i["id"] for i in batch["items"]], ["n01", "n02", "n03"])
        self.assertEqual(self.store.budget_state("2026-10-03")["cards"], 1)

    def test_second_run_is_idempotent(self) -> None:
        pipeline.run_slot(self.cfg, self.store, "am", now=NOW)
        runs = pipeline.run_slot(self.cfg, self.store, "am", now=NOW)
        self.assertEqual(runs[0].status, "nothing_new")
        self.assertEqual(len(self.sent), 1)

    def test_full_batch_defers_excess_instead_of_blocking(self) -> None:
        """同 (source, slot) 重跑且批次已满：新条目顺延，不把整批撑到元素上限之外。

        回归：超限分支曾把**已投递**的条目也排进 pending，下轮它们又被取回、把批次撑得更大
        → 再超限再排队，一个自我放大的死循环（aihot 2026-10-03 实测 17 条 / 243 元素 > 200，
        卡再也发不出去，且 pending 越滚越大）。
        """
        self.items[:] = _items(5)
        pipeline.run_slot(self.cfg, self.store, "am", now=NOW)
        self.assertEqual(len(self.sent), 1)
        self.items[:] = _items(7)  # 同一批又来了两条新的
        runs = pipeline.run_slot(self.cfg, self.store, "am", now=NOW)
        self.assertEqual(runs[0].status, "ok")
        self.assertEqual(runs[0].card, "updated")  # 原地更新，不新建卡
        self.assertEqual(len(self.sent), 1)
        batch = self.store.read_batch("2026-10-03", "s", "am")
        self.assertEqual(len(batch["items"]), 5)  # 卡上仍是已投递的 5 条
        queued = {json.loads(ln)["ext_id"] for ln in
                  (self.news / "state" / "pending" / "s.jsonl").read_text(
                      encoding="utf-8").splitlines() if ln.strip()}
        self.assertEqual(queued, {"e6", "e7"})  # 只有没投递过的两条顺延
        self.assertEqual(runs[0].queued, 2)
        self.assertEqual(self.store.pushed_ids("s"), {"e1", "e2", "e3", "e4", "e5"})
        self.assertFalse(queued & self.store.pushed_ids("s"))

    def test_queue_never_requeues_delivered_items(self) -> None:
        self.store.record_pushed("s", _items(2), "2026-10-03")
        self.assertEqual(pipeline._queue(self.store, "s", _items(4)), 2)
        queued = {json.loads(ln)["ext_id"] for ln in
                  (self.news / "state" / "pending" / "s.jsonl").read_text(
                      encoding="utf-8").splitlines() if ln.strip()}
        self.assertEqual(queued, {"e3", "e4"})

    def test_dry_run_has_no_side_effects(self) -> None:
        runs = pipeline.run_slot(self.cfg, self.store, "am", dry=True, now=NOW)
        self.assertEqual(runs[0].status, "dry")
        self.assertEqual(runs[0].planned, 3)
        self.assertEqual(self.sent, [])
        self.assertFalse((self.news / "state").exists())

    def test_send_failure_does_not_push_and_queues(self) -> None:
        with patch.object(channel, "create_entity", side_effect=RuntimeError("boom")):
            runs = pipeline.run_slot(self.cfg, self.store, "am", now=NOW)
        self.assertEqual(runs[0].status, "send_failed")
        self.assertEqual(self.store.pushed_ids("s"), set())     # 未投递 ⇒ 不记 pushed
        self.assertEqual(self.store.pending_count("s"), 3)      # 条目进顺延队列，不丢
        self.assertIsNone(self.store.read_batch("2026-10-03", "s", "am"))

    def test_overflow_goes_to_pending_and_full_batch_defers_excess(self) -> None:
        """8 条 / max_items 5 ⇒ 发 5 条、顺延 3 条；重跑时卡上仍是 5 条，多出来的继续顺延。

        旧行为是"重跑时把顺延的 3 条追加进同一张卡"（卡变成 8 条），一旦 `max_items` 贴着
        元素上限（13），追加就会把卡撑到 200 元素之外、整张卡被飞书拒收且再也发不出去。
        顺延队列本来就是交给**下一批**（下一个槽位/次日）的交接件，不是"同一张卡的第二页"。
        """
        self.items = _items(8)
        runs = pipeline.run_slot(self.cfg, self.store, "am", now=NOW)
        self.assertEqual(runs[0].planned, 5)
        self.assertEqual(self.store.pending_count("s"), 3)
        runs = pipeline.run_slot(self.cfg, self.store, "am", now=NOW)
        self.assertEqual(runs[0].card, "updated")
        self.assertEqual(len(self.sent), 1)                      # 不新建卡堆叠
        self.assertEqual(self.updated, [2])
        batch = self.store.read_batch("2026-10-03", "s", "am")
        self.assertEqual(len(batch["items"]), 5)                 # 卡没被撑大
        self.assertEqual(self.store.pending_count("s"), 3)       # 3 条仍等下一批，且不重复入队
        self.assertEqual(self.store.pushed_ids("s"), {"e1", "e2", "e3", "e4", "e5"})

    def test_state_only_source_ignores_card_element_budget(self) -> None:
        """不发卡的源不该被 200 元素预算卡住——它根本不渲染卡片。

        回归：render/assert 曾在 state_only 分支之前执行，20 条 = 285 元素被判 overflow，
        批次永远写不出来 ⇒ 下游 agent job（痛点提炼）天天读到空原料，而心跳显示"故障"，
        看起来像采集坏了。
        """
        self.news2 = _write_registry(Path(self._tmp.name) / "b", {"s": {
            "adapter": "aihot", "fetch": {"trigger": "slot", "slots": ["am"]},
            "filter": {"max_items": 40}, "deliver": {"form": "state_only"}}})
        cfg = config.load(self.news2)
        store = state.Store(self.news2)
        self.items = _items(20)
        runs = pipeline.run_slot(cfg, store, "am", now=NOW)
        self.assertEqual(runs[0].status, "state_only")
        self.assertEqual(runs[0].card, "none")
        batch = store.read_batch("2026-10-03", "s", "am")
        self.assertEqual(len(batch["items"]), 20)
        self.assertEqual(store.pending_count("s"), 0)
        self.assertEqual(len(store.pushed_ids("s")), 20)

    def test_budget_exceeded_defers_to_pending(self) -> None:
        for _ in range(12):                  # 当日已发满（max_cards_per_day=12）
            self.store.note_card("2026-10-03", at=NOW)
        runs = pipeline.run_slot(self.cfg, self.store, "am", now=NOW)
        self.assertEqual(runs[0].status, "budget_exceeded")
        self.assertEqual(self.sent, [])
        self.assertEqual(self.store.pending_count("s"), 3)

    def test_state_only_writes_batch_without_card(self) -> None:
        self.news2 = _write_registry(Path(self._tmp.name) / "b", {"s": {
            "adapter": "aihot", "fetch": {"trigger": "slot", "slots": ["am"]},
            "filter": {"max_items": 40}, "deliver": {"form": "state_only"}}})
        cfg = config.load(self.news2)
        store = state.Store(self.news2)
        runs = pipeline.run_slot(cfg, store, "am", now=NOW)
        self.assertEqual(runs[0].status, "state_only")
        self.assertEqual(self.sent, [])
        batch = store.read_batch("2026-10-03", "s", "am")
        self.assertEqual(len(batch["items"]), 3)
        self.assertEqual(store.pushed_ids("s"), {"e1", "e2", "e3"})

    def test_append_card_rolls_window(self) -> None:
        self.news3 = _write_registry(Path(self._tmp.name) / "c", {"s": {
            "adapter": "aihot", "fetch": {"trigger": "poll", "interval_min": 30},
            "filter": {"max_items": 3}, "deliver": {"form": "append_card"}}})
        cfg = config.load(self.news3)
        store = state.Store(self.news3)
        store.set_cursor("s", NOW.timestamp() - 3600)
        pipeline.run_poll(cfg, store, force=True, now=NOW)
        self.items = _items(4)
        pipeline.run_poll(cfg, store, force=True, now=NOW + timedelta(hours=1))
        batch = store.read_batch("2026-10-03", "s", "alert")
        self.assertEqual(len(batch["items"]), 3)          # 卡上只留最新 3 条
        self.assertEqual(len(batch["archived"]), 1)       # 更早的进 archived，state 不丢
        self.assertEqual(batch["items"][-1]["ext_id"], "e4")

    def test_poll_baseline_then_throttle(self) -> None:
        news = _write_registry(Path(self._tmp.name) / "d", {"s": {
            "adapter": "aihot", "fetch": {"trigger": "poll", "interval_min": 30},
            "deliver": {"form": "append_card"}}})
        cfg = config.load(news)
        store = state.Store(news)
        first = pipeline.run_poll(cfg, store, now=NOW)
        self.assertEqual(first[0].status, "baseline")
        self.assertEqual(self.sent, [])                    # 首轮不发卡
        second = pipeline.run_poll(cfg, store, now=NOW + timedelta(minutes=31))
        self.assertEqual(second[0].status, "ok")
        self.assertEqual(len(self.sent), 1)
        third = pipeline.run_poll(cfg, store, now=NOW + timedelta(minutes=32))
        self.assertEqual(third[0].status, "throttled")
        self.assertEqual(len(self.sent), 1)

    def test_poll_quiet_hours_does_not_fetch(self) -> None:
        news = _write_registry(Path(self._tmp.name) / "e", {"s": {
            "adapter": "aihot", "fetch": {"trigger": "poll", "interval_min": 30},
            "deliver": {"form": "append_card", "quiet_hours": "23:30-07:30"}}})
        cfg = config.load(news)
        store = state.Store(news)
        runs = pipeline.run_poll(cfg, store, now=datetime(2026, 10, 3, 23, 45))
        self.assertEqual(runs[0].status, "quiet_hours")
        self.assertEqual(runs[0].fetched, 0)
        self.assertIsNone(store.cursor("s"))

    def test_fetch_error_records_heartbeat_and_keeps_cursor(self) -> None:
        with patch.object(pipeline, "_adapter",
                          lambda name: _FakeAdapter(raise_exc=RuntimeError("502"))):
            runs = pipeline.run_slot(self.cfg, self.store, "am", now=NOW)
        self.assertEqual(runs[0].status, "error")
        self.assertIn("502", runs[0].note)
        self.assertEqual(self.store.status("s")["status"], "error")
        self.assertEqual(self.sent, [])

    def test_config_missing_is_not_an_error(self) -> None:
        with patch.object(pipeline, "_adapter",
                          lambda name: _FakeAdapter(raise_exc=ConfigError("缺 RSSHub 凭据"))):
            runs = pipeline.run_slot(self.cfg, self.store, "am", now=NOW)
        self.assertEqual(runs[0].status, "config_missing")
        self.assertEqual(self.store.status("s")["status"], "config_missing")

    def test_stats_rows_cover_every_source(self) -> None:
        rows = pipeline.stats(self.store, self.cfg)
        self.assertEqual([r["source"] for r in rows], ["s"])
        self.assertEqual(rows[0]["status"], "—")


class _FakeAdapter:
    def __init__(self, items=None, raise_exc=None):
        self.items = items or []
        self.raise_exc = raise_exc

    def fetch(self, cfg, since=None):
        if self.raise_exc:
            raise self.raise_exc
        return list(self.items)


class EnrichTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.store = state.Store(Path(self._tmp.name))
        self.budget = {"max_calls_per_day": 100, "max_calls_per_hour": 20, "timeout_seconds": 5}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_is_chinese(self) -> None:
        self.assertTrue(enrich.is_chinese("这是一个中文标题"))
        self.assertFalse(enrich.is_chinese("An English title here"))
        self.assertFalse(enrich.is_chinese(""))

    def test_chunks_are_stable(self) -> None:
        text = "第一句。" * 300
        blocks = enrich._chunks(text, size=100)
        self.assertGreater(len(blocks), 1)
        self.assertEqual("".join(blocks).replace("\n\n", ""), text)

    def test_degraded_keeps_failure_note_on_item(self) -> None:
        """降级必须把原因留在条目上——只有计数的话没人知道是模型/网络/预算的问题。"""
        fail = llm.ChatResult(None, "failed", "空响应（finish_reason='length'，思考吃满 1024 tokens）")
        with patch.object(llm, "chat_json", return_value=fail):
            out = enrich.enrich([_item(1, title="English", summary="body text")],
                                _src(summary="title"), self.store, {}, self.budget, now=NOW)
        self.assertEqual(out[0]["enrich_state"], "degraded")
        self.assertIn("空响应", out[0]["enrich_note"])

    def test_retry_clears_previous_failure_note(self) -> None:
        item = _item(1, title="English", summary="body text")
        item["enrich_state"] = "degraded"
        item["enrich_note"] = "空响应（旧的失败原因）"
        ok = llm.ChatResult({"title_zh": "中文标题", "summary_zh": "中文摘要"}, "ok")
        with patch.object(llm, "chat_json", return_value=ok):
            out = enrich.enrich([item], _src(summary="title"), self.store, {}, self.budget, now=NOW)
        self.assertEqual(out[0]["enrich_state"], "ok")
        self.assertNotIn("enrich_note", out[0])   # 补加工成功后不留旧原因

    def test_enrich_new_skips_degraded_unless_retry(self) -> None:
        """默认不重试失败过的条目；`retry_degraded=True` 才重来（修完模型/预算后用）。"""
        cfg = SimpleNamespace(models={}, budget=lambda: dict(self.budget))
        items = [_item(1, title="English", summary="body text")]
        items[0]["enrich_state"] = "degraded"
        with patch.object(llm, "chat_json") as call:
            pipeline._enrich_new(cfg, self.store, _src(summary="title"), items, NOW)
            call.assert_not_called()
        ok = llm.ChatResult({"title_zh": "中文标题"}, "ok")
        with patch.object(llm, "chat_json", return_value=ok):
            out, counts = pipeline._enrich_new(cfg, self.store, _src(summary="title"), items, NOW,
                                               retry_degraded=True)
        self.assertEqual(out[0]["enrich_state"], "ok")
        self.assertEqual(counts.get("ok"), 1)

    def test_off_mode_never_calls_model(self) -> None:
        with patch.object(llm, "chat_json") as call:
            out = enrich.enrich(_items(2), _src(summary="off"), self.store, {}, self.budget,
                                now=NOW)
        call.assert_not_called()
        self.assertEqual([i["enrich_state"] for i in out], ["off", "off"])

    def test_native_chinese_skips_model(self) -> None:
        items = [_item(1, title="中文标题就在这里", summary="这是中文正文内容。")]
        with patch.object(llm, "chat_json") as call:
            out = enrich.enrich(items, _src(summary="article"), self.store, {}, self.budget, now=NOW)
        call.assert_not_called()
        self.assertEqual(out[0]["enrich_state"], "native")

    def test_summary_fills_chinese_fields(self) -> None:
        result = llm.ChatResult({"title_zh": "中文标题", "summary_zh": "中文摘要", "judgment": "值得看"},
                                "ok")
        with patch.object(llm, "chat_json", return_value=result):
            out = enrich.enrich([_item(1, title="English", summary="body text")],
                                _src(summary="title"), self.store, {}, self.budget, now=NOW)
        self.assertEqual(out[0]["title_zh"], "中文标题")
        self.assertEqual(out[0]["summary_zh"], "中文摘要")
        self.assertEqual(out[0]["enrich_state"], "ok")

    def test_degrade_keeps_original_on_model_failure(self) -> None:
        with patch.object(llm, "chat_json", return_value=llm.ChatResult(None, "failed", "timeout")):
            out = enrich.enrich([_item(1, title="English", summary="body")],
                                _src(summary="title"), self.store, {}, self.budget, now=NOW)
        self.assertEqual(out[0]["enrich_state"], "degraded")
        self.assertNotIn("title_zh", out[0])           # 回退原文标题，不编造
        self.assertEqual(render.display_title(out[0]), "English")

    def test_translate_block_mismatch_keeps_original(self) -> None:
        bad = llm.ChatResult({"t": ["只译了一块"]}, "ok")
        body = "第一句。" * 200          # 800 字 ⇒ 切成多块，译文块数对不上
        with patch.object(llm, "chat_json", return_value=bad):
            translated, st = enrich.translate_body({"ext_id": "e1"}, _src(summary="post"),
                                                   self.store, {}, self.budget, body, NOW)
        self.assertEqual(translated, "")
        self.assertEqual(st, "incomplete")

    def test_prompt_partials_expand_and_hash(self) -> None:
        text = __import__("newspipe.prompts", fromlist=["text"]).text("summarize-article")
        self.assertIn("防幻觉", text)
        self.assertNotIn("{{>", text)
        self.assertNotEqual(
            __import__("newspipe.prompts", fromlist=["version"]).version("summarize-article"),
            __import__("newspipe.prompts", fromlist=["version"]).version("summarize-title"))


class LlmTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.home = Path(self._tmp.name)
        (self.home / "config.yaml").write_text(yaml.safe_dump({
            "model": {"default": "cloud-model", "provider": "local_gateway",
                      "base_url": "http://localhost:7863/v1", "api_key": "STALE"},
            "providers": {"local_gateway": {"key_env": "LOCAL_KEY"}}}), encoding="utf-8")
        (self.home / ".env").write_text("LOCAL_KEY=fresh-key\n", encoding="utf-8")
        self.env = self.home / ".env"
        self.cfg = self.home / "config.yaml"
        # 干净机器上没有宿主环境变量；把宿主目录钉到本用例的临时目录（patch 自动还原），
        # host 后端的用例不赌开发机的 shell / 钥匙串。
        host_patcher = patch.dict(os.environ, {"NEWSPIPE_HOST_HOME": str(self.home)})
        host_patcher.start()
        self.addCleanup(host_patcher.stop)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_host_follows_current_model_and_prefers_env_key(self) -> None:
        ref = llm.resolve_model("summarize", {"default": "host"},
                               host_config_path=self.cfg, env_path=self.env)
        self.assertEqual(ref.model, "cloud-model")
        self.assertEqual(ref.base_url, "http://localhost:7863/v1")
        self.assertEqual(ref.api_key, "fresh-key")     # env 优先于 config.model.api_key
        self.assertEqual(ref.describe()["key"], "有")   # describe 不含密钥

    def test_describe_never_leaks_key(self) -> None:
        ref = llm.resolve_model("summarize", {"default": "host"},
                               host_config_path=self.cfg, env_path=self.env)
        self.assertNotIn("fresh-key", json.dumps(ref.describe(), ensure_ascii=False))

    def test_missing_key_raises_config_error(self) -> None:
        (self.home / "config.yaml").write_text(yaml.safe_dump({
            "model": {"default": "m", "provider": "p", "base_url": "http://x/v1"}}),
            encoding="utf-8")
        (self.home / ".env").write_text("", encoding="utf-8")
        with self.assertRaises(ConfigError):
            llm.resolve_model("summarize", {"default": "host"},
                              host_config_path=self.cfg, env_path=self.env)

    def test_over_budget(self) -> None:
        budget = {"max_calls_per_day": 2, "max_calls_per_hour": 1}
        self.assertIsNone(llm.over_budget(budget, {"calls": 0, "recent": []}, NOW))
        self.assertEqual(llm.over_budget(budget, {"calls": 2, "recent": []}, NOW), "daily_calls")
        recent = [NOW.timestamp() - 60]
        self.assertEqual(llm.over_budget(budget, {"calls": 1, "recent": recent}, NOW), "hourly_calls")

    def test_parse_json_object(self) -> None:
        self.assertEqual(llm.parse_json_object('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(llm.parse_json_object('前言 {"a": 2} 后语'), {"a": 2})
        self.assertIsNone(llm.parse_json_object("no json here"))
        self.assertIsNone(llm.parse_json_object("[1,2]"))

    def test_budget_block_degrades_without_calling(self) -> None:
        store = state.Store(self.home / "news")
        store.add_usage(state.today(), calls=999)
        result = llm.chat_json("summarize", system="s", user="u", prompt_version="v",
                               store=store, models_cfg={"default": "host"},
                               budget={"max_calls_per_day": 1, "max_calls_per_hour": 1,
                                       "timeout_seconds": 1})
        self.assertFalse(result.ok)
        self.assertEqual(result.state, "degraded")

    def _fake_urlopen(self, payloads: list[dict]):
        """按顺序返回预设响应，并记录每次请求体（模拟 bridge）。"""
        self.requested: list[dict] = []

        class _Resp:
            def __init__(self, data: dict) -> None:
                self._data = data

            def read(self) -> bytes:
                return json.dumps(self._data).encode()

            def __enter__(self) -> "_Resp":
                return self

            def __exit__(self, *a: object) -> bool:
                return False

        def _open(req, timeout=None):  # noqa: ANN001, ARG001
            self.requested.append(json.loads(req.data.decode()))
            idx = min(len(self.requested) - 1, len(payloads) - 1)
            return _Resp(payloads[idx])

        return _open

    def test_reasoning_model_empty_content_retries_with_bigger_budget(self) -> None:
        """推理模型把 completion 预算全花在思考上 ⇒ content 空 + finish_reason=length。

        必须翻倍重试一次（reasoning 与答案共用同一预算），否则整批静默降级为原文标题——
        2026-10-03 实测 12 次调用 10 次降级，原因只是 `响应不是 JSON：''`。
        """
        store = state.Store(self.home / "news")
        empty = {"choices": [{"finish_reason": "length",
                              "message": {"content": "", "reasoning_content": "思考" * 100}}]}
        good = {"choices": [{"finish_reason": "stop",
                             "message": {"content": '{"title_zh": "标题", "summary_zh": "摘要"}'}}]}
        opener = self._fake_urlopen([empty, good])
        with patch.object(llm.urllib.request, "urlopen", opener):
            result = llm.chat_json("summarize", system="s", user="u", prompt_version="v1",
                                   store=store, models_cfg={"default": "host"},
                                   budget={"max_calls_per_day": 10, "max_calls_per_hour": 10,
                                           "max_tokens": 1024, "max_tokens_ceiling": 8192,
                                           "timeout_seconds": 5}, now=NOW)
        self.assertTrue(result.ok)
        self.assertEqual(result.data, {"title_zh": "标题", "summary_zh": "摘要"})
        self.assertEqual([r["max_tokens"] for r in self.requested], [1024, 2048])  # 翻倍重试
        self.assertEqual(store.usage(state.today())["calls"], 2)

    def test_persistent_empty_content_records_reason_code(self) -> None:
        """反复空响应 ⇒ 降级且**记下原因短码**，不再是一个没有线索的 degraded 计数。"""
        store = state.Store(self.home / "news")
        empty = {"choices": [{"finish_reason": "length", "message": {"content": ""}}]}
        opener = self._fake_urlopen([empty])
        with patch.object(llm.urllib.request, "urlopen", opener):
            result = llm.chat_json("summarize", system="s", user="u", prompt_version="v2",
                                   store=store, models_cfg={"default": "host"},
                                   budget={"max_calls_per_day": 10, "max_calls_per_hour": 10,
                                           "max_tokens": 8192, "max_tokens_ceiling": 8192,
                                           "timeout_seconds": 5}, now=NOW)
        self.assertFalse(result.ok)
        self.assertIn("空响应", result.note)
        self.assertEqual(len(self.requested), 1)  # 已到上限，不再重试
        usage = store.usage(state.today())
        self.assertEqual(usage["degraded"], 1)
        self.assertEqual(usage["reasons"], {"length_empty": 1})


class InteractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.news = Path(self._tmp.name)
        self.store = state.Store(self.news)
        items = _items(2)
        for i, it in enumerate(items, 1):
            it["id"] = f"n{i:02d}"
            it["status"] = "unread"
            it["updated_at"] = None
        self.batch = {"digest": "2026-10-03", "source": "s", "slot": "am", "title": "测试卡",
                      "items": items, "card_id": "c1", "message_id": "om1", "seq": 1,
                      "view": "list", "form": "card"}
        self.store.write_batch(self.batch)
        self.updated: list[tuple[str, int]] = []
        self._patch = patch.object(channel, "update_entity",
                                   lambda cid, seq, card: (self.updated.append((cid, seq)) or True))
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        self._tmp.cleanup()

    def _payload(self, action: str, item_id: str = "n01") -> dict:
        return {"domain": "news", "digest": "2026-10-03",
                "batch": "state/batches/2026-10-03/s-am.json", "id": item_id,
                "news_action": action}

    def _handle(self, action: str, item_id: str = "n01") -> str:
        """单测只写临时目录：收藏改记事件，不再写宿主的待办队列。"""
        return interaction.handle(self._payload(action, item_id), news_dir=self.news)

    def test_open_detail_sets_view_marks_read_and_updates_entity(self) -> None:
        msg = self._handle("open_detail")
        self.assertEqual(msg, "")
        batch = self.store.read_batch("2026-10-03", "s", "am")
        self.assertEqual(batch["view"], {"item": "n01"})
        self.assertEqual(batch["items"][0]["status"], "read")
        self.assertEqual(batch["seq"], 2)
        self.assertEqual(self.updated, [("c1", 2)])
        log = (self.news / "actions.log").read_text(encoding="utf-8")
        self.assertIn("open_detail", log)

    def test_back_to_list(self) -> None:
        self._handle("open_detail")
        self._handle("back_to_list")
        batch = self.store.read_batch("2026-10-03", "s", "am")
        self.assertEqual(batch["view"], "list")
        self.assertEqual(batch["seq"], 3)                 # sequence 严格递增

    def test_wiki_records_favorite_event_not_host_file(self) -> None:
        """⭐ 收藏 → 项目自己的事件队列（不再写宿主的队列文件）。"""
        msg = self._handle("wiki")
        batch = self.store.read_batch("2026-10-03", "s", "am")
        self.assertEqual(batch["items"][0]["status"], "wiki")
        self.assertIn("已提交入库", msg)          # 给用户明确的回执
        pending = events.queue(self.news)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["type"], "favorite")
        self.assertEqual(pending[0]["payload"]["item_id"], "n01")
        self.assertFalse((self.news.parent / "_staging").exists(), "不许写宿主目录")

    def test_dismiss_writes_preferences(self) -> None:
        self._handle("dismiss", "n02")
        batch = self.store.read_batch("2026-10-03", "s", "am")
        self.assertEqual(batch["items"][1]["status"], "dismissed")
        self.assertIn("Title 2", (self.news / "preferences.md").read_text(encoding="utf-8"))

    def test_unknown_domain_or_action_is_silent(self) -> None:
        self.assertEqual(interaction.handle({"domain": "other"}, news_dir=self.news), "")
        self.assertEqual(self._handle("nope"), "")
        self.assertEqual(interaction.handle_json("not json", news_dir=self.news), "")

    def test_missing_batch_is_silent(self) -> None:
        payload = self._payload("open_detail")
        payload["batch"] = "state/batches/2026-10-03/absent.json"
        payload["digest"] = "1999-01-01"
        self.assertEqual(interaction.handle(payload, news_dir=self.news), "")

    def test_entity_update_failure_still_records_state(self) -> None:
        with patch.object(channel, "update_entity", side_effect=RuntimeError("300317")):
            msg = self._handle("wiki")
        self.assertIn("卡片更新失败", msg)
        batch = self.store.read_batch("2026-10-03", "s", "am")
        self.assertEqual(batch["items"][0]["status"], "wiki")


class FilterTests(unittest.TestCase):
    def test_keywords_include_exclude(self) -> None:
        scfg = _src(include_keywords=("模型", "token"), exclude_keywords=("银行",))
        items = [_item(1, title="新模型发布"), _item(2, title="银行立减金"),
                 _item(3, title="抽奖活动"), _item(4, title="token 额度")]
        kept = filter_mod.apply(items, scfg)
        self.assertEqual([i["ext_id"] for i in kept], ["e1", "e4"])

    def test_rank_score_desc(self) -> None:
        scfg = _src(rank="score_desc")
        items = [_item(1, score=10), _item(2, score=99), _item(3, score=50)]
        self.assertEqual([i["ext_id"] for i in filter_mod.apply(items, scfg)], ["e2", "e3", "e1"])


class SchedulerTests(unittest.TestCase):
    def test_slot_sources_filter(self) -> None:
        cfg = _FakeConfig([_src(name="a", slots=("am",)), _src(name="b", slots=("pm",))])
        self.assertEqual([s.name for s in scheduler.slot_sources(cfg, "am")], ["a"])


class _FakeConfig:
    def __init__(self, sources):
        self._sources = sources

    def slot_sources(self, slot):
        return [s for s in self._sources if s.fetch.trigger == "slot" and slot in s.fetch.slots]


if __name__ == "__main__":
    unittest.main()
