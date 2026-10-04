# newspipe

**把一堆信源变成一张可以点开的卡片。**

多信源采集 → AI 加工（中文标题 / 摘要 / 翻译）→ 卡片投递（列表页 ↔ 详情页原地翻页），
**自带调度与事件入站，可以完全独立运行**。

```
信源（RSS / 站点 API / 论坛 / 社交）
   → 采集（四轴配置：何时拉 / 留什么 / 加工什么 / 怎么发）
   → AI 加工（标题、摘要、翻译；失败降级为原文，绝不阻塞投递）
   → 卡片投递（一张卡 = 一个卡片实体，原地翻页，点击有回显）
   → 事件流（投递 / 点击 / 收藏 / 屏蔽 / 降级 / 失败，落成 JSONL）
```

---

## 项目背景

每天要看的信源越来越多，而它们分散在不同平台、不同格式、不同语言里。已有的方案要么是
「一个 RSS 阅读器」（只能读，不能筛、不能加工、不能推给团队），要么是「一个爬虫脚本」
（跑起来容易，跑稳很难：重复推送、失败静默、投递限流、状态丢失）。

newspipe 想解决的是**中间那一层**：把「采集 → 判断 → 加工 → 推送」做成一条**可运维**的管线。
它来自一个真实需求（作者自己每天在用的资讯简报），所以里面很多设计不是拍脑袋，而是被故障
逼出来的 —— 例如「缺配置」与「真故障」必须分开记账（否则每 5 分钟的轮询会变成告警轰炸）、
「一张卡片实体只能发送一次」（否则翻页会不断堆新消息）、「AI 加工失败绝不能阻塞投递」。

它被刻意设计成**不绑定任何宿主**：可以自己跑（自带调度 + 自带长连接），
也可以寄生在一个已有 IM 机器人背后（宿主转发卡片点击）。两种方式见
[`AGENTS.md`](AGENTS.md)（给 Agent 的使用说明）与
[`docs/host-integration.md`](docs/host-integration.md)（宿主接入协议 + 参考实现）。

---

## 能做什么

| 能力 | 说明 |
|---|---|
| **多信源采集** | 任意 HTTP JSON / RSS / 需要分页与游标的站点。内置 `slot`（定点）与 `poll`（轮询）两种触发，配置里写时刻，进程自己算下一次触发 |
| **四轴正交过滤** | `fetch` 何时拉 / `filter` 留什么（关键词闸、条数上下限、排序）/ `enrich` 加工什么 / `deliver` 怎么发。改配置即改行为，不用重启 |
| **AI 加工** | 中文标题 + 摘要 + 正文翻译；支持「跟随宿主已有模型配置」或「自带 provider」。有**回执缓存**（同一输入不重复付费）与**预算熔断**（日/小时/单次 token） |
| **卡片投递** | 列表页 ↔ 详情页**同一条消息换页**（更新卡片实体，不是发新消息）；元素预算自检；按钮可扩展（第三方 hook） |
| **事件流** | 每次投递 / 点击 / 收藏 / 降级 / 失败都落 `state/events/<date>.jsonl`；消费确认幂等。**hook 实时推送 + 事件流补漏**两条路都有 |
| **可运维** | 心跳（区分「缺配置」与「故障」）、投递去重、失败重试、状态原子写、`doctor` 自检 |
| **给 Agent 的接口** | 所有命令输出**统一结果信封**（`ok`/`changed`/`data`/`error`/`next`）+ 语义化退出码；写操作先校验后落盘，支持 `--dry-run` |

### 不做什么

- **不替人做决定。** 「⭐ 收藏」这类动作只**进队列**，是否真的写进知识库由人确认。
- **不绑宿主。** 不 import 宿主任何模块，也不被宿主 import；两边只通过 CLI 契约、hook 声明、
  事件流说话（防腐层）。
- **不在转发路径上做重活。** 卡片回调有 3 秒预算，转发链路只做「查表 + 转发」。

---

## 技术实现

### 分层

```
配置层 (config.py)      sources.yaml / models.yaml / service.yaml / hooks.yaml —— 唯一人工编辑入口
采集层 (pipeline.py)    按四轴取数、去重、写 state
加工层 (llm.py)         模型调用 + 回执复用 + 预算熔断（解析器可替换：自带配置 / 跟随宿主）
投递层 (render + backends)  纯函数渲染卡片 + 通道实现（直连 OpenAPI / 委托外部 CLI）
交互层 (interaction.py) 卡片点击 → 改状态 + 更新卡片实体（自己不直接发消息）
入站层 (inbound.py)     长连接 ws 或 HTTP，两条入口共用同一个 dispatch()
服务层 (service.py)     一个常驻进程：调度线程 + 入站线程
```

`pipeline.py` 是唯一知道全序的地方，`render.py` 是纯函数 —— 其余模块只依赖 `ports.py`
定义的端口，因此每个可替换点都能独立测试。

### 四条不变量（都是故障换来的）

1. **先定展示集再花钱**：`delivery.plan` 先于 `enrich`，避免为不会被展示的内容付费。
2. **加工失败不阻塞投递**：模型挂了就降级用原文标题，卡片照发（`on_exceeded: degrade`）。
3. **交互层不发消息**：点击只改状态 + 更新卡片实体，避免「点一次多一条消息」。
4. **一张卡 = 一个卡片实体且只能发送一次**：翻页靠更新实体；`sequence` 严格递增。

### 卡片的两条硬约束

- 整卡 **≤200 元素 / ≤30KB**（超了飞书报 11310）。列表页每行按钮会 ×行数，所以
  默认只在详情页挂扩展按钮，`doctor --json` 会报元素预算余量。
- 交互进行中的卡片不可更新（错误码 200810）—— 所以回显走响应里的 `toast`，不走改卡片。

### 两种入站方式（关键设计）

卡片点击必须有人接住，而**飞书长连接是集群模式、不支持广播**：同一个应用起两个 client，
事件只会随机落到其中一个。所以「一个机器人」= 「一条长连接」：

| | 自带长连接 | 宿主转发 |
|---|---|---|
| 谁持连接 | 本项目（`inbound.mode: ws`） | 宿主 Agent（`inbound.mode: http`） |
| 需要公网 | 否（出网即可） | 否（宿主转 `127.0.0.1`） |
| 适用 | 独立部署 | 用户已经有别的机器人 |

两种方式的业务代码**完全相同**，差别只在事件从哪个入口进来；项目启动时会检测
「同一应用 + 双方都要长连接」的冲突并直接报错。协议与参考实现见
[`docs/host-integration.md`](docs/host-integration.md)。

### 状态布局

```
<news_dir>/
  sources.yaml models.yaml service.yaml hooks.yaml   配置（只能经 CLI 改）
  state/batches/<date>/<source>-<slot>.json          每批内容
  state/pushed/<source>.jsonl                        去重（推过就不再推）
  state/cursors/                                     分页游标
  state/status/                                      各源心跳
  state/budget/<date>.json                           模型预算与用量
  state/events/<date>.jsonl + acks.jsonl             事件流与消费确认
  llm/receipts/  llm/usage/<date>.json               回执缓存与账单
```

---

## 快速上手

```bash
python -m venv .venv && .venv/bin/pip install -e '.[feishu,crypto,h2]'

export NEWSPIPE_NEWS_DIR=$HOME/.newspipe/news        # 配置 + 状态放哪（显式给，不靠猜）
mkdir -p "$NEWSPIPE_NEWS_DIR"
cp examples/news/*.yaml "$NEWSPIPE_NEWS_DIR"/       # 示例配置：改 chat / 信源 / provider 即可

newspipe api describe --json                          # 权威契约：命令、参数、退出码
newspipe doctor --json                                # 自检：配置 / 凭据 / 元素预算 / 事件积压
newspipe run --slot am --dry --verbose                # 演练：真采集、真加工、不投递
newspipe run --slot am                                # 真发一张卡
```

> 用 `uv` 的话：`uv venv --seed .venv` 之后再 `pip install -e .`（实测 `uv pip install -e .`
> 不生成 console script；任何情况下 `python -m newspipe.cli` 都等价可用）。

### 跑成常驻服务

```bash
# service.yaml：channel + inbound.mode + 调度时区
newspipe serve --verbose            # 一个进程：调度线程 + 入站线程
newspipe serve --once --dry         # 演练：跑一轮到期任务就退出
```

两种常驻方式，**二选一，不能同时跑**（两个 runner 会各发一遍卡）：

```bash
# ① Docker（推荐；崩溃自拉起，且容器化会暴露打包/环境问题）
docker compose up -d --build
docker compose logs -f newspipe
curl -s http://127.0.0.1:8787/view | head     # 只读视图契约

# ② macOS launchd（备选）
./service/install.sh
./service/uninstall.sh                        # 切回容器前先跑这个
```

**切换时最容易漏的一步**：plist 带 `RunAtLoad` + `KeepAlive`，只要它还躺在
`~/Library/LaunchAgents/`，**下次登录就会自己起来**，于是容器和 launchd 双跑、每张卡发两遍 ——
而故障要等到下次登录才出现，极难归因。所以 `install.sh` 在容器运行时直接拒绝安装
（`NEWSPIPE_ALLOW_DOUBLE_RUNNER=1` 可强制），`uninstall.sh` 负责 bootout + 把 plist 移出加载路径。

容器的三件必办事项在 `compose.yaml` 里都显式给了，原因见下：容器里没有 macOS 钥匙串（凭据
挂宿主 `.env` 走 dotenv）、宿主的回环地址不是 `127.0.0.1`（模型端点与 Clash 都要改写）、
进程必须绑 `0.0.0.0`（否则 Docker 的转发够不着容器自己的回环，宿主 `curl` 直接失败而容器内
healthcheck 却是绿的）。

### 只读视图：宿主要展示，就读这里

```bash
newspipe view --json     # 契约：每源心跳（原始状态+中文标签+语气+超期）、今日批次、去重台账、AI 用量
newspipe view --html     # 同一份数据的服务端渲染页（无前端构建链）
```

`GET /view` 与 `GET /` 是同一个 builder 的 HTTP 出口（需 `service.yaml: view.enabled`），
自带 `contract_version`。**宿主不要解析 `<news_dir>/state/**`**：那是本项目的私有布局，
改一次布局就会让宿主静默读空（页面显示成「从未运行」，看着像没跑，其实是读错了地方）。

### 页面写操作：仪表盘变控制台（`view.actions`）

打开 `service.yaml: view.actions` 后，`GET /` 页面上会出现按钮（跑一轮、启用/停用信源、
⭐ 确认入库、标记事件已消费、现在发顺延队列）。机制：

```
页面表单 → POST /api/actions/<动作> → src/newspipe/actions.py（表单 → argv）→ CLI 的同一份 handler
```

- **不重写写逻辑**：校验、原子写、乐观并发（`--base-hash`）、审计都在 CLI 里，页面只做翻译。
  两份写路径必然漂移。
- **动作白名单在代码里**（`actions.py: ACTIONS`），不是配置 —— 配置能改出来的写权限
  等于一个远程可改的写面。加动作要改代码。
- **三道门禁**：`view.actions` 开关、同源（`Origin` 必须等于请求的 `Host`）、一次性令牌
  （进程启动时生成，嵌进表单）。本地端口对浏览器是可达的 —— 少了令牌，你浏览器里
  **任何网页**都能 POST 过来触发发卡。
- **结果就是 CLI 信封**：成功显示 `changed`/摘要，失败显示 `error.message` + `hint` +
  可直接复现的 CLI 命令。不是「操作成功」四个字。
- 默认是**试运行**（`--dry` 复选框默认勾选）；真发卡要显式取消勾选。
- 只读契约 `GET /view` 不含令牌，形状不变（只多一个 `sources_hash`，供页面做乐观并发）。

### 环境变量

| 变量 | 作用 |
|---|---|
| `NEWSPIPE_NEWS_DIR` | 配置与状态目录（推荐显式给） |
| `NEWSPIPE_HOME` | 数据根目录（未给 `NEWS_DIR` 时用 `<HOME>/news`） |
| `NEWSPIPE_MODEL_BACKEND` | `explicit`（自带配置）/ `host`（跟随宿主） |
| `NEWSPIPE_CHANNEL` | `feishu_direct` / `feishu_lark_cli` |
| `NEWSPIPE_HOST_HOME` | 宿主配置目录（只有用 `host` 后端/宿主凭据时才需要） |
| `NEWSPIPE_FEISHU_*` | 凭据（也可用 dotenv 或系统钥匙串） |
| `NEWSPIPE_SDK_LOG` | `info` / `debug`（调长连接用） |

**部署事实类的覆盖**（默认值对宿主机成立，容器里必须显式给）：

| 变量 | 作用 |
|---|---|
| `NEWSPIPE_PROXY` | 出网代理（默认 `127.0.0.1:7890`） |
| `NEWSPIPE_LOOPBACK_ALIAS` | 把模型端点里的回环主机名换成部署别名（容器里 `host.docker.internal`） |
| `NEWSPIPE_BIND_HOST` | 入站绑定地址（容器里必须 `0.0.0.0`，宿主侧仍只发布到回环） |

**凭据永不写进配置或仓库**：解析顺序是 显式配置 → 进程环境 → dotenv → 系统钥匙串，
所有探测命令只输出「有没有」，不输出值。

---

## 给 Agent 用的 CLI

所有命令支持 `--json`，返回同一个信封：

```json
{"ok": true, "command": "status", "contract_version": 1, "changed": false,
 "data": {…}, "error": null, "warnings": [], "next": ["newspipe run --slot am --json"]}
```

- `ok=true` ⇔ `error` 为空；`ok=false` ⇔ `data` 为空。
- `changed` = 这次调用到底改没改东西；`next` = 建议的下一步命令。
- 退出码：`0` 成功 / `1` 运行时故障 / `2` 用法或配置错 / `3` 网络或投递错 / `4` 校验失败。
- 写配置**只能**走 CLI（`source set|enable|remove`、`hooks add|remove`）：先校验后落盘、
  支持 `--dry-run` 与 `--base-hash`。直接手改 YAML 会被校验拦下。

```bash
newspipe api describe --json                              # 完整命令面（自取，不会过期）
newspipe status --json                                    # 各源心跳、AI 用量、降级原因、事件积压
newspipe source set hn --from-json - --dry-run --json     # 演练
newspipe hooks add --from-json '{"id":"myapp.wiki","label":"⭐ 入库",
    "action":"myapp.wiki","handler":"~/bin/hook.sh"}' --json
newspipe events list --unconsumed --json                  # 未消费事件
newspipe queue list --json                                # 待入库（人确认后才 ack）
```

细节与硬规则见 [`AGENTS.md`](AGENTS.md)。

---

## 测试

```bash
python -m unittest discover -s tests        # 282 个用例，无第三方测试框架依赖
```

覆盖：四轴配置与校验、采集与去重、预算熔断与回执复用、卡片渲染与元素预算、
卡片交互（翻页/收藏/屏蔽）、**两种入站方式的装配**（含「handler 没注册到 SDK 上」这类
静默故障）、CLI 契约与退出码、状态原子写。

---

## 文档

| 文档 | 内容 |
|---|---|
| [`AGENTS.md`](AGENTS.md) | 给 Agent 的使用说明：两种入站方式、CLI 契约、事件流、硬规则、排障 |
| [`docs/host-integration.md`](docs/host-integration.md) | 宿主接入协议 v1 + 两份参考实现（通用宿主 / 插件式宿主） |
| `examples/news/*.yaml` | 配置示例（信源 / 模型 / 服务 / 卡片按钮） |
| `newspipe api describe --json` | 命令面契约（运行时自取） |

---

## 边界与运维责任

独立运行意味着下面四件事从「宿主帮你做」变成「你自己做」：
**凭据存储、进程守护、投递重试、事件订阅**。代码里都有对应实现，但运维责任一并转移 ——
钥匙串/环境变量自己管、launchd/systemd 自己装、投递失败自己看日志、事件订阅自己盯连接。
如果你已经有一个稳定的宿主 Agent，用「宿主转发」那条路会省掉大部分运维。

## 许可

MIT（见 `LICENSE`）。
