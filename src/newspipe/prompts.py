"""加工层 —— prompt 装配。

prompt 是独立 md 文件，用 `{{> rules-xxx}}` 片段组合（照 AIHOT 的做法）：
公共规则写一次，各处引用，改规则不用改每个 prompt。

**prompt 版本 = 内容哈希**：改了任何被引用的片段，版本就变，之后的新条目按新版判断，
已经判过的（回执命中的）不重算 —— 这既是省钱机制，也是"改标准"与"已判结果"之间的边界。

参考实现：AIHOT（https://github.com/KKKKhazix/AIHOT，MIT）的 industry/prompts/ 组合方式与
rules-* 片段划分；本目录的正文是为本管线重写的。
"""
from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
_PARTIAL = re.compile(r"^\s*\{\{>\s*([a-z0-9\-_]+)\s*\}\}\s*$", re.MULTILINE)


class PromptError(Exception):
    """prompt 缺失或片段引用成环。"""


def _read(name: str, seen: tuple[str, ...] = ()) -> str:
    if name in seen:
        raise PromptError(f"prompt 片段循环引用：{' → '.join((*seen, name))}")
    path = PROMPTS_DIR / f"{name}.md"
    if not path.is_file():
        raise PromptError(f"缺少 prompt 文件：{path}")
    text = path.read_text(encoding="utf-8").strip()
    return _PARTIAL.sub(lambda m: _read(m.group(1), (*seen, name)), text)


@lru_cache(maxsize=64)
def text(name: str) -> str:
    """展开 `{{> partial}}` 后的 prompt 正文。"""
    return _read(name)


@lru_cache(maxsize=64)
def version(*names: str) -> str:
    """prompt 版本 = 展开后内容的哈希（前 12 位）。"""
    blob = "\x00".join(text(n) for n in names)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def available() -> tuple[str, ...]:
    return tuple(sorted(p.stem for p in PROMPTS_DIR.glob("*.md")))
