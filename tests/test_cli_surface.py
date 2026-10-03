"""CLI 表面契约测试 —— Agent 看到的那一层。

守三件事：

1. **风格判定是确定性的**：子命令 vs 旧 flag 不会互相误判（`--card-preview status` 这种
   「位置参数长得像子命令」的旧调用必须继续按旧语义跑）；
2. **每个命令无论成败都吐一个结构化结果**，退出码与错误码登记表一致；
3. **旧 flag 路径行为不变**（4 个 cron wrapper 与插件薄壳靠它）。
"""

from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
for _p in (str(SRC_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from newspipe import cli, events, result  # noqa: E402

from test_contract import SOURCES_YAML  # noqa: E402


def _run(argv: list[str]) -> tuple[int, str]:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = cli.main(argv)
    return code, buffer.getvalue()


def _json_of(out: str) -> dict:
    return json.loads(out)


class DispatchTests(unittest.TestCase):
    def test_subcommand_is_recognized_anywhere(self) -> None:
        self.assertEqual(cli._subcommand_of(["status", "--json"]), "status")
        self.assertEqual(cli._subcommand_of(["--json", "status"]), "status")
        self.assertEqual(cli._subcommand_of(["--news-dir", "/tmp/x", "doctor"]), "doctor")
        self.assertEqual(cli._subcommand_of(["source", "list"]), "source")

    def test_legacy_calls_never_misroute(self) -> None:
        # 位置参数恰好叫 status ⇒ 仍然是旧语义（--card-preview 的批次名可以是任何字符串）
        self.assertIsNone(cli._subcommand_of(["--card-preview", "status"]))
        self.assertIsNone(cli._subcommand_of(["--card", '{"domain":"news"}']))
        self.assertIsNone(cli._subcommand_of(["--slot", "am"]))
        self.assertIsNone(cli._subcommand_of(["--source", "status"]))   # 源名叫 status
        self.assertIsNone(cli._subcommand_of(["--serve"]))
        self.assertIsNone(cli._subcommand_of([]))

    def test_subcommand_keeps_its_own_flags(self) -> None:
        # `serve --once --dry` 必须走子命令：--once/--dry 两种风格都有
        self.assertEqual(cli._subcommand_of(["serve", "--once", "--dry"]), "serve")
        self.assertEqual(cli._subcommand_of(["run", "--slot", "am"]), "run")
        self.assertEqual(cli._subcommand_of(["--news-dir", "/tmp/x", "run", "--poll"]), "run")

    def test_every_contract_entry_names_a_real_subcommand(self) -> None:
        for entry in cli.CONTRACT:
            head = entry["name"].split()[0]
            self.assertIn(head, cli.SUBCOMMANDS, entry["name"])
            self.assertIn("usage", entry)
            self.assertTrue(entry["summary"])
            self.assertIn("writes", entry)

    def test_contract_lists_every_declared_error_code(self) -> None:
        _code, out = _run(["api", "describe", "--json"])
        payload = _json_of(out)
        self.assertEqual(set(payload["data"]["error_codes"]), set(result.ERROR_EXIT))
        self.assertEqual(payload["data"]["contract_version"], cli.CONTRACT_VERSION)


class SubcommandSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.news = Path(self._tmp.name) / "info" / "news"
        self.news.mkdir(parents=True)
        (self.news / "sources.yaml").write_text(SOURCES_YAML, encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _cli(self, *args: str) -> tuple[int, dict]:
        code, out = _run(["--news-dir", str(self.news), *args, "--json"])
        return code, _json_of(out)

    def test_doctor_reports_a_broken_config_as_a_failure(self) -> None:
        (self.news / "sources.yaml").write_text("chat: oc_x\nsources: {s: {adapter: nope}}\n",
                                                encoding="utf-8")
        code, payload = self._cli("doctor")
        self.assertNotEqual(code, 0)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "E_CONFIG")
        self.assertIn("newspipe", payload["error"]["hint"])

    def test_doctor_is_green_on_a_healthy_registry(self) -> None:
        code, payload = self._cli("doctor")
        self.assertEqual(code, 0, payload)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["sources"]["total"], 2)
        self.assertEqual(payload["data"]["card_budget"]["limit"], 200)
        self.assertGreater(payload["data"]["card_budget"]["headroom"], 0)

    def test_status_and_list_sources_are_structured(self) -> None:
        code, payload = self._cli("status")
        self.assertEqual(code, 0, payload)
        self.assertEqual([s["source"] for s in payload["data"]["sources"]], ["aihot", "hn"])
        self.assertIn("events", payload["data"])
        code, payload = self._cli("list-sources")
        self.assertEqual(code, 0)
        self.assertEqual(payload["data"]["sources"][0]["adapter"], "aihot")

    def test_source_set_rejects_and_keeps_the_file(self) -> None:
        before = (self.news / "sources.yaml").read_text(encoding="utf-8")
        code, payload = self._cli("source", "set", "bogus", "--from-json", '{"adapter":"nope"}')
        self.assertEqual(code, result.EXIT_VALIDATION)
        self.assertEqual(payload["error"]["code"], "E_VALIDATION")
        self.assertEqual((self.news / "sources.yaml").read_text(encoding="utf-8"), before)

    def test_source_set_dry_run_then_real_write(self) -> None:
        body = '{"adapter":"aihot","fetch":{"trigger":"slot","slots":["am"]}}'
        code, payload = self._cli("source", "set", "trial", "--from-json", body, "--dry-run")
        self.assertEqual(code, 0, payload)
        self.assertFalse(payload["changed"])                     # 演练：没改东西
        self.assertNotIn("trial", (self.news / "sources.yaml").read_text(encoding="utf-8"))
        code, payload = self._cli("source", "set", "trial", "--from-json", body)
        self.assertEqual(code, 0, payload)
        self.assertTrue(payload["changed"])
        self.assertIn("trial", (self.news / "sources.yaml").read_text(encoding="utf-8"))
        # 拿到的 hash 可以直接用于下一次乐观并发写入
        code, payload = self._cli("source", "show", "trial")
        self.assertEqual(code, 0)
        self.assertTrue(payload["data"]["hash"])

    def test_source_set_conflict_is_reported(self) -> None:
        body = '{"adapter":"aihot","fetch":{"trigger":"slot","slots":["am"]}}'
        code, payload = self._cli("source", "set", "trial", "--from-json", body,
                                  "--base-hash", "deadbeef")
        self.assertEqual(code, result.EXIT_VALIDATION)
        self.assertEqual(payload["error"]["code"], "E_CONFLICT")
        self.assertIn("source list", payload["error"]["hint"])

    def test_source_show_missing_is_not_found(self) -> None:
        code, payload = self._cli("source", "show", "ghost")
        self.assertEqual(code, result.EXIT_VALIDATION)
        self.assertEqual(payload["error"]["code"], "E_NOT_FOUND")

    def test_enable_disable_and_remove(self) -> None:
        code, payload = self._cli("source", "disable", "hn")
        self.assertEqual(code, 0, payload)
        self.assertTrue(payload["changed"])
        code, payload = self._cli("source", "show", "hn")
        self.assertFalse(payload["data"]["source"]["enabled"])
        code, payload = self._cli("source", "remove", "hn")
        self.assertEqual(code, 0, payload)
        code, payload = self._cli("source", "list")
        self.assertEqual([s["source"] for s in payload["data"]["sources"]], ["aihot"])

    def test_hooks_surface(self) -> None:
        code, payload = self._cli("hooks", "add", "--from-json",
                                  '{"id":"myapp.wiki","label":"⭐","action":"myapp.wiki",'
                                  '"handler":"/bin/echo"}')
        self.assertEqual(code, 0, payload)
        self.assertTrue(payload["changed"])
        code, payload = self._cli("hooks", "list")
        self.assertEqual([h["id"] for h in payload["data"]["hooks"]], ["myapp.wiki"])
        self.assertEqual(payload["data"]["problems"], [])
        code, payload = self._cli("hooks", "remove", "myapp.wiki")
        self.assertEqual(code, 0, payload)
        code, payload = self._cli("hooks", "add", "--from-json",
                                  '{"id":"x.y","label":"x","action":"x.y","handler":"/nope"}')
        self.assertEqual(code, result.EXIT_VALIDATION)
        self.assertEqual(payload["error"]["code"], "E_VALIDATION")

    def test_events_and_queue_surface(self) -> None:
        event = events.append(self.news, "favorite", payload={"item_id": "n01"},
                              source="aihot", slot="am")
        code, payload = self._cli("events", "list", "--unconsumed")
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["data"]["count"], 1)
        self.assertTrue(payload["data"]["events"][0]["id"])
        code, payload = self._cli("queue", "list")
        self.assertEqual([e["id"] for e in payload["data"]["items"]], [event["id"]])
        self.assertTrue(payload["next"])                          # 给出下一步命令
        code, payload = self._cli("queue", "ack", event["id"], "--note", "wiki/ai/x.md")
        self.assertEqual(code, 0, payload)
        self.assertTrue(payload["changed"])
        code, payload = self._cli("queue", "list")
        self.assertEqual(payload["data"]["count"], 0)
        code, payload = self._cli("queue", "ack", "ev_ghost")
        self.assertEqual(code, result.EXIT_VALIDATION)
        self.assertEqual(payload["error"]["code"], "E_NOT_FOUND")

    def test_events_ack_reports_unknown_ids(self) -> None:
        code, payload = self._cli("events", "ack", "ev_ghost")
        self.assertEqual(code, result.EXIT_VALIDATION)
        self.assertEqual(payload["error"]["details"]["unknown"], ["ev_ghost"])

    def test_run_requires_a_mode(self) -> None:
        code, payload = self._cli("run")
        self.assertEqual(code, result.EXIT_USAGE)
        self.assertEqual(payload["error"]["code"], "E_USAGE")
        self.assertIn("--slot", payload["error"]["message"])

    def test_json_flag_works_on_both_sides_of_the_subcommand(self) -> None:
        for argv in (["--news-dir", str(self.news), "status", "--json"],
                     ["--json", "--news-dir", str(self.news), "status"]):
            code, out = _run(argv)
            self.assertEqual(code, 0)
            self.assertTrue(_json_of(out)["ok"], argv)

    def test_human_mode_prints_a_table_not_a_stack(self) -> None:
        code, out = _run(["--news-dir", str(self.news), "status"])
        self.assertEqual(code, 0)
        self.assertIn("aihot", out)
        self.assertNotIn("Traceback", out)


class LegacyPathTests(unittest.TestCase):
    """旧 flag 路径必须行为不变：4 个 cron wrapper 与插件薄壳都靠它。"""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.news = Path(self._tmp.name) / "info" / "news"
        self.news.mkdir(parents=True)
        (self.news / "sources.yaml").write_text(SOURCES_YAML, encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_status_and_list_sources_still_work(self) -> None:
        for flag in ("--status", "--list-sources"):
            code, out = _run(["--news-dir", str(self.news), flag])
            self.assertEqual(code, 0, out)
            self.assertIn("aihot", out)

    def test_legacy_success_path_prints_nothing_for_unknown_domain(self) -> None:
        """插件薄壳走 --card：非本应用/未知动作必须**静默**（不在聊天里插消息）。"""
        code, out = _run(["--news-dir", str(self.news), "--card", '{"domain":"other"}'])
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_legacy_missing_mode_is_a_usage_error(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            _run(["--news-dir", str(self.news)])
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
