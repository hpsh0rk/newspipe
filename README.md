# newspipe —— 资讯管线（独立包）

**一句话**：多信源采集 → AI 加工（中文标题/摘要/翻译）→ 飞书卡片投递（列表页 ↔ 详情页可点），
**可以不依赖 Hermes 独立运行**（直连飞书 OpenAPI 投递 + 自建长连接入站 + 自带调度），
默认行为仍是「跟随 Hermes 主模型 + 经 lark-cli 投递」（缺 `service.yaml` 时与抽离前逐字节一致）。

从 Knowledge Vault 的 `scripts/news/` 抽离而来。设计权威源在 Vault，本仓库是代码的家。

## 现状表

| 维度 | 现状 |
|---|---|
| 设计讨论 | Vault：`thinkings/资讯管线架构-v2-2026-10-03.md`（设计权威）+ 研讨 6 轮 + 交接单 |
| 代码 | 本仓库：23 个模块 + 5 个后端 + 159 个测试（全绿） |
| 真实用户验证 | 1 人（作者自用）：在 Vault 里每天真跑，3 个槽位 + 每 5 分钟轮询 + 痛点提炼 |
| 独立运行 | ✅ 通道直连真机跑通（token→建实体→发卡→更新）+ 长连接真机握手 + `ping success` |
| 前置调研 | 已完成：决策模型 Jev（TypeSafe AI）调研，对应 `priority_judge` 位置（见 Vault Round 5） |
| 与宿主耦合 | **全部收进端口/开关**：模型解析、投递通道、入站来源、数据根目录 |

## 核心设计（30 秒版）

四轴正交配置（`fetch` 何时拉 / `filter` 留什么 / `enrich` 加工什么 / `deliver` 怎么发），
单向分层：配置 → 采集 → 加工 → 投递 → 状态，`pipeline.py` 是唯一知道全序的地方，`render.py` 是纯函数。

四条不变量：① `delivery.plan` 先于 `enrich`（先定展示集再花钱）；② enrich 失败**绝不阻塞投递**；
③ `interaction` 不直接发消息（只写状态 + 更新卡片实体）；④ 一张卡 = 一个卡片实体且只能发一次。

## 与宿主的边界（`src/newspipe/ports.py`）

| 端口 | 跟随 Hermes（默认） | 独立运行 |
|---|---|---|
| `ModelResolver` | `backends/model_hermes.py`（读 `~/.hermes/config.yaml` + `.env`） | `backends/model_openai.py`（纯显式配置） |
| `CardChannel` | `backends/feishu_lark_cli.py`（subprocess 调 lark-cli） | `backends/feishu_direct.py`（直连 OpenAPI，已真机验证） |
| 入站回调 | Hermes 插件 → `newspipe card <payload>` | `inbound.py`（长连接 ws / HTTP 回调，已真机握手） |
| 调度 | Hermes cron（5 条 job） | `service.py`（一个常驻进程，自带槽位 + 轮询节流） |

## 快速上手

```bash
python -m venv .venv && .venv/bin/pip install -e .          # 或 pip install -e '.[h2,feishu,crypto]'
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
`NEWSPIPE_MODEL_BACKEND`（`hermes`|`explicit`）、`NEWSPIPE_CHANNEL`、
`NEWSPIPE_FEISHU_*`（凭据）、`NEWSPIPE_SDK_LOG`（`info`|`debug`，调试长连接用）。

## 独立运行（阶段 2 + 3）

三步：**填 `service.yaml` → 自检通道 → 起常驻服务**。

```bash
# 1. 服务配置（密钥不要写在这里）
cat > $NEWSPIPE_HOME/info/news/service.yaml <<'YAML'
channel: feishu_direct        # 默认 feishu_lark_cli；显式开才脱离 lark-cli
inbound: {mode: ws}           # ws（长连接，只需出网）| http（需公网）| none
schedule: {tick_seconds: 300} # 轮询节流粒度
YAML

# 2. 通道自检：真发一张卡，输出里不含任何密钥
.venv/bin/newspipe --probe-channel --chat oc_xxxxxxxx

# 3. 常驻服务（自带调度 + 入站，一个进程）
.venv/bin/newspipe --serve --verbose
.venv/bin/newspipe --serve --once --dry     # 演练：跑一轮到期任务就退出
```

凭据解析顺序（`src/newspipe/credentials.py`，**永不打印值**）：
`service.yaml` 显式值 → env `NEWSPIPE_FEISHU_*` → 回落宿主命名 `FEISHU_*` → dotenv（`$NEWSPIPE_HOME/.env`、
`~/.hermes/.env`）→ macOS 钥匙串。缺关键项时报 `ConfigError` 并给可执行的修法。

开机自启（launchd）：

```bash
cp service/com.newspipe.service.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.newspipe.service.plist
launchctl kickstart -k gui/$(id -u)/com.newspipe.service
tail -f ~/Library/Logs/newspipe.log
```

### 两个必须知道的运行事实

1. **入站事件只发给应用的其中一条长连接**。如果 Hermes 网关（也用同一个 app）在跑，
   点击回调可能落到它那边——不是故障，但会让你以为「自建入站没生效」。
   切换期先停一侧（`launchctl bootout` / 停 Hermes 网关），别两边同时跑。
2. **飞书应用的卡片回调必须指向「长连接」模式**（`FEISHU_CONNECTION_MODE=websocket`）。
   `http` 模式需要公网可达地址 + 事件订阅回调 URL + `encrypt_key`（签名校验与 AES 解密）。

## 给 Agent 的 CLI（防腐层）

Hermes **不直接改这个仓库的文件**，只通过 CLI 说话；CLI 只通过**结果对象**回话。
Agent 的第一个动作永远是：

```bash
newspipe api describe --json     # 权威契约：命令、参数、退出码、示例
newspipe doctor --json           # 自检：配置 / hooks / 凭据 / 元素预算 / 事件积压
```

常用：

```bash
newspipe status --json                                    # 运行态（各源心跳、AI 用量与降级原因）
newspipe source list --json                               # 信源 + 文件 hash（乐观并发用）
newspipe source set <name> --from-json - --dry-run --json # 演练，不落盘
newspipe hooks add --from-json '{"id":"hermes.wiki","label":"⭐ 入库",
    "action":"hermes.wiki","handler":"~/bin/news_hook_wiki.sh"}' --json
newspipe events list --unconsumed --json                  # 投递结果/点击/降级/失败
newspipe queue list --json                                # ⭐ 待入库（人确认后才 ack）
newspipe queue ack <event_id> --note wiki/ai/x.md --by hermes --json
```

**硬规则**

1. 任何命令都输出**一个**结构化结果：`ok` / `command` / `changed` / `data` / `error` /
   `warnings` / `next`。未捕获异常变成 `E_INTERNAL`，**不吐堆栈**——agent 要能据此决断。
2. 退出码：`0` 成功 / `1` 运行时故障 / `2` 用法或配置错 / `3` 网络或投递错 / `4` 校验失败。
3. 写配置**只能**走 `source set|enable|disable|remove` 与 `hooks add|remove`：先校验后落盘
   （候选配置真的跑一遍 `config.load`），支持 `--dry-run`、`--base-hash`。
   **直接编辑 `sources.yaml` / `hooks.yaml` 被禁止**——校验会拦下静默失效的配置。
4. 写入是**文本级块编辑**：被替换的块内注释会丢，**块外逐字节不动**（`source enable/disable`
   只改一行）。`--dry-run` 恒不落盘。
5. `⭐ 收藏` 只进 `state/events/` 队列，**不直接入库**——人确认后才 `queue ack`。
6. 旧 flag 风格（`--slot am` / `--card '<json>'` / `--status` / `--serve`）保留给 4 个 cron
   wrapper 与插件薄壳，行为不变；它们**成功时 stdout 为空**（no_agent cron 任务把非空 stdout
   当告警投递给用户）。子命令则一律输出结果对象——两种受众，两套约定，别混。

## 文档索引

| 文档 | 内容 | 来源 |
|---|---|---|
| `docs/migration.md` | 抽离方案：耦合面清单 + 三阶段路线 + 每阶段代价 | 本项目 |
| `specs/stage1-package-extraction.md` | 阶段 1 规格与验收（已交付） | 本项目 |
| `specs/stage1-brainstorming.md` | 阶段 1 的设计决策与取舍 | 本项目 |
| `specs/stage2-3-standalone.md` | 阶段 2/3 规格与验收（已交付） | 本项目 |
| `handoff.md` | 交接单：现状 / 改动文件 / 坑 / 下一步 | 本项目 |
| `docs/vault-snapshot/` | Vault 设计文档的只读快照（含 sha256 清单） | Vault |

## 落地顺序

| 阶段 | 内容 | 状态 |
|---|---|---|
| P0 | 包化 + 端口化（行为不变） | ✅ 交付，`specs/stage1-package-extraction.md` |
| P1 | Vault 接线方式定案（editable install vs 独立部署） | ⏳ 待决策（不阻塞 P2/P3） |
| P2 | 通道直连飞书 OpenAPI，去掉 `lark-cli` 依赖 | ✅ 交付并真机验证，`specs/stage2-3-standalone.md` |
| P3 | 入站自建 + 自带调度（常驻服务） | ✅ 交付并真机握手；点击端到端待切换期实测 |

## 风险提醒（照抄自讨论，勿淡化）

抽离会把 Hermes 已经解决的四件事变成自己要维护的：**凭据存储、进程守护、投递重试、事件订阅**。
代码里这四件事都有了对应实现，但**运维责任也一并转移**了：钥匙串/环境变量要自己管、
launchd 要自己装、投递失败要自己看日志、事件订阅要自己盯连接。
只有当你确实要在**没有 Hermes 的机器**上跑，这套独立形态才值得启用。
