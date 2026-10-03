"""入站 ws（长连接）的**装配层**测试 —— 补齐 test_inbound.py 没覆盖的那一段。

`test_inbound.py` 里的 `WsHandlerTests` 测的是「事件进来之后」；`InboundHandleTests` 注入的
是一个现成的 client，**绕过了 dispatcher 的装配**。于是「handler 根本没注册到 SDK 上」这类
故障没有测试兜底 —— 而它的表现恰好是最难查的一种：长连接正常、日志正常、点了卡片没反应。

这里用替身 SDK 走完整装配路径，锁三件事：
1. `run_ws` 真的把卡片动作 handler 注册到了 dispatcher，并把 domain 传给 ws client；
2. 没装 SDK 时报可执行的错（`pip install`），而不是 ImportError 栈；
3. `port: 0` 真的是「让内核挑端口」（曾经被 `or 8787` 吃掉，导致测试撞在常驻服务上）。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
for _p in (str(SRC_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from newspipe import credentials, inbound  # noqa: E402


class _Response:
    def __init__(self, body: dict | None = None) -> None:
        self.body = body or {}


class _FakeLark:
    """只实现 `run_ws` 用到的那几个名字。"""

    class LogLevel:
        DEBUG, INFO, WARNING, ERROR = "debug", "info", "warning", "error"

    def __init__(self) -> None:
        self.registered: list[object] = []
        self.clients: list[dict] = []
        outer = self

        class _Builder:
            def __init__(self, encrypt_key: str, verification_token: str) -> None:
                self.encrypt_key = encrypt_key
                self.verification_token = verification_token

            @staticmethod
            def builder(encrypt_key: str, verification_token: str) -> "_Builder":
                return _Builder(encrypt_key, verification_token)

            def register_p2_card_action_trigger(self, fn: object) -> "_Builder":
                outer.registered.append(fn)
                return self

            def build(self) -> object:
                return object()

        class _WsClient:
            def __init__(self, app_id: str, app_secret: str, event_handler: object,
                         domain: str = "", log_level: str = "") -> None:
                self.app_id, self.app_secret = app_id, app_secret
                self.event_handler, self.domain, self.log_level = event_handler, domain, log_level
                self.started = self.stopped = False
                outer.clients.append({"client": self, "domain": domain, "log_level": log_level})

            def start(self) -> None:
                self.started = True

            def stop(self) -> None:
                self.stopped = True

        class _Ws:
            Client = _WsClient

        self.EventDispatcherHandler = _Builder
        self.ws = _Ws()


def _creds(**kw: object) -> credentials.FeishuCreds:
    base: dict = {"app_id": "cli_test", "app_secret": "s", "domain": "feishu",
                  "verification_token": "vt", "encrypt_key": "ek"}
    base.update(kw)
    return credentials.FeishuCreds(**base)


class RunWsAssemblyTests(unittest.TestCase):
    """`run_ws` —— 把 handler 装到 SDK 上并启动连接。"""

    def setUp(self) -> None:
        self.lark = _FakeLark()
        self.sdk = inbound._Sdk(self.lark, _Response)
        self.logs: list[str] = []

    def test_card_action_handler_is_registered(self) -> None:
        client = inbound.run_ws(_creds(), logger=self.logs.append, sdk=self.sdk)
        self.assertEqual(len(self.lark.registered), 1,
                         "卡片动作 handler 没注册到 dispatcher —— 点卡片会毫无反应")
        self.assertTrue(getattr(client, "started", False), "run_ws 应启动连接（阻塞式）")

    def test_ws_client_gets_domain_and_log_level(self) -> None:
        inbound.run_ws(_creds(), logger=self.logs.append, sdk=self.sdk)
        info = self.lark.clients[0]
        self.assertEqual(info["domain"], "https://open.feishu.cn", "domain 应透传 base_url")
        self.assertEqual(info["log_level"], "info")

    def test_log_level_is_configurable_by_env(self) -> None:
        with mock.patch.dict("os.environ", {"NEWSPIPE_SDK_LOG": "debug"}):
            inbound.run_ws(_creds(), logger=self.logs.append, sdk=self.sdk)
        self.assertEqual(self.lark.clients[0]["log_level"], "debug")

    def test_registered_handler_reaches_dispatch(self) -> None:
        """注册上去的那个 handler 必须真的连到 dispatch（防「注册了但没接线」）。"""
        inbound.run_ws(_creds(), logger=self.logs.append, sdk=self.sdk)
        fn = self.lark.registered[0]
        data = mock.MagicMock()
        data.event.action.value = {"domain": "news", "action": "open_detail"}
        with mock.patch.object(inbound, "dispatch", return_value={"toast": {}}) as d:
            fn(data)
        self.assertTrue(d.called, "handler 收到了事件但没有调用 dispatch")

    def test_injected_client_skips_assembly(self) -> None:
        """注入 client 时不建连接（测试与嵌入式用法依赖这一点）。"""
        class Client:
            def __init__(self) -> None:
                self.started = False

            def start(self) -> None:
                self.started = True

        client = Client()
        inbound.run_ws(_creds(), logger=self.logs.append, sdk=self.sdk, client=client)
        self.assertTrue(client.started)
        self.assertEqual(self.lark.registered, [], "注入 client 时不该再建 dispatcher")


class MissingSdkTests(unittest.TestCase):
    def test_missing_sdk_message_is_actionable(self) -> None:
        with mock.patch.dict(sys.modules, {"lark_oapi": None}):
            with self.assertRaises(inbound.InboundError) as ctx:
                inbound.load_sdk()
        msg = str(ctx.exception)
        self.assertIn("pip install", msg)
        self.assertIn("http", msg, "报错应给出「改用 http 模式」这条出路")


class HttpBindTests(unittest.TestCase):
    """绑定参数解析 —— 不占端口，纯逻辑。"""

    def test_port_zero_is_preserved(self) -> None:
        """`port: 0` 不能被 `or 8787` 吃掉（否则测试与多实例会撞在常驻服务上）。"""
        self.assertEqual(inbound.http_bind({"http": {"port": 0}})[1], 0)

    def test_default_port_when_unset(self) -> None:
        self.assertEqual(inbound.http_bind({})[1], inbound.DEFAULT_HTTP_PORT)
        self.assertEqual(inbound.http_bind({"http": {"host": "127.0.0.1"}})[1],
                         inbound.DEFAULT_HTTP_PORT)

    def test_explicit_port_and_path(self) -> None:
        host, port, path = inbound.http_bind({"http": {"host": "0.0.0.0", "port": 9999,
                                                       "path": "/cb"}})
        self.assertEqual((host, port, path), ("0.0.0.0", 9999, "/cb"))

    def test_real_bind_on_ephemeral_port(self) -> None:
        server, path = inbound.start_http(service_cfg={"http": {"port": 0}},
                                         creds=_creds(), logger=lambda _m: None)
        try:
            self.assertGreater(server.server_address[1], 0)
            self.assertEqual(path, "/feishu/events")
        finally:
            server.server_close()


if __name__ == "__main__":
    unittest.main()
