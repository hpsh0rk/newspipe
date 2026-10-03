"""适配器包：每个适配器只实现 `fetch(cfg, since=None) -> [Item]`。

Item 统一模型（引擎与渲染层只认这些字段）：

| 字段 | 含义 |
|---|---|
| `ext_id` | 源内稳定唯一 ID（幂等闸的键 = source + ext_id） |
| `title` | 原文标题（未加工） |
| `url` | 卡片上"查看原文"要跳的地址 |
| `original_url` | 原始出处（AIHOT 这类聚合源才有区别，其余同 url） |
| `source` | 展示用的来源名（渲染徽章） |
| `summary` | 原文摘要/正文片段（可空；enrich 会在此基础上产出中文摘要） |
| `category` | 可选分类 |
| `score` | 可选热度（排序用） |
| `pub_ts` | 可选发布时间（epoch 秒；poll 的时间闸靠它） |

契约要求：
- 网络一律走 `newspipe.net.request`（出口回落 / h2 在那一层）；
- 单个 feed 失败返回空列表继续，**不要**让一个死源沉掉整批；但**全 feed 失败必须抛错**
  （静默返回 `[]` 会让"源死了"和"今天很安静"在心跳里长得一模一样）。
"""
from __future__ import annotations
