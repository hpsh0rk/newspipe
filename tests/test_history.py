"""趋势（近 N 天）与诊断抽屉的护栏。

钉住两件事：

1. **趋势只算落盘过的东西。** 没有批次/预算/用量文件的那天就是 0，不许拿当前状态去反推历史
   （「各源历史成功率」根本没落盘 —— `state/status/` 只留最后一次心跳）。
   这个测试故意留一个空白天，就是为了让「补零」和「编数」长得不一样。
2. **诊断抽屉不吹牛。** 它给的是「最近一次计数」+「生效中的闸」，必须显式声明
   「历史拦截原因没有落盘」，否则用户会以为看到的是完整归因。
"""
from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(REPO_ROOT / "src"), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from newspipe import state, view  # noqa: E402

NOW = datetime(2026, 10, 4, 18, 0, 0)

SOURCES = """
chat: 'oc_test_chat'
slots:
  am: '08:15'
  noon: '12:35'
  pm: '18:35'
sources:
  aihot:
    adapter: aihot
    fetch:
      trigger: slot
      slots: [am]
  hn:
    adapter: hn
    fetch:
      trigger: slot
      slots: [am, pm]
    deliver:
      form: state_only
"""


def _day(back: int) -> str:
    return (NOW - timedelta(days=back)).strftime("%Y-%m-%d")


class HistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.news = Path(self._tmp.name) / "news"
        (self.news / "state").mkdir(parents=True)
        (self.news / "sources.yaml").write_text(SOURCES, encoding="utf-8")
        self.store = state.Store(self.news)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _batch(self, day: str, source: str, slot: str, statuses: list[str]) -> None:
        path = self.news / "state" / "batches" / day / f"{source}-{slot}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "source": source, "slot": slot, "title": f"{source} {slot}",
            "items": [{"ext_id": f"{source}-{i}", "status": s} for i, s in enumerate(statuses)],
            "card_id": f"card-{source}-{slot}",
        }, ensure_ascii=False), encoding="utf-8")

    def _budget(self, day: str, cards: int) -> None:
        path = self.news / "state" / "budget" / f"{day}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"cards": cards, "last_card_ts": f"{day}T08:15:00"}),
                        encoding="utf-8")

    def _usage(self, day: str, calls: int, degraded: int) -> None:
        path = self.news / "llm" / "usage" / f"{day}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"calls": calls, "chars": 100, "degraded": degraded}),
                        encoding="utf-8")

    def _pushed(self, source: str, entries: list[tuple[str, str]]) -> None:
        """写 `state/pushed/<源>.jsonl`（每行带 date）—— 台账是「产出」的另一路证据。"""
        path = self.news / "state" / "pushed" / f"{source}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(
            json.dumps({"ext_id": ext_id, "date": day, "title": f"{source} {ext_id}"},
                       ensure_ascii=False) + "\n" for day, ext_id in entries), encoding="utf-8")

    def test_window_is_oldest_first_and_ends_today(self) -> None:
        days = view.history(self.store, days=3, now=NOW)
        self.assertEqual([d["date"] for d in days], [_day(2), _day(1), _day(0)])

    def test_aggregates_only_what_is_on_disk(self) -> None:
        self._batch(_day(0), "aihot", "am", ["read", "read", "unread"])
        self._batch(_day(0), "hn", "am", ["dismissed"])
        self._budget(_day(0), 2)
        self._usage(_day(0), calls=5, degraded=1)
        self._batch(_day(2), "hn", "pm", ["read"])
        # _day(1) 故意什么都不写 —— 空白天必须是 0，不是「沿用昨天的数」

        days = view.history(self.store, days=3, now=NOW)
        today, gap, old = days[2], days[1], days[0]

        self.assertEqual((today["items"], today["marked"]), (4, 3))
        self.assertEqual(today["read_rate"], 0.75)
        self.assertEqual(today["cards"], 2)
        self.assertEqual((today["calls"], today["degraded"]), (5, 1))
        self.assertEqual(today["sources"], ["aihot", "hn"])

        self.assertEqual((gap["items"], gap["marked"], gap["cards"]), (0, 0, 0))
        self.assertIsNone(gap["read_rate"], "0 条那天不该编出一个 0% 的已标记率")
        self.assertEqual((gap["calls"], gap["degraded"]), (0, 0))
        self.assertEqual(gap["sources"], [])

        self.assertEqual((old["items"], old["marked"]), (1, 1))
        self.assertEqual(old["sources"], ["hn"])

    def test_build_carries_the_history_window(self) -> None:
        payload = view.build(self.news, now=NOW)
        self.assertEqual(len(payload["history"]), view.HISTORY_DAYS)
        self.assertEqual(payload["history"][-1]["date"], _day(0))

    def test_a_source_that_only_writes_a_pushed_ledger_still_counts_as_producing(self) -> None:
        """回归：poll + append_card 的源**不写批次文件**，只看批次会把它的产出算成「没有」。

        实测踩到：linuxdo_deals 台账 13 条（09-28/29、10-02）却 0 个批次文件，
        页面于是显示「一天都没有 —— 源可能一直没拉到东西」，是彻头彻尾的误导。
        """
        self._pushed("quiet_src", [(_day(1), "a"), (_day(1), "b"), (_day(3), "c")])
        by_date = {d["date"]: d for d in view.history(self.store, days=4, now=NOW)}

        self.assertEqual(by_date[_day(1)]["pushed"], 2)
        self.assertEqual(by_date[_day(1)]["sources"], ["quiet_src"])   # 无批次，靠台账认出来
        self.assertEqual(by_date[_day(3)]["pushed"], 1)
        self.assertEqual(by_date[_day(3)]["sources"], ["quiet_src"])
        self.assertEqual(by_date[_day(0)]["pushed"], 0)
        self.assertEqual(by_date[_day(0)]["sources"], [])
        self.assertEqual(by_date[_day(1)]["items"], 0)                # 条目仍是 0：批次确实没有

    def test_pushed_and_batch_sources_are_merged_not_replaced(self) -> None:
        self._batch(_day(0), "aihot", "am", ["read"])
        self._pushed("quiet_src", [(_day(0), "x")])
        days = view.history(self.store, days=1, now=NOW)
        self.assertEqual(days[0]["sources"], ["aihot", "quiet_src"])
        self.assertEqual(days[0]["pushed"], 1)

    def test_a_corrupt_ledger_line_does_not_kill_the_trend(self) -> None:
        self._pushed("quiet_src", [(_day(0), "ok")])
        path = self.news / "state" / "pushed" / "quiet_src.jsonl"
        path.write_text(path.read_text(encoding="utf-8") + "{坏行\n", encoding="utf-8")
        days = view.history(self.store, days=1, now=NOW)
        self.assertEqual(days[0]["pushed"], 1)

    def test_a_broken_batch_file_does_not_break_the_trend(self) -> None:
        self._batch(_day(0), "aihot", "am", ["read"])
        (self.news / "state" / "batches" / _day(0) / "broken.json").write_text(
            "{不是 json", encoding="utf-8")
        days = view.history(self.store, days=1, now=NOW)
        self.assertEqual(days[0]["items"], 1)


class CockpitTrendTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.news = Path(self._tmp.name) / "news"
        (self.news / "state").mkdir(parents=True)
        (self.news / "sources.yaml").write_text(SOURCES, encoding="utf-8")
        store = state.Store(self.news)
        batch = self.news / "state" / "batches" / _day(0) / "aihot-am.json"
        batch.parent.mkdir(parents=True, exist_ok=True)
        batch.write_text(json.dumps({
            "source": "aihot", "slot": "am", "items": [{"ext_id": "x", "status": "read"}],
            "card_id": "c1"}), encoding="utf-8")
        store.heartbeat("aihot", status="throttled", fetched=3, kept=1, queued=0,
                        note="30 分钟内已跑过")
        self.payload = view.build(self.news, now=NOW)
        self.page = view.cockpit_html(self.payload)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_trend_table_is_rendered_with_real_columns(self) -> None:
        self.assertIn(f"近 {view.HISTORY_DAYS} 天", self.page)
        for header in ("日期", "条目", "已标记率", "发卡", "入库", "AI 降级/调用", "有产出的源"):
            self.assertIn(f"<th>{header}</th>", self.page)
        self.assertIn(_day(0)[5:], self.page)                    # 今天的行在
        self.assertIn("只算落盘过的", self.page)                  # 数据来源写在脸上

    def test_trend_bars_are_pure_css(self) -> None:
        self.assertIn('<span class="bar', self.page)
        self.assertNotIn("url(", self.page)                      # 不引图表库、不拉外部资源
        self.assertNotIn("<script", self.page)                   # 也没有 JS

    def test_drawer_shows_the_gates_and_admits_what_is_not_recorded(self) -> None:
        self.assertIn("诊断抽屉", self.page)
        self.assertIn("<details", self.page)
        self.assertIn("30 分钟内已跑过", self.page)               # 最近一次心跳的 note
        self.assertIn("fetched=3", self.page)                    # 最近一次计数
        self.assertIn("生效中的闸", self.page)
        self.assertIn("历史拦截原因<b>没有落盘</b>", self.page)   # 诚实边界
        self.assertIn("只落 state", self.page)                    # state_only 不发卡要说清

    def test_drawer_flags_a_source_that_never_produced(self) -> None:
        self.assertIn("一天都没有", self.page)                    # hn 这些天没有任何批次

    def test_empty_history_renders_no_trend_table_and_no_fake_output_claim(self) -> None:
        """没有历史 ≠ 没产出：不许出现「近 0 天产出 0 天」这种会被误读的话。"""
        page = view.cockpit_html({**self.payload, "history": []})
        self.assertNotIn("近 14 天", page)                       # 趋势表整段消失
        self.assertNotIn("近 0 天", page)
        self.assertNotIn("一天都没有", page)
        self.assertIn("还没有历史", page)                         # 换成诚实的一句
        self.assertIn("诊断抽屉", page)                           # 抽屉本身仍有价值（心跳/闸）


class BarTests(unittest.TestCase):
    def test_zero_peak_does_not_divide_by_zero(self) -> None:
        self.assertIn('width:0%', view._bar(0, 0))

    def test_zero_value_gets_the_muted_class(self) -> None:
        self.assertIn('class="bar zero"', view._bar(0, 10))
        self.assertIn('class="bar"', view._bar(5, 10))

    def test_small_nonzero_value_still_visible(self) -> None:
        self.assertIn('width:2%', view._bar(1, 1000))            # 有值就不该是 0 宽


if __name__ == "__main__":
    unittest.main()
