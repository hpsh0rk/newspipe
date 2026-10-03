"""卡片 hook —— 第三方往卡片里加按钮，以及接住这些按钮的点击。

这是**防腐层**在回调方向上的实现：项目只认 `hooks.yaml` 的声明，加按钮不用改项目代码；
点击时项目调**第三方自己的可执行文件**（payload 走 stdin，结果走 stdout 与退出码）。
双方没有共享内存、没有互相 import。

三条硬规则：

1. **hook 只能新增，不能覆盖核心动作。** `action` 必须带命名空间（含 `.`，如
   `myapp.wiki_favorite`），核心动作是 `open_detail`/`back_to_list`/`wiki`/`dismiss`，
   不带点 ⇒ 天然不会撞。
2. **hook 失败绝不影响核心按钮。** handler 挂了只记事件 + 日志，用户点「返回列表」照常工作。
3. **坏 hook 不许静默。** 声明有问题（缺字段、handler 不可执行、id 重复）时：
   该 hook 被**跳过**（不渲染死按钮），问题进 `config.load()` 的 `hook_problems`，
   由 `newspipe doctor` 报出来，并在事件流里记一条 `config`。

`hooks.yaml` 示例见 `examples/news/hooks.yaml`。
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from newspipe.errors import ConfigError

#: 核心动作：hook 不得使用这些值（不带命名空间的 action 一律拒绝）
CORE_ACTIONS = ("open_detail", "back_to_list", "wiki", "dismiss")

SCOPES = ("detail", "list")

DEFAULT_TIMEOUT_S = 10.0


@dataclass
class Hook:
    id: str
    label: str
    action: str
    handler: str
    scope: str = "detail"
    enabled: bool = True
    timeout_s: float = DEFAULT_TIMEOUT_S

    def argv(self) -> list[str]:
        """把 handler 拆成 argv（支持 `"/path/x.sh --flag"` 这种写法）并展开 `~`。"""
        parts = shlex.split(self.handler)
        if not parts:
            return []
        parts[0] = str(Path(parts[0]).expanduser())
        return parts

    def describe(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "action": self.action, "scope": self.scope,
                "handler": self.handler, "enabled": self.enabled, "timeout_s": self.timeout_s}


@dataclass
class HookSet:
    """可用 hooks + 被跳过的声明（跳过原因必须能被人看到）。"""

    hooks: list[Hook] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:                       # 没配 hooks 时全流程零开销
        return bool(self.hooks)

    def buttons(self, scope: str) -> list[Hook]:
        return [h for h in self.hooks if h.enabled and h.scope == scope]

    def by_action(self, action: str) -> Hook | None:
        for hook in self.hooks:
            if hook.enabled and hook.action == action:
                return hook
        return None

    def actions(self) -> list[str]:
        return [h.action for h in self.hooks if h.enabled]


def _coerce_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("false", "0", "no", "off", "")


def _check_handler(handler: str) -> str:
    """返回空串 = 没问题；否则返回问题描述。handler 不存在 ⇒ 按钮点了没反应，
    这是最糟的结果，所以当成硬问题报出来（但只跳过这一个 hook，不拖垮管线）。

    两种写法都要支持：绝对/相对路径（含 `./x.sh`）与 PATH 上的命令（`python3 x.py`）。
    只查 `Path(argv[0]).exists()` 会把后者全部误判成「不存在」。
    """
    argv = shlex.split(handler) if handler else []
    if not argv:
        return "handler 为空"
    first = argv[0]
    path = Path(first).expanduser()
    looks_like_path = path.is_absolute() or first.startswith("./") or first.startswith("../") \
        or "/" in first
    if looks_like_path:
        if not path.exists():
            return f"handler 不存在：{path}"
        if path.is_dir():
            return f"handler 是目录：{path}"
        if not os.access(path, os.X_OK):
            return f"handler 不可执行：{path}"
        return ""
    if shutil.which(first) is None:
        return f"handler 不在 PATH 上：{first}"
    return ""


def load(path: Path) -> HookSet:
    """读 `hooks.yaml`。文件缺失 = 没有 hook（不是错误）；结构坏 = ConfigError。"""
    return load_from(path)


def load_from(path: Path, *, text: str | None = None) -> HookSet:
    """`text` 是校验用覆盖（编辑命令先验证再落盘）；None 表示真读文件。"""
    path = Path(path)
    if text is None and not path.is_file():
        return HookSet()
    import yaml

    try:
        raw = yaml.safe_load(text if text is not None else path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path.name} 不是合法 YAML：{exc}") from None
    if not isinstance(raw, dict):
        raise ConfigError(f"{path.name} 顶层必须是映射（hooks: [...]）")
    items = raw.get("hooks") or []
    if not isinstance(items, list):
        raise ConfigError(f"{path.name} 的 hooks 必须是列表")

    result = HookSet()
    seen: set[str] = set()
    for index, item in enumerate(items, 1):
        where = f"hooks[{index}]"
        if not isinstance(item, dict):
            result.problems.append(f"{where}：必须是映射，收到 {type(item).__name__}")
            continue
        hook_id = str(item.get("id") or "").strip()
        action = str(item.get("action") or "").strip()
        label = str(item.get("label") or "").strip()
        handler = str(item.get("handler") or "").strip()
        scope = str(item.get("scope") or "detail").strip().lower()
        if not hook_id:
            result.problems.append(f"{where}：缺 id")
            continue
        where = f"hook {hook_id!r}"
        if hook_id in seen:
            result.problems.append(f"{where}：id 重复")
            continue
        seen.add(hook_id)
        if not action:
            result.problems.append(f"{where}：缺 action")
            continue
        if "." not in action:
            result.problems.append(
                f"{where}：action={action!r} 缺命名空间（必须形如 'vendor.something'）——"
                f"核心动作 {list(CORE_ACTIONS)} 不允许被 hook 覆盖")
            continue
        if action in CORE_ACTIONS:
            result.problems.append(f"{where}：action={action!r} 与核心动作冲突")
            continue
        if not label:
            result.problems.append(f"{where}：缺 label（按钮上要显示的字）")
            continue
        if scope not in SCOPES:
            result.problems.append(f"{where}：scope={scope!r} 不合法（{'/'.join(SCOPES)}）")
            continue
        if not handler:
            result.problems.append(f"{where}：缺 handler")
            continue
        problem = _check_handler(handler)
        if problem:
            result.problems.append(f"{where}：{problem}")
            continue
        try:
            timeout = float(item.get("timeout_s") or DEFAULT_TIMEOUT_S)
        except (TypeError, ValueError):
            result.problems.append(f"{where}：timeout_s 不是数字")
            continue
        result.hooks.append(Hook(id=hook_id, label=label, action=action, handler=handler,
                                 scope=scope, enabled=_coerce_bool(item.get("enabled"), True),
                                 timeout_s=max(0.5, min(timeout, 120.0))))
    return result


def run_handler(hook: Hook, payload: dict[str, Any], *,
                timeout_s: float | None = None) -> dict[str, Any]:
    """调第三方 handler。**任何失败都返回结构化结果，不抛异常**（调用方只记账）。

    payload 走 stdin（JSON，一行），handler 的 stdout 会被截断到 2KB 回给用户当 toast。
    """
    argv = hook.argv()
    timeout = timeout_s or hook.timeout_s
    started = time.monotonic()
    if not argv:
        return {"ok": False, "error": "handler 为空", "exit_code": None,
                "stdout": "", "stderr": "", "ms": 0}
    try:
        proc = subprocess.run(argv, input=json.dumps(payload, ensure_ascii=False),
                              capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        return {"ok": False, "error": f"handler 找不到：{exc}", "exit_code": None,
                "stdout": "", "stderr": "", "ms": int((time.monotonic() - started) * 1000)}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"handler 超时（>{timeout:g}s）", "exit_code": None,
                "stdout": "", "stderr": "", "ms": int((time.monotonic() - started) * 1000)}
    except OSError as exc:
        return {"ok": False, "error": f"handler 无法启动：{exc}", "exit_code": None,
                "stdout": "", "stderr": "", "ms": int((time.monotonic() - started) * 1000)}
    return {
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "stdout": (proc.stdout or "")[:2000].strip(),
        "stderr": (proc.stderr or "")[:2000].strip(),
        "ms": int((time.monotonic() - started) * 1000),
    }
