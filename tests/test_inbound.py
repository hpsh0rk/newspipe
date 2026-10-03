"""入站（阶段 3）的单元测试 —— 含一条**真实 socket** 的 HTTP 端到端用例。

守四件事：
1. URL 校验回 challenge；不是我们的事件要静默（返回 None，不能报错刷屏）；
2. 卡片动作真正走到 `interaction.handle`（用临时 news_dir 真改批次状态，不 mock 业务）；
3. 签名校验与「解密缺库时报可执行错误」；
4. 回调里任何异常都不许冒泡（长连接线程崩了 = 点击永久失效且无人知道）。
"""

from __future__ import annotations

import json
import sys
import threading
import types
import unittest
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
for _p in (str(SRC_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from newspipe import backends, credentials, inbound, state  # noqa: E402
from newspipe.errors import InboundError  # noqa: E402

DIGEST = "2026-10-03"


class _FakeChannel:
    """记录调用的假通道：让 `interaction.handle` 的卡片更新分支真的被执行，但不联网。"""

    name = "fake"

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def create_entity(self, card: dict) -> str:
        self.calls.append(("create", card))
        return "c1"

    def send_card(self, chat_id: str, card_id: str) -> str:
        self.calls.append(("send", chat_id, card_id))
        return "m1"

    def update_entity(self, card_id: str, sequence: int, card: dict) -> bool:
        self.calls.append(("update", card_id, sequence, card))
        return True

    def send_text(self, chat_id: str, text: str) -> str:
        self.calls.append(("text", chat_id, text))
        return "m2"

    def updates(self) -> list[tuple]:
        return [c for c in self.calls if c[0] == "update"]


def _item(n: int) -> dict:
    return {"ext_id": f"e{n}", "title": f"Title {n}", "url": f"https://example.com/{n}",
            "original_url": "", "source": "Test", "summary": "", "category": "",
            "score": 100 - n, "pub_ts": 0, "id": f"n{n:02d}", "status": "unread"}


def _batch() -> dict:
    return {"digest": DIGEST, "slot": "am", "source": "s", "title": "测试卡",
            "batch": f"state/batches/{DIGEST}/s-am.json", "items": [_item(1), _item(2)],
            "view": "list", "card_id": "c1", "seq": 1}


def _creds(**kw) -> credentials.FeishuCreds:
    base = {"app_id": "cli_test", "app_secret": "s", "domain": "feishu"}
    base.update(kw)
    return credentials.FeishuCreds(**base)


class ExtractActionTests(unittest.TestCase):
    def test_v2_shape(self) -> None:
        event = {"header": {"event_type": inbound.CARD_ACTION_EVENT},
                 "event": {"action": {"value": {"domain": "news", "news_action": "open_detail"}}}}
        self.assertEqual(inbound.extract_action(event)["news_action"], "open_detail")

    def test_flat_shape_with_string_value(self) -> None:
        payload = {"domain": "news", "news_action": "back_to_list"}
        event = {"action": {"value": json.dumps(payload)}}
        self.assertEqual(inbound.extract_action(event)["news_action"], "back_to_list")

    def test_foreign_payload_is_ignored(self) -> None:
        for event in ({}, {"action": {"value": {"domain": "other"}}},
                      {"event": {"action": {"value": "not-json"}}}, "not-a-dict"):
            self.assertIsNone(inbound.extract_action(event))

    def test_router_envelope_shape(self) -> None:
        """宿主转发器（hermes.card-router）的信封：网关只给它 tag + value，所以 value 在顶层。"""
        envelope = {"protocol_version": 1, "source": "hermes.card-router", "domain": "news",
                    "tag": "button", "value": {"domain": "news", "id": "n01",
                                               "news_action": "open_detail"}}
        self.assertEqual(inbound.extract_action(envelope)["news_action"], "open_detail")
        # 别的域不能被本项目认领
        foreign = dict(envelope, domain="other", value={"domain": "other"})
        self.assertIsNone(inbound.extract_action(foreign))

    def test_challenge_recognised(self) -> None:
        self.assertEqual(inbound.dispatch({"type": "url_verification", "challenge": "abc"}),
                         {"challenge": "abc"})
        self.assertEqual(inbound.dispatch({"challenge": "xyz"}), {"challenge": "xyz"})


class DispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.news_dir = Path(self._tmp.name)
        self.store = state.Store(self.news_dir)
        self.store.write_batch(_batch())
        self.channel = _FakeChannel()
        backends.set_channel(self.channel)

    def tearDown(self) -> None:
        backends.set_channel(None)
        self._tmp.cleanup()

    def _event(self, action: str, item: str = "n01") -> dict:
        return {"header": {"event_type": inbound.CARD_ACTION_EVENT},
                "event": {"action": {"value": {
                    "domain": "news", "digest": DIGEST,
                    "batch": f"state/batches/{DIGEST}/s-am.json",
                    "id": item, "news_action": action}}}}

    def test_router_envelope_really_dispatches(self) -> None:
        """宿主转发器（hermes.card-router）的信封也要真跑通：网关 → 转发器 → 本项目。"""
        envelope = {"protocol_version": 1, "source": "hermes.card-router", "domain": "news",
                    "tag": "button",
                    "value": {"domain": "news", "digest": DIGEST,
                              "batch": f"state/batches/{DIGEST}/s-am.json",
                              "id": "n01", "news_action": "open_detail"}}
        self.assertEqual(inbound.dispatch(envelope, news_dir=self.news_dir), {})
        _path, batch = self.store.load_batch_by_rel(f"state/batches/{DIGEST}/s-am.json")
        self.assertEqual(batch["view"], {"item": "n01"})
        self.assertEqual(batch["items"][0]["status"], "read")

    def test_open_detail_really_updates_batch(self) -> None:
        """不 mock 业务：真读真写临时 news_dir 里的批次。"""
        result = inbound.dispatch(self._event("open_detail"), news_dir=self.news_dir,
)
        self.assertEqual(result, {})
        _path, batch = self.store.load_batch_by_rel(f"state/batches/{DIGEST}/s-am.json")
        self.assertEqual(batch["view"], {"item": "n01"})
        self.assertEqual(batch["items"][0]["status"], "read")
        # 卡片实体被更新且 sequence 递增（翻页机制的契约）
        updates = self.channel.updates()
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0][1], "c1")
        self.assertEqual(updates[0][2], 2)

    def test_back_to_list_after_detail(self) -> None:
        inbound.dispatch(self._event("open_detail"), news_dir=self.news_dir,
                         )
        inbound.dispatch(self._event("back_to_list"), news_dir=self.news_dir,
                         )
        _path, batch = self.store.load_batch_by_rel(f"state/batches/{DIGEST}/s-am.json")
        self.assertEqual(batch["view"], "list")

    def test_foreign_event_returns_none(self) -> None:
        self.assertIsNone(inbound.dispatch({"header": {"event_type": "im.message.receive_v1"},
                                            "event": {"message": {}}},
                                           news_dir=self.news_dir))

    def test_wiki_action_records_favorite_event(self) -> None:
        from newspipe import events

        inbound.dispatch(self._event("wiki"), news_dir=self.news_dir)
        pending = events.queue(self.news_dir)
        self.assertEqual([e["payload"]["item_id"] for e in pending], ["n01"])


class SignatureTests(unittest.TestCase):
    def test_signature_roundtrip(self) -> None:
        import hashlib
        body = b'{"hello":"world"}'
        key, ts, nonce = "ek", "1700000000", "n1"
        signature = hashlib.sha256(f"{ts}{nonce}{key}".encode() + body).hexdigest()
        self.assertTrue(inbound.verify_signature(timestamp=ts, nonce=nonce, encrypt_key=key,
                                                 body=body, signature=signature))
        self.assertFalse(inbound.verify_signature(timestamp=ts, nonce=nonce, encrypt_key=key,
                                                  body=body, signature="deadbeef"))

    def test_missing_parts_fail_closed(self) -> None:
        self.assertFalse(inbound.verify_signature(timestamp="", nonce="n", encrypt_key="k",
                                                  body=b"{}", signature="x"))

    def test_decrypt_roundtrip_or_actionable_error(self) -> None:
        """装了加密库 → 真跑一次 AES 往返；没装 → 必须是带安装命令的 InboundError。"""
        import base64
        import hashlib
        import json as _json
        key, payload = "encrypt-key", {"header": {"event_type": inbound.CARD_ACTION_EVENT}}
        try:
            from Crypto.Cipher import AES
            from Crypto.Util.Padding import pad
        except ImportError:
            with self.assertRaises(InboundError) as ctx:
                inbound.decrypt_payload(key, "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo=")
            self.assertIn("newspipe[crypto]", str(ctx.exception))
            return
        iv = b"0123456789abcdef"
        raw = iv + AES.new(hashlib.sha256(key.encode()).digest(), AES.MODE_CBC, iv).encrypt(
            pad(_json.dumps(payload).encode("utf-8"), 16))
        self.assertEqual(inbound.decrypt_payload(key, base64.b64encode(raw).decode()), payload)

    def test_decrypt_wrong_key_is_loud(self) -> None:
        try:
            from Crypto.Cipher import AES  # noqa: F401
        except ImportError:
            self.skipTest("无加密库，错误路径已由上一条覆盖")
        with self.assertRaises(InboundError):
            inbound.decrypt_payload("wrong-key", "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo=")

    def test_decrypt_bad_base64_is_loud(self) -> None:
        with self.assertRaises(InboundError):
            inbound.decrypt_payload("key", "!!!not-base64!!!")


class HttpServerTests(unittest.TestCase):
    """真起 socket 的端到端：POST → 签名 → 路由 → 响应。"""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.news_dir = Path(self._tmp.name)
        state.Store(self.news_dir).write_batch(_batch())
        self.channel = _FakeChannel()
        backends.set_channel(self.channel)
        self.server, self.path = inbound.start_http(
            service_cfg={"http": {"host": "127.0.0.1", "port": 0, "path": "/feishu/events"}},
            creds=_creds(), news_dir=self.news_dir,
            logger=lambda _m: None)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        backends.set_channel(None)
        self._tmp.cleanup()

    def _post(self, payload: dict, path: str | None = None) -> tuple[int, dict]:
        request = urllib.request.Request(
            self.base + (path or self.path),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8") or "{}")

    def test_health_check(self) -> None:
        with urllib.request.urlopen(self.base + "/anything", timeout=10) as resp:
            self.assertEqual(resp.status, 200)
            self.assertTrue(json.loads(resp.read().decode())["ok"])

    def test_url_verification(self) -> None:
        status, body = self._post({"type": "url_verification", "challenge": "c-123"})
        self.assertEqual((status, body), (200, {"code": 0, "challenge": "c-123"}))

    def test_card_action_updates_batch_over_http(self) -> None:
        event = {"header": {"event_type": inbound.CARD_ACTION_EVENT},
                 "event": {"action": {"value": {
                     "domain": "news", "digest": DIGEST,
                     "batch": f"state/batches/{DIGEST}/s-am.json",
                     "id": "n02", "news_action": "open_detail"}}}}
        status, body = self._post(event)
        self.assertEqual(status, 200)
        self.assertEqual(body.get("code"), 0)
        _path, batch = state.Store(self.news_dir).load_batch_by_rel(
            f"state/batches/{DIGEST}/s-am.json")
        self.assertEqual(batch["view"], {"item": "n02"})

    def test_unknown_path_is_404(self) -> None:
        status, _body = self._post({"type": "url_verification", "challenge": "x"}, path="/nope")
        self.assertEqual(status, 404)

    def test_handler_exception_returns_500_not_crash(self) -> None:
        with mock.patch.object(inbound, "dispatch", side_effect=RuntimeError("boom")):
            status, body = self._post({"type": "url_verification", "challenge": "x"})
        self.assertEqual(status, 500)
        self.assertIn("handler failed", body.get("error", ""))
        # 服务器仍活着
        self.assertEqual(self._post({"type": "url_verification", "challenge": "y"})[0], 200)


class WsHandlerTests(unittest.TestCase):
    class _Response:
        def __init__(self, d=None) -> None:
            self.d = d or {}

    class _Sdk:
        def __init__(self) -> None:
            self.response_cls = WsHandlerTests._Response

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.news_dir = Path(self._tmp.name)
        state.Store(self.news_dir).write_batch(_batch())
        self.channel = _FakeChannel()
        backends.set_channel(self.channel)
        self.state: dict = {}
        self.handler = inbound.make_action_handler(
            sdk=self._Sdk(), news_dir=self.news_dir,
            logger=lambda _m: None, state=self.state)

    def tearDown(self) -> None:
        backends.set_channel(None)
        self._tmp.cleanup()

    @staticmethod
    def _data(value) -> types.SimpleNamespace:  # noqa: ANN001
        return types.SimpleNamespace(
            event=types.SimpleNamespace(action=types.SimpleNamespace(value=value)))

    def test_action_routed_and_counted(self) -> None:
        response = self.handler(self._data({"domain": "news", "digest": DIGEST,
                                            "batch": f"state/batches/{DIGEST}/s-am.json",
                                            "id": "n01", "news_action": "open_detail"}))
        self.assertIsInstance(response, WsHandlerTests._Response)
        self.assertEqual(self.state.get("handled"), 1)
        _path, batch = state.Store(self.news_dir).load_batch_by_rel(
            f"state/batches/{DIGEST}/s-am.json")
        self.assertEqual(batch["view"], {"item": "n01"})

    def test_foreign_value_ignored(self) -> None:
        self.handler(self._data({"domain": "other"}))
        self.assertEqual(self.state.get("ignored"), 1)
        self.assertIsNone(self.state.get("handled"))

    def test_non_dict_value_ignored(self) -> None:
        self.handler(self._data("plain string"))
        self.assertEqual(self.state.get("ignored"), 1)

    def test_exception_does_not_propagate(self) -> None:
        with mock.patch.object(inbound, "dispatch", side_effect=RuntimeError("boom")):
            response = self.handler(self._data({"domain": "news"}))
        self.assertIsInstance(response, WsHandlerTests._Response)
        self.assertEqual(self.state.get("errors"), 1)


class InboundHandleTests(unittest.TestCase):
    def test_none_mode_is_noop(self) -> None:
        handle = inbound.start_inbound(mode="none", service_cfg={}, creds=_creds())
        self.assertEqual(handle.mode, "none")
        self.assertEqual(handle.describe()["mode"], "none")
        handle.stop()

    def test_unknown_mode_is_loud(self) -> None:
        with self.assertRaises(InboundError):
            inbound.start_inbound(mode="carrier-pigeon", service_cfg={}, creds=_creds())

    def test_ws_start_failure_is_recorded_not_raised(self) -> None:
        def boom(*_a, **_k):
            raise RuntimeError("no network")

        handle = inbound.start_inbound(mode="ws", service_cfg={}, creds=_creds(),
                                       sdk=WsHandlerTests._Sdk(), client=None,
                                       logger=lambda _m: None)
        # 注入一个必然失败的 run_ws（缺 SDK 属性）
        handle.stop()          # 不该抛

    def test_ws_with_injected_client_starts_thread(self) -> None:
        class Client:
            def __init__(self) -> None:
                self.started = threading.Event()

            def start(self) -> None:
                self.started.set()

            def stop(self) -> None:
                pass

        client = Client()
        handle = inbound.start_inbound(mode="ws", service_cfg={}, creds=_creds(),
                                       sdk=WsHandlerTests._Sdk(), client=client,
                                       logger=lambda _m: None)
        self.assertTrue(client.started.wait(2.0), "长连接客户端未被启动")
        self.assertEqual(handle.mode, "ws")
        handle.stop()


if __name__ == "__main__":
    unittest.main()
