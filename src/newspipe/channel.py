"""投递层的稳定门面：模块级函数转发给当前通道后端（见 `backends/`）。

为什么保留模块级函数而不是让调用方直接持有通道对象：
`pipeline` / `interaction` / `cli` 都是通过 `channel.create_entity(...)` 这种**属性访问**调用的，
测试也靠 `patch.object(channel, "create_entity")` 打桩。保留门面 = 抽离后调用方与测试一行都不用改。

实现本身（lark-cli 子进程）在 `backends/feishu_lark_cli.py`；换直连飞书 OpenAPI 时，
只需让 `get_channel()` 返回另一个满足 `ports.CardChannel` 的对象。
"""

from __future__ import annotations

from newspipe.backends import get_channel


def create_entity(card: dict) -> str:
    """建卡片实体，返回 card_id（后续发送与更新都用它）。"""
    return get_channel().create_entity(card)


def send_card(chat_id: str, card_id: str) -> str:
    """按 card_id 发卡，返回 message_id。"""
    return get_channel().send_card(chat_id, card_id)


def update_entity(card_id: str, sequence: int, card: dict) -> bool:
    """全量更新卡片实体（翻页就是它）。sequence 必须比上一次严格递增。"""
    return get_channel().update_entity(card_id, sequence, card)


def send_text(chat_id: str, text: str) -> str:
    """纯文本兜底（卡片发不出去时的降级通道）。"""
    return get_channel().send_text(chat_id, text)
