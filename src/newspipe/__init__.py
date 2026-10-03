"""newspipe —— 资讯管线：多信源采集 → AI 加工 → 卡片投递。

**与宿主解耦**：核心 15 个模块只依赖 PyYAML + 标准库；与宿主相关的三件事收在 `ports.py`
定义的两个 Protocol 里（模型解析、投递通道），默认实现保持「跟随宿主」的行为，
所以抽离这一步**不改运行时行为**，只把可替换点显式化。

分层与依赖方向（单向，`pipeline` 是唯一知道全序的地方）：

    配置面  config（sources.yaml 四轴 + models.yaml）
    采集面  scheduler → adapters → net
    加工面  dedup / filter → delivery.plan → enrich（llm + prompts）
    投递面  render → channel → interaction
    状态面  state
    编排    pipeline（唯一知道全序的地方）+ cli

不变量：`delivery.plan` 先于 `enrich`（先定展示集再花钱）；enrich 失败不阻塞投递；
interaction 不直接发消息；一张卡 = 一个卡片实体且只能发一次。
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
