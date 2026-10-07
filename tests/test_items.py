"""内容层（条目浏览）的护栏：条目面数据、服务端筛选、批次详情、路径安全。

钉住四件事：

1. **条目面不进 `/view` 契约** —— 那是给宿主的只读契约，形状要稳，也不该背几百条条目。
2. **筛选在服务端**（无 JS）—— 关键词命中标题/摘要/发布者/分类，源与状态精确匹配。
3. **状态取值从数据现取**，不硬编码清单（实测数据里出现过 `wiki` 这种没预料到的状态）。
4. **批次详情是 URL 直连的路由** ⇒ 路径校验必须两道：形状 + 越界。越界路径一次 IO 都不能发生。
"""
from __future__ import annotations

import json
import shutil
import sys
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(REPO_ROOT / "src"), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from newspipe import inbound, state, view  # noqa: E402

#: 测试必须钉住时钟：`recent_items()` 默认取墙钟，跨午夜跑就会换窗口（真踩过：
#: 10-04 23:58 写好的用例，10-05 00:05 跑 `days=1` 就一条都不剩了）。
NOW = datetime(2026, 10, 4, 12, 0)

SOURCES = """
chat: 'oc_test_chat'
slots:
  am: '08:15'
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
      slots: [pm]
"""


def _batch(source: str, slot: str, items: list[dict]) -> dict:
    return {"digest": "2026-10-04", "source": source, "slot": slot, "adapter": "rsshub",
            "title": f"{source} 批次", "created_at": "2026-10-04T08:15:00", "items": items,
            "card_id": "card-1", "overflow": 2, "updated_at": "2026-10-04T08:16:00"}


class ItemsFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.news = Path(self._tmp.name) / "news"
        (self.news / "state").mkdir(parents=True)
        (self.news / "sources.yaml").write_text(SOURCES, encoding="utf-8")
        self.store = state.Store(self.news)
        self._seed_batches("2026-10-04", "2026-10-03")

    def _seed_batches(self, day_new: str, day_old: str) -> None:
        """两天的批次。日期由调用方给：纯视图测试注入 `NOW` ⇒ 写死即可；
        走 HTTP 路由的测试用真实墙钟 ⇒ 必须相对今天生成（见 BatchRouteTests）。"""
        self._write(day_new, "aihot-am", [
            {"ext_id": "a1", "title": "开源模型 Vicuna 发布", "url": "https://aihot.news/items/a1",
             "original_url": "https://lmsys.org/blog/vicuna", "source": "LMSYS：Blog",
             "summary": "训练成本约 $300", "category": "模型", "score": 79, "status": "read"},
            {"ext_id": "a2", "title": "算力租赁价格战", "url": "", "original_url": "",
             "source": "量子位", "summary": "降价 40%", "category": "算力", "score": 61,
             "status": "unread"},
        ])
        self._write(day_old, "hn-pm", [
            {"ext_id": "h1", "title": "Surely you have ultra-wideband", "url": "https://news.ycombinator.com/item?id=1",
             "original_url": "https://example.com/uwb", "source": "HN", "summary": "UWB 讨论",
             "category": "", "score": 120, "status": "wiki"},
        ])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, day: str, stem: str, items: list[dict]) -> None:
        path = self.news / "state" / "batches" / day / f"{stem}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        source, slot = stem.split("-", 1)
        path.write_text(json.dumps(_batch(source, slot, items), ensure_ascii=False),
                        encoding="utf-8")


class RecentItemsTests(ItemsFixture):
    def test_newest_day_first_and_batch_identity_attached(self) -> None:
        rows = view.recent_items(self.store, days=7, now=NOW)
        self.assertEqual([r["ext_id"] for r in rows], ["a1", "a2", "h1"])
        self.assertEqual(rows[0]["date"], "2026-10-04")
        self.assertEqual(rows[0]["batch_file"], "aihot-am")
        self.assertEqual(rows[0]["slot"], "am")
        self.assertEqual(rows[0]["has_card"], True)
        self.assertEqual(rows[-1]["date"], "2026-10-03")

    def test_batch_source_and_item_source_do_not_collide(self) -> None:
        """两个字段都叫 source 会串味：批次源是 `aihot`，条目发布者是 `LMSYS：Blog`。"""
        rows = view.recent_items(self.store, days=7, now=NOW)
        self.assertEqual(rows[0]["batch_source"], "aihot")
        self.assertEqual(rows[0]["source"], "LMSYS：Blog")

    def test_window_and_limit_are_respected(self) -> None:
        self.assertEqual(len(view.recent_items(self.store, days=1, now=NOW)), 2)   # 只有 10-04
        self.assertEqual(len(view.recent_items(self.store, days=7, limit=1, now=NOW)), 1)

    def test_item_fields_come_from_the_batch_file(self) -> None:
        row = view.recent_items(self.store, days=7, now=NOW)[0]
        self.assertEqual(row["original_url"], "https://lmsys.org/blog/vicuna")
        self.assertEqual(row["category"], "模型")
        self.assertEqual(row["score"], 79)

    def test_junk_does_not_explode(self) -> None:
        path = self.news / "state" / "batches" / "2026-10-04" / "broken.json"
        path.write_text("{不是 json", encoding="utf-8")
        bad = self.news / "state" / "batches" / "2026-10-04" / "weird-am.json"
        bad.write_text(json.dumps({"items": ["不是 dict", 42, {"ext_id": "ok"}]}), encoding="utf-8")
        rows = view.recent_items(self.store, days=1, now=NOW)
        self.assertIn("ok", [r["ext_id"] for r in rows])                  # 好的条目还在
        self.assertNotIn(None, [r["ext_id"] for r in rows])               # 非 dict 被丢掉

    def test_snapshot_derives_statuses_from_data_not_a_hardcoded_list(self) -> None:
        snap = view.items_snapshot(self.news, now=NOW)
        self.assertEqual(snap["total"], 3)
        self.assertEqual(snap["sources"], ["aihot", "hn"])
        self.assertEqual(snap["statuses"], ["read", "unread", "wiki"])     # wiki 是数据里出现的

    def test_items_are_not_in_the_view_contract(self) -> None:
        payload = view.build(self.news)
        self.assertNotIn("items", payload)                                 # 契约形状要稳
        self.assertNotIn("history", payload.get("summary", {}))


class FilterTests(ItemsFixture):
    def setUp(self) -> None:
        super().setUp()
        self.rows = view.recent_items(self.store, days=7, now=NOW)

    def test_no_filter_passes_everything(self) -> None:
        self.assertEqual(len(view.filter_items(self.rows)), 3)

    def test_keyword_hits_title_summary_publisher_and_category(self) -> None:
        self.assertEqual([r["ext_id"] for r in view.filter_items(self.rows, q="vicuna")], ["a1"])
        self.assertEqual([r["ext_id"] for r in view.filter_items(self.rows, q="降价 40")], ["a2"])
        self.assertEqual([r["ext_id"] for r in view.filter_items(self.rows, q="量子位")], ["a2"])
        self.assertEqual([r["ext_id"] for r in view.filter_items(self.rows, q="算力")], ["a2"])

    def test_keyword_is_case_insensitive(self) -> None:
        self.assertEqual(len(view.filter_items(self.rows, q="ULTRA-WIDE")), 1)
        self.assertEqual(len(view.filter_items(self.rows, q="ultra-wide")), 1)

    def test_source_and_status_filters_are_exact(self) -> None:
        self.assertEqual([r["ext_id"] for r in view.filter_items(self.rows, source="hn")], ["h1"])
        self.assertEqual([r["ext_id"] for r in view.filter_items(self.rows, status="unread")], ["a2"])
        self.assertEqual(view.filter_items(self.rows, source="nosuch"), [])

    def test_missing_status_counts_as_unread(self) -> None:
        rows = [{"ext_id": "x", "title": "无状态"}]
        self.assertEqual(len(view.filter_items(rows, status="unread")), 1)

    def test_filters_combine(self) -> None:
        self.assertEqual(view.filter_items(self.rows, q="模型", source="hn"), [])


class RenderTests(ItemsFixture):
    def setUp(self) -> None:
        super().setUp()
        self.view = view.build(self.news)

    def test_items_tab_has_a_server_side_filter_form(self) -> None:
        page = view.items_html(self.view, page={**view.items_snapshot(self.news, now=NOW)})
        self.assertIn('action="/items"', page)
        self.assertIn('name="q"', page)
        self.assertIn('name="source"', page)
        self.assertIn('name="status"', page)
        self.assertIn("开源模型 Vicuna 发布", page)
        self.assertIn("近 7 天共 3 条，命中 3 条", page)

    def test_items_tab_reports_hits_after_filtering(self) -> None:
        page = view.items_html(self.view, page={**view.items_snapshot(self.news, now=NOW), "q": "vicuna"})
        self.assertIn("命中 1 条", page)
        self.assertNotIn("算力租赁价格战", page)

    def test_items_tab_has_an_honest_empty_state(self) -> None:
        page = view.items_html(self.view, page={**view.items_snapshot(self.news, now=NOW), "q": "zzz不存在"})
        self.assertIn("没有命中的条目", page)
        self.assertIn("命中 0 条", page)

    def test_titles_link_to_the_original_url_not_the_proxy(self) -> None:
        page = view.items_html(self.view, page={**view.items_snapshot(self.news, now=NOW)})
        self.assertIn('href="https://lmsys.org/blog/vicuna"', page)        # 原文出处
        self.assertNotIn('href="https://aihot.news/items/a1"', page)       # 不是抓取侧代理页

    def test_item_rows_escape_hostile_content(self) -> None:
        self._write("2026-10-04", "aihot-pm", [
            {"ext_id": "x", "title": "<script>alert(1)</script>", "original_url": "javascript:alert(1)",
             "summary": "<img src=x onerror=alert(1)>", "status": "read"}])
        page = view.items_html(self.view, page={**view.items_snapshot(self.news, now=NOW)})
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertNotIn("<img src=x", page)
        self.assertIn("&lt;script&gt;", page)

    def test_batch_detail_lists_the_items_with_stats(self) -> None:
        loaded = self.store.load_batch_by_rel("state/batches/2026-10-04/aihot-am.json")
        self.assertIsNotNone(loaded)
        _path, batch = loaded
        page = view.batch_html(self.view, page={"date": "2026-10-04", "stem": "aihot-am",
                                                "batch": batch, "items": batch["items"]})
        self.assertIn("aihot 批次", page)
        self.assertIn("开源模型 Vicuna 发布", page)
        self.assertIn("<span>条目</span>", page)
        self.assertIn("已发卡", page)
        self.assertIn("顺延", page)
        self.assertIn("state/batches/2026-10-04/aihot-am.json", page)

    def test_an_http_original_url_is_a_link_not_a_resource_load(self) -> None:
        """真实数据里完全可能出现 `http://` 原文；那是 `href`，不产生请求。

        页面自己的红线是「不加载外部资源」—— 把两者混成一个字符串检查会在这种数据上误报
        （旧的 `assertNotIn("http://")` 就是这样，条目链接一进驾驶舱就会踩）。
        """
        self._write("2026-10-04", "aihot-pm", [
            {"ext_id": "x", "title": "明文站点", "original_url": "http://example.com/a",
             "status": "unread"}])
        snap = view.items_snapshot(self.news, now=NOW)
        page = view.cockpit_html(self.view, page={"recent": snap["items"][:8],
                                                  "recent_total": snap["total"]})
        self.assertIn('href="http://example.com/a"', page)      # 数据照原样出现
        for tag in ('src="http', "<link", "url("):              # 但页面自己不拉外部资源
            self.assertNotIn(tag, page)

    def test_batch_detail_missing_batch_says_so(self) -> None:
        page = view.batch_html(self.view, page={"date": "2026-01-01", "stem": "nosuch-am"})
        self.assertIn("没有这个批次", page)
        self.assertIn("2026-01-01/nosuch-am", page)

    def test_cockpit_shows_recent_titles_and_stays_read_only(self) -> None:
        snap = view.items_snapshot(self.news, now=NOW)
        page = view.cockpit_html(self.view, page={"recent": snap["items"][:8],
                                                  "recent_total": snap["total"]})
        self.assertIn("最近入库", page)
        self.assertIn("开源模型 Vicuna 发布", page)                        # 总览里终于有内容了
        self.assertNotIn("<form", page)                                    # 但仍不许有写操作
        self.assertIn('href="/items"', page)                               # 且有去条目页的路

    def test_ops_page_has_no_recent_items_section(self) -> None:
        page = view.ops_html(self.view, actions=None)
        self.assertNotIn("最近入库", page)


class BatchRouteTests(ItemsFixture):
    """URL 直连的路由：形状与越界都要拒。"""

    def setUp(self) -> None:
        super().setUp()
        # 路由按真实墙钟算「近 N 天」窗口 ⇒ 批次日期必须相对今天重新播种，
        # 否则写死的日子会滚出窗口（10-03 写的用例，10-07 跑 days=3 就红了）。
        self.today = state.today()
        yesterday = (date.fromisoformat(self.today) - timedelta(days=1)).isoformat()
        shutil.rmtree(self.news / "state" / "batches")
        self._seed_batches(self.today, yesterday)
        self.service_cfg = {"http": {"host": "127.0.0.1", "port": 0, "path": "/feishu/events"}}
        server, _path = inbound.start_http(service_cfg=self.service_cfg, creds=None,
                                           news_dir=self.news, accept_events=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.base = f"http://127.0.0.1:{server.server_address[1]}"

    def _get(self, path: str) -> tuple[int, str]:
        try:
            with urllib.request.urlopen(f"{self.base}{path}", timeout=5) as resp:
                return resp.status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8")

    def test_items_route_serves_the_content_tab(self) -> None:
        code, page = self._get("/items")
        self.assertEqual(code, 200)
        self.assertIn("newspipe · 条目", page)
        self.assertIn("开源模型 Vicuna 发布", page)

    def test_items_route_filters_from_the_query_string(self) -> None:
        _code, page = self._get("/items?q=vicuna&source=aihot&days=3")
        self.assertIn("命中 1 条", page)
        _code, page = self._get("/items?days=notanumber")                  # 手改 URL
        self.assertEqual(_code, 200)
        self.assertIn("近 7 天共", page)                                    # 回落默认值，不 500

    def test_batch_route_serves_a_real_batch(self) -> None:
        code, page = self._get(f"/batch/{self.today}/aihot-am")
        self.assertEqual(code, 200)
        self.assertIn("开源模型 Vicuna 发布", page)

    def test_batch_route_404s_for_a_missing_batch(self) -> None:
        code, page = self._get(f"/batch/{self.today}/nosuch-am")
        self.assertEqual(code, 404)
        self.assertIn("没有这个批次", page)

    def test_batch_route_400s_on_bad_shapes(self) -> None:
        for bad in ("/batch/abc/aihot-am", "/batch/2026-10-04/..%2F..%2Fsecret",
                    "/batch/2026-10-04/.hidden", "/batch/2026-10-04/only"):
            code, _page = self._get(bad)
            self.assertIn(code, (400, 404), bad)
            self.assertNotEqual(code, 200, bad)

    def test_traversal_never_reaches_outside_the_news_dir(self) -> None:
        secret = Path(self._tmp.name) / "secret.json"
        secret.write_text(json.dumps({"items": [{"title": "泄露了"}]}), encoding="utf-8")
        for bad in ("/batch/2026-10-04/..%2F..%2Fsecret",
                    "/batch/2026-10-04/....%2F%2Fsecret",
                    "/batch/..%2F..%2F/secret"):
            code, page = self._get(bad)
            self.assertNotEqual(code, 200, bad)
            self.assertNotIn("泄露了", page, bad)

    def test_batch_route_without_a_file_part_is_404(self) -> None:
        code, _page = self._get("/batch/2026-10-04")
        self.assertEqual(code, 404)


if __name__ == "__main__":
    unittest.main()
