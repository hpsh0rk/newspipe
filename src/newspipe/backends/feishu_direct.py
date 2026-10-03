"""投递通道实现 —— **直连飞书 OpenAPI**（阶段 2，替代 `lark-cli` 子进程）。

抽离的意义在这里落地：`lark-cli` 是宿主的二进制，它自己管凭据、自己找 app；直连之后
凭据来自 `credentials.py`（env / dotenv / 钥匙串），进程只要能出网就能发卡。

覆盖四个端点（与 `feishu_lark_cli.py` 行为等价，实机验证见 `specs/stage2-3-standalone.md`）：

```
POST /open-apis/auth/v3/tenant_access_token/internal      → tenant_access_token（缓存 2h，提前 5min 换）
POST /open-apis/cardkit/v1/cards                          → card_id
POST /open-apis/im/v1/messages?receive_id_type=chat_id    → message_id
PUT  /open-apis/cardkit/v1/cards/<card_id>                → 全量更新（翻页；sequence 严格递增）
```

错误分三类处理，**不静默**：
- token 失效（99991663 等）→ 强制刷新后重试一次（这是最容易发生的瞬时故障）；
- 限流（99991400 等）/ 5xx / 网络异常 → 退避重试；
- 配置与权限类（230002 bot 不在群、11310 元素超限、230099 content 包错层）→ **不重试**，
  直接抛带原因的错误（重试只会重复失败，还会掩盖真因）。

HTTP 出口**显式禁用环境代理**：飞书是境内直连服务，而本机 shell 常年带 ClashX 注入的
代理变量；走代理在 rule 模式下虽然也通，但多一跳、且 Clash 重启间隙会瞬时失败。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable

from newspipe import credentials
from newspipe.errors import DeliveryError

# token 失效/过期：刷新后重试有意义
_TOKEN_CODES = frozenset({99991663, 99991661, 99991664, 99991668})
# 限流：退避重试
_RETRY_CODES = frozenset({99991400, 99991401, 99991402})
# 配置/权限类：重试无意义，直接暴露原因
_FATAL_CODES = frozenset({230002, 11310, 230099, 230013, 230020})
# token 端点返回里拿 token 的两种形状（internal 端点不带 data 包裹）
_TOKEN_PATHS = ("data.tenant_access_token", "tenant_access_token")


def _dig(payload: dict, dotted: str) -> Any:
    cur: Any = payload
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _default_transport(method: str, url: str, headers: dict[str, str],
                       body: bytes | None, timeout: float) -> tuple[int, bytes]:
    """默认 HTTP 出口：标准库 urllib，显式禁用环境代理（见模块头）。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, data=body, method=method.upper())
    for key, value in headers.items():
        req.add_header(key, value)
    try:
        with opener.open(req, timeout=timeout) as resp:
            return int(resp.status), resp.read()
    except urllib.error.HTTPError as exc:          # 4xx/5xx 也要读 body：飞书的错误码在里面
        try:
            return int(exc.code), exc.read() or b""
        finally:
            exc.close()


class FeishuDirectChannel:
    """满足 `ports.CardChannel`：直连 OpenAPI 建实体 / 发卡 / 更新实体 / 发文本。

    凭据**惰性解析**：构造时不需要凭据，首次调用才解析。这样注册表、协议测试、
    以及「只想 import 一下」的场景都不会因为缺密钥而炸。
    """

    name = "feishu_direct"

    def __init__(self, creds: credentials.FeishuCreds | None = None, *,
                 service_cfg: dict[str, Any] | None = None,
                 news_dir: Path | None = None,
                 transport: Callable[..., tuple[int, bytes]] | None = None,
                 timeout: float = 30.0,
                 clock: Callable[[], float] = time.time,
                 sleeper: Callable[[float], None] = time.sleep) -> None:
        self._creds = creds
        self._service_cfg = dict(service_cfg or {})
        self._news_dir = news_dir
        self._transport = transport or _default_transport
        self._timeout = timeout
        self._clock = clock
        self._sleep = sleeper
        self._token = ""
        self._token_expire = 0.0
        self.stats: dict[str, int] = {"calls": 0, "retries": 0, "token_refreshes": 0}

    # ---------------------------------------------------------------- 凭据
    @property
    def creds(self) -> credentials.FeishuCreds:
        if self._creds is None:
            self._creds = credentials.resolve_feishu(self._service_cfg, news_dir=self._news_dir)
        return self._creds

    def describe(self) -> dict[str, Any]:
        """可安全打印的自述（无密钥）。"""
        info: dict[str, Any] = {"channel": self.name, "stats": dict(self.stats)}
        try:
            info["creds"] = self.creds.describe()
        except Exception as exc:                    # 凭据缺失也要能自述，否则 --status 会炸
            info["creds"] = {"error": f"{type(exc).__name__}: {exc}"}
        return info

    # ---------------------------------------------------------------- HTTP
    def _backoff(self, attempt: int) -> None:
        self.stats["retries"] += 1
        self._sleep(0.5 * (2 ** attempt))

    def _http(self, method: str, path: str, body: dict[str, Any] | None = None, *,
              token: bool = True, retries: int = 2) -> dict[str, Any]:
        url = self.creds.base_url + path
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        last_error = ""
        for attempt in range(retries + 1):
            headers = {"Content-Type": "application/json; charset=utf-8"}
            if token:
                headers["Authorization"] = f"Bearer {self._token_now()}"
            try:
                status, raw = self._transport(method, url, headers, data, self._timeout)
            except Exception as exc:                # 网络层异常：退避重试
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < retries:
                    self._backoff(attempt)
                    continue
                raise DeliveryError(f"飞书 API 不可达（{method} {path}）：{last_error}") from None
            self.stats["calls"] += 1
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                if status >= 500 and attempt < retries:
                    self._backoff(attempt)
                    continue
                raise DeliveryError(
                    f"飞书 API 返回不可解析（HTTP {status}，{method} {path}）：{raw[:200]!r}") from None
            if not isinstance(payload, dict):
                raise DeliveryError(f"飞书 API 返回非对象（{method} {path}）：{str(payload)[:200]}")
            code = int(payload.get("code") or 0)
            if code == 0:
                return payload
            message = str(payload.get("msg") or payload.get("message") or "")
            if token and code in _TOKEN_CODES:
                self._token_now(force=True)         # 刷新后重试（不消耗退避，token 过期是瞬时的）
                if attempt < retries:
                    continue
            if code in _RETRY_CODES or (status >= 500 and code not in _FATAL_CODES):
                if attempt < retries:
                    self._backoff(attempt)
                    continue
            raise DeliveryError(f"飞书 API 错误 code={code} msg={message}（{method} {path}）")
        raise DeliveryError(f"飞书 API 重试耗尽（{method} {path}）：{last_error}")

    def _token_now(self, *, force: bool = False) -> str:
        """取 tenant_access_token（内存缓存；提前 5 分钟换，避免边界过期）。"""
        now = self._clock()
        if not force and self._token and now < self._token_expire:
            return self._token
        payload = self._http("POST", "/open-apis/auth/v3/tenant_access_token/internal",
                             {"app_id": self.creds.app_id, "app_secret": self.creds.app_secret},
                             token=False, retries=1)
        tok = ""
        for dotted in _TOKEN_PATHS:
            value = _dig(payload, dotted)
            if value:
                tok = str(value)
                break
        if not tok:
            raise DeliveryError("飞书未返回 tenant_access_token（检查 app_id/app_secret 是否匹配）")
        expire = 0.0
        for dotted in ("data.expire", "expire"):
            value = _dig(payload, dotted)
            if value:
                expire = float(value)
                break
        self._token = tok
        self._token_expire = now + max(60.0, (expire or 7200.0) - 300.0)
        self.stats["token_refreshes"] += 1
        return tok

    # ---------------------------------------------------------------- 通道
    def create_entity(self, card: dict) -> str:
        """建卡片实体，返回 card_id。"""
        payload = self._http("POST", "/open-apis/cardkit/v1/cards",
                             {"type": "card_json",
                              "data": json.dumps(card, ensure_ascii=False)})
        card_id = str(_dig(payload, "data.card_id") or "")
        if not card_id:
            raise DeliveryError(f"创建卡片实体未返回 card_id：{str(payload)[:200]}")
        return card_id

    def send_card(self, chat_id: str, card_id: str) -> str:
        """按 card_id 发卡，返回 message_id。"""
        content = json.dumps({"type": "card", "data": {"card_id": card_id}}, ensure_ascii=False)
        payload = self._http("POST", "/open-apis/im/v1/messages?receive_id_type=chat_id",
                             {"receive_id": chat_id, "msg_type": "interactive", "content": content})
        message_id = str(_dig(payload, "data.message_id") or "")
        if not message_id:
            raise DeliveryError(f"发送卡片未返回 message_id：{str(payload)[:200]}")
        return message_id

    def update_entity(self, card_id: str, sequence: int, card: dict) -> bool:
        """全量更新卡片实体（翻页就是它）。sequence 必须严格递增。"""
        self._http("PUT", f"/open-apis/cardkit/v1/cards/{card_id}",
                   {"card": {"type": "card_json", "data": json.dumps(card, ensure_ascii=False)},
                    "uuid": str(uuid.uuid4()),
                    "sequence": int(sequence)})
        return True

    def send_text(self, chat_id: str, text: str) -> str:
        """纯文本兜底（卡片发不出去时的降级通道；也是 painpoints 摘要的投递方式）。"""
        payload = self._http("POST", "/open-apis/im/v1/messages?receive_id_type=chat_id",
                             {"receive_id": chat_id, "msg_type": "text",
                              "content": json.dumps({"text": text}, ensure_ascii=False)})
        return str(_dig(payload, "data.message_id") or "")

    # ---------------------------------------------------------------- 自检
    def probe(self, chat_id: str, *, send: bool = True) -> dict[str, Any]:
        """自检四步：token → 建实体 → 发卡 → 更新实体。返回**可安全打印**的步骤表。

        `send=False` 只验 token（不往群里发任何东西）。
        """
        steps: list[dict[str, Any]] = []

        def step(name: str, fn: Callable[[], Any]) -> Any:
            started = self._clock()
            try:
                value = fn()
            except Exception as exc:
                steps.append({"step": name, "ok": False, "ms": round((self._clock() - started) * 1000),
                              "error": f"{type(exc).__name__}: {exc}"})
                raise
            steps.append({"step": name, "ok": True, "ms": round((self._clock() - started) * 1000),
                          "result": (value if isinstance(value, str) else "")[:80] or "ok"})
            return value

        def token_step() -> str:
            token = self._token_now(force=True)
            return f"已获取（{len(token)} 字符，不打印）"

        result: dict[str, Any] = {"channel": self.name}
        try:
            step("tenant_access_token", token_step)
            result["creds"] = self.creds.describe()
            if send:
                # ⚠️ 必须是 **schema 2.0** 卡片：给 schema 1.0 的 `{config, elements}` 形状，
                # 服务端会返回误导性的 `200610 body is nil`（实测 2026-10-03），让人以为是
                # HTTP 层或鉴权问题。管线渲染出来的本来就是 2.0，这里保持一致。
                card = {"schema": "2.0",
                        "config": {"update_multi": True},
                        "header": {"template": "blue",
                                   "title": {"tag": "plain_text", "content": "newspipe 直连自检"}},
                        "body": {"elements": [
                            {"tag": "div",
                             "text": {"tag": "lark_md",
                                      "content": "这条卡片由 `backends/feishu_direct.py` "
                                                 "**直接调用飞书 OpenAPI** 发出（未经 lark-cli）。"}}]}}
                card_id = step("create_entity", lambda: self.create_entity(card))
                message_id = step("send_card", lambda: self.send_card(chat_id, card_id))
                step("update_entity", lambda: self.update_entity(
                    card_id, 1, {**card, "header": {
                        "template": "green",
                        "title": {"tag": "plain_text", "content": "自检通过（已更新实体）"}}}))
                result["card_id"] = card_id
                result["message_id"] = message_id
        finally:
            result["steps"] = steps
        return result
