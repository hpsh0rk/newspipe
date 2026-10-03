"""解析结果的值类型：一次模型调用的全部所需信息。

放在独立模块是为了避免循环导入：`backends/*` 需要构造它，而 `llm.py` 需要返回它。
`llm.ModelRef` 是这里的再导出，外部用法不变。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelRef:
    model: str
    provider: str
    base_url: str
    api_key: str
    source: str

    def describe(self) -> dict[str, str]:
        """可安全打印的描述（不含密钥）。"""
        return {"model": self.model, "provider": self.provider,
                "base_url": self.base_url, "key": "有" if self.api_key else "无",
                "source": self.source}
