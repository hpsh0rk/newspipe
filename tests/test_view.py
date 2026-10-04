"""只读视图契约测试 —— 「宿主不再解析 state 布局」这件事的护栏。

三件事必须被钉住：

1. **视图的语义在包这边**：心跳状态 → 标签/语气、超期判定，都由 `view.build` 给出；
   宿主只渲染。词表漏一个状态，面板就会显示裸状态码（看不出是故障还是攒批）。
2. **同一份 builder 服务三条出口**：CLI `view`、`GET /view`、`GET /`（HTML）。
   三边内容不一致 = 面板看到的和页面看到的不是一件事。
3. **默认不多开监听**：`view.enabled` 不配 ⇒ 行为与以前逐字节一致（`mode=none` 不起服务器）。
"""
from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
for _p in (str(SRC_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from newspipe import config, inbound, net, state, view  # noqa: E402

from test_contract import SOURCES_YAML  # noqa: E402

TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 4, 9, 0, tzinfo=TZ)


class ViewBuildTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.news = Path(self._tmp.name) / "news"
        self.news.mkdir(parents=True)
        (self.news / "sources.yaml").write_text(SOURCES_YAML, encoding="utf-8")
        self.store = state.Store(self.news)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_shape_covers_every_axis_and_runtime_field(self) -> None:
        payload = view.build(self.news, now=NOW)
        self.assertEqual(payload["contract_version"], view.CONTRACT_VERSION)
        self.assertEqual(payload["chat"], "oc_test_chat")
        self.assertEqual(payload["slots"], {"am": "08:15", "noon": "12:35", "pm": "18:35"})
        row = next(r for r in payload["sources"] if r["name"] == "aihot")
        for field in ("name", "enabled", "adapter", "trigger", "slots", "form", "priority",
                      "summary_mode", "include_keywords", "exclude_keywords", "max_items",
                      "card_title", "sends_card", "status", "status_label", "status_tone",
                      "status_ts", "stale", "status_detail", "status_note",
                      "pushed_total", "pending_total"):
            self.assertIn(field, row, field)
        self.assertEqual(row["trigger"], "slot")
        self.assertEqual(row["slots"], ["am"])
        self.assertEqual(row["priority"], "high")
        self.assertEqual(row["card_title"], "aihot")
        # 从没跑过的源必须区别于「跑了但没话说」
        self.assertEqual(row["status"], "never")
        self.assertFalse(row["stale"])

    def test_heartbeat_status_becomes_label_and_tone(self) -> None:
        self.store.heartbeat("aihot", status="below_min_items", fetched=2, kept=2, note="攒批")
        payload = view.build(self.news, now=NOW)
        row = next(r for r in payload["sources"] if r["name"] == "aihot")
        self.assertEqual(row["status_label"], "攒批中")
        self.assertEqual(row["status_tone"], "muted")
        self.assertEqual(row["status_detail"]["fetched"], 2)
        self.assertEqual(row["status_note"], "攒批")

    def test_stale_detection_uses_the_trigger_window_and_aligns_timezones(self) -> None:
        self.store.heartbeat("aihot", status="ok")            # 槽位源：30h
        # 心跳写的是墙钟时间，把文件改成 31 小时前
        path = self.news / "state" / "status" / "aihot.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["ts"] = (NOW - timedelta(hours=31)).isoformat(timespec="seconds")
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        row = next(r for r in view.build(self.news, now=NOW)["sources"] if r["name"] == "aihot")
        self.assertTrue(row["stale"])
        self.assertEqual(row["status_label"], "超期未运行")
        self.assertEqual(row["status_tone"], "warn")
        # naive 的 now 也必须能算（时区不对齐会把超期判定吞成 False —— 那才是静默故障）
        naive = NOW.replace(tzinfo=None)
        row2 = next(r for r in view.build(self.news, now=naive)["sources"] if r["name"] == "aihot")
        self.assertTrue(row2["stale"])

    def test_dedup_ledger_and_pending_counts_come_from_state(self) -> None:
        self.store.record_pushed("aihot", [{"ext_id": "a1", "title": "x"},
                                           {"ext_id": "a2", "title": "y"}], "2026-10-04")
        self.store.append_pending("aihot", [{"ext_id": "a3", "title": "z"}])
        row = next(r for r in view.build(self.news, now=NOW)["sources"] if r["name"] == "aihot")
        self.assertEqual(row["pushed_total"], 2)
        self.assertEqual(row["pending_total"], 1)

    def test_today_batches_and_summary(self) -> None:
        batch = {"digest": "2026-10-04", "source": "aihot", "slot": "am", "title": "早间",
                 "card_id": "om_1", "view": {"item": "a1"}, "overflow": 2,
                 "items": [{"ext_id": "a1", "status": "read"}, {"ext_id": "a2"}]}
        self.store.write_batch(batch)
        payload = view.build(self.news, now=NOW)
        self.assertEqual(len(payload["today"]), 1)
        row = payload["today"][0]
        self.assertEqual(row["count"], 2)
        self.assertEqual(row["marked"], 1)
        self.assertTrue(row["has_card"])
        self.assertEqual(row["view"], "详情")
        self.assertEqual(row["overflow"], 2)
        self.assertEqual(payload["summary"]["items"], 2)
        self.assertEqual(payload["summary"]["read_rate"], 0.5)

    def test_llm_usage_and_preferences_are_exposed(self) -> None:
        self.store.add_usage("2026-10-04", calls=3, chars=120, degraded=1, reason="length_empty")
        (self.news / "preferences.md").write_text("- 2026-10-04 来源=aihot 降权\n", encoding="utf-8")
        payload = view.build(self.news, now=NOW)
        self.assertEqual(payload["llm"]["calls"], 3)
        self.assertEqual(payload["llm"]["degraded"], 1)
        self.assertEqual(payload["llm"]["reasons"], {"length_empty": 1})
        self.assertIn("降权", payload["preferences_preview"])

    def test_html_page_is_self_contained_and_escaped(self) -> None:
        self.store.heartbeat("aihot", status="error", note="<script>alert(1)</script>")
        page = view.as_html(view.build(self.news, now=NOW))
        self.assertIn("newspipe · 资讯管线", page)
        self.assertNotIn("<script>alert(1)</script>", page)      # 转义过
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn("http://", page.replace("http://127.0.0.1", ""))  # 不发外部请求


class ServiceViewConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.news = Path(self._tmp.name) / "news"
        self.news.mkdir(parents=True)
        (self.news / "sources.yaml").write_text(SOURCES_YAML, encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_view_is_off_by_default(self) -> None:
        svc = config.load_service(self.news)
        self.assertFalse(svc.view_enabled)
        self.assertEqual(svc.view_path, "/view")

    def test_view_block_is_parsed_and_validated(self) -> None:
        (self.news / "service.yaml").write_text(
            "channel: feishu_direct\ninbound:\n  mode: none\n"
            "view:\n  enabled: true\n  path: /view\n", encoding="utf-8")
        svc = config.load_service(self.news)
        self.assertTrue(svc.view_enabled)
        self.assertEqual(svc.view_path, "/view")

    def test_a_relative_view_path_is_rejected(self) -> None:
        (self.news / "service.yaml").write_text(
            "view:\n  enabled: true\n  path: view\n", encoding="utf-8")
        from newspipe.errors import ConfigError

        with self.assertRaises(ConfigError):
            config.load_service(self.news)

    def test_none_mode_starts_no_server_unless_view_is_enabled(self) -> None:
        handle = inbound.start_inbound(mode="none", service_cfg={}, creds=None,
                                       news_dir=self.news, view={"enabled": False})
        self.assertIsNone(handle.server)
        self.assertEqual(handle.describe(), {"mode": "none", "alive": False})


class ViewHttpTests(unittest.TestCase):
    """`GET /view` 与 `GET /` 走的是同一台（只读）服务器。"""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.news = Path(self._tmp.name) / "news"
        self.news.mkdir(parents=True)
        (self.news / "sources.yaml").write_text(SOURCES_YAML, encoding="utf-8")
        self.service_cfg = {"http": {"host": "127.0.0.1", "port": 0, "path": "/feishu/events"}}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _serve(self, *, accept_events: bool) -> tuple[object, threading.Thread, str]:
        server, path = inbound.start_http(service_cfg=self.service_cfg, creds=None,
                                          news_dir=self.news, accept_events=accept_events)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        base = f"http://127.0.0.1:{server.server_address[1]}"
        return server, thread, base

    def test_get_view_returns_the_contract(self) -> None:
        _server, _thread, base = self._serve(accept_events=True)
        with urllib.request.urlopen(f"{base}/view", timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(payload["contract_version"], view.CONTRACT_VERSION)
        self.assertEqual({r["name"] for r in payload["sources"]}, {"aihot", "hn"})
        self.assertIn("summary", payload)

    def test_get_root_returns_html(self) -> None:
        _server, _thread, base = self._serve(accept_events=True)
        with urllib.request.urlopen(f"{base}/", timeout=5) as resp:
            self.assertIn("text/html", resp.headers["Content-Type"])
            page = resp.read().decode("utf-8")
        self.assertIn("资讯管线", page)

    def test_view_only_server_refuses_events(self) -> None:
        _server, _thread, base = self._serve(accept_events=False)
        with urllib.request.urlopen(f"{base}/view", timeout=5) as resp:
            self.assertEqual(resp.status, 200)
        request = urllib.request.Request(f"{base}/feishu/events", data=b"{}", method="POST")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(caught.exception.code, 404)
        self.assertIn("events disabled", caught.exception.read().decode("utf-8"))

    def test_health_probe_still_answers(self) -> None:
        _server, _thread, base = self._serve(accept_events=True)
        with urllib.request.urlopen(f"{base}/healthz", timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["view"], "/view")


class DeploymentEnvTests(unittest.TestCase):
    """容器里「宿主的回环地址」不是 127.0.0.1 —— 两个出口都得能被部署显式改写。"""

    def test_proxy_defaults_to_clash_and_follows_the_env_override(self) -> None:
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NEWSPIPE_PROXY", None)
            self.assertEqual(net.proxy_url(), net.DEFAULT_PROXY)
        with mock.patch.dict(os.environ, {"NEWSPIPE_PROXY": "http://host.docker.internal:7890"}):
            self.assertEqual(net.proxy_url(), "http://host.docker.internal:7890")

    def test_loopback_model_endpoints_are_rewritten_only_when_asked(self) -> None:
        import os
        from unittest import mock

        from newspipe.backends.model_ref import rewrite_loopback

        url = "http://localhost:7863/v1"
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NEWSPIPE_LOOPBACK_ALIAS", None)
            self.assertEqual(rewrite_loopback(url), url)              # 默认不动
        with mock.patch.dict(os.environ, {"NEWSPIPE_LOOPBACK_ALIAS": "host.docker.internal"}):
            self.assertEqual(rewrite_loopback(url), "http://host.docker.internal:7863/v1")
            self.assertEqual(rewrite_loopback("http://127.0.0.1:1200/x"),
                             "http://host.docker.internal:1200/x")
            # 真实域名绝不能被改写
            self.assertEqual(rewrite_loopback("https://api.deepseek.com/v1"),
                             "https://api.deepseek.com/v1")

    def test_the_bind_host_can_be_overridden_by_the_deployment(self) -> None:
        """容器里必须绑 0.0.0.0：否则 Docker 的端口转发够不着容器自己的回环。"""
        import os
        from unittest import mock

        cfg = {"http": {"host": "127.0.0.1", "port": 8787, "path": "/feishu/events"}}
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NEWSPIPE_BIND_HOST", None)
            self.assertEqual(inbound.http_bind(cfg)[0], "127.0.0.1")     # 宿主机默认
        with mock.patch.dict(os.environ, {"NEWSPIPE_BIND_HOST": "0.0.0.0"}):
            self.assertEqual(inbound.http_bind(cfg)[0], "0.0.0.0")


class ViewCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.news = Path(self._tmp.name) / "news"
        self.news.mkdir(parents=True)
        (self.news / "sources.yaml").write_text(SOURCES_YAML, encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, *args: str) -> tuple[int, str]:
        import io
        from contextlib import redirect_stdout

        from newspipe import cli

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cli.main(["--news-dir", str(self.news), *args])
        return code, buffer.getvalue()

    def test_view_json_emits_the_envelope(self) -> None:
        code, out = self._run("view", "--json")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["changed"])
        self.assertEqual(payload["data"]["contract_version"], view.CONTRACT_VERSION)

    def test_view_html_and_json_together_is_a_usage_error(self) -> None:
        code, out = self._run("view", "--html", "--json")
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["error"]["code"], "E_USAGE")

    def test_view_html_prints_a_page(self) -> None:
        code, out = self._run("view", "--html")
        self.assertEqual(code, 0)
        self.assertIn("<!doctype html>", out)

    def test_view_is_listed_in_the_api_contract(self) -> None:
        code, out = self._run("api", "describe", "--json")
        self.assertEqual(code, 0)
        names = {entry["name"] for entry in json.loads(out)["data"]["commands"]}
        self.assertIn("view", names)


if __name__ == "__main__":
    unittest.main()
