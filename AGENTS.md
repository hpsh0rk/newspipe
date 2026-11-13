# AGENTS.md — 给 Agent 的使用说明

本文件是**给 AI Agent 读的**（不是给人类贡献者读的，贡献者请看 `README.md`）。
一个 Agent 拿到这个仓库后，应当只读本文件就能：把服务跑起来、接住卡片点击、消费事件，
并且知道哪些事**绝对不能**替用户做。

> 术语：**宿主 Agent**（host agent）= 持有 IM 长连接、代表用户收发消息的那个 Agent。
> **本项目** = newspipe，负责抓信源、加工、发卡片。

---

## 0. 三条硬规则（违反会造成静默故障）

1. **一个 IM 应用只能有一条长连接。** 长连接是集群模式、**不支持广播**：同一应用起两个
   client，事件只会**随机**落到其中一个。所以要么本项目自己持连接（`inbound.mode: ws`），
   要么由宿主持有、把点击转发过来（`inbound.mode: http`）——**不能两个都开**。
   本项目启动时会检测「同一应用 + 双方都要 ws」并直接报错（`doctor --json` 的
   `inbound_conflict`）。
   冲突是配置错误：`serve` 在打开连接前拒绝启动，`doctor` 的错误详情包含 `inbound_conflict`。
2. **密钥永不进仓库、永不打印。** 凭据只从 显式配置 → 进程环境 → dotenv → 系统钥匙串 读
   （见 `src/newspipe/credentials.py`）。任何探测/自检命令只输出「有没有、够不够」，不输出值。
3. **写知识库的动作只发生在宿主侧，且必须留回执。** `favorite`（⭐ 收藏）只**进台账**
   （`state/events` + `queue`）：本项目自己**永不**写知识库、**永不**调宿主的入库流程。
   入库由宿主侧的消费器执行（自动化或人审由部署自选；本项目作者的默认部署 = Hermes
   定时任务走 wiki-C-compile 自动编译，无需人审）。无论哪种消费器，成功入库后必须
   `queue ack <id> --note <入库路径>` 出队 —— 没有回执的入库等于没入库。

---

## 1. 两种入站方式（二选一）

卡片点击是**平台推给应用**的事件。这个事件必须有人接住，而接住它的人必须持有长连接。

| | ① 自带长连接（`ws`） | ② 宿主转发（`http`） |
|---|---|---|
| 谁持有长连接 | **本项目** | **宿主 Agent** |
| 需要公网地址 | 不需要（出网即可） | 不需要（宿主转发到 `127.0.0.1`） |
| 适用场景 | 独立部署；没有宿主，或宿主不提供转发能力 | 已经有宿主 Agent 在跑（用户已经有一个机器人了） |
| 平台后台要配 | 事件订阅（长连接） | 事件订阅（长连接，配在宿主上）+ 宿主把卡片动作转发给本项目 |
| 代价 | 本项目必须独占这个应用 | 本项目依赖宿主进程活着 |
| 配置 | `service.yaml: inbound.mode: ws` | `service.yaml: inbound.mode: http` + 宿主侧装转发器 |
| 通用性 | 项目自带能力 | **通用能力**：任何 Agent 都能按协议接入，见 §4 |

**怎么选**：用户已经有宿主 Agent 在收发消息 → 选 ②（否则会出现两个长连接抢事件，违反硬规则 1）。
没有宿主、或要让本项目完全独立 → 选 ①。

两种方式的**业务代码完全相同**：事件解析、去重、翻页、hook 调用都走同一个 `dispatch()`。
差别只在「事件从哪个入口进来」。

### 不用宿主能跑吗？（能）

选 ① `ws` 模式本项目就是**完全自足**的：调度、长连接、发卡、页面、队列全部自带，
不需要任何宿主 Agent。唯一的差别是**收藏入库没有内置消费器** —— `favorite` 事件会
留在队列里等人消费，你要么用 `/ops` 页手动回执，要么自己写个轮询脚本（见 §3）。
配套的运维责任（进程守护、凭据、事件盯守）也一并转到你自己头上，见 README「边界与运维责任」。

### ① ws：自带长连接

```yaml
# service.yaml
inbound:
  mode: ws
```

```sh
pip install 'newspipe[feishu]'        # 长连接依赖 lark-oapi
newspipe serve --verbose             # 日志会打「入站 ws：连接 …」
```

平台后台需要：**事件订阅方式 = 长连接**，并订阅「卡片回传交互」（`card.action.trigger`）。
排查入站问题：`NEWSPIPE_SDK_LOG=debug newspipe serve`。

### ② http：宿主转发（已有宿主时推荐）

```yaml
# service.yaml
inbound:
  mode: http
  http:
    host: 127.0.0.1
    port: 8787                       # 0 = 让内核挑一个空闲端口
    path: /feishu/events
```

本项目起一个**只监听本机**的 HTTP 端点。宿主收到卡片动作后，把信封 POST 过来：

```http
POST http://127.0.0.1:8787/feishu/events
Content-Type: application/json

{"domain": "news", "tag": "button", "value": { …卡片按钮的 value… }}
```

响应 `{"code": 0, "accepted": true}` = 点击已持久化接收（后台执行，不代表已完成）；
`toast` 是即时接收提示，后台状态读 `status --json` 的 `callbacks`；
非 0 或超时 = 宿主可以回一句「服务未响应」。**3 秒预算**：不要在转发路径上跑 LLM 或长任务。

完整协议、注册表格式与两份参考实现（通用宿主 / 插件式宿主）见
[`docs/host-integration.md`](docs/host-integration.md)。

### 两种方式都要能自证

```sh
newspipe doctor --json                             # 当前模式 + 冲突检测
newspipe probe-channel --json                      # 真发一张卡（输出不含任何密钥）
```

### ③ 常驻方式：容器或 launchd（**二选一，不能同时跑**）

两个 runner 同时开着会各发一遍卡（重复投递）。选容器通常是因为 launchd job 会**静默消失**
（plist 还在 `~/Library/LaunchAgents/`、`launchctl list` 里没有、端口空着，管线就这么停着不报警）。

```sh
docker compose up -d --build        # ① 容器（restart: unless-stopped 兜崩溃）
./service/install.sh                # ② macOS launchd
./service/uninstall.sh              # 切回容器前先跑：bootout + 把 plist 移出加载路径
```

**切换时最容易漏的一步**：plist 带 `RunAtLoad` + `KeepAlive`，只要它还躺在
`~/Library/LaunchAgents/`，**下次登录就会自己起来**，于是容器和 launchd 双跑、每张卡发两遍
——而且当天看不出问题，故障要等到下次登录才出现。所以 `install.sh` 在容器运行时直接拒绝安装
（`NEWSPIPE_ALLOW_DOUBLE_RUNNER=1` 可强制），`uninstall.sh` 负责反向清理。

容器里必须显式给三个「部署事实」（`compose.yaml` 已给，原因见 §5 排障表）：绑定地址、回环别名、出网代理。
凭据走挂载的 dotenv（容器里没有钥匙串），**密钥不进镜像、不进 Git**。

判断「活着」永远看**端口 + 心跳时间戳**（`doctor --json` / `view --json`），不看 plist 是否存在。

---

## 2. CLI 契约（Agent 靠这个做判断）

所有命令都支持 `--json`，返回**同一个信封**：

```json
{"ok": true, "command": "status", "contract_version": 1, "changed": false,
 "data": {…}, "error": null, "warnings": [], "next": ["newspipe run --slot am --json"]}
```

- `ok=true` ⇔ `error` 为空；`ok=false` ⇔ `data` 为空。**先看 `ok`，再看 `error.code`。**
- `changed` 告诉你这次调用**到底改没改东西**（只读命令永远 `false`）。
- `next` 是建议的下一步命令 —— Agent 可以直接照着走。
- `error.hint` 是可执行的修法，不要只把 `message` 转述给用户。

退出码：`0` 成功 / `1` 运行时故障 / `2` 用法或配置错 / `3` 网络或投递错 / `4` 校验失败。

子命令面（完整清单用 `newspipe api describe --json` 自取，不要背）：

| 类别 | 命令 |
|---|---|
| 自检 | `doctor` / `status` / `probe-model` / `probe-channel` / `rsshub show\|probe` |
| 通知群 | `chats list --json`（当前发送机器人已加入的群；显式联网、分页、不保存不发送） |
| RSSHub | `rsshub set --base-url <地址>`（显式空字符串恢复继承；支持 `--dry-run` / `--base-hash`） |
| 信源 | `source list\|show\|set\|patch\|copy\|enable\|disable\|remove`（写操作支持 `--dry-run` / `--base-hash`；新建可用 `--create-only`） |
| 通知 | `policy list\|show\|set\|remove` / `global set` / `notification preview` / `notification retry <源> --operation-id <ID>` |
| 分阶段运行 | `collect <源>`（只采集）/ `notify <源>`（只通知待处理；`--bypass-clock` 只越过时钟，不越过限制） |
| 卡片按钮 | `hooks list\|add\|remove` |
| 事件 | `events list --unconsumed` / `events ack <id> --by <who>` / `events prune` |
| 队列 | `queue list` / `queue ack <id>` |
| 跑一轮 | `run --slot am` / `run --poll` / `run --source <id>` / `run --poll --dry` |
| 服务 | `serve`（常驻：自带调度 + 入站） |
| 配置 | `config describe`（字段/类型/枚举/默认值的**唯一事实来源**；页面表单由它生成） |
| 展示 | `view [--html]`（只读视图契约 / 服务端渲染页） |

**约定**：`set/remove/add/ack` 这类写操作都「先校验再落盘」，被拒绝时 `ok=false` 且退出码 `4`，
同时给出 `hint`。Agent 不要绕过 CLI 直接改 YAML —— 校验与原子写都在 CLI 里。

### 2.1 只读视图契约：宿主要展示，就读这里

`newspipe view --json`、`GET /view`（需 `service.yaml: view.enabled`）、`newspipe view --html`
三条出口用的是**同一份** builder，自带 `contract_version`。内容：每个源的心跳（原始状态 +
中文标签 + 语气 + 是否超期）、今日批次、去重台账、AI 调用与降级计数、通道与入站方式；
`notification` 提供策略有效值/来源、待通知原因、全局/每源预算和未完成操作身份。配置值与哈希来自同一字节快照。

**不要**去读 `<news_dir>/state/**` —— 那是本项目的私有布局。宿主一旦解析它，改一次布局就会让
宿主**静默读空**：页面显示成「从未运行」，看着像没跑，其实是读错了地方，全程不报错。要展示就读契约。

`GET /view` 是只读 JSON 契约；**页面**按职责分五个 Tab（同一个 builder、服务端路由、无前端构建、
可深链）：

| 路由 | Tab | 内容 | 写操作 |
|---|---|---|---|
| `GET /` | 驾驶舱 | KPI + 信源心跳 + 今日批次 | 无（只读） |
| `GET /config` | 消息源 | 搜索筛选/增删改/复制 + 采集时间管理 + 策略绑定与参数只读展示 + RSS 目录/订阅 | 有 |
| `GET /notifications` | 通知 | 机器人群选择/全局硬限制 + 策略独立发送时间与前后预览 | 有 |
| `GET /items` | 条目 | 条目/批次浏览、关联预览与原始版本详情 | 仅关联预览设置 |
| `GET /ops` | 运维 | 分开采集/通知/安全重试原操作 + 旧式补跑/启停/⭐入库回执 | 有 |

宿主读不到时应当显示「服务不可达 + 地址」，不要退化成空看板（空看板与「真的没数据」无法区分）。

### 2.2 页面写操作：三条铁律

打开 `service.yaml: view.actions` 后，`/ops`、`/config` 与 `/notifications` 上出现按钮与表单。

**铁律一：写路径只有一条。** 页面表单 → `POST /api/actions/<动作>` → `src/newspipe/actions.py`
（表单翻成 argv）→ CLI 的同一份 handler。校验、原子写、乐观并发、审计都在 CLI 里，页面只做翻译。
**不要**在页面/宿主侧另写一份写逻辑 —— 两份写路径必然漂移。

**铁律二：动作白名单在代码里**（`actions.py: ACTIONS`），不是配置。配置能改出来的写权限
等于一个远程可改的写面。加动作必须改代码。

**铁律三：三道门禁一个都不能少。** `view.actions` 开关（没开就**不渲染按钮**，也拒绝 POST）、
同源（`Origin` 的 netloc 必须等于请求的 `Host`）、一次性令牌（进程启动生成、嵌进表单）。
本地端口对浏览器是可达的：少了令牌，用户浏览器里**任何网页**都能 POST 过来触发发卡。

配置页的字段**不是第二份 schema**：全部来自 `newspipe config describe --json`
（`config.py: describe_schema()`），编辑表单的当前值来自 `config.source_values()`。
加配置字段只改 `config.py` 一处。漏一个字段的后果不是报错，而是**编辑表单把那个字段按默认值
静默写回去** —— 有测试钉住「schema 的每个 path 都能取到当前值」。

编辑 = 用 `source patch` 合并补丁，未提交字段不动，`null` 删除字段；扩展/高级字段和显式继承保留。
CLI `source set` 仍是整体替换，新建/复制用 `--create-only` 拒绝 ID 碰撞，新源默认停用。
修改块内注释可能丢失；CLI warning 显示在横幅上。唯一同源脚本 `/assets/workbench.js` 是渐进增强，
无 JS 仍能提交/预览：失败草稿只存进程内存（32 份/10 分钟/一次性），不进 URL/磁盘/浏览器存储。
表单默认 `--dry-run`，取消勾选才真写。RSS 目录是历史验证记录，**不是当前实例可用性的证明**
（当时本地无 `/routes`，实测 404）—— 不做「全网 RSS 搜索」这种吹牛功能。

**RSSHub 不是强依赖。** `rsshub` 适配器兼容完整 RSS 地址与相对路由；仅相对路由依赖 RSSHub。
基础地址按 `sources.yaml: rsshub.base_url` → `RSSHub_BASE` → `http://127.0.0.1:1200` 解析，
支持本地 / 远端 HTTP(S) 和反向代理前缀。容器中的 localhost 是容器自身；宿主地址显式使用
`http://host.docker.internal:1200`，不会跟随模型回环别名自动改写，也不自动发现 / 部署 RSSHub。
`doctor` / `view` / `rsshub show` 只描述地址、来源与启用的依赖信源，**不联网检测**。
需要测试时显式执行 `rsshub probe [--base-url <候选>] [--feed <完整地址或相对路由>] --json`，
或用 `/config` 的“测试连接 / 测试订阅”：同样走动作三道门禁，**只检测、不保存、不采集、不发卡**。
检测结果不确认草稿已保存，也不推进表单哈希。端点可达不等于路由可用；订阅暂只支持 RSS 2.0，
Atom / RDF 明确报错。直接访问端点、不走出网代理，单次请求无重试、套接字超时 5 秒、响应上限
1 MiB，DNS / TLS / 跳转可能增加总耗时。诊断不得暴露上游正文、原始异常或 URL 查询值。

### 2.3 策略与安全重试

单用户/单目标，不加账户或任意规则引擎。`sources.yaml` 的 `policies` 保存策略，信源用
`notify.policy` 绑定，参数在源弹窗只读展示。不同参数应复制策略后绑定，不再创建源级覆盖；
旧 `notify.overrides` 仍兼容读取，需显式迁移为独立策略，不能静默丢弃。
`immediate` 有待处理即评估、`digest` 使用策略独立 `times` 汇总、`collect` 消费为本地批次不发送。
模板预算按每源独立计数，顶层 `notifications` 是包含旧式源在内的全局硬门禁；更新卡片也计一次。
0 上限是不发送。源级例外不能越过全局、时钟、停用或卡片容量。

旧式源不会自动迁移。绑定/解绑需先预览并确认预算范围变化，不清零计数；旧字段保留用于回退。
切到 collect 后本地已消费内容不会在切回通知时重发。暂停通知不暂停采集；停用源保留队列。
策略删除必须先解除全部引用（含停用源），采集时间修改必须通过全部采集/旧通知引用校验。
采集时间在 `/config?acquisition=1` 管理，表单隐藏但保留稳定 `slots` 键，改时不改身份。
只提交采集时间，不修改通知目标或限制；通知策略的独立 `times` 不受影响，旧引用明确警告。
添加/移除仅改进程内草稿，Enter 只校验；无 JS 时通过三道门禁后的单次 PRG 保留完整时间行。
群选择在 `/notifications?global=1` 显式调用 `chat-list`，来自发送机器人而非个人身份。
加载/刷新/分页均为只读，不推进配置哈希或清除未保存编辑；查找只覆盖已加载的群。
备用手动输入与列表选择只有一个生效的 `chat` 字段；达到 1000 个群的本地上限时明确提示。

未完成远端操作固定身份/目标/载荷。安全重试只重置原操作的三次尝试计数，不立即发送、不换身份。
解绑也不解除原操作的保护。确认时间独立于尝试时间：按确认日计数，新全局/每源间隔跨日连续；
已确认但未完成本地回执的操作仍占用预算。所有重试复用持久化的完整卡片 JSON，不随新内容/hooks 漂移。
未知结果超过幂等窗口保持阻塞；用 `notification preview` 或 `/ops` 的目标、卡片、操作 ID 与时间
核对平台回执。本版没有远端回执对账写入器：禁止强制重发、伪造回执或改私有状态绕过保护。

---

### 2.4 原始版本与关联预览

`stories settings/set`、`observations list/show`、`stories preview` 使用统一 CLI 信封。
字段与范围来自 `config describe` 的 `story_fields`，设置只能通过 CLI 保存。
页面 `/items?story_settings=1` 使用相同的 `story-settings` 白名单动作，要求 actions、同源、令牌、配置哈希。
保存不采集、不激活历史；初始默认 `off`，仅支持 `off/observe`，`apply` 明确拒绝。
真实采集激活新修订及独立前沿；慢任务遇到设置/历史改变只保留原文，不恢复旧前沿或生成新提案。
关闭后不删历史。读操作、dry-run、首次轮询基线、通知与纯恢复路径均不启用观察。

`observations list --after N --limit N` 的游标单位是不可变版本序号；
`stories preview --after N --limit N` 的游标单位是采集回执序号，两者不可混用。
读契约从未初始化时明确返回 `not_initialized`，锁定/损坏是错误，不能静默显示为空。
`stories settings` 和 `/view` 在旧历史不可用时仍返回已保存设置与 `availability: unavailable`，
未知计数/激活状态是 null，不猜测为空；用户仍可关闭功能。
宿主只读 Store/CLI 契约，不解析私有 SQLite，也不访问或导出原始 JSON。

候选回看默认 14 天、每条 5 个候选、扫描上限 500；仅影响召回，不自动删除原始版本。
确定副本要求明确文章类型、完整原文声明与足够正文；链接相同/词面相似不等于同一事件。
同原生 ID 的材料变化只叫未分类变化，截断快照只能证明材料变了，不能声称具体正文/事实差异。
旧材料不可用时建立不确定基线，不生成“重复/进展”结论。明确截断不能与“完整正文”声明共同构成确定副本。
完整原始 JSON 私有保留，写路径限定目录 0700/数据库 0600，读取不改权限，需备份与容量监控。
公开事实递归脱敏，不能安全展示的过大/深层 JSON 明确省略，不回显未脱敏的 JSON 前缀。
观察失败先保留成功待处理内容、暂缓新完成/游标；已冻结旧投递独立恢复。
关闭功能后旧历史故障只警告，不阻断正常采集；没有自动重建或删库命令，禁止 Agent 绕过契约修复私有状态。
本阶段不合并、不抑制通知、不迁移队列、不重发历史、不动收藏，也不调比较模型。
后续信源升级、语义差异分类、显式 apply/undo 与进展/观点追加属于后续独立阶段。

---

## 3. 事件流与队列（Agent 怎么消费）

```
<news_dir>/state/events/<YYYY-MM-DD>.jsonl    一行一个事件
<news_dir>/state/events/acks.jsonl            消费确认
```

事件类型：`delivered`（发卡成功）/ `clicked`（点了）/ `favorite`（⭐ 收藏，入库意图的台账）/
`muted`（🔕）/ `degraded`（AI 加工降级）/ `failed`（投递失败）。

```sh
newspipe events list --unconsumed --json       # 还没被消费的
newspipe events ack ev_xxx --by myagent --json # 消费掉（幂等：区分 acked/already/unknown）
newspipe queue list --json                     # 只看「待入库」的收藏（= 未 ack 的 favorite）
newspipe queue ack ev_xxx --note <入库路径> --by myagent --json  # 真的入库之后才调（回执）
```

**收藏的入库（硬规则 3）**：⭐ 只把意图记进台账，执行在宿主侧。两种消费器任选：

- **自动化（本项目默认部署）**：Hermes 定时任务 `sch_news-favorite-ingest` 轮询
  `queue list --json`，对每条收藏走 Knowledge vault 的 wiki-C-compile 流程
  （propose → 自审 → approve → promote，自动晋升），成功后
  `queue ack <id> --note <wiki 页面路径> --by hermes-ingest`。人审不是必须环节，
  只有提案可能失实时才留给人工。
- **人工兜底**：`/ops` 页的「标记已入库」按钮（同一个 `queue ack`），用于自动化失败的补录。

队列是自愈的：消费器挂了条目就留在队列里，`status --json` 会一直报
「待入库队列有 N 条」，修好后重跑即可 —— 所以**先 ack 再入库是绝对禁止**的。

待入库收藏没有天数上限（`queue list --days` 仅兼容旧调用）。`queue ack` 必须带非空
`--note`，只能确认收藏；通用 `events ack` 禁止确认收藏。批量确认有无效 ID 时整批不写。
`events prune` 只清理全部已消费的过期日文件，不删除尚未消费的收藏或其他事件。

消费没有推送通道，靠**轮询**：宿主按需轮询 `events list --unconsumed` 与 `queue list`，
轮询间隔自定（收藏入库的实时性要求不高，分钟级足够）。卡片上的第三方 hook 按钮
（`hooks.yaml`，见 §2）是点击瞬间跑第三方脚本的路径，但它不是事件推送 —— 事件流的
真相永远以文件为准，轮询永远能补上错过的。

---

## 4. 宿主接入：第二种方式怎么落地（通用能力）

这一节描述的是**协议**，不是某个产品的实现 —— 任何持有 IM 长连接的 Agent 都能接。
参考实现见 [`docs/host-integration.md`](docs/host-integration.md)。

### 4.1 宿主需要做四件事

1. **约定一个卡片域**：用 `value.domain` 标识「这张卡片归哪个项目」（本项目 = `news`）。
2. **拿到卡片动作事件**：宿主自己的长连接会收到 `card.action.trigger`。
3. **转发信封**：把 `{domain, tag, value}` POST 到本项目的 `inbound.http` 端点。
4. **取回显**：把响应里的 `toast` 回给用户；失败则回一句「服务未响应」（不要静默）。

### 4.2 通用实现（伪代码，任何宿主都适用）

```python
ROUTES = load_yaml("~/.myagent/card_routes.yaml")   # {"news": "http://127.0.0.1:8787/feishu/events"}

def on_card_action(event):
    value = event.action.value                       # 按钮 value（含 domain 字段）
    domain = value.get("domain")
    target = ROUTES.get(domain)
    if not target:
        return ack()                                 # 不是我们认识的域：静默（别人的卡片）
    try:
        r = httpx.post(target, json={"domain": domain, "tag": event.action.tag, "value": value,
                                    "event_id": event.event_id},
                       timeout=2.5)                  # 3 秒预算：超时要留余量
        r.raise_for_status()
        return ack(toast=r.json().get("toast"))
    except Exception:
        return ack(toast={"type": "error", "content": f"{domain} 服务未响应"})
```

### 4.3 宿主侧要点（都是踩过坑换来的）

- **注册表热加载**（按 mtime）：加一个域不该需要重启宿主 —— 重启会掐断长连接。
- **注册表坏了必须出声**（回 `⚠️ 卡片域注册表不可用`），只有「域不在表里」才静默 ——
  否则故障会伪装成「点了没反应」，排查从几分钟变成几小时。
- **转发件跑在宿主的进程里**：别假设宿主环境里有你喜欢的第三方库（YAML 解析要能回退）。
- **零业务逻辑**：转发件只做「拆信封 → 查表 → 转发 → 取回显」，业务属于各个项目。

### 4.4 加一个新域要做什么（5 步）

1. 定域：确认项目的域标识（本项目 = `news`）。
2. 本项目起端点：`inbound.mode: http` + 端口。
3. 宿主注册表加一段：`news: http://127.0.0.1:8787/feishu/events`。
4. 自检：`newspipe doctor --json`，再用宿主侧的路由自检打一发。
5. 真发一张卡点一下：`newspipe probe-channel --json`。

### 4.5 用 Hermes 做宿主：接入清单（本项目作者的默认部署）

Hermes 是「宿主协议」的一个具体实现，上面 §4.1–4.4 的步骤对它原样成立。落地时的
操作清单（照做即可）：

1. **长连接归 Hermes**：平台后台的事件订阅（长连接）+「卡片回传交互」都配在 Hermes
   的应用上；newspipe 侧 `service.yaml: inbound.mode: http`（别开 ws，硬规则 1）。
2. **注册卡片域**：Hermes 的转发器注册表加
   `news: http://127.0.0.1:8787/feishu/events`（参考 `docs/host-integration.md`
   的两份实现；注册表热加载，不用重启 Hermes）。
3. **凭据复用宿主**：容器部署时挂载 Hermes 的 `.env` + `config.yaml`
   （`NEWSPIPE_HOST_HOME=/hermes`），模型跟随宿主配置（`NEWSPIPE_MODEL_BACKEND=host`）；
   三个部署事实显式给：`NEWSPIPE_BIND_HOST=0.0.0.0`、`NEWSPIPE_LOOPBACK_ALIAS=
   host.docker.internal`、`NEWSPIPE_PROXY`（见 §5 排障表与 compose.yaml）。
4. **收藏入库消费器**：Hermes 定时任务轮询 `queue list --json` → 逐条走你自己的
   入库流程 → `queue ack --note <入库路径>`（本项目作者用 Knowledge vault 的
   wiki-C-compile，条目定义在 vault 的 `_meta/automation/schedules.md`）。
   没有这一步，收藏只会在队列里攒着 —— `status --json` 会一直 warning。
5. **（可选）面板入 portal**：把 `GET /view` 与页面地址注册进宿主的服务面板，
   读契约不读 `state/**`（见 §2.1）。
6. **自检三连**：`newspipe doctor --json` → `newspipe probe-model --json`（看
   `base_url` 是否按容器事实改写）→ `newspipe probe-channel --json`（真发一张卡，
   点一下，确认转发链路与 toast 都通）。

---

## 5. 排障表

| 现象 | 先查什么 |
|---|---|
| 点卡片没反应 | `doctor --json` 的 `inbound`；http 模式查端点是否在监听（`lsof -nP -iTCP:8787 -sTCP:LISTEN`）；ws 模式查日志有没有「入站 ws：连接」 |
| 卡片发出去了但点不动 | 卡片实体是否只发过一次；`seq` 是否严格递增；交互进行中不可更新（错误码 200810） |
| 报 11310 / 卡片发不出 | 元素超限：`doctor --json` 看 `card_budget`（≤200 元素 / ≤30KB）；减少每行按钮或 `CARD_MAX_ITEMS` |
| 报 230002 out of the chat | 应用身份不对（宿主环境变量丢了 → 外部 CLI 回落到另一个应用） |
| 机器人不回话 | **两个长连接在抢事件**（硬规则 1）。检查是否 ws 与宿主同时开着 |
| AI 加工全降级成原文标题 | `status --json` 看降级原因；常见是 completion 预算被 reasoning 吃光（调 `max_tokens`） |
| `config_missing` 心跳 | 配置缺失（不是故障）：按 `error.hint` 补配置，重试无意义 |
| 宿主 `curl 127.0.0.1:8787` 失败，但容器 healthy | 进程绑了**容器自己的回环** ⇒ Docker 转发够不着：`NEWSPIPE_BIND_HOST=0.0.0.0`（宿主侧仍只发布到 `127.0.0.1`） |
| 容器里 AI 加工全降级 | 宿主回环在容器里不是 `127.0.0.1`：`NEWSPIPE_LOOPBACK_ALIAS=host.docker.internal`；`probe-model --json` 看 `base_url` 是否已改写 |
| 容器里抓不到需要代理的源 | `NEWSPIPE_PROXY=http://host.docker.internal:7890` + `extra_hosts: host.docker.internal:host-gateway` |
| 容器里凭据解析不到 | 挂宿主 `.env` + `config.yaml` 到 `/hermes`（`NEWSPIPE_HOST_HOME=/hermes`）；`doctor --json` 的 `creds.source` 会写成 `dotenv:/hermes/.env` |
| 重启后管线不再跑 | 先确认哪个 runner 活着（容器 `docker compose ps` / launchd `launchctl list`）；launchd job 会静默消失，**别用 plist 是否存在当证据** |

---

## 6. 改本项目时的约定

- **不 import 宿主，也不被宿主 import**：两边只通过 CLI 契约、hooks 声明、事件流说话。
  这是防腐层，不是风格偏好。
- **默认值要能独立跑**：任何「需要宿主」的能力都必须显式配置才启用（例：`NEWSPIPE_HOST_HOME`），
  且没配时报可执行的错 —— 猜一个路径会导致静默错配。
- **失败要出声**：入站、投递、hook 的失败都必须留痕（事件流 / 日志），
  「静默 + 重试」是最贵的故障形态。
- **发布前跑一次**：`python3 scripts/scan_secrets.py`（0 = 干净）与
  `NEWSPIPE_REQUIRE_JS_TESTS=1 python -m unittest discover -s tests`（Node 24；缺 Node 必须失败）。
  CI 对全部分支 push / PR 验证 Python 3.11/3.12/3.13。
- **前端改动还要真浏览器验收**：安装 `python -m pip install -e '.[browser-test]'`，
  `python -m playwright install chromium webkit`（Linux 加 `--with-deps`），再运行
  `NEWSPIPE_BROWSER_TESTS=1 python -m unittest discover -s tests -p test_browser_e2e.py`。
  默认单元测试跳过浏览器用例；显式启用时缺 Playwright/任一引擎必须失败。
  临时离线夹具不使用生产凭据、调度或消息，失败证据在被忽略的 `test-results/browser/`。
- **离线通过不等于飞书实机通过**：按 `docs/live-feishu-acceptance.md` 先确认专用应用/群
  或独占窗口、唯一 WS 所有者和 runner，再由用户明确批准有外部副作用的步骤。
  Agent 不得擅自发探针卡、新增 WS、消费真实收藏、伪造回执或部署。
- **发卡相关改动**：跑 `newspipe doctor --json` 确认元素预算没被顶破。
