"""直连通道（阶段 2）的单元测试 —— 用假 transport，不联网、不依赖凭据。

守四件事：
1. token 缓存与「失效即刷新重试」；
2. 错误码分流（限流退避重试 vs 配置类不重试）；
3. 缺凭据时报 ConfigError 且**消息里不含任何密钥**；
4. 请求体形状（含 schema 2.0 那条血泪坑：给 1.0 卡片会被回 `200610 body is nil`）。
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
for _p in (str(SRC_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from newspipe import credentials  # noqa: E402
from newspipe.backends.feishu_direct import FeishuDirectChannel  # noqa: E402
from newspipe.errors import ConfigError, DeliveryError  # noqa: E402

SECRET = "s3cr3t-do-not-print"


class FakeTransport:
    """按脚本回放响应；记录每次调用，供断言。"""

    def __init__(self, script: list[tuple[int, dict]]) -> None:
        self.script = list(script)
        self.calls: list[dict] = []

    def __call__(self, method, url, headers, body, timeout):  # noqa: ANN001
        self.calls.append({"method": method, "url": url, "headers": dict(headers),
                           "body": json.loads(body.decode("utf-8")) if body else None})
        if not self.script:
            raise AssertionError(f"未预期的额外请求：{method} {url}")
        status, payload = self.script.pop(0)
        return status, json.dumps(payload).encode("utf-8")

    def paths(self) -> list[str]:
        return [c["url"].split("open.feishu.cn")[-1] for c in self.calls]


def _creds() -> credentials.FeishuCreds:
    return credentials.FeishuCreds(app_id="cli_test", app_secret=SECRET, source="test")


TOKEN_OK = (200, {"code": 0, "tenant_access_token": "t-abc", "expire": 7200})


class TokenTests(unittest.TestCase):
    def test_token_is_cached_between_calls(self) -> None:
        transport = FakeTransport([TOKEN_OK, (200, {"code": 0, "data": {"card_id": "c1"}})])
        channel = FeishuDirectChannel(_creds(), transport=transport, sleeper=lambda _s: None)
        channel.create_entity({"schema": "2.0"})
        self.assertEqual(transport.paths(), ["/open-apis/auth/v3/tenant_access_token/internal",
                                             "/open-apis/cardkit/v1/cards"])
        # 第二次调用复用缓存 token，不再打 token 端点
        transport.script.append((200, {"code": 0, "data": {"card_id": "c2"}}))
        channel.create_entity({"schema": "2.0"})
        self.assertEqual(channel.stats["token_refreshes"], 1)

    def test_token_invalid_forces_refresh_and_retries(self) -> None:
        transport = FakeTransport([
            TOKEN_OK,
            (200, {"code": 99991663, "msg": "token expired"}),
            TOKEN_OK,
            (200, {"code": 0, "data": {"card_id": "c1"}}),
        ])
        channel = FeishuDirectChannel(_creds(), transport=transport, sleeper=lambda _s: None)
        self.assertEqual(channel.create_entity({"schema": "2.0"}), "c1")
        self.assertEqual(channel.stats["token_refreshes"], 2)

    def test_missing_token_in_response_is_loud(self) -> None:
        transport = FakeTransport([(200, {"code": 0, "msg": "ok"})])
        channel = FeishuDirectChannel(_creds(), transport=transport, sleeper=lambda _s: None)
        with self.assertRaises(DeliveryError):
            channel.create_entity({"schema": "2.0"})


class ErrorMappingTests(unittest.TestCase):
    def _channel(self, script):
        return FeishuDirectChannel(_creds(), transport=FakeTransport(script),
                                   sleeper=lambda _s: None)

    def test_rate_limit_is_retried(self) -> None:
        transport = FakeTransport([TOKEN_OK,
                                   (200, {"code": 99991400, "msg": "too many request"}),
                                   (200, {"code": 0, "data": {"card_id": "c1"}})])
        channel = FeishuDirectChannel(_creds(), transport=transport, sleeper=lambda _s: None)
        self.assertEqual(channel.create_entity({"schema": "2.0"}), "c1")
        self.assertEqual(channel.stats["retries"], 1)

    def test_bot_not_in_chat_is_not_retried(self) -> None:
        transport = FakeTransport([TOKEN_OK,
                                   (200, {"code": 230002, "msg": "out of the chat"}),
                                   (200, {"code": 230002, "msg": "out of the chat"})])
        channel = FeishuDirectChannel(_creds(), transport=transport, sleeper=lambda _s: None)
        with self.assertRaises(DeliveryError) as ctx:
            channel.send_card("oc_x", "c1")
        self.assertIn("230002", str(ctx.exception))
        self.assertEqual(len(transport.calls), 2, "配置类错误不该重试")

    def test_element_limit_is_not_retried(self) -> None:
        transport = FakeTransport([TOKEN_OK, (200, {"code": 11310, "msg": "element exceeds"})])
        channel = FeishuDirectChannel(_creds(), transport=transport, sleeper=lambda _s: None)
        with self.assertRaises(DeliveryError):
            channel.update_entity("c1", 2, {"schema": "2.0"})

    def test_http_5xx_is_retried_then_reported(self) -> None:
        transport = FakeTransport([TOKEN_OK, (503, {"code": 1, "msg": "unavailable"}),
                                   (503, {"code": 1, "msg": "unavailable"}),
                                   (503, {"code": 1, "msg": "unavailable"})])
        channel = FeishuDirectChannel(_creds(), transport=transport, sleeper=lambda _s: None)
        with self.assertRaises(DeliveryError):
            channel.create_entity({"schema": "2.0"})
        self.assertGreaterEqual(channel.stats["retries"], 1)

    def test_unparseable_body_is_loud(self) -> None:
        def transport(method, url, headers, body, timeout):  # noqa: ANN001
            return 200, b"<html>not json</html>"
        channel = FeishuDirectChannel(_creds(), transport=transport, sleeper=lambda _s: None)
        with self.assertRaises(DeliveryError):
            channel.create_entity({"schema": "2.0"})

    def test_network_exception_is_wrapped(self) -> None:
        def transport(method, url, headers, body, timeout):  # noqa: ANN001
            raise TimeoutError("boom")
        channel = FeishuDirectChannel(_creds(), transport=transport, sleeper=lambda _s: None)
        with self.assertRaises(DeliveryError) as ctx:
            channel.create_entity({"schema": "2.0"})
        self.assertIn("不可达", str(ctx.exception))


class RequestShapeTests(unittest.TestCase):
    def test_create_entity_body_is_card_json_string(self) -> None:
        transport = FakeTransport([TOKEN_OK, (200, {"code": 0, "data": {"card_id": "c1"}})])
        channel = FeishuDirectChannel(_creds(), transport=transport, sleeper=lambda _s: None)
        channel.create_entity({"schema": "2.0", "body": {"elements": []}})
        body = transport.calls[-1]["body"]
        self.assertEqual(body["type"], "card_json")
        self.assertIsInstance(body["data"], str, "data 必须是字符串（给对象会被 9499 拒）")
        self.assertEqual(json.loads(body["data"])["schema"], "2.0")

    def test_send_card_uses_interactive_with_card_id(self) -> None:
        transport = FakeTransport([TOKEN_OK, (200, {"code": 0, "data": {"message_id": "m1"}})])
        channel = FeishuDirectChannel(_creds(), transport=transport, sleeper=lambda _s: None)
        channel.send_card("oc_x", "c1")
        call = transport.calls[-1]
        self.assertIn("receive_id_type=chat_id", call["url"])
        self.assertEqual(call["body"]["receive_type"] if "receive_type" in call["body"]
                         else call["body"]["receive_id"], "oc_x")
        self.assertEqual(call["body"]["msg_type"], "interactive")
        self.assertEqual(json.loads(call["body"]["content"])["data"]["card_id"], "c1")

    def test_update_entity_sends_sequence_and_uuid(self) -> None:
        transport = FakeTransport([TOKEN_OK, (200, {"code": 0})])
        channel = FeishuDirectChannel(_creds(), transport=transport, sleeper=lambda _s: None)
        self.assertTrue(channel.update_entity("c1", 7, {"schema": "2.0"}))
        body = transport.calls[-1]["body"]
        self.assertEqual(body["sequence"], 7)
        self.assertTrue(body["uuid"])

    def test_probe_card_is_schema_2_0(self) -> None:
        """回归守卫：自检卡给 schema 1.0 会被服务端回误导性的 200610 body is nil。"""
        transport = FakeTransport([TOKEN_OK, (200, {"code": 0, "data": {"card_id": "c1"}}),
                                   (200, {"code": 0, "data": {"message_id": "m1"}}),
                                   (200, {"code": 0})])
        channel = FeishuDirectChannel(_creds(), transport=transport, sleeper=lambda _s: None)
        result = channel.probe("oc_x")
        created = json.loads(transport.calls[1]["body"]["data"])
        self.assertEqual(created["schema"], "2.0")
        self.assertIn("body", created)
        self.assertEqual([s["step"] for s in result["steps"]],
                         ["tenant_access_token", "create_entity", "send_card", "update_entity"])

    def test_probe_without_send_only_touches_token(self) -> None:
        transport = FakeTransport([TOKEN_OK])
        channel = FeishuDirectChannel(_creds(), transport=transport, sleeper=lambda _s: None)
        channel.probe("oc_x", send=False)
        self.assertEqual(transport.paths(),
                         ["/open-apis/auth/v3/tenant_access_token/internal"])


class CredentialTests(unittest.TestCase):
    def test_missing_credentials_raise_config_error_without_values(self) -> None:
        with TemporaryDirectory() as tmp:
            channel = FeishuDirectChannel(service_cfg={}, news_dir=Path(tmp))
            # 屏蔽环境与宿主 dotenv，制造「什么都没有」的场景
            original = credentials.default_dotenv_paths
            credentials.default_dotenv_paths = lambda news_dir=None: [Path(tmp) / "none.env"]
            try:
                import os
                saved = {k: os.environ.pop(k, None)
                         for k in ("NEWSPIPE_FEISHU_APP_ID", "FEISHU_APP_ID",
                                   "NEWSPIPE_FEISHU_APP_SECRET", "FEISHU_APP_SECRET")}
                try:
                    with self.assertRaises(ConfigError) as ctx:
                        channel.creds
                    message = str(ctx.exception)
                    self.assertIn("NEWSPIPE_FEISHU_APP_ID", message)
                    self.assertIn("NEWSPIPE_FEISHU_APP_SECRET", message)
                    self.assertNotIn(SECRET, message)
                finally:
                    for key, value in saved.items():
                        if value is not None:
                            os.environ[key] = value
            finally:
                credentials.default_dotenv_paths = original

    def test_describe_never_contains_secret(self) -> None:
        channel = FeishuDirectChannel(_creds())
        rendered = json.dumps(channel.describe(), ensure_ascii=False)
        self.assertNotIn(SECRET, rendered)
        self.assertIn("已设置", rendered)

    def test_resolve_reads_dotenv_in_priority_order(self) -> None:
        with TemporaryDirectory() as tmp:
            dotenv = Path(tmp) / ".env"
            dotenv.write_text("NEWSPIPE_FEISHU_APP_ID=cli_from_file\n"
                              "NEWSPIPE_FEISHU_APP_SECRET=from_file\n", encoding="utf-8")
            creds = credentials.resolve_feishu({}, news_dir=Path(tmp), env={})
            self.assertEqual(creds.app_id, "cli_from_file")
            self.assertEqual(creds.app_secret, "from_file")
            # 环境变量优先于文件
            creds2 = credentials.resolve_feishu({}, news_dir=Path(tmp),
                                                env={"NEWSPIPE_FEISHU_APP_ID": "cli_from_env"})
            self.assertEqual(creds2.app_id, "cli_from_env")

    def test_service_yaml_values_win_over_dotenv(self) -> None:
        with TemporaryDirectory() as tmp:
            (Path(tmp) / ".env").write_text("NEWSPIPE_FEISHU_APP_ID=cli_from_file\n"
                                            "NEWSPIPE_FEISHU_APP_SECRET=from_file\n",
                                            encoding="utf-8")
            creds = credentials.resolve_feishu({"app_id": "cli_explicit"}, news_dir=Path(tmp),
                                               env={})
            self.assertEqual(creds.app_id, "cli_explicit")
            self.assertEqual(creds.app_secret, "from_file")

    def test_verification_token_via_named_env(self) -> None:
        creds = credentials.resolve_feishu(
            {"app_id": "cli_x", "app_secret": "s", "verification_token_env": "MY_TOKEN"},
            env={"MY_TOKEN": "vt"})
        self.assertEqual(creds.verification_token, "vt")


if __name__ == "__main__":
    unittest.main()
