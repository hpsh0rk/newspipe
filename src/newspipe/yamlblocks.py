"""YAML 文本级块编辑 —— 只动目标块，块外**逐字节不动**。

为什么不用 `yaml.safe_dump` 整文件重写：`sources.yaml` 是人工维护的，注释解释了词闸为什么
这么调、静默窗口为什么是 23:30–07:30。整文件重写会把注释全丢掉——那是拿可维护性换方便。

代价说清楚：**被替换的那一块内的注释会丢**（调用方在结果里必须明说），块外一字不动。
"""

from __future__ import annotations

import re


def _lines(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _top_span(lines: list[str], top_key: str) -> tuple[int, int, int] | None:
    """返回 `(标题行号, 缩进, 体结束行号)`。"""
    pattern = re.compile(rf"^(\s*){re.escape(top_key)}\s*:\s*(#.*)?$")
    for index, raw in enumerate(lines):
        match = pattern.match(raw.rstrip("\n"))
        if not match:
            continue
        indent = len(match.group(1))
        end = index + 1
        while end < len(lines):
            body = lines[end].rstrip("\n")
            if body.strip() and _indent(body) <= indent:
                break
            end += 1
        return index, indent, end
    return None


def _child_span(lines: list[str], start: int, end: int, child_indent: int,
                name: str) -> tuple[int, int] | None:
    pattern = re.compile(rf"^(\s*){re.escape(name)}\s*:\s*(#.*)?$")
    for index in range(start, end):
        raw = lines[index].rstrip("\n")
        if not raw.strip():
            continue
        match = pattern.match(raw)
        if match and len(match.group(1)) == child_indent:
            stop = index + 1
            while stop < end:
                body = lines[stop].rstrip("\n")
                if body.strip() and _indent(body) <= child_indent:
                    break
                stop += 1
            return index, stop
    return None


def get_block(text: str, top_key: str, name: str) -> str | None:
    """取出某个子块的原文（含其缩进），找不到返回 None。"""
    lines = _lines(text)
    top = _top_span(lines, top_key)
    if top is None:
        return None
    _index, indent, end = top
    span = _child_span(lines, _index + 1, end, indent + 2, name)
    if span is None:
        return None
    return "".join(lines[span[0]:span[1]])


def has_block(text: str, top_key: str, name: str) -> bool:
    return get_block(text, top_key, name) is not None


def _normalise(block: str, child_indent: int) -> str:
    """把传入的块统一缩进到 child_indent，并保证以换行结束。

    关键是**按块自己的最小缩进做相对平移**：直接 `lstrip()` 会把嵌套层级压平
    （`fetch:` 和它下面的 `trigger:` 变成同级）——那是最难查的一类配置损坏。
    """
    rows = block.strip("\n").splitlines()
    base = min((_indent(line) for line in rows if line.strip()), default=0)
    out: list[str] = []
    for line in rows:
        if not line.strip():
            out.append("")
        else:
            out.append(" " * child_indent + line[base:])
    return "\n".join(out).rstrip() + "\n"


def set_block(text: str, top_key: str, name: str, block: str) -> str:
    """替换已存在的子块（块外不动）。不存在则抛 KeyError。"""
    lines = _lines(text)
    top = _top_span(lines, top_key)
    if top is None:
        raise KeyError(f"找不到顶层键 {top_key!r}")
    _index, indent, end = top
    span = _child_span(lines, _index + 1, end, indent + 2, name)
    if span is None:
        raise KeyError(f"{top_key} 下没有 {name!r}")
    new = lines[:span[0]] + [_normalise(block, indent + 2)] + lines[span[1]:]
    return "".join(new)


def set_key_in_block(text: str, top_key: str, name: str, key: str, value: str) -> str:
    """在子块内**只改一行**：`key` 已存在则就地替换，不存在则插到块首。

    用来做「翻一个开关」这类最小改动——整块重序列化会把 `slots: [am, pm]` 摊成多行，
    制造无意义的 diff 噪音。
    """
    lines = _lines(text)
    top = _top_span(lines, top_key)
    if top is None:
        raise KeyError(f"找不到顶层键 {top_key!r}")
    _index, indent, end = top
    span = _child_span(lines, _index + 1, end, indent + 2, name)
    if span is None:
        raise KeyError(f"{top_key} 下没有 {name!r}")
    start, stop = span
    key_indent = indent + 4
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*:")
    for index in range(start + 1, stop):
        if pattern.match(lines[index]):
            lines[index] = " " * key_indent + f"{key}: {value}\n"
            return "".join(lines)
    lines.insert(start + 1, " " * key_indent + f"{key}: {value}\n")
    return "".join(lines)


def append_block(text: str, top_key: str, name: str, block: str) -> str:
    """在顶层键的体末尾追加子块（不存在则先建顶层键）。"""
    lines = _lines(text)
    top = _top_span(lines, top_key)
    if top is None:
        base = text.rstrip("\n")
        joiner = "\n\n" if base else ""
        return f"{base}{joiner}{top_key}:\n{_normalise(block, 2)}"
    _index, indent, end = top
    # 回退到最后一个非空行之后，避免把空行留在块中间
    insert_at = end
    while insert_at > _index + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    new = lines[:insert_at] + [_normalise(block, indent + 2)] + lines[insert_at:]
    return "".join(new)


def remove_block(text: str, top_key: str, name: str) -> str:
    """删除子块（连同它自己的空行），块外不动。不存在则抛 KeyError。"""
    lines = _lines(text)
    top = _top_span(lines, top_key)
    if top is None:
        raise KeyError(f"找不到顶层键 {top_key!r}")
    _index, indent, end = top
    span = _child_span(lines, _index + 1, end, indent + 2, name)
    if span is None:
        raise KeyError(f"{top_key} 下没有 {name!r}")
    start, stop = span
    # 吞掉紧跟其后的空行，避免留下连续空行
    while stop < end and not lines[stop].strip():
        stop += 1
    return "".join(lines[:start] + lines[stop:])


def block_names(text: str, top_key: str) -> list[str]:
    """列出顶层键下的一级子键名（保持文件顺序）。"""
    lines = _lines(text)
    top = _top_span(lines, top_key)
    if top is None:
        return []
    _index, indent, end = top
    names: list[str] = []
    for index in range(_index + 1, end):
        raw = lines[index].rstrip("\n")
        if not raw.strip():
            continue
        if _indent(raw) == indent + 2:
            match = re.match(r"^\s*([^#:\s][^:]*?)\s*:", raw)
            if match:
                names.append(match.group(1))
    return names
