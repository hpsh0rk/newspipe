"""投递通道实现 —— 飞书卡片实体，经 `lark-cli` 子进程（原 `channel.py`，行为不变）。

为什么用卡片实体而不是「发卡 + PATCH 消息」：
- 列表页 ↔ 详情页这种**同一条消息换页**，靠 `PUT /cardkit/v1/cards/:id` 全量更新实体实现，
  没有次数限制（回调响应式更新走 `event.token`，只有 30 分钟 / 最多 2 次，做不了翻页）；
- 实体带 `element_id`，将来要做局部更新（只改一行、只改一段文本）不用换机制。

硬约束（2026-10-03 实机验证）：
- 一个卡片实体**只能发送一次**；实体有效期 14 天；`sequence` 必须严格递增；
- 整卡 ≤200 元素、≤30KB；回调交互进行中卡片不可更新（200810）。

调度器的 env 会被 sanitize：外部 CLI 靠环境变量定位自己的配置目录，变量丢了就会静默回落到
另一个应用身份（不在目标群里）→ 持续性 230002 "out of the chat"。所以启动时把宿主目录
同步给它（见 `hostenv.export_host_home_for_child`），并把 node 的目录补进 PATH。

**这是宿主耦合的一半**：换成直连飞书 OpenAPI 只需另写一个满足 `ports.CardChannel` 的类，
核心一行不用改。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

from newspipe import hostenv
from newspipe.errors import DeliveryError

_extra = ["/opt/homebrew/bin", "/usr/local/bin", str(Path.home() / ".local/bin")]
_p = os.environ.get("PATH", "").split(":")
os.environ["PATH"] = ":".join(_p + [x for x in _extra if x not in _p])
# 补上外部 CLI 需要的宿主目录（没配宿主就不猜，让它用自己的默认）
hostenv.export_host_home_for_child()


def _lark_cli() -> str:
    found = shutil.which("lark-cli")
    if found:
        return found
    for cand in ("/opt/homebrew/bin/lark-cli", "/usr/local/bin/lark-cli",
                 str(Path.home() / ".local/bin/lark-cli")):
        if Path(cand).exists():
            return cand
    return "lark-cli"


def _call(*args: str, timeout: int = 60) -> dict:
    proc = subprocess.run([_lark_cli(), *args], capture_output=True, text=True, timeout=timeout)
    raw = proc.stdout or proc.stderr or ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise DeliveryError(f"lark-cli 输出不可解析 rc={proc.returncode}: {raw[:300]}") from None
    if not isinstance(data, dict):
        raise DeliveryError(f"lark-cli 输出不是对象：{raw[:300]}")
    return data


class LarkCliChannel:
    """满足 `ports.CardChannel`：靠 lark-cli 发卡与更新实体。"""

    name = "feishu_lark_cli"

    def create_entity(self, card: dict) -> str:
        """建卡片实体，返回 card_id（后续发送与更新都用它）。"""
        payload = json.dumps({"type": "card_json",
                              "data": json.dumps(card, ensure_ascii=False)}, ensure_ascii=False)
        data = _call("api", "POST", "/open-apis/cardkit/v1/cards", "--data", payload)
        if not data.get("ok"):
            raise DeliveryError(f"创建卡片实体失败：{(data.get('error') or {}).get('message', str(data)[:300])}")
        card_id = (data.get("data") or {}).get("card_id")
        if not card_id:
            raise DeliveryError(f"创建卡片实体未返回 card_id：{str(data)[:300]}")
        return str(card_id)

    def send_card(self, chat_id: str, card_id: str) -> str:
        """按 card_id 发卡，返回 message_id。"""
        content = json.dumps({"type": "card", "data": {"card_id": card_id}}, ensure_ascii=False)
        data = _call("im", "+messages-send", "--chat-id", chat_id,
                     "--msg-type", "interactive", "--content", content)
        if not data.get("ok"):
            raise DeliveryError(f"发送卡片失败：{(data.get('error') or {}).get('message', str(data)[:300])}")
        message_id = (data.get("data") or {}).get("message_id")
        if not message_id:
            raise DeliveryError(f"发送卡片未返回 message_id：{str(data)[:300]}")
        return str(message_id)

    def update_entity(self, card_id: str, sequence: int, card: dict) -> bool:
        """全量更新卡片实体（翻页就是它）。sequence 必须比上一次严格递增。"""
        payload = json.dumps({"card": {"type": "card_json",
                                       "data": json.dumps(card, ensure_ascii=False)},
                              "uuid": str(uuid.uuid4()),
                              "sequence": int(sequence)}, ensure_ascii=False)
        data = _call("api", "PUT", f"/open-apis/cardkit/v1/cards/{card_id}", "--data", payload)
        return bool(data.get("ok"))

    def send_text(self, chat_id: str, text: str) -> str:
        """纯文本兜底（卡片发不出去时的降级通道；也是 painpoints 摘要的投递方式）。"""
        data = _call("im", "+messages-send", "--chat-id", chat_id, "--text", text)
        if not data.get("ok"):
            raise DeliveryError(f"发送文本失败：{(data.get('error') or {}).get('message', str(data)[:300])}")
        return str((data.get("data") or {}).get("message_id", ""))
