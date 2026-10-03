"""防腐层契约测试 —— 守住「无论成功还是失败，返回结果都明确」这条硬要求。

守四件事：

1. **结果信封的不变量**：`ok=True` ⇔ `error is None`；`ok=False` ⇔ `data is None`；每个错误码
   都在退出码登记表里；未捕获异常必须变成 `E_INTERNAL` 而不是 traceback。
2. **事件流的读侧语义**：`acked` / `already` / `unknown` 必须分得清——agent 靠这个判断要不要重试。
3. **卡片 hook 的 fail-soft 与核心锁定**：hook 挂了核心按钮照常，核心动作不许被 hook 覆盖。
4. **配置写入层**：先校验后落盘、`--dry-run` 不落盘、乐观并发、**块外逐字节不动**。
"""

from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
for _p in (str(SRC_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from newspipe import edit, events, hooks, render, result, yamlblocks  # noqa: E402
from newspipe.errors import ConfigError  # noqa: E402

#: 人工维护风格的配置：注释与顺序都必须活过每一次写入。
SOURCES_YAML = """\
# 人工维护的注释：写入层不许把它弄丢
chat: oc_test_chat
slots:
  am: '08:15'
  noon: '12:35'
  pm: '18:35'

sources:
  # aihot 是主力源
  aihot:
    adapter: aihot
    enabled: true
    fetch: {trigger: slot, slots: [am]}
    deliver:
      form: card
      priority: high
  hn:
    adapter: hn
    fetch: {trigger: slot, slots: [am, pm]}
    filter:
      include_keywords: [ai, llm]
"""


def _write_news(tmp: Path, *, sources: str = SOURCES_YAML) -> Path:
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "sources.yaml").write_text(sources, encoding="utf-8")
    return tmp


# ------------------------------------------------------------------ 结果信封
class ResultEnvelopeTests(unittest.TestCase):
    def test_ok_has_no_error_and_fail_has_no_data(self) -> None:
        good = result.ok("x", {"a": 1})
        self.assertTrue(good.ok)
        self.assertIsNone(good.error)
        bad = result.fail("x", "E_USAGE", "参数错")
        self.assertFalse(bad.ok)
        self.assertIsNone(bad.data)
        self.assertEqual(bad.error["code"], "E_USAGE")

    def test_every_error_code_is_registered_with_a_documented_exit(self) -> None:
        self.assertTrue(result.ERROR_EXIT, "登记表不能为空")
        for code, code_exit in result.ERROR_EXIT.items():
            self.assertTrue(code.startswith("E_"), code)
            self.assertIn(code_exit, (1, 2, 3, 4), f"{code} 的退出码 {code_exit} 不在契约里")
        # 契约里承诺的 5 个退出码（0 由 ok 承担）
        self.assertEqual(set(result.ERROR_EXIT.values()), {1, 2, 3, 4})

    def test_unknown_code_falls_back_to_runtime_exit(self) -> None:
        self.assertEqual(result.fail("x", "E_MADE_UP", "?").exit_code, result.EXIT_RUNTIME)

    def test_json_output_is_exactly_one_object(self) -> None:
        buffer = io.StringIO()
        result.emit(result.ok("status", {"n": 1}), json_out=True, out=buffer)
        parsed = json.loads(buffer.getvalue())          # 整段必须能被一次解析
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["command"], "status")
        self.assertEqual(parsed["data"], {"n": 1})
        self.assertEqual(parsed["contract_version"], result.Result("c", True).contract_version)

    def test_guard_turns_unexpected_exception_into_internal(self) -> None:
        buffer = io.StringIO()

        def boom() -> result.Result:
            raise ZeroDivisionError("division by zero")

        code = result.guard("x", boom, json_out=True, out=buffer)
        self.assertEqual(code, result.EXIT_RUNTIME)
        payload = json.loads(buffer.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "E_INTERNAL")
        self.assertIn("ZeroDivisionError", payload["error"]["message"])
        self.assertNotIn("Traceback", buffer.getvalue())     # 绝不吐堆栈

    def test_guard_classifies_known_error_types(self) -> None:
        for exc, code in ((ConfigError("坏配置"), "E_CONFIG"),
                          (__import__("newspipe.errors", fromlist=["x"]).DeliveryError("发不出去"),
                           "E_DELIVERY")):
            buffer = io.StringIO()
            result.guard("x", lambda e=exc: (_ for _ in ()).throw(e), json_out=True, out=buffer)
            self.assertEqual(json.loads(buffer.getvalue())["error"]["code"], code)

    def test_printed_result_is_silent_in_human_mode_but_loud_when_failed(self) -> None:
        """命令体自己打了表格 ⇒ 人读模式不再套信封；但失败**一定**要说出来。"""
        buffer = io.StringIO()
        result.emit(result.ok("status", {"n": 1}, printed=True), json_out=False, out=buffer)
        self.assertEqual(buffer.getvalue(), "")
        buffer = io.StringIO()
        result.emit(result.fail("status", "E_CONFIG", "坏了"), json_out=False, out=buffer)
        self.assertIn("E_CONFIG", buffer.getvalue())
        buffer = io.StringIO()
        result.emit(result.ok("status", {"n": 1}, printed=True), json_out=True, out=buffer)
        self.assertTrue(json.loads(buffer.getvalue())["ok"])   # --json 不受影响


# ------------------------------------------------------------------ 事件流
class EventStreamTests(unittest.TestCase):
    def test_append_requires_a_registered_type(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                events.append(Path(tmp), "not_a_type")
            event = events.append(Path(tmp), "delivered", payload={"n": 3}, source="aihot")
            self.assertTrue(event["id"].startswith("ev_"))
            self.assertEqual(event["source"], "aihot")

    def test_append_then_list_roundtrip(self) -> None:
        with TemporaryDirectory() as tmp:
            news = Path(tmp)
            first = events.append(news, "delivered", payload={"a": 1})
            second = events.append(news, "clicked", payload={"b": 2})
            got = events.list_events(news, days=1)
            self.assertEqual([e["id"] for e in got], [first["id"], second["id"]])
            self.assertEqual(events.list_events(news, days=1, types=["clicked"]), [second])
            self.assertEqual(events.list_events(news, days=1, limit=1), [first])

    def test_ack_distinguishes_acked_already_and_unknown(self) -> None:
        with TemporaryDirectory() as tmp:
            news = Path(tmp)
            event = events.append(news, "delivered")
            out = events.ack(news, [event["id"]], by="hermes")
            self.assertEqual(out["acked"], [event["id"]])
            self.assertEqual(out["already"], [])
            again = events.ack(news, [event["id"]], by="hermes")
            self.assertEqual(again["already"], [event["id"]])      # 幂等，不算错
            self.assertEqual(again["acked"], [])
            missing = events.ack(news, ["ev_nope"], by="hermes")
            self.assertEqual(missing["unknown"], ["ev_nope"])      # 必须让 agent 知道

    def test_queue_is_only_unacked_favorites(self) -> None:
        with TemporaryDirectory() as tmp:
            news = Path(tmp)
            fav = events.append(news, "favorite", payload={"item_id": "n01"})
            events.append(news, "clicked", payload={"item_id": "n02"})
            queued = events.queue(news)
            self.assertEqual([e["id"] for e in queued], [fav["id"]])
            events.ack(news, [fav["id"]], by="hermes", note="wiki/x.md")
            self.assertEqual(events.queue(news), [])
            # 确认记录里带上了入库路径（人确认过才写）
            ack_line = json.loads(events.acks_path(news).read_text(encoding="utf-8").strip())
            self.assertEqual(ack_line["note"], "wiki/x.md")

    def test_unconsumed_filter_uses_acks(self) -> None:
        with TemporaryDirectory() as tmp:
            news = Path(tmp)
            a = events.append(news, "delivered")
            b = events.append(news, "delivered")
            events.ack(news, [a["id"]], by="hermes")
            left = events.list_events(news, days=1, unconsumed=True)
            self.assertEqual([e["id"] for e in left], [b["id"]])
            self.assertEqual(events.stats(news, days=1)["unconsumed"], 1)

    def test_prune_only_removes_expired_days(self) -> None:
        with TemporaryDirectory() as tmp:
            news = Path(tmp)
            old = events.append(news, "delivered", ts=datetime.now() - timedelta(days=60))
            new = events.append(news, "delivered")
            out = events.prune(news, keep_days=30)
            self.assertEqual(out["removed"], [f"{old['ts'][:10]}.jsonl"])
            self.assertEqual([e["id"] for e in events.list_events(news, days=90)], [new["id"]])


# ------------------------------------------------------------------ hook 框架
class HookFrameworkTests(unittest.TestCase):
    def _load(self, text: str, tmp: Path) -> hooks.HookSet:
        path = tmp / "hooks.yaml"
        path.write_text(text, encoding="utf-8")
        return hooks.load_from(path, text=text)

    def test_missing_file_is_empty_not_an_error(self) -> None:
        with TemporaryDirectory() as tmp:
            loaded = hooks.load(Path(tmp) / "absent.yaml")
            self.assertEqual(loaded.hooks, [])
            self.assertEqual(loaded.problems, [])
            self.assertFalse(loaded)                     # 没 hook ⇒ 全流程零开销

    def test_broken_declaration_is_reported_and_skipped(self) -> None:
        with TemporaryDirectory() as tmp:
            loaded = self._load(
                "hooks:\n"
                "  - {id: bad.handler, label: x, action: bad.x, handler: /nonexistent/h.sh}\n"
                "  - {id: no.namespace, label: y, action: plain, handler: /bin/echo}\n"
                "  - {id: core.clash, label: z, action: open_detail, handler: /bin/echo}\n",
                Path(tmp))
            self.assertEqual(loaded.hooks, [])
            self.assertEqual(len(loaded.problems), 3)
            self.assertTrue(all("bad.handler" in p or "no.namespace" in p or "core.clash" in p
                                for p in loaded.problems))

    def test_valid_hook_is_loaded_and_scoped(self) -> None:
        with TemporaryDirectory() as tmp:
            loaded = self._load(
                "hooks:\n"
                "  - {id: hermes.wiki, label: '⭐ 入库', action: hermes.wiki, handler: /bin/echo}\n"
                "  - {id: hermes.list, label: '列表页', action: hermes.list, scope: list,"
                " handler: /bin/echo}\n",
                Path(tmp))
            self.assertEqual(loaded.problems, [])
            self.assertEqual([h.id for h in loaded.buttons("detail")], ["hermes.wiki"])
            self.assertEqual([h.id for h in loaded.buttons("list")], ["hermes.list"])
            self.assertIn("hermes.wiki", loaded.actions())
            self.assertIsNotNone(loaded.by_action("hermes.wiki"))

    def test_run_handler_receives_payload_on_stdin(self) -> None:
        with TemporaryDirectory() as tmp:
            loaded = self._load(
                "hooks:\n  - {id: echo, label: e, action: hermes.echo, handler: /bin/cat}\n",
                Path(tmp))
            out = hooks.run_handler(loaded.hooks[0], {"item_id": "n01", "action": "hermes.echo"})
            self.assertTrue(out["ok"], out)
            self.assertIn("n01", out["stdout"])

    def test_run_handler_failure_is_structured_not_raised(self) -> None:
        """handler 自己失败 ⇒ 结构化返回，**不抛异常**（调用方只记账，核心按钮照常）。"""
        with TemporaryDirectory() as tmp:
            script = Path(tmp) / "boom.sh"
            script.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
            script.chmod(0o755)
            loaded = self._load(
                f"hooks:\n  - {{id: hermes.no, label: n, action: hermes.no, handler: {script}}}\n",
                Path(tmp))
            self.assertEqual(loaded.problems, [], loaded.problems)
            out = hooks.run_handler(loaded.hooks[0], {})
            self.assertFalse(out["ok"])
            self.assertEqual(out["exit_code"], 7)

    def test_handler_may_be_a_path_command(self) -> None:
        """`python3 x.py` 这种写法必须被接受——只查 `Path(argv[0]).exists()` 会误判。"""
        with TemporaryDirectory() as tmp:
            loaded = self._load(
                "hooks:\n  - {id: hermes.py, label: p, action: hermes.py,"
                " handler: \"python3 -c pass\"}\n",
                Path(tmp))
            self.assertEqual(loaded.problems, [], loaded.problems)
            self.assertTrue(hooks.run_handler(loaded.hooks[0], {})["ok"])

    def test_load_from_rejects_malformed_yaml(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ConfigError):
                self._load("hooks: [\n", Path(tmp))


# ------------------------------------------------------------------ 文本级块编辑
class YamlBlocksTests(unittest.TestCase):
    def test_set_block_keeps_everything_else_byte_for_byte(self) -> None:
        new_body = "  aihot:\n    adapter: aihot\n    fetch: {trigger: poll, interval_min: 5}\n"
        out = yamlblocks.set_block(SOURCES_YAML, "sources", "aihot", new_body)
        # 注释还在、别的源一字不动
        self.assertIn("# 人工维护的注释", out)
        self.assertIn("# aihot 是主力源", out)
        self.assertIn("  hn:\n    adapter: hn", out)
        self.assertIn("trigger: poll", out)
        self.assertNotIn("priority: high", out)          # 只有 aihot 的块被替换

    def test_nested_indentation_survives_the_round_trip(self) -> None:
        """回归守卫：块替换必须做**相对**缩进平移，不能把嵌套压平。"""
        import yaml

        out = yamlblocks.set_block(
            SOURCES_YAML, "sources", "aihot",
            "  aihot:\n    adapter: aihot\n    deliver:\n      form: card\n"
            "      budget: {min_gap_min: 10}\n")
        parsed = yaml.safe_load(out)
        self.assertEqual(parsed["sources"]["aihot"]["deliver"]["form"], "card")
        self.assertEqual(parsed["sources"]["aihot"]["deliver"]["budget"]["min_gap_min"], 10)
        self.assertEqual(parsed["sources"]["hn"]["adapter"], "hn")   # 邻居没被牵连

    def test_append_and_remove(self) -> None:
        out = yamlblocks.append_block(SOURCES_YAML, "sources", "new_src",
                                      "  new_src:\n    adapter: aihot\n")
        self.assertIn("new_src", yamlblocks.block_names(out, "sources"))
        self.assertEqual(yamlblocks.block_names(out, "sources"), ["aihot", "hn", "new_src"])
        back = yamlblocks.remove_block(out, "sources", "new_src")
        self.assertEqual(yamlblocks.block_names(back, "sources"), ["aihot", "hn"])
        self.assertIn("# aihot 是主力源", back)

    def test_missing_target_raises_key_error(self) -> None:
        with self.assertRaises(KeyError):
            yamlblocks.set_block(SOURCES_YAML, "sources", "absent", "  absent:\n")
        with self.assertRaises(KeyError):
            yamlblocks.remove_block(SOURCES_YAML, "sources", "absent")
        with self.assertRaises(KeyError):
            yamlblocks.set_block(SOURCES_YAML, "nope", "aihot", "  aihot:\n")

    def test_get_block_returns_only_that_block(self) -> None:
        block = yamlblocks.get_block(SOURCES_YAML, "sources", "hn")
        self.assertIn("adapter: hn", block)
        self.assertNotIn("aihot", block)


# ------------------------------------------------------------------ 写入层
class EditLayerTests(unittest.TestCase):
    def _news(self, tmp: Path) -> Path:
        return _write_news(Path(tmp) / "info" / "news")

    def test_invalid_source_is_rejected_and_file_untouched(self) -> None:
        with TemporaryDirectory() as tmp:
            news = self._news(Path(tmp))
            before = (news / "sources.yaml").read_text(encoding="utf-8")
            out = edit.set_source(news, "bogus", {"adapter": "nope"})
            self.assertNotIn("ok", out)
            self.assertEqual(out["code"], "E_VALIDATION")
            self.assertEqual((news / "sources.yaml").read_text(encoding="utf-8"), before)

    def test_dry_run_does_not_write(self) -> None:
        with TemporaryDirectory() as tmp:
            news = self._news(Path(tmp))
            before = (news / "sources.yaml").read_text(encoding="utf-8")
            out = edit.set_source(news, "trial", {"adapter": "aihot",
                                                 "fetch": {"trigger": "slot", "slots": ["am"]}},
                                  dry_run=True)
            self.assertTrue(out["ok"])
            self.assertFalse(out["changed"])
            self.assertEqual((news / "sources.yaml").read_text(encoding="utf-8"), before)

    def test_base_hash_mismatch_is_a_conflict(self) -> None:
        with TemporaryDirectory() as tmp:
            news = self._news(Path(tmp))
            out = edit.set_source(news, "trial", {"adapter": "aihot",
                                                 "fetch": {"trigger": "slot", "slots": ["am"]}},
                                  base_hash="deadbeef")
            self.assertEqual(out["code"], "E_CONFLICT")
            self.assertIn("sources.yaml", out["message"])          # 消息里给出现在的哈希
            self.assertIn("deadbeef", out["message"])
            self.assertNotIn("trial", (news / "sources.yaml").read_text(encoding="utf-8"))

    def test_set_source_happy_path_keeps_comments(self) -> None:
        with TemporaryDirectory() as tmp:
            news = self._news(Path(tmp))
            out = edit.set_source(news, "trial", {"adapter": "aihot",
                                                 "fetch": {"trigger": "slot", "slots": ["am"]}})
            self.assertTrue(out["ok"], out)
            self.assertTrue(out["changed"])
            text = (news / "sources.yaml").read_text(encoding="utf-8")
            self.assertIn("# 人工维护的注释", text)
            self.assertIn("trial", text)
            self.assertEqual(yamlblocks.block_names(text, "sources"), ["aihot", "hn", "trial"])

    def test_enable_disable_only_flips_the_flag(self) -> None:
        with TemporaryDirectory() as tmp:
            news = self._news(Path(tmp))
            edit.set_source_enabled(news, "hn", False)
            text = (news / "sources.yaml").read_text(encoding="utf-8")
            self.assertIn("enabled: false", text)
            self.assertIn("include_keywords: [ai, llm]", text)      # 其他字段原样
            self.assertIn("# aihot 是主力源", text)                  # 邻居块没被动

    def test_remove_source_and_missing_target(self) -> None:
        with TemporaryDirectory() as tmp:
            news = self._news(Path(tmp))
            self.assertTrue(edit.remove_source(news, "hn")["ok"])
            self.assertEqual(yamlblocks.block_names(
                (news / "sources.yaml").read_text(encoding="utf-8"), "sources"), ["aihot"])
            missing = edit.remove_source(news, "hn")
            self.assertEqual(missing["code"], "E_NOT_FOUND")

    def test_hooks_add_rejects_a_dead_handler(self) -> None:
        with TemporaryDirectory() as tmp:
            news = self._news(Path(tmp))
            out = edit.set_hook(news, {"id": "hermes.wiki", "label": "⭐", "action": "hermes.wiki",
                                       "handler": "/nonexistent/h.sh"})
            self.assertEqual(out["code"], "E_VALIDATION")
            self.assertFalse((news / "hooks.yaml").is_file())

    def test_hooks_add_list_remove(self) -> None:
        with TemporaryDirectory() as tmp:
            news = self._news(Path(tmp))
            out = edit.set_hook(news, {"id": "hermes.wiki", "label": "⭐ 入库",
                                       "action": "hermes.wiki", "handler": "/bin/echo"})
            self.assertTrue(out["ok"], out)
            loaded = hooks.load(news / "hooks.yaml")
            self.assertEqual([h.id for h in loaded.hooks], ["hermes.wiki"])
            self.assertTrue(edit.remove_hook(news, "hermes.wiki")["ok"])
            self.assertEqual(hooks.load(news / "hooks.yaml").hooks, [])

    def test_file_hash_is_stable_and_changes_on_write(self) -> None:
        with TemporaryDirectory() as tmp:
            news = self._news(Path(tmp))
            first = edit.file_hash(news / "sources.yaml")
            self.assertEqual(first, edit.file_hash(news / "sources.yaml"))
            edit.set_source(news, "trial", {"adapter": "aihot",
                                            "fetch": {"trigger": "slot", "slots": ["am"]}})
            self.assertNotEqual(first, edit.file_hash(news / "sources.yaml"))
            self.assertEqual(edit.file_hash(news / "absent.yaml"), "")


# ------------------------------------------------------------------ hook 渲染
class HookRenderTests(unittest.TestCase):
    """hook 按钮真的出现在卡片里，且**不会**把列表页撑爆元素预算。"""

    def _batch(self, n: int = 3) -> dict:
        items = [{"ext_id": f"e{i}", "title": f"Title {i}", "url": f"https://e.com/{i}",
                  "original_url": "", "source": "Test", "summary": "", "category": "",
                  "score": 100 - i, "pub_ts": 0} for i in range(1, n + 1)]
        return {"digest": "2026-10-03", "source": "s", "slot": "am", "title": "测试卡",
                "items": items, "card_id": "c1", "message_id": "om1", "seq": 1,
                "view": "list", "form": "card"}

    def _hooks(self, tmp: str, scope: str = "detail") -> hooks.HookSet:
        text = ("hooks:\n  - {id: hermes.wiki, label: '⭐ 入库', action: hermes.wiki,"
                " scope: " + scope + ", handler: /bin/echo}\n")
        path = Path(tmp) / "hooks.yaml"
        path.write_text(text, encoding="utf-8")
        return hooks.load_from(path, text=text)

    def test_detail_card_shows_the_hook_button(self) -> None:
        with TemporaryDirectory() as tmp:
            hs = self._hooks(tmp)
            batch = self._batch()
            card = render.detail_card(batch, batch["items"][0], hs)
            blob = json.dumps(card, ensure_ascii=False)
            self.assertIn("hermes.wiki", blob)          # 点击时回传的 action
            self.assertIn("⭐ 入库", blob)               # 按钮上显示的字

    def test_list_page_has_no_hook_button_by_default(self) -> None:
        with TemporaryDirectory() as tmp:
            card = render.list_card(self._batch(), self._hooks(tmp))    # scope: detail
            self.assertNotIn("hermes.wiki", json.dumps(card, ensure_ascii=False))

    def test_no_hooks_means_identical_output(self) -> None:
        """没配 hook 时逐字节一致（默认路径零变化）。"""
        plain = json.dumps(render.render(self._batch()), ensure_ascii=False)
        empty = json.dumps(render.render(self._batch(), hooks.HookSet()), ensure_ascii=False)
        self.assertEqual(plain, empty)

    def test_list_scope_hook_eats_the_budget(self) -> None:
        """列表页 hook 每行都吃预算 ⇒ 必须能从 doctor 的预算里看出来。"""
        with TemporaryDirectory() as tmp:
            hs = self._hooks(tmp, scope="list")
            with_hook = render.projected_elements(13, hs)
            plain = render.projected_elements(13)
            self.assertGreater(with_hook["elements"], plain["elements"])
            self.assertLess(with_hook["headroom"], plain["headroom"])


if __name__ == "__main__":
    unittest.main()
