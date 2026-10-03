# 宿主接入：把卡片点击转给本项目（通用协议 + 参考实现）

本文描述**第二种入站方式**：飞书的长连接由**宿主 Agent** 持有，本项目只监听本机 HTTP，
由宿主把卡片动作转发过来。这是一个**通用能力** —— 任何持有 IM 长连接的 Agent 都能按本文接入，
不限于某个产品。文末给出两份可直接抄的参考实现（通用 Python 宿主 / Hermes Agent 插件）。

---

## 1. 为什么需要「宿主转发」这个方式

飞书的长连接是**集群模式，不支持广播**：同一个应用部署多个长连接 client 时，事件只会
**随机**落到其中一个。所以「一个机器人」和「一条长连接」是同一件事。

现实中经常是：用户已经有一个宿主 Agent 在收发消息了（他不想再要第二个机器人）。
那么长连接必须归宿主，本项目不能再去抢 —— 但本项目的卡片仍然需要被点。

解法：宿主把**属于本项目的**卡片动作转给本项目，其余事件（普通聊天、宿主自己的卡片）
仍归宿主。判定依据是卡片按钮 `value` 里的 `domain` 字段。

```
飞书卡片点击
  → 宿主的长连接收到 card.action.trigger
  → 宿主按 value.domain 查注册表
  → POST 给本项目（127.0.0.1）
  → 本项目改状态 / 更新卡片实体，返回一行 toast
  → 宿主把 toast 回给用户
```

本项目侧的配置只有一个：

```yaml
# service.yaml
inbound:
  mode: http
  http:
    host: 127.0.0.1        # 只监听本机：这不是公网 webhook
    port: 8787
    path: /feishu/events
```

---

## 2. 协议 v1

### 2.1 请求

```http
POST http://127.0.0.1:8787/feishu/events
Content-Type: application/json

{
  "protocol_version": 1,
  "source": "myagent.card-router",
  "domain": "news",
  "tag": "button",
  "value": { …卡片按钮的 value 原样… },
  "received_at": "2026-01-01T12:00:00+0800"
}
```

| 字段 | 必须 | 说明 |
|---|---|---|
| `domain` | ✅ | 域标识。本项目只处理 `value.domain == "news"` 的事件，其他一律忽略 |
| `value` | ✅ | 卡片按钮的 `value`（含 `domain` / `action` / 定位字段）。**原样透传**，宿主不要改写 |
| `tag` | ➖ | 动作标签（飞书回调里的 `action.tag`），用于日志与排障 |
| `protocol_version` | ➖ | 协议版本，当前 `1` |
| `source` | ➖ | 转发方标识，写进日志便于定位是哪一层出的问题 |

**兼容性**：本项目也接受原始飞书回调体（`{"header":…,"event":{"action":{"value":…}}}`），
但宿主转发场景推荐用上面的**扁平信封** —— 转发器通常拿不到完整原始回调体。

### 2.2 响应

```json
{"code": 0}
{"code": 0, "toast": {"type": "info", "content": "已记录 ⭐"}}
```

- `code = 0`：处理完成。`toast.content` 非空时，宿主应当把它回显给用户（飞书 toast）。
- `code != 0` 或 HTTP 非 200：宿主应回一句「服务未响应」，**不要静默**。

### 2.3 时限

**3 秒预算**：飞书要求卡片回调在 3 秒内响应。转发链路要留余量，建议宿主侧超时 2.5s。
不要在转发路径上跑 LLM、抓网页、写大文件 —— 那些属于异步任务。

---

## 3. 宿主侧注册表

宿主需要一个「域 → 本地端点」的映射。推荐放在宿主的配置目录里，独立于代码：

```yaml
# ~/.myagent/card_routes.yaml
protocol_version: 1
routes:
  news:
    transport: http                        # http | command
    target: http://127.0.0.1:8787/feishu/events
    timeout_ms: 2500
    description: 资讯卡片（newspipe）
```

两条设计建议（都是踩过坑换来的）：

1. **热加载**（按文件 mtime）。加一个域不该需要重启宿主进程 —— 重启网关会掐断长连接。
2. **注册表坏了必须出声**。只有「域不在表里」才静默（那可能是宿主自己的卡片）；
   注册表解析失败必须回一句告警。否则故障会伪装成「点了没反应」，排查从几分钟变成几小时。

---

## 4. 参考实现 A：最小宿主（通用 Python）

```python
"""任何宿主都能用的最小转发器：拿到卡片动作 → 查表 → 转发 → 取回显。"""
import json
import urllib.request
from pathlib import Path

import yaml

ROUTES_PATH = Path.home() / ".myagent" / "card_routes.yaml"
_cache: dict = {"mtime": None, "routes": {}, "error": ""}


def load_routes() -> tuple[dict, str]:
    """读注册表（mtime 热加载）。坏文件返回空表 + 说明，不抛异常。"""
    try:
        mtime = ROUTES_PATH.stat().st_mtime
    except OSError:
        return {}, f"注册表不存在：{ROUTES_PATH}"
    if _cache["mtime"] == mtime:
        return _cache["routes"], _cache["error"]
    try:
        raw = yaml.safe_load(ROUTES_PATH.read_text(encoding="utf-8")) or {}
        routes = raw.get("routes") or {}
    except Exception as exc:
        _cache.update({"mtime": mtime, "routes": {},
                       "error": f"注册表解析失败：{type(exc).__name__}: {exc}"})
        return {}, _cache["error"]
    _cache.update({"mtime": mtime, "routes": routes, "error": ""})
    return routes, ""


def forward(domain: str, tag: str, value: dict) -> str:
    """转发一封卡片动作信封，返回要回显给用户的文本（空串 = 不提示）。"""
    routes, registry_error = load_routes()
    route = routes.get(domain)
    if route is None:
        if registry_error:                       # 表坏了 ≠ 不是我们的域
            return f"⚠️ 卡片域注册表不可用：{registry_error[:140]}"
        return ""                                # 别人的卡片：静默
    envelope = {"protocol_version": 1, "source": "myagent.card-router",
                "domain": domain, "tag": tag, "value": value}
    timeout = max(0.3, min(int(route.get("timeout_ms") or 2500) / 1000.0, 5.0))
    try:
        request = urllib.request.Request(
            route["target"], data=json.dumps(envelope, ensure_ascii=False).encode("utf-8"),
            method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8") or "{}")
        toast = body.get("toast") or {}
        return str(toast.get("content") or "")
    except Exception as exc:                     # 连接被拒/超时/解析失败都走这里
        return f"⚠️ {domain} 服务未响应（{type(exc).__name__}）"


# 宿主的事件管线里挂上它：
def on_card_action(event) -> str:
    value = getattr(event.action, "value", None) or {}
    if not isinstance(value, dict) or not value.get("domain"):
        return ""
    return forward(str(value["domain"]), getattr(event.action, "tag", "button"), value)
```

**接上宿主时需要改的只有一处**：`on_card_action` 的签名（对接你自己的事件管线）。

---

## 5. 参考实现 B：Hermes Agent 插件（配套示例代码）

Hermes Agent 的做法是「**薄转发插件 + 注册表**」：插件零业务逻辑，只做
「拆信封 → 查表 → 转发 → 取回显」，改注册表不用重启网关。

**为什么插件要这么薄**：它跑在宿主网关进程里，是长连接的持有者所在的那个进程。
在里面写业务逻辑（抓网页、调模型）会拖垮整条消息链路。

```python
"""card-router —— 卡片域路由插件（Hermes 侧唯一的转发件，零业务逻辑）。

注册表：~/.hermes/card_routes.yaml（mtime 热加载）
挂载点：Hermes 收到卡片点击会合成 `/card <tag> <value_json>` 命令，
        插件注册同名命令即可接住（register_command）。
"""
from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
DEFAULT_TIMEOUT_MS = 2500
MAX_TOAST_CHARS = 2000


def routes_path() -> Path:
    home = os.environ.get("HERMES_HOME") or (Path.home() / ".hermes")
    return Path(home).expanduser() / "card_routes.yaml"


_CACHE: dict[str, Any] = {"mtime": None, "routes": {}, "error": ""}


def _yaml_module() -> Any:
    """解析 YAML —— **必须容错**，因为插件跑在网关进程的解释器里。

    网关解释器可能没有 PyYAML（宿主自带一个包装模块）。直接 `import yaml` 失败会让
    注册表静默解析失败 → 路由表为空 → 按「未命中即静默」什么都不做，
    表现为「点了卡片毫无反应」。所以按顺序回退。
    """
    try:
        import yaml
        return yaml
    except ModuleNotFoundError:
        import hermes_yaml          # 宿主自带的 YAML 包装
        return hermes_yaml


def load_routes(use_cache: bool = True) -> tuple[dict[str, dict], str]:
    """读注册表，返回 (routes, error)。文件坏了返回空表 + 说明（不抛异常）。"""
    path = routes_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}, f"注册表不存在：{path}"
    if use_cache and _CACHE["mtime"] == mtime:
        return dict(_CACHE["routes"]), _CACHE["error"]
    try:
        raw = _yaml_module().safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        _CACHE.update({"mtime": mtime, "routes": {},
                       "error": f"注册表解析失败：{type(exc).__name__}: {exc}"})
        logger.warning("[card-router] %s", _CACHE["error"])
        return {}, _CACHE["error"]
    routes: dict[str, dict] = {}
    problems: list[str] = []
    items = raw.get("routes") if isinstance(raw, dict) else None
    if not isinstance(items, dict):
        problems.append("顶层缺少 routes: 映射")
    else:
        for domain, route in items.items():
            name = str(domain).strip()
            if not isinstance(route, dict) or not str(route.get("target") or "").strip():
                problems.append(f"{name}: 必须是映射且带 target")
                continue
            routes[name] = {"domain": name,
                            "transport": str(route.get("transport") or "http").lower(),
                            "target": str(route["target"]).strip(),
                            "timeout_ms": int(route.get("timeout_ms") or DEFAULT_TIMEOUT_MS)}
    error = "；".join(problems)
    _CACHE.update({"mtime": mtime, "routes": routes, "error": error})
    if error:
        logger.warning("[card-router] 注册表有 %d 个问题：%s", len(problems), error)
    return dict(routes), error


def parse_command(args: str) -> tuple[str, dict[str, Any]]:
    """拆网关合成的 `<tag> <value_json>`（防御性：整行 `/card …` 也拆对）。"""
    raw = (args or "").strip()
    if raw.startswith("/card"):
        raw = raw[len("/card"):].lstrip()
    tag, _, rest = raw.partition(" ")
    value: dict[str, Any] = {}
    rest = rest.strip()
    if rest:
        try:
            parsed = json.loads(rest)
            if isinstance(parsed, dict):
                value = parsed
        except json.JSONDecodeError:
            logger.debug("[card-router] value 不是 JSON：%r", rest[:200])
    return (tag or "button"), value


def extract_toast(body: str) -> str:
    """宽容三种回显形状：{"toast":{"content":…}} / {"content":…} / 纯文本。"""
    text = (body or "").strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text[:MAX_TOAST_CHARS]
    if isinstance(parsed, dict):
        toast = parsed.get("toast")
        if isinstance(toast, dict) and toast.get("content"):
            return str(toast["content"])[:MAX_TOAST_CHARS]
        for key in ("content", "message"):
            if isinstance(parsed.get(key), str) and parsed[key]:
                return str(parsed[key])[:MAX_TOAST_CHARS]
    return ""


def _forward_http(route: dict, envelope: dict) -> str:
    body = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        route["target"], data=body, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "card-router/1"})
    timeout = max(0.3, min(route["timeout_ms"] / 1000.0, 5.0))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return extract_toast(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        logger.warning("[card-router] %s 返回 HTTP %s", route["domain"], exc.code)
        return f"⚠️ {route['domain']} 服务返回 {exc.code}"
    except Exception as exc:
        logger.warning("[card-router] %s 转发失败：%s: %s",
                       route["domain"], type(exc).__name__, exc)
        return f"⚠️ {route['domain']} 服务未响应（{type(exc).__name__}）"


def _forward_command(route: dict, envelope: dict) -> str:
    argv = shlex.split(route["target"])
    if not argv:
        return f"⚠️ {route['domain']} 的 target 为空"
    env = dict(os.environ)
    # 调度器会 sanitize 环境；外部 CLI 缺宿主目录时会静默回落到另一个应用身份
    env.setdefault("HERMES_HOME", str(Path.home() / ".hermes"))
    try:
        proc = subprocess.run(argv, input=json.dumps(envelope, ensure_ascii=False),
                              capture_output=True, text=True,
                              timeout=max(0.3, min(route["timeout_ms"] / 1000.0, 30.0)), env=env)
    except Exception as exc:
        return f"⚠️ {route['domain']} 服务未响应（{type(exc).__name__}）"
    if proc.returncode != 0:
        logger.warning("[card-router] %s 退出码 %s", route["domain"], proc.returncode)
        return f"⚠️ {route['domain']} 处理失败（退出码 {proc.returncode}）"
    return extract_toast(proc.stdout)


def route_envelope(tag: str, value: dict[str, Any]) -> str:
    """查表 + 转发。返回给用户的 toast 文本（空 = 静默）。"""
    table, registry_error = load_routes()
    domain = str(value.get("domain") or "").strip()
    if not domain:
        return ""
    route = table.get(domain)
    if route is None:
        if registry_error:            # 表坏了 ≠ 不是我们管的域：必须出声
            return f"⚠️ 卡片域注册表不可用：{registry_error[:140]}"
        return ""                     # 不是我们管的域 ⇒ 静默，交给宿主自己
    envelope = {"protocol_version": PROTOCOL_VERSION, "source": "hermes.card-router",
                "domain": domain, "tag": tag, "value": value,
                "received_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    forward = _forward_http if route["transport"] == "http" else _forward_command
    try:
        return forward(route, envelope)
    except Exception as exc:          # 绝不冒泡：一次网络抖动不该等于「机器人坏了」
        logger.warning("[card-router] 未预期错误：%s: %s", type(exc).__name__, exc)
        return f"⚠️ {domain} 转发异常（{type(exc).__name__}）"


def handle_card(args: str) -> str:
    """网关 `/card <tag> <value_json>` 的处理器。"""
    tag, value = parse_command(args)
    return route_envelope(tag, value) if value else ""


def handle_routes(args: str) -> str:
    """`/card-routes` —— 排障：列出注册表与状态。"""
    routes, error = load_routes(use_cache=False)
    lines = [f"卡片域注册表（{routes_path()}）", f"协议版本 {PROTOCOL_VERSION}"]
    for name, route in sorted(routes.items()):
        lines.append(f"  · {name} → {route['transport']} {route['target']}"
                     f"（{route['timeout_ms']}ms）")
    if error:
        lines.append(f"⚠️ 问题：{error}")
    return "\n".join(lines)


def register(ctx) -> None:
    """Hermes 插件入口：把转发命令挂到事件管线上。"""
    ctx.register_command("card", handle_card,
                         description="Internal: route card callbacks by value.domain.",
                         args_hint="<tag> <json>")
    ctx.register_command("card-routes", handle_routes,
                         description="Show the card-domain routing registry.")
```

配套的插件清单（`plugin.yaml`）：

```yaml
name: card-router
kind: standalone
description: Route Feishu card callbacks to local project endpoints by value.domain.
```

启用与自检：

```sh
hermes plugins enable card-router          # 插件要进白名单才会加载
# 插件跑在网关进程的解释器里，自检必须用那个解释器（它可能没有 PyYAML）
"$HERMES_GATEWAY_PYTHON" ~/.hermes/plugins/card-router/__init__.py
launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway   # 改插件代码后必须重启网关
```

> 注意：**改注册表不用重启**（mtime 热加载）；**改插件代码必须重启**（插件在进程里）。
> 自检如果用另一个有 PyYAML 的解释器跑，会得到「我这儿全绿」的假象。

---

## 6. 硬规则（三条，都会造成静默故障）

1. **一个飞书应用只能有一条长连接。** 宿主持有它，本项目就不能再开 ws。
   本项目启动时会检测这种冲突（`newspipe doctor --json` 的 `inbound_conflict`）。
2. **只处理自己域的卡片，其他一律静默。** 宿主自己的卡片（审批、授权等）不能被吞掉。
   但「注册表坏了」必须出声 —— 这两件事看起来像，实际完全不同。
3. **任何异常都不冒泡。** 转发失败只回一行告警。让宿主那一轮崩掉 = 一次网络抖动
   等于「机器人坏了」。

## 7. 排障

| 现象 | 查什么 |
|---|---|
| 点了卡片完全没反应 | ① 本项目端点在监听吗（`lsof -nP -iTCP:8787 -sTCP:LISTEN`）② 注册表解析成功吗（宿主侧 `/card-routes`）③ 卡片是宿主发的吗（回调只会发给发卡的应用） |
| 回「⚠️ …服务未响应」 | 本项目进程没起，或端口/路径与注册表不一致 |
| 回「⚠️ 卡片域注册表不可用」 | 注册表文件语法错或缺 `routes:` 映射 —— 修它，别忽略 |
| 宿主自己的卡片也点不动了 | 插件把非本项目域的卡片也吞了：检查 `domain` 判定与「未命中即静默」分支 |
| 转发偶发超时 | 转发路径上是不是跑了慢操作；超时应 ≤2.5s（飞书预算 3s） |
