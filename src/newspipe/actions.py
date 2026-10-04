"""Web 写操作：把页面表单翻译成 CLI argv，交给**同一份** handler 执行。

设计约束（讨论记录 `thinkings/spec-workshops/资讯管线-Web运维交互-2026-10-04.md`）：

1. **不重写任何写逻辑**。校验、原子写、乐观并发（`--base-hash`）、审计都在 CLI 里；
   这里只做「表单 → argv」的翻译。重写一份写路径必然与 CLI 漂移。
2. **白名单写死在代码里**，不是配置。页面能触发的动作 = 本模块登记的动作；加动作要改代码
   （配置文件能改出来的写权限，等于给了一个远程可改的写面）。
3. **动作不碰文件**。除了读表单，本模块不读写任何 state —— 落盘一律由 CLI 完成。
4. 每个动作返回 CLI 信封（`ok` / `error.code` / `error.hint` / `changed` / `next`），
   页面就地渲染。**不要**把它压成「操作成功」四个字：`changed` 与 `hint` 才是用户要的信息。

`danger=True` 的动作会真的发卡/改配置（非 `--dry`），页面必须让用户显式点它。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from newspipe import cli, result

#: 表单值 → argv。抛 `ActionInputError` 表示用户输入不合法（会变成 E_USAGE 信封）。
FormBuilder = Callable[[dict[str, list[str]]], list[str]]


class ActionInputError(ValueError):
    """表单输入不合法。消息会原样进 `error.message`，所以要说人话。"""


@dataclass(frozen=True)
class Action:
    name: str
    label: str
    argv: FormBuilder
    danger: bool = False


def _one(form: dict[str, list[str]], key: str) -> str:
    values = form.get(key) or []
    if not values or not str(values[0]).strip():
        raise ActionInputError(f"缺少参数 {key}")
    return str(values[0]).strip()


def _flag(form: dict[str, list[str]], key: str) -> bool:
    """复选框语义：出现且不是 0/false/off 即为真。"""
    values = form.get(key) or []
    if not values:
        return False
    return str(values[0]).strip().lower() not in ("", "0", "false", "off", "no")


# --------------------------------------------------------------------- 动作实现
def _argv_run(form: dict[str, list[str]]) -> list[str]:
    """跑一轮。`target` = `poll` / `am|noon|pm` / `source:<名字>`。"""
    target = _one(form, "target")
    if target == "poll":
        argv = ["run", "--poll"]
    elif target in ("am", "noon", "pm"):
        argv = ["run", "--slot", target]
    elif target.startswith("source:"):
        name = target.split(":", 1)[1].strip()
        if not name:
            raise ActionInputError("source: 后面要跟信源名")
        argv = ["run", "--source", name]
    else:
        raise ActionInputError(f"未知 target：{target!r}")
    if _flag(form, "dry"):
        argv.append("--dry")
    return argv


def _argv_source_toggle(form: dict[str, list[str]]) -> list[str]:
    """启用/停用某个源（改 sources.yaml，走 CLI 的校验与原子写）。"""
    name = _one(form, "name")
    want = _one(form, "state")
    if want not in ("enable", "disable"):
        raise ActionInputError(f"state 只能是 enable / disable（得到 {want!r}）")
    argv = ["source", want, name]
    base_hash = (form.get("base_hash") or [""])[0].strip()
    if base_hash:                       # 页面渲染时的哈希：期间别人改过就会被拒（E_CONFLICT）
        argv += ["--base-hash", base_hash]
    return argv


def _argv_queue_ack(form: dict[str, list[str]]) -> list[str]:
    """⭐ 待入库队列的「确认入库」—— 人环动作。"""
    event_id = _one(form, "event_id")
    argv = ["queue", "ack", event_id, "--by", "webui"]
    note = (form.get("note") or [""])[0].strip()
    if note:
        argv += ["--note", note]
    return argv


def _argv_events_ack(form: dict[str, list[str]]) -> list[str]:
    """标记事件已消费（可一次多个，逗号分隔）。"""
    raw = _one(form, "ids")
    ids = [i.strip() for i in raw.replace(",", " ").split() if i.strip()]
    if not ids:
        raise ActionInputError("缺少事件 id")
    if len(ids) > 50:
        raise ActionInputError("一次最多 50 个事件 id")
    return ["events", "ack", *ids, "--by", "webui"]


def _argv_flush(form: dict[str, list[str]]) -> list[str]:
    """把某个源的顺延队列**现在发掉**（非 dry ⇒ 会真发卡）。"""
    name = _one(form, "name")
    return ["run", "--source", name]


# --------------------------------------------------------- 配置页：表单 → 整段映射
#: 表单字段前缀。页面上的输入名是 `f.<schema path>`（如 `f.fetch.trigger`），
#: 这样加字段只改 `config.describe_schema()`，翻译层不用动。
FORM_PREFIX = "f."


def _coerce(field: dict[str, Any], raw: list[str]) -> Any:
    """按 schema 的类型把表单值转成 YAML 值。类型表只有 `config.describe_schema()` 一份。"""
    kind = str(field.get("type") or "str")
    values = [v for v in raw if v is not None]
    if kind == "bool":
        # 复选框配一个隐藏的 0：没勾 ⇒ ["0"]，勾了 ⇒ ["0", "1"]
        return any(str(v).strip().lower() in ("1", "true", "on", "yes") for v in values)
    first = str(values[0]) if values else ""
    if kind in ("list", "slots"):
        if kind == "slots":
            return [v for v in values if v.strip()]
        return [part.strip() for part in re.split(r"[,\n、]", first) if part.strip()]
    if kind == "int":
        return int(first) if first.strip() else None
    if kind == "float":
        return float(first) if first.strip() else None
    return first.strip()


def build_source_body(form: dict[str, list[str]],
                      schema: dict[str, Any] | None = None) -> dict[str, Any]:
    """把配置页的表单拼成**整段映射**（`source set --from-json` 要的就是这个）。

    为什么是整段而不是补丁：`source set` 是「新增或整体替换」。页面必须提交全部字段，
    否则没提交的字段会退回默认值 —— 所以编辑表单预填当前值（`config.source_values`）。
    """
    from newspipe import config as config_mod

    schema = schema or config_mod.describe_schema()
    body: dict[str, Any] = {}
    for field in schema.get("fields") or []:
        path = str(field.get("path") or "")
        raw = form.get(f"{FORM_PREFIX}{path}")
        if raw is None:
            continue
        value = _coerce(field, raw)
        if value is None:                      # 空数字 = 不写（让默认值生效），不静默变 0
            continue
        cursor = body
        parts = path.split(".")
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = value
    return body


def _argv_source_save(form: dict[str, list[str]]) -> list[str]:
    """新增 / 修改信源：表单 → 整段映射 JSON → `source set`。"""
    name = _one(form, "name")
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,40}", name):
        raise ActionInputError("信源名只能用字母、数字、下划线、短横，1–40 字符")
    body = build_source_body(form)
    if not body:
        raise ActionInputError("表单是空的 —— 至少要提交 adapter 与 fetch.trigger")
    argv = ["source", "set", name, "--from-json", json.dumps(body, ensure_ascii=False)]
    if _flag(form, "dry"):
        argv.append("--dry-run")
    base = (form.get("base_hash") or [""])[0].strip()
    if base:
        argv += ["--base-hash", base]
    return argv


def _argv_source_remove(form: dict[str, list[str]]) -> list[str]:
    """删除信源（危险动作：页面必须二次确认）。"""
    name = _one(form, "name")
    argv = ["source", "remove", name]
    if _flag(form, "dry"):
        argv.append("--dry-run")
    base = (form.get("base_hash") or [""])[0].strip()
    if base:
        argv += ["--base-hash", base]
    return argv


ACTIONS: dict[str, Action] = {
    a.name: a for a in (
        Action("run", "跑一轮", _argv_run),
        Action("source-toggle", "启用 / 停用信源", _argv_source_toggle, danger=True),
        Action("source-save", "保存信源（新增 / 修改）", _argv_source_save, danger=True),
        Action("source-remove", "删除信源", _argv_source_remove, danger=True),
        Action("queue-ack", "确认入库（⭐）", _argv_queue_ack),
        Action("events-ack", "标记事件已消费", _argv_events_ack),
        Action("flush", "现在发掉顺延队列", _argv_flush, danger=True),
    )
}


def dispatch(name: str, form: dict[str, list[str]], news_dir: Path) -> dict[str, Any]:
    """执行一个动作，返回 CLI 信封（`dict`，可直接 `json.dumps`）。

    未知动作 ⇒ `E_NOT_FOUND`；表单不合法 ⇒ `E_USAGE`。两者都**不**落盘。
    """
    spec = ACTIONS.get(name)
    if spec is None:
        return result.fail("action", "E_NOT_FOUND", f"没有这个动作：{name!r}",
                           hint=f"可用动作：{', '.join(sorted(ACTIONS))}").as_dict()
    try:
        argv = spec.argv(form)
    except ActionInputError as exc:
        return result.fail(name, "E_USAGE", str(exc)).as_dict()
    argv = [*argv, "--json"]
    res = cli.run_subcommand(argv, news_dir)
    data = res.as_dict()
    # 把「这个信封是哪个 Web 动作、以及它实际跑了哪条 CLI」记进 payload：
    # 用户看到失败时，下一步就是去终端复现这条命令。
    data["action"] = {"name": spec.name, "label": spec.label, "danger": spec.danger,
                      "cli": " ".join(a if " " not in a else f"'{a}'" for a in argv)}
    return data
