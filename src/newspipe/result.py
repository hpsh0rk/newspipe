"""统一结果信封 —— 每条命令、无论成败，都只输出**一个**结构化结果对象。

为什么要有它：agent 要**按结果做决断**。所以三种东西被禁止：
沉默（什么都不打印）、半句话（打印了但不是结构化的）、堆栈（agent 无法决策）。

契约（由 `tests/test_result.py` 守住）：

1. `ok=True`  ⇒ `error` 必须是 `None`；
2. `ok=False` ⇒ `error` 必须非空，且 `data` 必须是 `None`；
3. `--json` 下 stdout **恰好一个** JSON 对象——未捕获异常也转成 `E_INTERNAL`，绝不吐堆栈；
4. `changed` 明确回答「这次调用到底改没改东西」（agent 最常需要判断的就是这个）；
5. `next` 给出建议的下一步命令，让 agent 不必猜。

退出码：`0` 成功 / `1` 运行时故障 / `2` 用法或配置错 / `3` 网络或投递错 / `4` 校验失败（写被拒）。
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, TextIO

# --------------------------------------------------------------------- 退出码
EXIT_OK = 0
EXIT_RUNTIME = 1
EXIT_USAGE = 2
EXIT_DELIVERY = 3
EXIT_VALIDATION = 4

# 错误码 → 退出码。新增错误码必须在这里登记，否则测试会失败（这是故意的：
# 没登记的码会让 agent 拿到一个它无法分类的失败）。
ERROR_EXIT: dict[str, int] = {
    "E_USAGE": EXIT_USAGE,          # 参数写错
    "E_CONFIG": EXIT_USAGE,         # 配置文件本身坏了（sources.yaml / service.yaml / hooks.yaml）
    "E_VALIDATION": EXIT_VALIDATION,  # 写入被校验拒绝（没有落盘）
    "E_NOT_FOUND": EXIT_VALIDATION,   # 目标不存在（源、批次、事件 id）
    "E_CONFLICT": EXIT_VALIDATION,    # base-hash 不匹配（别人改过了）
    "E_DELIVERY": EXIT_DELIVERY,      # 发卡失败
    "E_NETWORK": EXIT_DELIVERY,       # 采集失败
    "E_RUNTIME": EXIT_RUNTIME,        # 其他运行时故障
    "E_INTERNAL": EXIT_RUNTIME,       # 未捕获异常（兜底，绝不让堆栈冒到 stdout）
}


@dataclass
class Result:
    """一次命令调用的完整结果。字段顺序 = 输出顺序，便于人读。"""

    command: str
    ok: bool
    data: Any = None
    error: dict[str, Any] | None = None
    changed: bool = False
    warnings: list[str] = field(default_factory=list)
    next: list[str] = field(default_factory=list)
    exit_code: int = EXIT_OK
    contract_version: int = 1
    #: 命令体已经自己打印了人读版（表格等）⇒ 人读模式下别再套一层信封。
    #: `--json` 不受影响：契约要求 stdout 恰好一个 JSON 对象。
    printed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "command": self.command,
            "contract_version": self.contract_version,
            "changed": self.changed,
            "data": self.data,
            "error": self.error,
            "warnings": list(self.warnings),
            "next": list(self.next),
        }

    def to_text(self) -> str:
        """人读版：**同样的信息**，只是排版不同（不允许只在一侧出现的信息）。"""
        head = "✅" if self.ok else "❌"
        lines = [f"{head} {self.command}" + ("（已改动）" if self.changed and self.ok else "")]
        if not self.ok and self.error:
            err = self.error
            lines.append(f"   失败：{err.get('code', 'E_RUNTIME')} {err.get('message', '')}")
            if err.get("hint"):
                lines.append(f"   提示：{err['hint']}")
            details = err.get("details")
            if details:
                lines.append(f"   细节：{json.dumps(details, ensure_ascii=False)}")
        if self.ok and self.data is not None:
            if isinstance(self.data, (dict, list)):
                lines.append("   " + json.dumps(self.data, ensure_ascii=False,
                                                indent=2).replace("\n", "\n   "))
            else:
                lines.append(f"   {self.data}")
        for warning in self.warnings:
            lines.append(f"   ⚠️ {warning}")
        for hint in self.next:
            lines.append(f"   下一步：{hint}")
        return "\n".join(lines)


def ok(command: str, data: Any = None, *, changed: bool = False,
       warnings: list[str] | None = None, next: list[str] | None = None,
       printed: bool = False) -> Result:
    return Result(command=command, ok=True, data=data, error=None, changed=changed,
                  warnings=list(warnings or []), next=list(next or []), printed=printed)


def fail(command: str, code: str, message: str, *, hint: str = "",
         details: Any = None, warnings: list[str] | None = None,
         next: list[str] | None = None, exit_code: int | None = None) -> Result:
    """构造失败结果。`data` 恒为 None（契约 2）。"""
    error: dict[str, Any] = {"code": code, "message": message}
    if hint:
        error["hint"] = hint
    if details is not None:
        error["details"] = details
    return Result(command=command, ok=False, data=None, error=error, changed=False,
                  warnings=list(warnings or []), next=list(next or []),
                  exit_code=ERROR_EXIT.get(code, EXIT_RUNTIME) if exit_code is None else exit_code)


def emit(result: Result, *, json_out: bool, out: TextIO | None = None) -> int:
    """打印结果并返回退出码。`--json` 下 stdout 恰好一个 JSON 对象。"""
    stream = out or sys.stdout
    if json_out:
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2), file=stream)
    elif not (result.printed and result.ok):
        # 失败**一定**要说话，哪怕命令体已经打印过表格（否则 agent 会以为成功）。
        print(result.to_text(), file=stream)
    return result.exit_code


def guard(command: str, fn: Callable[[], Result], *, json_out: bool,
          out: TextIO | None = None) -> int:
    """把命令体包起来：任何未捕获异常都变成 `E_INTERNAL` 结果，而不是堆栈。

    没有它，agent 拿到的是「退出码 1 + 一段 traceback」——无法分类、无法决策。
    """
    try:
        result = fn()
    except KeyboardInterrupt:
        raise
    except Exception as exc:                      # noqa: BLE001 —— 这里就是要兜住一切
        from newspipe.errors import ConfigError, DeliveryError, NewsError

        if isinstance(exc, ConfigError):
            result = fail(command, "E_CONFIG", str(exc),
                          hint="检查 info/news/ 下的 YAML；`newspipe doctor --json` 会列出问题")
        elif isinstance(exc, DeliveryError):
            result = fail(command, "E_DELIVERY", str(exc),
                          hint="`newspipe status --json` 看通道与最近一轮；凭据问题用 --probe-channel")
        elif isinstance(exc, NewsError):
            result = fail(command, "E_RUNTIME", str(exc))
        else:
            result = fail(command, "E_INTERNAL", f"{type(exc).__name__}: {exc}",
                          hint="这是未预期的错误，请连同 command 一起报给维护者")
    return emit(result, json_out=json_out, out=out)
