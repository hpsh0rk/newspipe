"""解析结果的值类型：一次模型调用的全部所需信息。

放在独立模块是为了避免循环导入：`backends/*` 需要构造它，而 `llm.py` 需要返回它。
`llm.ModelRef` 是这里的再导出，外部用法不变。

`rewrite_loopback` 也是给两个 resolver 共用的：容器里「宿主的回环地址」不是 127.0.0.1，
而宿主配置里写的模型端点常常就是 `http://localhost:7863/v1`（本机网关）。这是**部署事实**，
只能由部署显式给别名，不能让代码猜。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

#: 部署给的别名（例如容器里的 `host.docker.internal`）。不配则原样返回。
LOOPBACK_ALIAS_ENV = "NEWSPIPE_LOOPBACK_ALIAS"
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def rewrite_loopback(url: str) -> str:
    """把 URL 里的回环主机名换成部署给的别名；非回环地址一律不动。"""
    alias = (os.environ.get(LOOPBACK_ALIAS_ENV) or "").strip()
    if not alias or not url:
        return url
    parsed = urlsplit(url)
    if parsed.hostname not in _LOOPBACK_HOSTS:
        return url
    netloc = f"{alias}:{parsed.port}" if parsed.port else alias
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


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
