"""端口契约测试 —— 抽离后的「可替换性」必须由测试守住，而不是靠文档承诺。

守三件事：
1. 注册表里每个实现都结构上满足对应 Protocol；
2. `explicit` 模型后端在**没有任何宿主文件**时也能解析（把 HERMES_HOME 指到空目录来证明）；
3. `channel` 门面确实转发给注入的通道（`pipeline` / `interaction` 依赖这个属性访问契约，
   测试也靠 `patch.object(channel, ...)` 打桩）。
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
for _p in (str(SRC_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from newspipe import backends, channel, llm, ports  # noqa: E402
from newspipe.errors import ConfigError  # noqa: E402


class PortContractTests(unittest.TestCase):
    def test_every_model_backend_satisfies_protocol(self) -> None:
        for name, cls in backends._MODEL_RESOLVERS.items():
            self.assertIsInstance(cls(), ports.ModelResolver, f"{name} 不满足 ModelResolver")

    def test_every_channel_satisfies_protocol(self) -> None:
        for name, cls in backends._CHANNELS.items():
            self.assertIsInstance(cls(), ports.CardChannel, f"{name} 不满足 CardChannel")

    def test_explicit_backend_works_without_host_files(self) -> None:
        """宿主目录指向空目录（无 config.yaml、无 .env）时，explicit 后端仍必须解析成功。"""
        cfg = {"backend": "explicit", "default": "demo_provider:demo-model",
               "providers": {"demo_provider": {
                   "base_url": "https://api.example.com/v1", "key_env": "NEWSPIPE_TEST_KEY"}}}
        with TemporaryDirectory() as empty_home:
            old_home, old_key = os.environ.get("HERMES_HOME"), os.environ.get("NEWSPIPE_TEST_KEY")
            os.environ["HERMES_HOME"] = empty_home
            os.environ["NEWSPIPE_TEST_KEY"] = "secret-value"
            try:
                ref = llm.resolve_model("summarize", cfg)
                self.assertEqual(ref.model, "demo-model")
                self.assertEqual(ref.base_url, "https://api.example.com/v1")
                self.assertEqual(ref.source, "capability:summarize")
                self.assertNotIn("secret-value", json.dumps(ref.describe(), ensure_ascii=False))
            finally:
                for k, v in (("HERMES_HOME", old_home), ("NEWSPIPE_TEST_KEY", old_key)):
                    os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def test_explicit_backend_missing_key_raises(self) -> None:
        cfg = {"backend": "explicit", "default": "p:m",
               "providers": {"p": {"base_url": "https://x/v1", "key_env": "NEWSPIPE_ABSENT_KEY"}}}
        os.environ.pop("NEWSPIPE_ABSENT_KEY", None)
        with self.assertRaises(ConfigError):
            llm.resolve_model("summarize", cfg)

    def test_explicit_backend_requires_base_url(self) -> None:
        cfg = {"backend": "explicit", "default": "p:m", "providers": {"p": {"key_env": "K"}}}
        with self.assertRaises(ConfigError):
            llm.resolve_model("summarize", cfg)

    def test_unknown_backend_is_loud(self) -> None:
        with self.assertRaises(KeyError):
            llm.resolve_model("summarize", {"backend": "nope"})

    def test_channel_facade_delegates_to_injected_channel(self) -> None:
        class FakeChannel:
            def __init__(self) -> None:
                self.calls: list[tuple] = []

            def create_entity(self, card: dict) -> str:
                self.calls.append(("create", card))
                return "card_1"

            def send_card(self, chat_id: str, card_id: str) -> str:
                self.calls.append(("send", chat_id, card_id))
                return "msg_1"

            def update_entity(self, card_id: str, sequence: int, card: dict) -> bool:
                self.calls.append(("update", card_id, sequence))
                return True

            def send_text(self, chat_id: str, text: str) -> str:
                self.calls.append(("text", chat_id, text))
                return "msg_2"

        fake = FakeChannel()
        backends.set_channel(fake)
        try:
            self.assertEqual(channel.create_entity({"a": 1}), "card_1")
            self.assertEqual(channel.send_card("oc_x", "card_1"), "msg_1")
            self.assertTrue(channel.update_entity("card_1", 2, {}))
            self.assertEqual(channel.send_text("oc_x", "hi"), "msg_2")
            self.assertEqual([c[0] for c in fake.calls], ["create", "send", "update", "text"])
        finally:
            backends.set_channel(None)


if __name__ == "__main__":
    unittest.main()
