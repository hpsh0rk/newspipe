"""入站 —— 接收飞书卡片回调并路由到 `interaction.handle`（阶段 3）。

抽离前，卡片回调由宿主的飞书适配器接住、合成 `/card <tag> <json>` 斜杠命令、再进插件；
独立运行后这条链要自己接。两种模式：

| 模式 | 需要什么 | 适用 |
|---|---|---|
| `ws`（默认推荐） | `lark-oapi`，**只需出网** | 本机/NAT 后（没有公网 URL，飞书要求 HTTPS 回调地址） |
| `http` | 公网可达的 HTTPS 地址（或隧道） | 有服务器/反代的场景；只用标准库，配了 Encrypt Key 才需加密库 |

两种模式最后都汇到同一个 `dispatch()` —— 也就是宿主插件当初调的那个函数。
**回调只写状态 + 更新卡片实体，不直接发消息**（沿用 v2 不变量 3）。

为什么不自己实现长连接：飞书的长连接是 protobuf 帧 + 心跳（`pbbp2.Frame`），
手写属于逆向且没有控制台可调试；官方 SDK 只多一个可选依赖。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from newspipe import credentials, hooks as hooks_mod, interaction
from newspipe.errors import InboundError

# 飞书卡片动作事件类型（v2 schema）
CARD_ACTION_EVENT = "card.action.trigger"


# --------------------------------------------------------------------- 校验
def verify_signature(*, timestamp: str, nonce: str, encrypt_key: str, body: bytes,
                     signature: str) -> bool:
    """飞书签名：`sha256(timestamp + nonce + encrypt_key + body)` 的十六进制。

    仅在应用配置了 Encrypt Key 时才需要（也是唯一需要加密库的场景）。
    """
    if not (timestamp and nonce and encrypt_key and signature):
        return False
    expected = hashlib.sha256(
        f"{timestamp}{nonce}{encrypt_key}".encode("utf-8") + body).hexdigest()
    return hmac.compare_digest(expected, signature)


def _unpad(data: bytes) -> bytes:
    if not data:
        raise InboundError("解密结果为空")
    pad = data[-1]
    if pad < 1 or pad > 16 or data[-pad:] != bytes([pad]) * pad:
        raise InboundError("解密结果填充非法（密钥不匹配？）")
    return data[:-pad]


def decrypt_payload(encrypt_key: str, encrypted: str) -> dict[str, Any]:
    """AES-256-CBC 解密事件体（key=sha256(encrypt_key)，iv=密文前 16 字节）。

    需要 `pycryptodome`（`pip install 'newspipe[crypto]'`）—— 只有配了 Encrypt Key 才用到。
    """
    try:
        raw = base64.b64decode(encrypted)
    except Exception as exc:
        raise InboundError(f"encrypt 字段不是合法 base64：{type(exc).__name__}") from None
    if len(raw) < 32:
        raise InboundError("encrypt 字段过短，无法包含 IV + 密文")
    key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
    iv, ciphertext = raw[:16], raw[16:]
    try:
        from Crypto.Cipher import AES                      # pycryptodome
    except ImportError:
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        except ImportError:
            raise InboundError(
                "事件体已加密，但缺解密库：pip install 'newspipe[crypto]'"
                "（或把飞书应用的 Encrypt Key 清空，载荷即为明文 JSON）") from None
        decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        plain = _unpad(decryptor.update(ciphertext) + decryptor.finalize())
    else:
        plain = _unpad(AES.new(key, AES.MODE_CBC, iv).decrypt(ciphertext))
    try:
        data = json.loads(plain.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InboundError(f"解密后不是 JSON：{type(exc).__name__}") from None
    if not isinstance(data, dict):
        raise InboundError("解密后不是对象")
    return data


# --------------------------------------------------------------------- 路由
def extract_action(event: dict[str, Any]) -> dict[str, Any] | None:
    """从事件体里取出**我们自己的**卡片动作 payload（`domain: news`）。

    兼容两种形状：v2 `{"header":…,"event":{"action":{"value":{…}}}}`、
    以及老回调把 `action` 放在顶层的形状。`value` 也可能是 JSON 字符串。
    """
    if not isinstance(event, dict):
        return None
    node: Any = event.get("event") if isinstance(event.get("event"), dict) else event
    for candidate in (node.get("action") if isinstance(node, dict) else None,
                      event.get("action")):
        if not isinstance(candidate, dict):
            continue
        value = candidate.get("value")
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                continue
        if isinstance(value, dict) and value.get("domain") == "news":
            return value
    return None


def _resolve_hooks(hook_set: Any) -> Any:
    """允许传 `HookSet` 或 `() -> HookSet`。

    常驻服务传 lambda（读的是**当前**配置）⇒ 热加载的 hooks 立刻对点击生效，
    不必重启进程。解析失败按「没有 hook」处理：核心按钮必须照常工作。
    """
    if hook_set is None or isinstance(hook_set, hooks_mod.HookSet):
        return hook_set
    if callable(hook_set):
        try:
            return hook_set()
        except Exception:                                # noqa: BLE001
            return None
    return hook_set


def dispatch(event: dict[str, Any], *, news_dir: Path | None = None,
             hook_set: Any = None) -> dict[str, Any] | None:
    """事件 → 响应体。返回 None 表示「不是我们的事件」，调用方应静默忽略。

    - URL 校验（`url_verification`）→ 回 challenge；
    - 卡片动作 → 交给 `interaction.handle_json`（与宿主插件走同一个函数），
      有告警才回 toast，否则空对象（不改卡片、不插消息）。
    """
    if not isinstance(event, dict):
        return None
    challenge = event.get("challenge")
    if event.get("type") == "url_verification" or (challenge and "event" not in event):
        return {"challenge": challenge or ""}
    value = extract_action(event)
    if value is None:
        return None
    message = interaction.handle_json(json.dumps(value, ensure_ascii=False),
                                     news_dir=news_dir, hook_set=_resolve_hooks(hook_set))
    return {"toast": {"type": "info", "content": message}} if message else {}


# --------------------------------------------------------------------- HTTP
def _make_handler(*, path: str, creds: credentials.FeishuCreds, news_dir: Path | None,
                  hook_set: Any, logger: Callable[[str], None]) -> type:
    class Handler(BaseHTTPRequestHandler):
        server_version = "newspipe"

        def _send(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:                          # noqa: N802（stdlib 命名）
            self._send(200, {"ok": True, "service": "newspipe", "inbound": "http"})

        def do_POST(self) -> None:                         # noqa: N802
            if self.path.split("?")[0] != path:
                self._send(404, {"error": "not found"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            if creds.encrypt_key:
                ok = verify_signature(
                    timestamp=self.headers.get("X-Lark-Request-Timestamp") or "",
                    nonce=self.headers.get("X-Lark-Request-Nonce") or "",
                    encrypt_key=creds.encrypt_key, body=body,
                    signature=self.headers.get("X-Lark-Signature") or "")
                if not ok:
                    logger("入站：签名校验失败，已拒绝")
                    self._send(401, {"error": "invalid signature"})
                    return
            try:
                payload = json.loads(body.decode("utf-8")) if body else {}
                if isinstance(payload, dict) and payload.get("encrypt"):
                    payload = decrypt_payload(creds.encrypt_key, str(payload["encrypt"]))
            except (UnicodeDecodeError, json.JSONDecodeError, InboundError) as exc:
                logger(f"入站：请求体无法解析（{type(exc).__name__}: {exc}）")
                self._send(400, {"error": "bad request"})
                return
            try:
                result = dispatch(payload, news_dir=news_dir, hook_set=hook_set)
            except Exception as exc:                       # 回调绝不能让进程崩
                logger(f"入站：处理失败（{type(exc).__name__}: {exc}）")
                self._send(500, {"error": "handler failed"})
                return
            if result is None:
                self._send(200, {"code": 0})
                return
            self._send(200, {"code": 0, **result})

        def log_message(self, fmt: str, *args: Any) -> None:   # 别往 stderr 打原始日志
            logger(f"入站 http: {fmt % args}")

    return Handler


def start_http(*, service_cfg: dict[str, Any], creds: credentials.FeishuCreds,
               news_dir: Path | None = None, hook_set: Any = None,
               logger: Callable[[str], None] = print) -> tuple[ThreadingHTTPServer, str]:
    """起 HTTP 回调服务器（返回 server 与路径；调用方负责 serve_forever 线程）。"""
    http_cfg = dict(service_cfg.get("http") or {})
    host = str(http_cfg.get("host") or "127.0.0.1")
    port = int(http_cfg.get("port") or 8787)
    path = str(http_cfg.get("path") or "/feishu/events")
    handler = _make_handler(path=path, creds=creds, news_dir=news_dir,
                            hook_set=hook_set, logger=logger)
    server = ThreadingHTTPServer((host, port), handler)
    return server, path


# --------------------------------------------------------------------- 长连接
class _Sdk:
    """把 lark-oapi 的入口收在一处，方便测试注入替身。"""

    def __init__(self, lark: Any, response_cls: Any) -> None:
        self.lark = lark
        self.response_cls = response_cls


def load_sdk() -> _Sdk:
    try:
        import lark_oapi as lark
        from lark_oapi.event.callback.model.p2_card_action_trigger import (
            P2CardActionTriggerResponse,
        )
    except ImportError:
        raise InboundError(
            "长连接模式需要官方 SDK：pip install 'newspipe[feishu]'"
            "（或把 inbound.mode 改成 http/none）") from None
    return _Sdk(lark, P2CardActionTriggerResponse)


def make_action_handler(*, sdk: _Sdk, news_dir: Path | None = None,
                        hook_set: Any = None,
                        logger: Callable[[str], None] = print,
                        state: dict[str, Any] | None = None) -> Callable[[Any], Any]:
    """把 SDK 的 `P2CardActionTrigger` 转成我们的 payload 并 dispatch。

    返回 SDK 期望的响应对象（空响应 = 不改卡片，翻页靠更新卡片实体）。
    """
    counters = state if state is not None else {}

    def on_action(data: Any) -> Any:
        counters["events"] = counters.get("events", 0) + 1
        try:
            event = getattr(data, "event", None)
            action = getattr(event, "action", None)
            value = getattr(action, "value", None)
            if not isinstance(value, dict):
                counters["ignored"] = counters.get("ignored", 0) + 1
                return sdk.response_cls()
            result = dispatch({"header": {"event_type": CARD_ACTION_EVENT},
                               "event": {"action": {"value": value}}},
                              news_dir=news_dir, hook_set=hook_set)
            if result is None:
                counters["ignored"] = counters.get("ignored", 0) + 1
                return sdk.response_cls()
            counters["handled"] = counters.get("handled", 0) + 1
            toast = (result or {}).get("toast") or {}
            if toast.get("content"):
                return sdk.response_cls({"toast": {"type": toast.get("type", "info"),
                                                   "content": toast["content"]}})
            return sdk.response_cls()
        except Exception as exc:                            # 回调里任何异常都不能冒泡
            counters["errors"] = counters.get("errors", 0) + 1
            logger(f"入站 ws：处理失败（{type(exc).__name__}: {exc}）")
            return sdk.response_cls()

    return on_action


def run_ws(creds: credentials.FeishuCreds, *, news_dir: Path | None = None,
           hook_set: Any = None, logger: Callable[[str], None] = print,
           sdk: _Sdk | None = None, client: Any | None = None,
           state: dict[str, Any] | None = None,
           log_level: str | None = None) -> Any:
    """起长连接并**阻塞**（SDK 自带重连）。`sdk` / `client` 可注入以便测试。

    SDK 日志级别默认 INFO（能看到「connected to」）；排查入站问题时用
    `NEWSPIPE_SDK_LOG=debug` 打开 ping/pong 与消息类型（含事件体，仅本地调试用）。
    """
    sdk = sdk or load_sdk()
    on_action = make_action_handler(sdk=sdk, news_dir=news_dir, hook_set=hook_set,
                                    logger=logger, state=state)
    if client is None:
        wanted = (log_level or os.environ.get("NEWSPIPE_SDK_LOG") or "info").strip().lower()
        levels = {"debug": sdk.lark.LogLevel.DEBUG, "info": sdk.lark.LogLevel.INFO,
                  "warning": sdk.lark.LogLevel.WARNING, "warn": sdk.lark.LogLevel.WARNING,
                  "error": sdk.lark.LogLevel.ERROR}
        handler = (sdk.lark.EventDispatcherHandler
                   .builder(creds.encrypt_key, creds.verification_token)
                   .register_p2_card_action_trigger(on_action)
                   .build())
        client = sdk.lark.ws.Client(creds.app_id, creds.app_secret, event_handler=handler,
                                    domain=creds.base_url,
                                    log_level=levels.get(wanted, sdk.lark.LogLevel.INFO))
    logger(f"入站 ws：连接 {creds.base_url}（app_id={creds.app_id}）…")
    client.start()
    return client


# --------------------------------------------------------------------- 句柄
@dataclass
class InboundHandle:
    """入站运行句柄：`describe()` 给状态用，`stop()` 给退出用。"""

    mode: str
    thread: threading.Thread | None = None
    server: Any = None
    client: Any = None
    path: str = ""
    state: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def describe(self) -> dict[str, Any]:
        info: dict[str, Any] = {"mode": self.mode, "alive": bool(self.thread and self.thread.is_alive())}
        if self.path:
            info["path"] = self.path
        if self.state:
            info["counters"] = dict(self.state)
        if self.error:
            info["error"] = self.error
        return info

    def stop(self) -> None:
        if self.server is not None:
            try:
                self.server.shutdown()
                self.server.server_close()
            except Exception:
                pass
        client = self.client
        stop = getattr(client, "stop", None) or getattr(client, "close", None)
        if callable(stop):
            try:
                stop()
            except Exception:
                pass


def start_inbound(*, mode: str, service_cfg: dict[str, Any], creds: credentials.FeishuCreds,
                  news_dir: Path | None = None, hook_set: Any = None,
                  logger: Callable[[str], None] = print, sdk: _Sdk | None = None,
                  client: Any | None = None) -> InboundHandle:
    """按模式起入站（daemon 线程），返回句柄。`mode=none` 直接返回空句柄。"""
    if mode == "none":
        return InboundHandle(mode="none")
    state: dict[str, Any] = {}
    if mode == "http":
        server, path = start_http(service_cfg=service_cfg, creds=creds, news_dir=news_dir,
                                  hook_set=hook_set, logger=logger)
        thread = threading.Thread(target=server.serve_forever, name="newspipe-inbound-http",
                                  daemon=True)
        thread.start()
        logger(f"入站 http：监听 {server.server_address[0]}:{server.server_address[1]}{path}")
        return InboundHandle(mode="http", thread=thread, server=server, path=path, state=state)
    if mode == "ws":
        holder: dict[str, Any] = {}

        def _run() -> None:
            try:
                holder["client"] = run_ws(creds, news_dir=news_dir, hook_set=hook_set,
                                          logger=logger, sdk=sdk, client=client, state=state)
            except Exception as exc:                       # 连接失败要留痕，不许静默
                holder["error"] = f"{type(exc).__name__}: {exc}"
                logger(f"入站 ws：连接失败（{holder['error']}）")

        thread = threading.Thread(target=_run, name="newspipe-inbound-ws", daemon=True)
        thread.start()
        return InboundHandle(mode="ws", thread=thread, client=client, state=state)
    raise InboundError(f"未知的入站模式 {mode!r}（可选：ws / http / none）")
