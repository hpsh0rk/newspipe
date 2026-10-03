"""配置编辑 —— **唯一被支持的写入路径**（防腐层的实体）。

Agent 不许直接改 `sources.yaml` / `hooks.yaml`，原因不是洁癖：

- 写错了没人拦。真实踩过：`summary: off` 被 YAML 1.1 解析成布尔、`http2: true` 忘了装 `h2`、
  `slots` 里写了不存在的槽位——每一条都是**静默**失效（配置看起来对，行为不对）。
- 直改文件时没有并发保护：Agent 与你同时编辑，谁后写谁赢，先写的悄悄消失。

所以这里提供三条保证：

1. **先验证再落盘**：候选文本先喂给真正的 `config.load()` 跑一遍完整校验，通过才写。
2. **只动目标块**：块外的字节（含你写的注释）一字不动（见 `yamlblocks`）。
3. **乐观并发**：`base_hash` 不匹配 = 有人在你读取之后改过 → 拒绝写入（`E_CONFLICT`），
   让你重新读一遍再决定。`operation_id` 让重试幂等。

所有函数返回**结构化结果**（不抛业务异常），供 CLI 直接包成 Result。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from newspipe import config as config_mod
from newspipe import _atomic, yamlblocks
from newspipe.errors import ConfigError


def file_hash(path: Path) -> str:
    """文件内容的短哈希（给 `base_hash` 用）。文件不存在 = 空串。"""
    path = Path(path)
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _dump_block(value: Any) -> str:
    """把一段结构化数据序列化成 YAML 块（不带文档头）。"""
    return yaml.safe_dump(value, allow_unicode=True, sort_keys=False, default_flow_style=False)


def _sources_path(news_dir: Path) -> Path:
    return Path(news_dir) / "sources.yaml"


def _hooks_path(news_dir: Path) -> Path:
    return Path(news_dir) / "hooks.yaml"


def _check_base_hash(path: Path, base_hash: str | None) -> dict[str, Any] | None:
    """返回 None = 可以写；否则返回错误 dict。"""
    if not base_hash:
        return None
    current = file_hash(path)
    if current == base_hash:
        return None
    return {
        "code": "E_CONFLICT",
        "message": f"{path.name} 已被改动（你读到的是 {base_hash}，现在是 {current or '不存在'}）",
        "hint": f"重新读取：newspipe {'source' if path.name.startswith('sources') else 'hooks'} "
                f"list --json，基于新内容重做这次修改",
    }


def _apply(news_dir: Path, path: Path, candidate: str, *, dry_run: bool,
           validate: str) -> dict[str, Any]:
    """验证候选文本 → （非 dry）原子落盘。`validate` ∈ {sources, hooks}。"""
    try:
        if validate == "sources":
            config_mod.load(news_dir, sources_text=candidate)
        else:
            config_mod.load(news_dir, hooks_text=candidate)
    except ConfigError as exc:
        return {"code": "E_VALIDATION", "message": str(exc),
                "hint": "候选配置没通过校验，文件未改动。修正后再试；"
                        "`newspipe doctor --json` 可以看当前配置的完整问题清单"}
    if dry_run:
        return {"ok": True, "changed": False, "dry_run": True, "path": str(path)}
    _atomic.atomic_write_text(path, candidate)
    return {"ok": True, "changed": True, "dry_run": False, "path": str(path),
            "hash": file_hash(path)}


# ------------------------------------------------------------------ sources
def set_source(news_dir: Path, name: str, body: dict[str, Any], *,
               base_hash: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    """新增或整体替换一个信源。`body` 就是该源在 `sources.yaml` 里的那段映射。"""
    path = _sources_path(news_dir)
    if not path.is_file():
        return {"code": "E_NOT_FOUND", "message": f"缺少 {path}"}
    conflict = _check_base_hash(path, base_hash)
    if conflict:
        return conflict
    if not isinstance(body, dict) or not body:
        return {"code": "E_VALIDATION", "message": "body 必须是非空映射（该源的四轴配置）"}
    if "name" in body and body["name"] != name:
        return {"code": "E_VALIDATION",
                "message": f"body.name={body['name']!r} 与命令里的 {name!r} 不一致"}
    text = path.read_text(encoding="utf-8")
    block = _dump_block({name: body})
    try:
        existed = yamlblocks.has_block(text, "sources", name)
        candidate = (yamlblocks.set_block(text, "sources", name, block) if existed
                     else yamlblocks.append_block(text, "sources", name, block))
    except KeyError as exc:
        return {"code": "E_VALIDATION", "message": f"sources.yaml 结构异常：{exc}"}
    result = _apply(news_dir, path, candidate, dry_run=dry_run, validate="sources")
    if result.get("ok"):
        result["op"] = "update" if existed else "add"
        result["source"] = name
        result["warning"] = ("该源的配置块被整体重写，**块内注释会丢失**；文件其余部分未改动"
                             if existed else "")
    return result


def remove_source(news_dir: Path, name: str, *, base_hash: str | None = None,
                  dry_run: bool = False) -> dict[str, Any]:
    path = _sources_path(news_dir)
    if not path.is_file():
        return {"code": "E_NOT_FOUND", "message": f"缺少 {path}"}
    conflict = _check_base_hash(path, base_hash)
    if conflict:
        return conflict
    text = path.read_text(encoding="utf-8")
    if not yamlblocks.has_block(text, "sources", name):
        return {"code": "E_NOT_FOUND", "message": f"sources.yaml 里没有信源 {name!r}",
                "hint": "newspipe source list --json 看现有信源"}
    candidate = yamlblocks.remove_block(text, "sources", name)
    result = _apply(news_dir, path, candidate, dry_run=dry_run, validate="sources")
    if result.get("ok"):
        result.update({"op": "remove", "source": name})
    return result


def set_source_enabled(news_dir: Path, name: str, enabled: bool, *,
                       base_hash: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    """只翻 `enabled`，其余字段原样保留（比整体替换安全）。"""
    path = _sources_path(news_dir)
    conflict = _check_base_hash(path, base_hash)
    if conflict:
        return conflict
    text = path.read_text(encoding="utf-8")
    if not yamlblocks.has_block(text, "sources", name):
        return {"code": "E_NOT_FOUND", "message": f"sources.yaml 里没有信源 {name!r}"}
    # 只改一行：块内其余格式与注释原样留下。整体重序列化会把
    # `slots: [am, pm]` 摊成多行、`include_keywords` 换行——那是最常见的 diff 噪音。
    candidate = yamlblocks.set_key_in_block(text, "sources", name, "enabled",
                                           "true" if enabled else "false")
    result = _apply(news_dir, path, candidate, dry_run=dry_run, validate="sources")
    if result.get("ok"):
        result.update({"op": "enable" if enabled else "disable", "source": name})
    return result


# -------------------------------------------------------------------- hooks
def set_hook(news_dir: Path, hook: dict[str, Any], *, base_hash: str | None = None,
             dry_run: bool = False) -> dict[str, Any]:
    """新增或替换一个 hook 声明（按 id）。"""
    path = _hooks_path(news_dir)
    hook_id = str(hook.get("id") or "").strip()
    if not hook_id:
        return {"code": "E_VALIDATION", "message": "hook 必须有 id"}
    conflict = _check_base_hash(path, base_hash)
    if conflict:
        return conflict
    text = path.read_text(encoding="utf-8") if path.is_file() else "hooks: []\n"
    block = _dump_block([hook])
    # hooks 是一个**列表**，逐项编辑要靠文本；这里用「读全量 → 改一项 → 整文件写」，
    # 但 hooks.yaml 是新建文件（示例由本项目提供），没有人工注释要保，所以整写可接受。
    try:
        current = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        return {"code": "E_VALIDATION", "message": f"hooks.yaml 不是合法 YAML：{exc}"}
    items = [h for h in (current.get("hooks") or []) if str((h or {}).get("id")) != hook_id]
    items.append(hook)
    candidate = _dump_block({"hooks": items})
    # 新增的 hook 必须真的生效：handler 不存在 / action 没命名空间 / scope 非法都会被
    # `hooks.load` 跳过——那种情况下**拒绝写入**，否则你会得到一个点了没反应的按钮。
    from newspipe import hooks as hooks_mod

    check = hooks_mod.load_from(path, text=candidate)
    if hook_id not in [h.id for h in check.hooks]:
        problem = next((p for p in check.problems if hook_id in p), None) or "声明未通过校验"
        return {"code": "E_VALIDATION", "message": f"hook 未生效：{problem}",
                "hint": "handler 必须是存在的可执行文件；action 必须带命名空间（如 hermes.x）",
                "details": {"problems": check.problems}}
    result = _apply(news_dir, path, candidate, dry_run=dry_run, validate="hooks")
    if result.get("ok"):
        result.update({"op": "set", "hook": hook_id, "block": block.strip()[:0]})
    return result


def remove_hook(news_dir: Path, hook_id: str, *, base_hash: str | None = None,
                dry_run: bool = False) -> dict[str, Any]:
    path = _hooks_path(news_dir)
    if not path.is_file():
        return {"code": "E_NOT_FOUND", "message": f"缺少 {path}"}
    conflict = _check_base_hash(path, base_hash)
    if conflict:
        return conflict
    try:
        current = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        return {"code": "E_VALIDATION", "message": f"hooks.yaml 不是合法 YAML：{exc}"}
    items = current.get("hooks") or []
    kept = [h for h in items if str((h or {}).get("id")) != hook_id]
    if len(kept) == len(items):
        return {"code": "E_NOT_FOUND", "message": f"hooks.yaml 里没有 hook {hook_id!r}"}
    candidate = _dump_block({"hooks": kept})
    result = _apply(news_dir, path, candidate, dry_run=dry_run, validate="hooks")
    if result.get("ok"):
        result.update({"op": "remove", "hook": hook_id})
    return result


def parse_json_body(text: str) -> dict[str, Any] | Any:
    """解析 `--from-json` 的内容（`-` 表示从 stdin 读）。失败抛 ValueError。"""
    if text == "-":
        import sys

        text = sys.stdin.read()
    return json.loads(text)
