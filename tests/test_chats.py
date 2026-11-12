"""Joined-group reads use the delivery identity and never expose provider data."""
import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from newspipe import actions, chats, cli, credentials, runtime
from newspipe.backends.feishu_direct import FeishuDirectChannel, _default_transport
from newspipe.backends.feishu_lark_cli import LarkCliChannel, _call


def page(items=None, **extra):
    return {"items": [] if items is None else items, "has_more": False, "page_token": "", **extra}


GROUP = {"chat_id": "oc_group", "name": "Readers", "external": False, "description": "PRIVATE"}


class DirectoryTests(unittest.TestCase):
    def test_empty_success_and_only_selection_fields(self):
        backend = SimpleNamespace(list_chats=Mock(return_value=page([GROUP, GROUP])))
        out = chats.list_joined(backend)
        self.assertEqual(out, {"groups": [{"chat_id": "oc_group", "name": "Readers", "external": False}],
                               "has_more": False, "page_token": ""})
        backend.list_chats.return_value = page()
        self.assertEqual(chats.list_joined(backend)["groups"], [])

    def test_invalid_requests_fail_before_backend(self):
        backend = SimpleNamespace(list_chats=Mock())
        for size, cursor in [(0, ""), (101, ""), (True, ""), (100, "x" * 2049), (100, "bad\n")]:
            with self.subTest(size=size, cursor=cursor[:8]), self.assertRaises(chats.DirectoryError):
                chats.list_joined(backend, page_size=size, page_token=cursor)
        backend.list_chats.assert_not_called()

    def test_malformed_pages_are_not_empty_success(self):
        for body in [None, {}, page(has_more="false"), page(has_more=True),
                     page(has_more=True, page_token="same"), page(items={}),
                     page([{"chat_id": "oc_", "name": "x"}]),
                     page([{**GROUP, "name": "x" * 301}]), page([{**GROUP, "external": "false"}]),
                     page([GROUP] * 101)]:
            with self.subTest(body=str(body)[:80]), self.assertRaises(chats.DirectoryError):
                chats.list_joined(SimpleNamespace(list_chats=lambda **_: body), page_token="same")

    def test_backend_errors_are_sanitized_and_no_identity_fallback(self):
        with self.assertRaises(chats.DirectoryError) as error:
            chats.list_joined(SimpleNamespace(list_chats=Mock(side_effect=RuntimeError("TOKEN_PRIVATE"))))
        self.assertNotIn("TOKEN_PRIVATE", str(error.exception))
        with self.assertRaises(chats.DirectoryError) as error:
            chats.list_joined(object())
        self.assertEqual(error.exception.code, "E_CONFIG")

    def test_cli_uses_bound_channel_and_preserves_all_files(self):
        with TemporaryDirectory() as tmp:
            news = Path(tmp)
            (news / "sources.yaml").write_text("chat: oc_test\nslots: {}\nsources: {}\n")
            backend = SimpleNamespace(list_chats=Mock(return_value=page([GROUP])))
            before = (news / "sources.yaml").read_bytes()
            with runtime.bind_channel(news, backend):
                out = cli.run_subcommand(["chats", "list", "--json"], news).as_dict()
            self.assertTrue(out["ok"], out)
            self.assertFalse(out["changed"])
            self.assertEqual(out["command"], "chats.list")
            self.assertEqual(out["data"]["groups"][0]["name"], "Readers")
            self.assertEqual((news / "sources.yaml").read_bytes(), before)

    def test_action_cursor_is_not_in_diagnostics(self):
        with TemporaryDirectory() as tmp, patch.object(cli, "run_subcommand") as run:
            from newspipe import result
            run.return_value = result.ok("chats.list", {"groups": [], "has_more": False, "page_token": ""})
            out = actions.dispatch("chat-list", {"page_token": ["PRIVATE_CURSOR"]}, Path(tmp))
            self.assertTrue(out["ok"], out)
            self.assertFalse(out["changed"])
            self.assertNotIn("PRIVATE_CURSOR", out["action"]["cli"])
            self.assertIn("PRIVATE_CURSOR", run.call_args.args[0])


class DirectDirectoryTests(unittest.TestCase):
    def channel(self, responses):
        self.calls = []
        def transport(method, url, headers, body, timeout):
            self.calls.append((method, url, headers, body, timeout))
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response[0], response[1] if isinstance(response[1], bytes) else json.dumps(response[1]).encode()
        return FeishuDirectChannel(credentials.FeishuCreds("cli_test", "PRIVATE_SECRET", "test"),
                                    transport=transport, sleeper=lambda _: None)

    def token(self):
        return (200, {"code": 0, "tenant_access_token": "PRIVATE_TOKEN", "expire": 7200})

    def test_read_get_tenant_identity_and_token_cache_with_bounded_timeout(self):
        channel = self.channel([self.token(), (200, {"code": 0, "data": page([GROUP])}),
                                (200, {"code": 0, "data": page()})])
        self.assertEqual(chats.list_joined(channel)["groups"][0]["chat_id"], "oc_group")
        chats.list_joined(channel, page_token="next +&?")
        self.assertEqual(len(self.calls), 3)
        self.assertTrue(self.calls[1][1].startswith("https://open.feishu.cn/open-apis/im/v1/chats?"))
        self.assertIn("page_token=next+%2B%26%3F", self.calls[2][1])
        self.assertEqual(self.calls[1][0], "GET")
        self.assertEqual(self.calls[1][2]["Authorization"], "Bearer PRIVATE_TOKEN")
        self.assertTrue(all(call[4] == 5 for call in self.calls))

    def test_permission_bot_network_and_bad_json_are_safe_without_retries(self):
        for response, expected in [((200, {"code": 99991672, "msg": "PRIVATE"}), "im:chat:read"),
                                   ((200, {"code": 232025, "msg": "PRIVATE"}), "机器人"),
                                   (TimeoutError("PRIVATE"), "重试"),
                                   ((200, b"PRIVATE_NOT_JSON"), "响应"),
                                   ((503, {"code": 0, "data": page()}), "重试")]:
            with self.subTest(response=response):
                channel = self.channel([self.token(), response])
                with self.assertRaises(chats.DirectoryError) as error:
                    chats.list_joined(channel)
                self.assertNotIn("PRIVATE", str(error.exception))
                self.assertIn(expected, str(error.exception) + error.exception.hint)
                self.assertEqual(len(self.calls), 2)

    def test_directory_auth_response_is_bounded_and_secret_free(self):
        for response in [(200, b"P" * (1024 * 1024 + 1)), (200, {"code": 0, "tenant_access_token": ""})]:
            channel = self.channel([response])
            with self.assertRaises(chats.DirectoryError):
                chats.list_joined(channel)
            self.assertEqual(len(self.calls), 1)

    def test_incomplete_explicit_credentials_do_not_call_provider(self):
        channel = FeishuDirectChannel(credentials.FeishuCreds("cli_test", ""), transport=Mock())
        with self.assertRaises(chats.DirectoryError) as error:
            chats.list_joined(channel)
        self.assertEqual(error.exception.code, "E_CONFIG")
        channel._transport.assert_not_called()

    def test_invalid_cached_token_can_be_reloaded_without_automatic_retry(self):
        channel = self.channel([self.token(), (200, {"code": 99991663}), self.token(), (200, {"code": 0, "data": page()})])
        with self.assertRaises(chats.DirectoryError):
            chats.list_joined(channel)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(chats.list_joined(channel)["groups"], [])
        self.assertEqual(len(self.calls), 4)

    def test_streaming_transport_enforces_byte_limit(self):
        response = Mock()
        response.status = 200
        response.read.return_value = b"12345"
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch("urllib.request.build_opener") as opener:
            opener.return_value.open.return_value = response
            with self.assertRaises(chats.DirectoryError):
                _default_transport("GET", "https://example.invalid", {}, None, 5, response_limit=4)
            response.read.assert_called_once_with(5)


class CliDirectoryTests(unittest.TestCase):
    def test_external_cli_explicit_bot_and_same_application_environment(self):
        with patch("newspipe.backends.feishu_lark_cli._call", return_value={"ok": True, "data": page([GROUP])}) as call:
            self.assertEqual(chats.list_joined(LarkCliChannel())["groups"][0]["name"], "Readers")
            self.assertIn("--as", call.call_args.args)
            self.assertIn("bot", call.call_args.args)
            self.assertEqual(call.call_args.kwargs["timeout"], 10)
            self.assertEqual(call.call_args.kwargs["output_limit"], 1024 * 1024)

    def test_bounded_child_reads_stdout_and_stderr_and_reaps(self):
        from newspipe.backends.feishu_lark_cli import _bounded_run
        proc = _bounded_run([sys.executable, "-c", 'print("{\\"ok\\": true}")'], {}, timeout=2, output_limit=1024)
        self.assertEqual(json.loads(proc.stdout)["ok"], True)
        with self.assertRaises(chats.DirectoryError):
            _bounded_run([sys.executable, "-c", 'import sys; sys.stderr.write("x" * 2048)'], {}, timeout=2, output_limit=1024)
        with self.assertRaises(chats.DirectoryError):
            _bounded_run([sys.executable, "-c", "import time; time.sleep(10)"], {}, timeout=.1, output_limit=1024)

    def test_failed_cli_page_does_not_expose_payload(self):
        with patch("newspipe.backends.feishu_lark_cli._call", return_value={"ok": False, "error": {"code": 99991672, "message": "PRIVATE"}}):
            with self.assertRaises(chats.DirectoryError) as error:
                chats.list_joined(LarkCliChannel())
            self.assertNotIn("PRIVATE", str(error.exception))
            self.assertIn("im:chat:read", error.exception.hint)
