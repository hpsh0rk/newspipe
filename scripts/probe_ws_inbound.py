#!/usr/bin/env python3
"""真机探测：长连接入站（阶段 3）。只报告可用性与计数，绝不打印任何密钥。

用法（在仓库根目录）：
    .venv/bin/python scripts/probe_ws_inbound.py                    # INFO：只看握手
    NEWSPIPE_SDK_LOG=debug .venv/bin/python scripts/probe_ws_inbound.py   # 看 ping/pong

成功标志：
    connected to wss://msg-frontier.feishu.cn/ws/v2?...   ← 握手成功
    ping success / receive pong                            ← 链路真的活着（debug 级别）

失败常见原因：
    - 应用没开「长连接」模式（飞书后台 → 事件订阅 → 订阅方式）
    - app_id/app_secret 不是同一个应用，或 secret 已轮换
    - 网络出不去（境内直连 open.feishu.cn 通常没问题）
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from newspipe import config, credentials, inbound  # noqa: E402

news_dir = config.default_news_dir()
news_dir.mkdir(parents=True, exist_ok=True)

creds = credentials.resolve_feishu(news_dir=news_dir)
print(f"凭据：app_id={creds.app_id} domain={creds.domain} "
      f"secret={'已解析（不打印）' if creds.app_secret else '缺失'} "
      f"encrypt_key={'已配置' if creds.encrypt_key else '未配置'}")
print(f"来源：{creds.source}")
print(f"数据根：{news_dir}")
print(f"SDK 日志级别：{os.environ.get('NEWSPIPE_SDK_LOG', 'info')}")

lines: list[str] = []
watch = ("connected to", "connect failed", "disconnect", "ping")


class Capture(logging.Handler):
    """SDK 的 logger 名是 'Lark'（默认 WARNING，Client 构造时按 log_level 调低）。"""

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        lines.append(msg)
        if any(k in msg.lower() for k in watch):
            print("  [sdk]", msg[:150], flush=True)


sdk_logger = logging.getLogger("Lark")
sdk_logger.setLevel(logging.DEBUG)
sdk_logger.addHandler(Capture())

print("\n启动长连接（最多等 30s 握手）…")
handle = inbound.start_inbound(mode="ws", service_cfg={}, creds=creds, news_dir=news_dir,
                              logger=lambda m: print("[newspipe]", m, flush=True))

connected, failed = False, ""
deadline = time.time() + 30
while time.time() < deadline:
    time.sleep(0.5)
    if any("connected to" in line for line in lines):
        connected = True
        break
    hit = [line for line in lines if "connect failed" in line]
    if hit:
        failed = hit[0]
        break

print(f"\n握手结果：{'CONNECTED ✓' if connected else 'FAILED ✗'}")
if failed:
    print("失败原因：", failed[:300])
print("线程存活：", handle.thread.is_alive() if handle.thread else None)
print("计数：", handle.describe())

if connected:
    print("观察 5s（debug 级别应能看到 ping success / receive pong）…")
    before = len(lines)
    time.sleep(5)
    alive = [line for line in lines[before:] if "ping" in line.lower() or "pong" in line.lower()]
    print("链路活动：", alive[:2] or "（INFO 级别不打印 ping，用 NEWSPIPE_SDK_LOG=debug 看）")

handle.stop()
print("已停止。")
