"""端口（Port）——宿主耦合面的全部定义。

抽离的目标不是「重写」，而是把**与宿主相关的三件事**收进可替换的接口，其余 15 个模块
保持纯 Python（只依赖 PyYAML + 标准库）。三个端口：

| 端口 | 现状实现 | 契约 |
|---|---|---|
| `ModelResolver` | `backends.model_host`（跟随宿主主模型） | 把 capability 解析成一个可调用的 `ModelRef` |
| `CardChannel` | `backends.feishu_lark_cli`（subprocess 调 lark-cli） | 建卡片实体 / 发卡 / 全量更新实体 / 发纯文本 |
| 入站回调 | **不是接口，是 CLI 入口** | 宿主把回调载荷交给 `newspipe card <payload>` |

第三个刻意不做成 Protocol：回调的边界本来就是一个进程调用（宿主侧 shell 出
`cli.py --card`，将来可以是飞书事件订阅直接调同一个函数）。给它套一层接口只会多一层间接。

新增一个实现时，只要它满足下面的 Protocol，就不需要改核心任何一行；`tests/test_ports.py`
用 `runtime_checkable` 对每个已注册实现做结构校验。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ModelResolver(Protocol):
    """把 capability 名解析成「用哪个模型、走哪个 base_url、用哪个密钥」。

    只负责**解析**，不负责发请求：HTTP 调用、回执复用、预算熔断都在 `llm.py` 里，
    对两种实现完全一致。这正是「跟随宿主主模型」与「显式配置」唯一真正的差别所在。
    """

    def resolve(self, capability: str, models_cfg: dict) -> Any:
        """返回 `llm.ModelRef`；解析不出来时抛 `errors.ConfigError`。"""
        ...


@runtime_checkable
class CardChannel(Protocol):
    """投递通道：卡片实体生命周期 + 纯文本兜底。

    实现必须遵守飞书的硬约束（一个实体只能发一次、`sequence` 严格递增），
    或在自己的平台上等价地保证「同一条消息原地换页」。
    """

    def create_entity(self, card: dict) -> str:
        """建卡片实体，返回后续发送/更新都要用的 id。"""
        ...

    def send_card(self, chat_id: str, card_id: str) -> str:
        """按实体 id 发卡，返回消息 id。"""
        ...

    def update_entity(self, card_id: str, sequence: int, card: dict) -> bool:
        """全量更新实体（翻页就是它）。`sequence` 必须比上次严格递增。"""
        ...

    def send_text(self, chat_id: str, text: str) -> str:
        """纯文本兜底（卡片发不出去时的降级通道）。"""
        ...
