# newspipe —— 资讯管线（独立包）

**一句话**：多信源采集 → AI 加工（中文标题/摘要/翻译）→ 飞书卡片投递（列表页 ↔ 详情页可点），
**核心与宿主解耦**（只依赖 PyYAML + 标准库），默认行为仍是「跟随 Hermes 主模型 + 经 lark-cli 投递」。

从 Knowledge Vault 的 `scripts/news/` 抽离而来。设计权威源在 Vault，本仓库是代码的家。

## 现状表

| 维度 | 现状 |
|---|---|
| 设计讨论 | Vault：`thinkings/资讯管线架构-v2-2026-10-03.md`（设计权威）+ 研讨 5 轮 + 交接单 |
| 代码 | 本仓库：21 个模块 / 2964 行（原样搬迁 + 端口化）+ 81 个测试 |
| 真实用户验证 | 1 人（作者自用）：在 Vault 里每天真跑，3 个槽位 + 每 5 分钟轮询 + 痛点提炼 |
| 前置调研 | 已完成：决策模型 Jev（TypeSafe AI）调研，对应 `priority_judge` 位置（见 Vault Round 5） |
| 与宿主耦合 | **3 处，已收进端口**：模型解析、投递通道、（第 4 处）数据根目录 → 见 `docs/migration.md` |

## 核心设计（30 秒版）

四轴正交配置（`fetch` 何时拉 / `filter` 留什么 / `enrich` 加工什么 / `deliver` 怎么发），
单向分层：配置 → 采集 → 加工 → 投递 → 状态，`pipeline.py` 是唯一知道全序的地方，`render.py` 是纯函数。

四条不变量：① `delivery.plan` 先于 `enrich`（先定展示集再花钱）；② enrich 失败**绝不阻塞投递**；
③ `interaction` 不直接发消息（只写状态 + 更新卡片实体）；④ 一张卡 = 一个卡片实体且只能发一次。

## 与宿主的边界（`src/newspipe/ports.py`）

| 端口 | 默认实现（跟随 Hermes） | 独立实现 |
|---|---|---|
| `ModelResolver` | `backends/model_hermes.py`（读 `~/.hermes/config.yaml` + `.env`） | `backends/model_openai.py`（纯显式配置，已实现，7 条契约测试） |
| `CardChannel` | `backends/feishu_lark_cli.py`（subprocess 调 lark-cli） | 直连飞书 OpenAPI（阶段 2，未做） |
| 入站回调 | Hermes 插件 → `newspipe card <payload>` | 飞书事件订阅 → 同一个函数（阶段 3，未做） |

## 快速上手

```bash
python -m venv .venv && .venv/bin/pip install -e .          # 或 pip install -e '.[h2]'
export NEWSPIPE_HOME=/path/to/data          # 放 info/news/ 的那一层（配置 + 状态）
cp examples/news/*.yaml $NEWSPIPE_HOME/info/news/           # 示例配置，改 chat 即可
.venv/bin/newspipe --list-sources
.venv/bin/newspipe --slot am --dry --verbose                # 演练：真采集，不投递
.venv/bin/newspipe --status                                 # 运行态 + 模型用量与失败原因
```

> 用 `uv` 的话：`uv venv --seed .venv` 后跑 `.venv/bin/pip install -e .`——实测 `uv pip install -e .`
> **不生成 console script**（包能 import，但 `.venv/bin/newspipe` 不存在）。
> 任何情况下 `python -m newspipe.cli` 都等价可用。

环境变量：`NEWSPIPE_HOME`（数据根，默认 cwd）、`NEWSPIPE_NEWS_DIR`、`NEWSPIPE_STAGING_QUEUE`、
`NEWSPIPE_MODEL_BACKEND`（`hermes`|`explicit`）、`NEWSPIPE_CHANNEL`。

## 文档索引

| 文档 | 内容 | 来源 |
|---|---|---|
| `docs/migration.md` | 抽离方案：耦合面清单 + 三阶段路线 + 每阶段代价 | 本项目 |
| `specs/stage1-package-extraction.md` | 阶段 1 规格与验收（已交付） | 本项目 |
| `specs/stage1-brainstorming.md` | 阶段 1 的设计决策与取舍 | 本项目 |
| `handoff.md` | 交接单：现状 / 改动文件 / 坑 / 下一步 | 本项目 |
| `docs/vault-snapshot/` | Vault 设计文档的只读快照（含 sha256 清单） | Vault |

## 落地顺序

| 阶段 | 内容 | 状态 |
|---|---|---|
| P0 | 包化 + 端口化（行为不变） | ✅ 已完成，见 `specs/stage1-package-extraction.md` |
| P1 | Vault 接线方式定案（editable install vs 独立部署） | ⏳ 待决策 |
| P2 | 通道直连飞书 OpenAPI，去掉 `lark-cli` 依赖 | 未开始 |
| P3 | 入站自建常驻服务（事件订阅），真正零 Hermes | 未开始（占抽离总工作量约 80%） |

## 风险提醒（照抄自讨论，勿淡化）

抽离会把 Hermes 已经解决的四件事变成自己要维护的：**凭据存储、进程守护、投递重试、事件订阅**。
只有当你确实要在**没有 Hermes 的机器**上跑，阶段 3 才值得做。
