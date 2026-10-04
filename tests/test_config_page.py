"""配置页 / 表单 schema / RSS 搜索的护栏。

钉住三件事：

1. **表单不是第二份 schema**：`config.describe_schema()` 的每个 `path` 都能从
   `config.source_values()` 取到当前值。少一个字段的后果不是报错，而是编辑表单把那个字段
   按默认值写回去 —— 静默的数据损坏（比如把 `feeds` 清空、把 `max_items` 打回 13）。
2. **表单 → 整段映射**的翻译：嵌套（`deliver.budget.*`）、布尔隐藏 0、空数字不写（让默认值生效，
   而不是静默变 0）、列表按逗号/顿号切分。
3. **配置页真的能增删改**：只经 `source set|remove`，落盘可查，`--base-hash` 过期会被拒。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
for _p in (str(SRC_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from newspipe import actions, config, rss, view  # noqa: E402

#: 一份「最刁」的信源 —— 带 keywords/quiet_hours/http2/feeds/card.group_by。
#: 用它做编辑往返：这些字段任何一个在表单里丢了，都会静默写回默认值。
RICH_SOURCES = """
chat: 'oc_test_chat'
slots:
  am: '08:15'
  noon: '12:35'
  pm: '18:35'
sources:
  linuxdo_deals:
    adapter: rsshub
    enabled: true
    tier: T2
    feeds:
      - https://linux.do/tag/福利羊毛.rss
    feed_label: LINUX DO
    http2: true
    fetch:
      trigger: poll
      interval_min: 30
    filter:
      include_keywords: [token, 羊毛]
      min_items: 1
      max_items: 8
      rank: pub_desc
    enrich:
      summary: title
      fetch_body: true
      max_body_chars: 4000
    deliver:
      form: card
      priority: normal
      quiet_hours: 23:30-07:30
      budget:
        max_cards_per_day: 6
        min_gap_min: 90
    card:
      title: AI 羊毛情报 · LINUX DO
      group_by: category
"""

from test_contract import SOURCES_YAML  # noqa: E402

KNOWN_TYPES = {"str", "int", "float", "bool", "enum", "list", "slots"}


class SchemaTests(unittest.TestCase):
    """schema 与取值器必须一一对应 —— 这是「表单不是第二份 schema」的唯一保证。"""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.news = Path(self._tmp.name) / "news"
        self.news.mkdir(parents=True)
        (self.news / "sources.yaml").write_text(RICH_SOURCES, encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_every_schema_path_has_a_current_value(self) -> None:
        schema = config.describe_schema(self.news)
        cfg = config.load(self.news)
        paths = {f["path"] for f in schema["fields"]}
        for name, src in cfg.sources.items():
            values = config.source_values(src)
            self.assertEqual(paths, set(values), f"{name}: schema 与取值器不一致")
        # 每个 path 在 schema 里只出现一次
        self.assertEqual(len(paths), len(schema["fields"]))

    def test_field_types_and_choices_are_declared(self) -> None:
        for field in config.describe_schema(self.news)["fields"]:
            self.assertIn(field["type"], KNOWN_TYPES, field["path"])
            if field["type"] == "enum":
                self.assertTrue(field.get("choices"), field["path"])
            self.assertIn(field["axis"], {a["key"] for a in config.describe_schema()["axes"]})

    def test_adapters_come_from_the_package_not_a_hand_list(self) -> None:
        self.assertEqual(config.describe_schema()["adapters"], config.adapter_names())
        self.assertIn("rsshub", config.adapter_names())

    def test_values_survive_a_round_trip_through_the_form(self) -> None:
        """当前值 → 表单字段 → `build_source_body` → 与原配置一致（编辑不损坏数据）。"""
        cfg = config.load(self.news)
        src = cfg.sources["linuxdo_deals"]              # 有 keywords/quiet_hours/http2/feeds，最刁
        values = config.source_values(src)
        form = {"name": ["linuxdo_deals"]}
        for field in config.describe_schema(self.news)["fields"]:
            value = values[field["path"]]
            key = f"{actions.FORM_PREFIX}{field['path']}"
            if field["type"] == "bool":
                form[key] = ["0", "1"] if value else ["0"]
            elif field["type"] == "slots":
                form[key] = list(value)
            elif field["type"] == "list":
                form[key] = [", ".join(value)]
            else:
                form[key] = [str(value)]
        body = actions.build_source_body(form)
        self.assertEqual(body["feeds"], ["https://linux.do/tag/福利羊毛.rss"])
        self.assertEqual(body["http2"], True)
        self.assertEqual(body["fetch"]["interval_min"], 30)
        self.assertIn("token", body["filter"]["include_keywords"])
        self.assertEqual(body["deliver"]["quiet_hours"], "23:30-07:30")
        self.assertEqual(body["card"]["title"], "AI 羊毛情报 · LINUX DO")


class FormBodyTests(unittest.TestCase):
    def test_nested_paths_and_bool_and_numbers(self) -> None:
        body = actions.build_source_body({
            "f.adapter": ["rsshub"], "f.enabled": ["0", "1"], "f.feeds": ["/sspai/matrix"],
            "f.fetch.trigger": ["poll"], "f.fetch.interval_min": ["30"],
            "f.deliver.budget.max_cards_per_day": ["6"],
            "f.deliver.budget.min_gap_min": ["1.5"],
            "f.filter.include_keywords": ["AI, 模型、算力\ntoken"],
        })
        self.assertTrue(body["enabled"])
        self.assertEqual(body["feeds"], ["/sspai/matrix"])
        self.assertEqual(body["fetch"], {"trigger": "poll", "interval_min": 30.0})
        self.assertEqual(body["deliver"], {"budget": {"max_cards_per_day": 6, "min_gap_min": 1.5}})
        self.assertEqual(body["filter"]["include_keywords"], ["AI", "模型", "算力", "token"])

    def test_unchecked_bool_is_false_not_missing(self) -> None:
        body = actions.build_source_body({"f.enabled": ["0"], "f.adapter": ["rsshub"]})
        self.assertIs(body["enabled"], False)

    def test_empty_number_is_skipped_so_the_default_applies(self) -> None:
        body = actions.build_source_body({"f.adapter": ["rsshub"], "f.filter.max_items": [""]})
        self.assertNotIn("max_items", body.get("filter", {}))

    def test_fields_absent_from_the_form_are_not_written(self) -> None:
        body = actions.build_source_body({"f.adapter": ["rsshub"]})
        self.assertEqual(body, {"adapter": "rsshub"})


class SourceCrudTests(unittest.TestCase):
    """增删改只经 CLI：dry-run 不落盘、真写落盘、过期哈希被拒。"""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.news = Path(self._tmp.name) / "news"
        self.news.mkdir(parents=True)
        (self.news / "sources.yaml").write_text(SOURCES_YAML, encoding="utf-8")
        self.before = (self.news / "sources.yaml").read_text(encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _form(self, **over: str) -> dict[str, list[str]]:
        form = {"name": ["demo_feed"], "f.adapter": ["rsshub"],
                "f.feeds": ["/sspai/matrix"], "f.feed_label": ["少数派"],
                "f.fetch.trigger": ["slot"], "f.fetch.slots": ["am"],
                "f.deliver.form": ["card"], "f.card.title": ["演示"]}
        for key, value in over.items():
            form[key] = [value]
        return form

    def test_dry_run_shows_the_diff_without_touching_the_file(self) -> None:
        out = actions.dispatch("source-save", self._form(dry="1"), self.news)
        self.assertTrue(out["ok"], out)
        self.assertEqual((self.news / "sources.yaml").read_text(encoding="utf-8"), self.before)
        self.assertFalse(out["changed"])

    def test_save_creates_a_source_that_the_engine_then_loads(self) -> None:
        out = actions.dispatch("source-save", self._form(), self.news)
        self.assertTrue(out["ok"], out)
        cfg = config.load(self.news)
        self.assertIn("demo_feed", cfg.sources)
        self.assertEqual(cfg.sources["demo_feed"].card_title, "演示")
        self.assertEqual(list(cfg.sources["demo_feed"].fetch.slots), ["am"])

    def test_stale_hash_is_refused(self) -> None:
        out = actions.dispatch("source-save", self._form(base_hash="deadbeef"), self.news)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "E_CONFLICT")

    def test_bad_name_is_a_usage_error(self) -> None:
        out = actions.dispatch("source-save", self._form(name="有 空格"), self.news)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "E_USAGE")

    def test_remove_deletes_and_then_reports_not_found(self) -> None:
        actions.dispatch("source-save", self._form(), self.news)
        out = actions.dispatch("source-remove", {"name": ["demo_feed"]}, self.news)
        self.assertTrue(out["ok"], out)
        self.assertNotIn("demo_feed", config.load(self.news).sources)
        again = actions.dispatch("source-remove", {"name": ["demo_feed"]}, self.news)
        self.assertFalse(again["ok"])
        self.assertEqual(again["error"]["code"], "E_NOT_FOUND")

    def test_the_cli_warns_that_block_comments_are_lost(self) -> None:
        """整体替换会重写块 ⇒ 更新既有源时必须把 warning 带到用户眼前（页面横幅显示它）。"""
        first = actions.dispatch("source-save", self._form(), self.news)
        self.assertEqual(str((first.get("data") or {}).get("warning") or ""), "",
                         "新建不该报「注释会丢」")
        second = actions.dispatch("source-save", self._form(**{"f.card.title": "演示 2"}), self.news)
        self.assertTrue(second["ok"], second)
        self.assertIn("注释", str((second.get("data") or {}).get("warning") or ""))


class RssTests(unittest.TestCase):
    """RSS 搜索/发现：目录匹配 + 页面声明解析 + 常见路径回落。不依赖真网络（打桩 net）。"""

    def test_catalog_search_matches_name_tags_and_feed(self) -> None:
        self.assertEqual(len(rss.search("")), len(rss.CATALOG))
        self.assertTrue(any("少数派" in row["name"] for row in rss.search("少数派")))
        self.assertTrue(rss.search("不存在的关键词zzz") == [])
        # 目录里的每条都必须带实测记录 —— 没实测过的条目不该进来
        for row in rss.CATALOG:
            self.assertTrue(row.get("verified"), row["name"])

    def test_candidate_prefill_is_ready_to_subscribe(self) -> None:
        cand = rss.as_candidate(rss.CATALOG[0])
        self.assertEqual(cand["adapter"], "rsshub")
        self.assertTrue(cand["feed"])
        self.assertTrue(cand["name_hint"])

    def test_discover_reads_the_link_tag(self) -> None:
        html = ('<html><head><link rel="alternate" type="application/rss+xml" '
                'title="站点 Feed" href="/atom.xml"></head></html>')
        self._stub({"https://example.com": html.encode()})
        out = rss.discover("example.com")
        self.assertEqual(out["url"], "https://example.com")
        self.assertEqual([c["url"] for c in out["candidates"]], ["https://example.com/atom.xml"])
        self.assertEqual(out["candidates"][0]["title"], "站点 Feed")

    def test_discover_falls_back_to_common_paths(self) -> None:
        self._stub({"https://example.com": b"<html><head></head></html>",
                    "https://example.com/feed": b"<?xml version='1.0'?><rss version='2.0'><channel/>"})
        out = rss.discover("example.com")
        self.assertEqual([c["url"] for c in out["candidates"]], ["https://example.com/feed"])
        self.assertIn("常见路径", out["candidates"][0]["how"])

    def test_discover_reports_a_dead_site_instead_of_pretending(self) -> None:
        def boom(url: str, **kw: object) -> bytes:
            raise OSError("connection refused")

        original, rss.net.request = rss.net.request, boom      # type: ignore[assignment]
        try:
            with self.assertRaises(rss.RssError) as caught:
                rss.discover("example.com")
        finally:
            rss.net.request = original                         # type: ignore[assignment]
        self.assertIn("取不到", str(caught.exception))

    def _stub(self, table: dict[str, bytes]) -> None:
        def fake(url: str, **kw: object) -> bytes:
            if url in table:
                return table[url]
            raise OSError(f"stub: {url} 不在表里")

        self._original = rss.net.request
        rss.net.request = fake                                # type: ignore[assignment]
        self.addCleanup(setattr, rss.net, "request", self._original)


class ConfigPageTests(unittest.TestCase):
    """配置页渲染：字段来自 schema、编辑表单预填当前值、RSS 区两处入口都在。"""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.news = Path(self._tmp.name) / "news"
        self.news.mkdir(parents=True)
        (self.news / "sources.yaml").write_text(SOURCES_YAML, encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _page(self, page: dict | None = None, token: str = "T") -> str:
        act = {"token": token, "sources_hash": "abc123", "queue": [], "events": [], "pending": {}}
        return view.config_html(view.build(self.news), schema=config.describe_schema(self.news),
                                actions=act, page=page or {})

    def test_form_fields_come_from_the_schema(self) -> None:
        page = self._page()
        for path in ("fetch.trigger", "filter.max_items", "enrich.summary", "deliver.form",
                     "deliver.budget.max_cards_per_day", "card.title"):
            self.assertIn(f'name="f.{path}"', page, path)
        self.assertIn("① 何时拉", page)                 # 四轴分组都在
        self.assertIn("④ 怎么发", page)
        self.assertIn("高级（", page)                   # 高级字段折叠

    def test_edit_form_is_prefilled_with_current_values(self) -> None:
        cfg = config.load(self.news)
        page = self._page({"edit": "aihot", "values": config.source_values(cfg.sources["aihot"])})
        self.assertIn("编辑 <code>aihot</code>", page)
        self.assertIn('<option value="aihot" selected>', page)         # 适配器预选
        self.assertIn('name="f.fetch.slots" value="am" checked', page)  # 槽位预勾
        self.assertIn('name="name" value="aihot"', page)                # 名字锁定（改名 = 新建）

    def test_catalog_hits_offer_a_subscribe_link(self) -> None:
        page = self._page({"q": "少数派", "catalog": rss.search("少数派")})
        self.assertIn("用这个新建信源", page)
        self.assertIn("/config?use=0", page)

    def test_url_discovery_results_offer_a_subscribe_link(self) -> None:
        page = self._page({"discover_url": "example.com",
                           "discover": {"candidates": [{"url": "https://example.com/feed",
                                                        "title": "", "how": "探测到常见路径 /feed"}]}})
        self.assertIn("example.com/feed", page)
        self.assertIn("探测到常见路径", page)

    def test_write_buttons_disappear_without_a_token(self) -> None:
        page = self._page(token="")
        self.assertNotIn('action="/api/actions/source-save"', page)
        self.assertIn("写操作未开启", page)


if __name__ == "__main__":
    unittest.main()
