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
import secrets
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from newspipe import credentials, hooks as hooks_mod, interaction
from newspipe.errors import InboundError

# 飞书卡片动作事件类型（v2 schema）
CARD_ACTION_EVENT = "card.action.trigger"

# 本项目拥有的卡片域（按钮 value.domain 的值）。宿主侧的转发器就是按这个域把事件路由
# 过来的 —— 协议与宿主接入示例见仓库根目录的 AGENTS.md。
DOMAIN = "news"


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

    兼容三种形状：

    1. v2 回调：`{"header":…,"event":{"action":{"value":{…}}}}`；
    2. 老回调把 `action` 放在顶层；
    3. **宿主转发器信封**（见 AGENTS.md 的宿主接入示例）：`{"domain":"news","tag":…,"value":{…}}`
       —— 转发器拿不到原始回调体（网关只给它 tag + value），所以 value 在顶层。

    `value` 也可能是 JSON 字符串。
    """
    if not isinstance(event, dict):
        return None
    candidates: list[Any] = []
    node: Any = event.get("event") if isinstance(event.get("event"), dict) else event
    if isinstance(node, dict):
        candidates.append(node.get("action"))
    candidates.append(event.get("action"))
    candidates.append({"value": event.get("value")})       # 形状 3
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        value = candidate.get("value")
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                continue
        if isinstance(value, dict) and value.get("domain") == DOMAIN:
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
def _same_origin(origin: str, host: str) -> bool:
    """同源校验：`Origin` 的 netloc 必须等于请求的 `Host`。

    比对 `Host` 而不是写死 `127.0.0.1` —— 门户 embed 时页面是从主机名（tailnet / LAN）
    打开的，写死回环会把正常的手机访问挡掉。跨站页面的 `Origin` 不可能等于我们的 `Host`。
    """
    try:
        return bool(host) and urlparse(origin).netloc.lower() == host.lower()
    except ValueError:
        return False


def _flash_of(envelope: dict[str, Any]) -> dict[str, Any]:
    """从 CLI 信封里挑出横幅要显示的东西（**必须短**：它要编码进 URL）。"""
    action = envelope.get("action") or {}
    err = envelope.get("error") or {}
    raw = envelope.get("data")
    data: dict[str, Any] = raw if isinstance(raw, dict) else {}
    ok = bool(envelope.get("ok"))
    summary = ""
    if ok:
        bits = [f"{k}: {data[k]}" for k in ("summary", "message", "changed", "note")
                if data.get(k)]
        summary = " · ".join(bits) or "完成"
    return {
        "ok": ok,
        "label": str(action.get("label") or envelope.get("command") or "操作"),
        "summary": str(summary)[:400],
        "message": str(err.get("message") or "")[:400],
        "hint": str(err.get("hint") or "")[:300],
        "detail": json.dumps(data, ensure_ascii=False)[:600] if data else "",
        "cli": str(action.get("cli") or ""),
    }


def _make_handler(*, path: str, creds: credentials.FeishuCreds | None, news_dir: Path | None,
                  hook_set: Any, logger: Callable[[str], None],
                  accept_events: bool = True, view_path: str = "/view",
                  view_actions: bool = False, action_token: str = "") -> type:
    #: 写操作令牌：进程启动时生成一次，页面渲染时嵌进表单。
    #: 没有它，浏览器里**任何一个网页**都能 POST 到这个本地端口（CSRF）—— 本地端口对
    #: 浏览器是可达的，这不是理论风险。
    token = (action_token or secrets.token_urlsafe(24)) if view_actions else ""

    class Handler(BaseHTTPRequestHandler):
        server_version = "newspipe"

        def _send(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, status: int, page: str) -> None:
            body = page.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _view(self) -> dict[str, Any]:
            """只读视图契约。这里**不**碰 state 布局——布局知识归 `view.build`。"""
            from newspipe import view as view_mod

            return view_mod.build(news_dir)

        def _config_page(self, payload: dict[str, Any], act: dict[str, Any] | None,
                         flash: dict[str, Any] | None, query: dict[str, list[str]]) -> str:
            """配置页的输入装配：schema + 目录搜索 + URL 发现 + 预填 + 编辑预填。"""
            from newspipe import config as config_mod
            from newspipe import rss as rss_mod
            from newspipe import view as view_mod

            news = news_dir or config_mod.default_news_dir()
            schema = config_mod.describe_schema(news)
            page: dict[str, Any] = {"q": (query.get("q") or [""])[0],
                                    "hash": str((act or {}).get("sources_hash") or "")}
            if query.get("u"):
                page["discover_url"] = (query.get("u") or [""])[0]
                try:
                    page["discover"] = rss_mod.discover(page["discover_url"])
                except Exception as exc:                # noqa: BLE001 —— 失败要显示给人看
                    page["discover_error"] = f"{type(exc).__name__}: {exc}"
            prefill: dict[str, Any] = {}
            hint = ""
            if query.get("use"):
                try:
                    row: dict[str, Any] | None = rss_mod.CATALOG[int((query["use"] or ["0"])[0])]
                except (ValueError, IndexError):
                    row = None
                if row:
                    cand = rss_mod.as_candidate(row)
                    prefill = {"adapter": "rsshub", "feeds": [cand["feed"]],
                               "feed_label": cand["feed_label"], "fetch.trigger": "slot",
                               "fetch.slots": ["am"]}
                    hint = str(cand["name_hint"])
            if query.get("feed"):
                url = (query["feed"] or [""])[0]
                prefill = {"adapter": "rsshub", "feeds": [url], "fetch.trigger": "slot",
                           "fetch.slots": ["am"]}
                hint = rss_mod._slug(urlparse(url).netloc or "feed")
            if prefill:
                page["prefill"] = prefill
                page["prefill_name"] = hint
            if query.get("edit"):
                name = (query["edit"] or [""])[0]
                try:
                    cfg = config_mod.load(news)
                    if name in cfg.sources:
                        page["edit"] = name
                        page["values"] = config_mod.source_values(cfg.sources[name])
                    else:
                        page["discover_error"] = f"没有信源 {name!r}"
                except Exception as exc:                # noqa: BLE001
                    page["discover_error"] = f"读配置失败：{exc}"
            if not any(query.get(k) for k in ("q", "u", "use", "feed", "edit")):
                page["catalog"] = rss_mod.search("")    # 空查询 = 目录浏览
            elif page["q"]:
                page["catalog"] = rss_mod.search(page["q"])
            return view_mod.config_html(payload, schema=schema, actions=act, flash=flash, page=page)

        def do_GET(self) -> None:                          # noqa: N802（stdlib 命名）
            route = self.path.split("?")[0]
            query = parse_qs(urlparse(self.path).query)
            if route in ("/", "/ops", "/config", view_path):
                try:
                    payload = self._view()
                except Exception as exc:                   # 配置坏了要说清楚，别回空壳
                    self._send(503, {"ok": False, "service": "newspipe", "view": route,
                                     "error": {"code": "E_CONFIG", "message": f"{type(exc).__name__}: {exc}",
                                               "hint": "检查 info/news/{sources,service}.yaml"}})
                    return
                if route == view_path:
                    self._send(200, payload)               # JSON 契约：形状与页面无关
                    return
                from newspipe import view as view_mod

                act, flash = None, None
                if token:
                    from newspipe import config as config_mod

                    try:
                        act = view_mod.action_snapshot(
                            news_dir or config_mod.default_news_dir())
                        act["token"] = token
                    except Exception as exc:               # 动作面坏了不该连累只读页
                        logger(f"写操作面构建失败（{type(exc).__name__}: {exc}）")
                        act = None
                    flash = view_mod.flash_decode((query.get("r") or [""])[0])
                if route == "/ops":
                    page = view_mod.ops_html(payload, actions=act, flash=flash)
                elif route == "/config":
                    page = self._config_page(payload, act, flash, query)
                else:
                    page = view_mod.cockpit_html(payload, flash=flash)
                self._send_html(200, page)
                return
            self._send(200, {"ok": True, "service": "newspipe", "inbound": "http",
                             "view": view_path if view_path else None,
                             "events": path if accept_events else None})

        def _do_action(self, name: str) -> None:
            """页面写操作：表单 → CLI（**同一份 handler**）。

            三道门禁，缺一不可：
            1. `view.actions` 开关（没开就 404，页面里连表单都不会渲染）；
            2. 同源（`Origin` 有且不等于本请求 `Host` ⇒ 403）—— 浏览器里任何网页都能
               POST 到本地端口，跨站请求必须挡在这里；
            3. 一次性令牌（`secrets.compare_digest`）—— 真正的守门人，令牌只有渲染页面
               的那一次 GET 才拿得到，跨站页面读不到。
            """
            from newspipe import actions as actions_mod
            from newspipe import config as config_mod
            from newspipe import view as view_mod

            if not token:
                self._send(404, {"ok": False, "error": {
                    "code": "E_DISABLED",
                    "message": "写操作未开启（service.yaml: view.actions）"}})
                return
            origin = self.headers.get("Origin")
            if origin and not _same_origin(origin, self.headers.get("Host") or ""):
                logger(f"写操作：拒绝跨站来源 {origin}")
                self._send(403, {"ok": False, "error": {
                    "code": "E_ORIGIN", "message": f"跨站请求已拒绝（Origin: {origin}）"}})
                return
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            form = parse_qs(body.decode("utf-8", "replace"))
            given = (form.get("token") or [""])[0]
            if not secrets.compare_digest(given, token):
                logger("写操作：令牌不匹配，已拒绝")
                self._send(403, {"ok": False, "error": {
                    "code": "E_TOKEN", "message": "令牌不匹配（页面可能过期，刷新重试）",
                    "hint": "令牌在服务重启后更换；刷新页面即可"}})
                return
            try:
                envelope = actions_mod.dispatch(
                    name, form, news_dir or config_mod.default_news_dir())
            except Exception as exc:                       # 写操作绝不能让进程崩
                logger(f"写操作 {name} 崩了（{type(exc).__name__}: {exc}）")
                self._send(500, {"ok": False, "error": {
                    "code": "E_INTERNAL", "message": f"{type(exc).__name__}: {exc}"}})
                return
            logger(f"写操作 {name} → {envelope.get('command')} ok={envelope.get('ok')}")
            # PRG（Post/Redirect/Get）：结果编码进 URL，刷新页面不会重放写操作
            self.send_response(303)
            self.send_header("Location", f"/?r={view_mod.flash_encode(_flash_of(envelope))}")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self) -> None:                         # noqa: N802
            route = self.path.split("?")[0]
            if route.startswith("/api/actions/"):
                self._do_action(route.rsplit("/", 1)[-1])
                return
            if not accept_events:
                self._send(404, {"error": "events disabled",
                                 "hint": "inbound.mode 不是 http；卡片事件由持有长连接的一方转发"})
                return
            if self.path.split("?")[0] != path:
                self._send(404, {"error": "not found"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            if creds is not None and creds.encrypt_key:
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
                    if creds is None or not creds.encrypt_key:
                        raise InboundError("收到加密事件体但没配 Encrypt Key")
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


DEFAULT_HTTP_PORT = 8787


def http_bind(service_cfg: dict[str, Any]) -> tuple[str, int, str]:
    """解析 `service.yaml: inbound.http` 的绑定参数。

    `port: 0` 是「让内核挑一个空闲端口」，**不能**被 `or` 吃掉（否则测试与多实例会撞在默认
    端口上）—— 所以这里显式区分「没配」与「配了 0」。

    `NEWSPIPE_BIND_HOST` 是**部署事实**的逃生口：容器里必须绑 `0.0.0.0`，否则发布端口
    够不着（Docker 的转发落到容器网卡上，而进程只听了容器自己的回环）。宿主机上绑
    127.0.0.1 是对的，所以这个改写只由部署显式给，不改默认。
    """
    http_cfg = dict(service_cfg.get("http") or {})
    host = (os.environ.get("NEWSPIPE_BIND_HOST") or "").strip() \
        or str(http_cfg.get("host") or "127.0.0.1")
    raw_port = http_cfg.get("port")
    port = DEFAULT_HTTP_PORT if raw_port is None else int(raw_port)
    path = str(http_cfg.get("path") or "/feishu/events")
    return host, port, path


def start_http(*, service_cfg: dict[str, Any], creds: credentials.FeishuCreds | None,
               news_dir: Path | None = None, hook_set: Any = None,
               logger: Callable[[str], None] = print, accept_events: bool = True,
               view_path: str = "/view", view_actions: bool = False,
               action_token: str = "") -> tuple[ThreadingHTTPServer, str]:
    """起 HTTP 服务器（返回 server 与事件路径；调用方负责 serve_forever 线程）。

    `accept_events=False` = 只读视图模式：同一台服务器只回答 `GET /` 与 `GET <view_path>`，
    POST 一律 404。给 `inbound.mode != http` 的部署用（视图与入站解耦，但不另开端口）。

    `view_actions=True` 才开写操作（`POST /api/actions/*`）；`action_token` 不给就自动生成。
    """
    host, port, path = http_bind(service_cfg)
    handler = _make_handler(path=path, creds=creds, news_dir=news_dir,
                            hook_set=hook_set, logger=logger, accept_events=accept_events,
                            view_path=view_path, view_actions=view_actions,
                            action_token=action_token)
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
    view_path: str = ""
    state: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def describe(self) -> dict[str, Any]:
        info: dict[str, Any] = {"mode": self.mode, "alive": bool(self.thread and self.thread.is_alive())}
        if self.path:
            info["path"] = self.path
        if self.view_path:
            info["view"] = self.view_path
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


def start_inbound(*, mode: str, service_cfg: dict[str, Any], creds: credentials.FeishuCreds | None,
                  news_dir: Path | None = None, hook_set: Any = None,
                  logger: Callable[[str], None] = print, sdk: _Sdk | None = None,
                  client: Any | None = None, view: dict[str, Any] | None = None) -> InboundHandle:
    """按模式起入站（daemon 线程），返回句柄。

    只读视图（`GET /` 与 `GET <view.path>`）与事件入站**解耦**：`view.enabled` 打开时，
    即使 `mode=none/ws` 也会起同一台只读服务器（`accept_events=False`，POST 404）。
    视图起不来**不阻塞调度**（只读的附加面不该拖垮采集）——只记一条告警。
    """
    view_cfg = dict(view or {})
    view_on = bool(view_cfg.get("enabled"))
    view_path = str(view_cfg.get("path") or "/view")
    #: 写操作是**只读视图之上的第二层开关**：没有页面就没有按钮。
    view_actions = view_on and bool(view_cfg.get("actions"))
    state: dict[str, Any] = {}

    def _view_only() -> tuple[Any, threading.Thread, str]:
        server, _ = start_http(service_cfg=service_cfg, creds=None, news_dir=news_dir,
                               hook_set=hook_set, logger=logger, accept_events=False,
                               view_path=view_path, view_actions=view_actions)
        thread = threading.Thread(target=server.serve_forever, name="newspipe-view-http",
                                  daemon=True)
        thread.start()
        return server, thread, view_path

    if mode == "none":
        if not view_on:
            return InboundHandle(mode="none")
        try:
            server, thread, vpath = _view_only()
        except OSError as exc:                              # 端口被占不该拖垮调度
            logger(f"⚠️ 只读视图未启动（{type(exc).__name__}: {exc}）")
            return InboundHandle(mode="none")
        logger(f"只读视图：监听 {server.server_address[0]}:{server.server_address[1]}{vpath}")
        return InboundHandle(mode="none", thread=thread, server=server, view_path=vpath, state=state)

    if mode == "http":
        server, path = start_http(service_cfg=service_cfg, creds=creds, news_dir=news_dir,
                                  hook_set=hook_set, logger=logger, accept_events=True,
                                  view_path=view_path if view_on else "",
                                  view_actions=view_actions)
        thread = threading.Thread(target=server.serve_forever, name="newspipe-inbound-http",
                                  daemon=True)
        thread.start()
        logger(f"入站 http：监听 {server.server_address[0]}:{server.server_address[1]}{path}"
               + (f"（只读视图 {view_path}）" if view_on else ""))
        return InboundHandle(mode="http", thread=thread, server=server, path=path,
                             view_path=view_path if view_on else "", state=state)
    if mode == "ws":
        if creds is None:
            raise InboundError("inbound.mode=ws 需要飞书应用凭据（app_id/app_secret）")
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
        handle = InboundHandle(mode="ws", thread=thread, client=client, state=state)
        if view_on:
            try:
                server, _vt, vpath = _view_only()
            except OSError as exc:
                logger(f"⚠️ 只读视图未启动（{type(exc).__name__}: {exc}）")
            else:
                handle.server = server
                handle.view_path = vpath
                logger(f"只读视图：监听 {server.server_address[0]}:{server.server_address[1]}{vpath}")
        return handle
    raise InboundError(f"未知的入站模式 {mode!r}（可选：ws / http / none）")
