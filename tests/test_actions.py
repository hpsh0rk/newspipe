"""Web 写操作测试 —— 「页面按钮 = CLI 同一份 handler」这件事的护栏。

钉住四件事：

1. **表单 → argv 的翻译**：`--dry` 是显式勾选才加、白名单外的动作一律拒绝、
   参数错是 `E_USAGE` 信封而不是 500。
2. **不重写写逻辑**：动作真的落到 CLI 的 handler 上（改的是 `sources.yaml`、
   ack 的是事件、顺延队列真的被清）。这一条是「两份写路径必然漂移」的反面。
3. **三道门禁**：开关（`view.actions`）、同源（`Origin` vs `Host`）、一次性令牌。
   少一道，浏览器里**任意网页**都能 POST 到这个本地端口触发发卡。
4. **只读契约不受影响**：`GET /view` 的 JSON 里没有令牌，页面不开写操作时连表单都不渲染。
"""
from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
for _p in (str(SRC_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from newspipe import actions, events, inbound, view  # noqa: E402

from test_contract import SOURCES_YAML  # noqa: E402

TOKEN = "test-token-0123456789"


class ActionFormTests(unittest.TestCase):
    """表单 → argv：纯翻译，不碰任何文件。"""

    def test_run_targets_map_to_the_cli_flags(self) -> None:
        spec = actions.ACTIONS["run"]
        self.assertEqual(spec.argv({"target": ["poll"]}), ["run", "--poll"])
        self.assertEqual(spec.argv({"target": ["noon"]}), ["run", "--slot", "noon"])
        self.assertEqual(spec.argv({"target": ["source:aihot"]}),
                         ["run", "--source", "aihot"])

    def test_dry_is_opt_in_so_a_bare_form_sends_for_real(self) -> None:
        """复选框没勾 ⇒ 不加 `--dry` ⇒ 真发。所以页面必须默认勾上（见页面测试）。"""
        spec = actions.ACTIONS["run"]
        self.assertNotIn("--dry", spec.argv({"target": ["am"]}))
        self.assertIn("--dry", spec.argv({"target": ["am"], "dry": ["1"]}))
        for falsy in ("0", "false", "off", ""):
            self.assertNotIn("--dry", spec.argv({"target": ["am"], "dry": [falsy]}))

    def test_bad_targets_are_input_errors_not_tracebacks(self) -> None:
        spec = actions.ACTIONS["run"]
        for bad in ({}, {"target": [""]}, {"target": ["whatever"]}, {"target": ["source:"]}):
            with self.assertRaises(actions.ActionInputError):
                spec.argv(bad)

    def test_source_toggle_passes_the_hash_for_optimistic_concurrency(self) -> None:
        spec = actions.ACTIONS["source-toggle"]
        self.assertEqual(spec.argv({"name": ["aihot"], "state": ["disable"]}),
                         ["source", "disable", "aihot"])
        self.assertEqual(
            spec.argv({"name": ["aihot"], "state": ["enable"], "base_hash": ["abc"]}),
            ["source", "enable", "aihot", "--base-hash", "abc"])
        with self.assertRaises(actions.ActionInputError):
            spec.argv({"name": ["aihot"], "state": ["delete"]})

    def test_queue_ack_carries_the_webui_actor(self) -> None:
        argv = actions.ACTIONS["queue-ack"].argv({"event_id": ["ev_1"], "note": ["好文"]})
        self.assertEqual(argv, ["queue", "ack", "ev_1", "--by", "webui", "--note", "好文"])
        # 没有 note 就不加这个 flag（CLI 侧不需要空串）
        self.assertEqual(actions.ACTIONS["queue-ack"].argv({"event_id": ["ev_1"]}),
                         ["queue", "ack", "ev_1", "--by", "webui"])

    def test_events_ack_accepts_several_ids_and_caps_them(self) -> None:
        argv = actions.ACTIONS["events-ack"].argv({"ids": ["ev_1, ev_2"]})
        self.assertEqual(argv, ["events", "ack", "ev_1", "ev_2", "--by", "webui"])
        with self.assertRaises(actions.ActionInputError):
            actions.ACTIONS["events-ack"].argv({"ids": [","]})
        with self.assertRaises(actions.ActionInputError):
            actions.ACTIONS["events-ack"].argv({"ids": [" ".join(f"ev_{i}" for i in range(51))]})

    def test_danger_actions_are_marked(self) -> None:
        self.assertTrue(actions.ACTIONS["flush"].danger)
        self.assertTrue(actions.ACTIONS["source-toggle"].danger)
        self.assertFalse(actions.ACTIONS["run"].danger)
        self.assertFalse(actions.ACTIONS["queue-ack"].danger)


class ActionDispatchTests(unittest.TestCase):
    """动作真的落到 CLI 上：改了配置、清了队列、错误原样透出。"""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.news = Path(self._tmp.name) / "news"
        self.news.mkdir(parents=True)
        (self.news / "sources.yaml").write_text(SOURCES_YAML, encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _dispatch(self, name: str, form: dict[str, list[str]]) -> dict:
        return actions.dispatch(name, form, self.news)

    def test_unknown_action_is_refused_without_touching_anything(self) -> None:
        before = (self.news / "sources.yaml").read_text(encoding="utf-8")
        out = self._dispatch("rm-rf", {})
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "E_NOT_FOUND")
        self.assertEqual((self.news / "sources.yaml").read_text(encoding="utf-8"), before)

    def test_bad_form_is_a_usage_error(self) -> None:
        out = self._dispatch("source-toggle", {"name": ["aihot"], "state": ["nuke"]})
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "E_USAGE")

    def test_toggle_writes_through_the_cli_and_respects_base_hash(self) -> None:
        """停用走 CLI 的原子写；哈希过期 ⇒ E_CONFLICT（页面渲染后被别人改过）。"""
        stale = view.build(self.news)["sources_hash"]
        out = self._dispatch("source-toggle",
                             {"name": ["aihot"], "state": ["disable"], "base_hash": [stale]})
        self.assertTrue(out["ok"], out)
        self.assertIn("enabled: false", (self.news / "sources.yaml").read_text(encoding="utf-8"))
        again = self._dispatch("source-toggle",
                               {"name": ["aihot"], "state": ["enable"], "base_hash": [stale]})
        self.assertFalse(again["ok"])
        self.assertEqual(again["error"]["code"], "E_CONFLICT")

    def test_run_reaches_the_pipeline_and_reports_the_cli_error(self) -> None:
        """不带网络也能证明「按钮 = CLI」：未知信源由 CLI 自己报错。"""
        out = self._dispatch("run", {"target": ["source:nosuch"], "dry": ["1"]})
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "E_CONFIG")
        self.assertIn("nosuch", out["error"]["message"])
        self.assertEqual(out["action"]["cli"], "run --source nosuch --dry --json")

    def test_invalid_slot_becomes_a_usage_envelope(self) -> None:
        out = self._dispatch("run", {"target": ["xyz"]})
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "E_USAGE")

    def test_queue_ack_consumes_the_favorite_event(self) -> None:
        ev = events.append(self.news, "favorite", source="aihot",
                           payload={"item_id": "a01", "title": "好文", "url": "https://e/x"})
        self.assertEqual(len(events.queue(self.news)), 1)
        out = self._dispatch("queue-ack", {"event_id": [ev["id"]], "note": ["已入库"]})
        self.assertTrue(out["ok"], out)
        self.assertEqual(events.queue(self.news), [])
        # 幂等：再点一次是 already，不是错
        again = self._dispatch("queue-ack", {"event_id": [ev["id"]]})
        self.assertTrue(again["ok"], again)

    def test_events_ack_surfaces_unknown_ids_instead_of_pretending(self) -> None:
        out = self._dispatch("events-ack", {"ids": ["ev_bogus"]})
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "E_NOT_FOUND")
        self.assertIn("ev_bogus", json.dumps(out["error"].get("details") or {}, ensure_ascii=False))


class ActionGateTests(unittest.TestCase):
    """三道门禁 + 只读契约不被污染。"""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.news = Path(self._tmp.name) / "news"
        self.news.mkdir(parents=True)
        (self.news / "sources.yaml").write_text(SOURCES_YAML, encoding="utf-8")
        self.service_cfg = {"http": {"host": "127.0.0.1", "port": 0, "path": "/feishu/events"}}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _serve(self, *, view_actions: bool) -> str:
        server, _path = inbound.start_http(service_cfg=self.service_cfg, creds=None,
                                          news_dir=self.news, accept_events=True,
                                          view_actions=view_actions, action_token=TOKEN)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def _post(self, base: str, action: str, form: dict[str, str],
              headers: dict[str, str] | None = None) -> tuple[int, str]:
        data = urllib.parse.urlencode(form).encode("utf-8")
        request = urllib.request.Request(f"{base}/api/actions/{action}", data=data, method="POST")
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=5) as resp:
                return resp.status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8")

    def test_actions_off_means_404_and_no_forms_in_the_page(self) -> None:
        base = self._serve(view_actions=False)
        code, _body = self._post(base, "run", {"token": TOKEN, "target": "poll"})
        self.assertEqual(code, 404)
        with urllib.request.urlopen(f"{base}/", timeout=5) as resp:
            page = resp.read().decode("utf-8")
        self.assertNotIn("<form", page)
        self.assertNotIn(TOKEN, page)

    def test_token_is_required_and_rendered_into_the_page(self) -> None:
        base = self._serve(view_actions=True)
        code, body = self._post(base, "run", {"target": "poll", "dry": "1"})
        self.assertEqual(code, 403)
        self.assertIn("E_TOKEN", body)
        code, body = self._post(base, "run", {"token": "wrong", "target": "poll"})
        self.assertEqual(code, 403)
        self.assertIn("E_TOKEN", body)
        with urllib.request.urlopen(f"{base}/ops", timeout=5) as resp:
            page = resp.read().decode("utf-8")
        self.assertIn(TOKEN, page)                    # 令牌嵌进表单
        self.assertIn('action="/api/actions/run"', page)
        self.assertIn('name="dry" value="1" checked', page)   # 默认试运行
        with urllib.request.urlopen(f"{base}/", timeout=5) as resp:
            cockpit = resp.read().decode("utf-8")
        self.assertNotIn("<form", cockpit)            # 驾驶舱不该有写操作

    def test_cross_origin_posts_are_refused(self) -> None:
        base = self._serve(view_actions=True)
        code, body = self._post(base, "run", {"token": TOKEN, "target": "poll", "dry": "1"},
                                headers={"Origin": "https://evil.example"})
        self.assertEqual(code, 403)
        self.assertIn("E_ORIGIN", body)

    def test_same_origin_post_redirects_and_the_banner_shows_the_envelope(self) -> None:
        base = self._serve(view_actions=True)
        origin = base                                   # 同源（netloc == Host）
        request = urllib.request.Request(
            f"{base}/api/actions/run",
            data=urllib.parse.urlencode({"token": TOKEN, "target": "source:nosuch",
                                         "dry": "1"}).encode("utf-8"),
            method="POST")
        request.add_header("Origin", origin)
        opener = urllib.request.build_opener(NoRedirect)
        try:
            with opener.open(request, timeout=5) as resp:
                self.fail(f"写操作应当 303 重定向，实际 {resp.status}")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 303)
            location = exc.headers["Location"]
        self.assertTrue(location.startswith("/?r="), location)
        with urllib.request.urlopen(f"{base}{location}", timeout=5) as resp:
            page = resp.read().decode("utf-8")
        self.assertIn("⛔", page)                        # 失败横幅
        self.assertIn("nosuch", page)                   # CLI 的原始错误信息
        self.assertIn("run --source nosuch --dry --json", page)   # 复现命令

    def test_the_read_only_contract_never_carries_the_token(self) -> None:
        base = self._serve(view_actions=True)
        with urllib.request.urlopen(f"{base}/view", timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        self.assertNotIn("token", json.dumps(payload))
        self.assertIn("sources_hash", payload)          # 面板要用的并发令牌（不是秘密）


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """别自动跟 303 —— 测试要自己看 Location。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


if __name__ == "__main__":
    unittest.main()
