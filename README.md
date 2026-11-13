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
| **事件流** | 每次投递 / 点击 / 收藏 / 降级 / 失败都落 `state/events/<date>.jsonl`；消费确认幂等。**没有推送通道** —— 宿主/脚本靠轮询契约拿数据，点击回显走卡片 toast |
| **可运维** | 心跳（区分「缺配置」与「故障」）、投递去重、失败重试、状态原子写、`doctor` 自检 |
| **给 Agent 的接口** | 所有命令输出**统一结果信封**（`ok`/`changed`/`data`/`error`/`next`）+ 语义化退出码；写操作先校验后落盘，支持 `--dry-run` |

配置生效边界：信源、通知策略发送时间、采集计划、hooks 与调度间隔可以热加载；
`service.inbound`、`service.view`、`service.feishu`、`service.log` 需要重启服务。
服务不会为了尝试新端口或凭据先拆掉工作的连接，更不会同时开两条 WS。
`status --json` 与 `/view` 的 `service.runtime` 给出 `effective`（当前资源）、
`desired`（已保存配置）、`restart_required`（待重启字段）和最后观测时间；凭据不输出。
这是最后一次运行观测，不是存活证明：判断健康仍需检查端口与时间戳。
槽位采集失败在原有 15 分钟窗口内最多尝试三次，只重试未完成信源；缺配置等修改后再尝试。
有入站连接时，直连通道与入站共享启动时解析的凭据快照；dotenv/环境中的凭据变化也会提示需重启，
不会让后台更新卡片的应用身份先于入站改变。兼容的 `feishu_lark_cli` 后端由外部 CLI 管理应用身份，
不在本项目凭据快照的控制范围内；需要统一身份与生命周期时使用默认的 `feishu_direct`。
WS 退出会取消未启动的连接并等待 SDK 关闭；若 SDK 未退出，状态明确报 `ws_shutdown_incomplete`，
此时必须结束旧进程后再启动替代连接，不能按“已调用 stop”判断已关闭。

### 消息源采集契约

原有 `fetch(config, since)` 返回条目列表或生成器的自定义适配器继续可用，原生 `ext_id`、
未知条目字段和未知发布时间原样保留，不迁移旧队列或已投递记录。
新增适配器可返回 `FetchResult(items, reports)`，每个 `SubscriptionReport` 标识订阅的
成功、无内容或失败；错误只报告预定义分类，不回显提供方响应、订阅凭据或异常原文。
`config describe --json` 增加代码声明的 `adapter_descriptors`，能力声明不代表在线可用性；
消息源弹窗根据选中的适配器显示专用参数，页面与 CLI 表单翻译共用 schema。
切换适配器不会提交其他适配器的默认参数；无 JavaScript 时点击“更新订阅参数”只调整草稿。
凭据字段只接受环境变量名，误填密钥会拒绝并清除该字段的服务端草稿，不回显到动作命令。

部分失败会保留成功条目，但不推进全局内容游标，失败订阅下轮可以重试。
完整结果使用安全水位：各订阅显式水位取最小值；没有完整水位时使用已知发布时间，
无时间的内容不能凭采集时间获得虚构的发布时间或完成水位。相同时间的条目仍按原生 ID 去重。
旧列表适配器暂时保留旧水位规则；内置 RSS/Atom、HN、Reddit、GitHub、Google News 已使用订阅结果契约。
真实采集尝试独立计时，失败也遵守 `interval_min`；首次轮询基线不算采集尝试，
手动强制采集只跳过采集节流，正常 dry-run 不写状态、不调用模型、不发卡。
最近采集诊断通过 `view --json` 和驾驶舱消息源详情展示，不会被后续节流或通知心跳覆盖；
最多展开 100 个订阅诊断，多余数量明确列出。

消息源基础边界与本地原始版本观察已实现；尚未新增 Horizon 全部信源，也未启用跨源通知去重、
事件进展追加或 AI 差异比较。不依赖 Horizon，不接入 MCP / Skill。

### 现有信源的原始证据

RSS 2.0 / Atom 保留订阅正文与原始标记，不再只留下短摘要；HN 保留原始讨论材料，
原帖链接与引用文章分别记录；Reddit 保留帖子材料，并报告各社区的访问失败。
发布时间未知时保持未知，Atom 更新时间只用于增量选择，不冒充发布时间。
正文最多 20,000 字符、原始标记最多 40,000 字符，截断明确标注。
订阅字段包含正文不代表完整文章：通用 RSS 类型仍未知，HN/Reddit 是帖子，均不声明全文。

RSS 长 GUID 不再截断；旧 120 字符 ID 仅用于识别已有完成记录/队列，不改写历史或重发。
若历史只有截断 ID，新原文建立不确定基线，不能声称已确认副本或进展。
新长 GUID 即使共享前缀也保持独立；新完成记录携带版本标记，不另行登记截断别名。
新采集的恰好 120 字符 GUID 不会被误认为旧截断记录。
升级后不能直接回退到旧的截断 RSS 适配器，否则旧代码可能错误重发或合并新长 ID。

采集限制为每源最多 100 个订阅、每订阅最多 1000 条、响应最多 2 MiB、每出口最多 5 次跳转；
直连/代理最多两次，不再叠加适配器级即时重试。超限与限流均留下分类诊断。
跳转响应直接关闭，不读取其正文；gzip/deflate 解压输出也受限，无法安全解析的编码明确失败。
部分失败仍整体保留旧游标：成功订阅可能重抓，但队列幂等；不保证找回上游已移出窗口的内容。
暂未加入条件请求、HN 更多榜单、Reddit 用户/排序/评论；GitHub 的独立能力见下一节。
详见 [信源证据验收](docs/source-evidence-quality-acceptance.md)。

正式采集入口在短生命周期子进程执行适配器，`fetch.timeout_seconds` 默认 90，范围 1..600 秒。
同一消息源的全部订阅共用预算，涵盖启动、导入、抓取、解析与 worker 校验；终止清理另限 2 秒。
这不是整轮多信源任务、AI 加工或发送的总时限，也不是实时操作系统的硬调度保证。
RSS/Reddit 按订阅保留已完成结果；超时、崩溃或协议超限明确失败，不推进全局游标，重试队列幂等。
采集时限在消息源弹窗的高级设置中配置；不改变通知策略，编辑与复制保留当前值。
进程输入/结果分别限 1/16 MiB，最多 100 个完成分区；超限保留此前完整分区，不接收截断结果。
worker 不获得 Store、模型、通道或入站配置；适配器打印/异常文本不进入主日志或诊断。
输入用受限的安全 YAML 保留原配置的日期、非字符串键等类型，输出仍是校验后的 JSON 帧；不使用任意对象反序列化。

### GitHub 公开动态与发布

添加消息源时选择 **GitHub**，填写“公开用户动态”的用户名或“仓库发布”的 `owner/repo`，可同时订阅。
不依赖 RSSHub、GitHub SDK 或 Horizon。需要的只是公开 API 的网络可达性。
每个事件/发布按 GitHub 原生 ID 保持身份；共享仓库或 tag URL 不会把不同记录合并。
保留 Release 原始 Markdown、发布时间/更新时间，以及动态的相关事实和可用原始链接。
没有独立永久链接的动态只提供仓库入口，明确标注，不能声称它是事件 permalink。

Token 可选，默认匿名；`settings.token_env` 只填写进程环境变量名，例如 `GITHUB_TOKEN`。
显式指定却缺失时会报凭据错误，不匿名回退或切换账号；不会用它采集私有事件或草稿发布。
使用 Token 读取仓库发布前，会额外检查仓库明确为公开；私有或可见性未知时拒绝读取发布正文。
API 请求每页限 2 MiB、20 秒套接字超时，禁止跳转；每个订阅最多 1..3 页、每页最多 100 条。
共享消息源采集时限，部分失败保留已完成订阅。满页达到上限时不推进游标，并显示历史窗口受限。
最近采集详情/JSON 契约展示匿名或 Token 模式、提供方返回的额度及限流分类，不主动探测额度。
公开 Events API 具有有限历史与提供方延迟；不是实时 webhook，也不保证长时间停机后补齐。
普通 RSS 已能满足的发布订阅可继续使用 RSS，不会自动改为 API 或重写已有身份。
详见 [GitHub 验收与边界](docs/github-source-acceptance.md)。
自定义适配器需是可导入模块，原有 `fetch` 列表/生成器仍支持；进程不是权限沙箱，适配器代码仍必须可信且只采集。
直接调用适配器 `fetch` 的库使用者须自行管理执行边界；RSSHub 的只读连接探测不使用此采集预算。
旧式未绑定通知策略的手动源现在也显式执行采集；停用源仍不采集，dry-run 不写状态。
详见 [采集进程验收](docs/source-worker-deadlines-acceptance.md)。

### Google News 新闻搜索

添加消息源时选择 **Google News**，在“新闻查询词”中每行填写一条查询。
逗号、中文顿号不会拆分查询，例如 `"ACME, Inc." OR model` 是一条，下一行可填 `人工智能、技术`。
不需要 RSSHub、SDK 或凭据；需要能访问 `https://news.google.com/rss/search`。
默认界面语言 `zh-CN`、新闻地区 `CN`，可改为 `en-US` / `US` 等组合；它们影响新闻版面，
不是严格的文章语言过滤。可选版面代码与回看天数、每条查询保留数在高级参数中配置。

每次重新搜索最近 7 天（可选 1..90 天），每条查询默认保留 50 条（可选 1..100 条）。
最多 25 条查询，每条最多 1024 字符；时间由回看天数统一管理，不要再写 `when:/after:/before:`。
双引号内的时间条件作为字面文本保留；单引号不是短语边界。
搜索窗口有限，即使未达到条数上限也不推进完整历史游标，不保证停机后补齐所有文章。
部分查询失败保留已完成查询，下一轮通过原生 ID 保持重试幂等，不消耗额外 AI token 做采集去重。

保存 Google News 聚合文章链接及有限摘要，出版方名称/主页是独立来源标注，不能当作文章链接。
不解析聚合跳转、不抓出版方网页或全文，摘要不能被判为完整文章副本。
普通校验、预览、切换适配器都不采集；创建后默认停用，通知策略仍在策略管理弹窗配置。
不同语言/地区的真实可用性需单独在线验收；不会把已有 RSS 源自动迁移为 Google News。
详见 [Google News 验收与边界](docs/google-news-source-acceptance.md)。

### 原始版本与关联预览

“条目”页的“关联预览”展示已保存的采集回执和原始版本；“预览设置”使用弹窗，
只支持关闭（默认）和记录原始版本与候选。保存后等待下一次真实采集激活，
不会迁移已有队列、回看后自动重发、改收藏或合并事件。读取与 dry-run 不初始化历史。

```sh
newspipe stories settings --json
newspipe stories set --from-json '{"mode":"observe"}' --dry-run --json
newspipe observations list --source research --limit 20 --json
newspipe observations show ob_ID --json
newspipe stories preview --limit 5 --json
```

同一原生 ID 的正文或事实改变会保留新版本，互动计数或时间戳变化不单独创建版本。
采集在过滤和 AI 加工之前记录原始材料，不把生成的摘要当证据；同 ID 的历史版本独立于候选回看窗口。
“完整正文相同”也只是提案：必须是明确声明的文章与完整 `source_text`，正文至少 160 个非空白字符，
标题、正文、事实与类型化引用一致。共享链接或词面相似只给候选，不能证明同一事件。
旧适配器未声明内容类型/正文完整性时不会产生确定副本提案。未分类变化尚不代表进展、纠正或不同观点。
历史只有生成内容、截断内容或没有原文时，只建立不确定迁移基线，不伪装成去重或新进展。

高级检索默认回看 14 天、每条最多 5 个候选、扫描最近 500 条；边界分别为 1..90、1..20、50..2000。
本地词面匹配最多使用 20,000 字符和 256 个词/中文双字项，不调比较模型、不花比较 token。
这些范围只限制召回，不删除原始历史。页面每页 5 次回执，每次展示前 5 个提案/版本，
完整有界回执通过 CLI 读取；预览总是使用采集时保存的范围，不按新设置重算旧提案。

私有 SQLite 保存完整原始 JSON，写路径将目录/数据库限制为所属用户权限（0700/0600），读取不修改权限。
版本不可变且不自动删除，磁盘占用会持续增长，需要部署侧备份与容量监控。
公共契约只输出有界、脱敏的允许字段；脱敏/截断链接不能当成原文跳转。
结构化事实中的敏感键会递归脱敏；超过安全展示范围的事实不输出截断 JSON 前缀，完整原文仍私有保留。
`/view` 只附带计数、已保存模式、已激活模式及前沿，不输出全部原文。
历史缺失、锁定、损坏会明确区分，读取不修复历史；损坏不隐藏关闭开关。
开启观察后记录失败时，成功内容留在待处理队列，内容游标不推进；新完成可能延迟，
已冻结的旧投递独立恢复。关闭观察后旧历史不可用只警告，不阻断采集与通知。
验收范围与已知限制见 [`docs/story-observations-preview-acceptance.md`](docs/story-observations-preview-acceptance.md)。

### 不做什么

- **不直接写知识库。** 「⭐ 收藏」只是记一条入库意图（`favorite` 事件），真正写进知识库的
  动作发生在**宿主侧的消费器**里（要不要人审由部署自选；作者的默认部署 = Hermes 定时任务
  自动编译入库）。newspipe 自己既不写知识库，也不调用宿主的入库流程 —— 但入库必须留回执
  （`queue ack --note <入库路径>`），没有回执的入库等于没入库。
- **不绑宿主。** 不 import 宿主任何模块，也不被宿主 import；两边只通过 CLI 契约、hook 声明、
  事件流说话（防腐层）。
- **不在转发路径上做重活。** 卡片回调有 3 秒预算，转发链路只做「查表 + 转发」。

---

## 技术实现

### 分层

```
配置层 (config.py)      sources.yaml / models.yaml / service.yaml / hooks.yaml —— 唯一人工编辑入口
采集层 (pipeline.py)    按四轴取数、去重、写 state
观察层 (observations + story_matching + story_capture) 原文指纹、本地提案、采集边界协调
历史层 (Store → story_state) 不可变版本/采集回执，只读预览；不感知配置或投递
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

模型预算按每次 HTTP 尝试预先记账（失败、超时与推理重试都计入），小时限额是跨午夜的滚动
窗口；免费回执缓存不受额度限制。`max_chars_per_call` 约束 system + user 的总字符数，
超限直接降级，不截断提示词。旧用量缺少时间戳时按最后更新时间保守估算小时用量。
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

### 部署形态：不带宿主能用吗？接 Hermes 要做什么？

**能，完全独立可用。** 选 `inbound.mode: ws`（自带长连接），调度、采集、AI 加工、发卡、
网页面板、事件流全部自带，不需要任何宿主 Agent。唯一的差别是**收藏入库没有内置消费器**：
⭐ 只把意图记进队列，你要么自己在 `/ops` 页点「标记已入库」做回执，要么写个轮询脚本消费它
（读 `queue list --json` → 入库 → `queue ack --note <路径>`，几十行的事）。配套地，
进程守护、凭据存储、事件盯守这些运维责任也从宿主转到你自己头上（见文末「边界与运维责任」）。

**已经有 Hermes（或任何宿主 Agent）的话，接过去更省事**，六步：

1. 长连接归宿主：平台后台的事件订阅配在宿主的应用上；newspipe 设
   `inbound.mode: http`（**不要**再开 ws —— 一个应用两条长连接会随机抢事件）。
2. 注册卡片域：宿主转发器注册表加一行
   `news: http://127.0.0.1:8787/feishu/events`（协议见
   [`docs/host-integration.md`](docs/host-integration.md)，注册表热加载不用重启宿主）。
3. 凭据与模型复用宿主：挂载宿主的 `.env`/`config.yaml`（`NEWSPIPE_HOST_HOME`）+
   `NEWSPIPE_MODEL_BACKEND=host`；容器部署再显式给三个部署事实
   （`NEWSPIPE_BIND_HOST` / `NEWSPIPE_LOOPBACK_ALIAS` / `NEWSPIPE_PROXY`，
   `compose.yaml` 里都有现成示例）。
4. 收藏入库消费器：宿主定时任务轮询 `queue list --json`，逐条走你自己的入库流程
   （作者的部署 = Knowledge vault 的 wiki-C-compile 自动编译），成功后
   `queue ack <id> --note <入库路径>` 出队。没这一步收藏会在队列里一直攒着。
5. （可选）把 `GET /view` 或页面地址注册进宿主的服务面板 —— 宿主读契约，**不要**解析
   `state/**`（教训见 `webui/server/data/news.py` 的模块注释）。
6. 自检三连：`newspipe doctor --json` → `newspipe probe-model --json` →
   `newspipe probe-channel --json`（真发一张卡点一下，确认转发与 toast 全通）。

> AI/Agent 视角的同一份说明在 [`AGENTS.md`](AGENTS.md) §1「不用宿主能跑吗」与 §4.5。

### 状态布局

```
<news_dir>/
  sources.yaml models.yaml service.yaml hooks.yaml   配置（只能经 CLI 改）
  state/batches/<date>/<source>-<slot>.json          每批内容
  state/pushed/<source>.jsonl                        去重（推过就不再推）
  state/cursors/                                     分页游标
  state/status/                                      各源心跳
  state/stories/history.sqlite3                      私有不可变原文与提案（只读契约访问）
  state/budget/<date>.json                           发卡打扰预算
  llm/usage/<date>.json                              模型用量与小时窗口
  state/events/<date>.jsonl + acks.jsonl             事件流与消费确认
  llm/receipts/  llm/usage/<date>.json               回执缓存与账单
```

---

## 快速上手

### 收藏回执与保留

未配置 `service.yaml` 或未指定 `channel` 时默认直连飞书（`feishu_direct`）。
CLI、常驻调度与后台回调按同一配置装配通道；`feishu_lark_cli` 必须显式选择。

`newspipe chats list --json` 只读查询当前发送机器人已加入的群，支持
`--page-size 1..100` / `--page-token`。需要该应用的 `im:chat:read` 权限，不需要个人授权。
此命令显式联网，但不保存目标、不发送消息、不启动长连接；页面加载不会自动查询。
群列表不支持时可继续使用手动群 ID，不会自动切换通道或应用。
后台“通知 → 全局设置”可显式加载机器人已加入的群，再按名称筛选和选择。
筛选只匹配已加载的群，有下一页时点击“加载更多群”；最多保留 1000 个群，达到上限会明确提示。
加载、筛选与选择都不会保存配置或发送消息；选择后仍需校验并保存。手动群 ID 在备用选项中。

HTTP/WS 点击入口先持久化任务并立即返回“已接收”，慢卡片更新和 hook 由服务后台消费。
宿主应透传平台 `event_id`（重试复用），否则旧信封仍可接收，但无法可靠区分重试与独立点击。
后台状态在 `status --json` / `/view` 的 `callbacks`，失败也写事件日志；执行中断的第三方
hook 不自动重放，必须核对外部回执。

待入库收藏不会按天数过期，`queue list` 始终返回全部未确认收藏（`--days` 仅保留兼容）。
`queue ack` 必须提供非空 `--note <入库路径或回执>`，且只能确认收藏；通用 `events ack`
不能确认收藏。批量确认先校验全部 ID，任一无效时不写入任何确认。
`events prune` 只删除全部事件均已消费的过期日文件，未消费事件始终保留。

投递会先持久化卡片身份和发送幂等标识，未完成投递跨天继续恢复，不新建另一张卡。
发送结果无法确认且超过平台的一小时幂等窗口时停止自动重发，必须先核对远端消息；
不能把网络超时当成“肯定没发出去”。

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

`GET /view` 是只读 JSON 契约；**页面**分五个 Tab（同一个 builder + 同一份数据，服务端路由，
可深链、可加书签、无前端构建）：

| 路由 | Tab | 内容 |
|---|---|---|
| `GET /` | 驾驶舱 | 全局总览：KPI、**近 14 天趋势**、**最近入库**（最新 8 条标题）、信源心跳表、**诊断抽屉**、今日批次。**只读**，一个表单都没有 |
| `GET /items` | 条目 | **内容浏览**：近 3/7/14 天所有条目 + 服务端筛选（关键词/源/状态），批次列可点进详情 |
| `GET /batch/<日期>/<文件>` | 条目 | **批次详情**：一个批次里到底有哪些条目（标题/发布者/分类/分数/摘要/原文链接/状态） |
| `GET /config` | 消息源 | 搜索/筛选、增删改/复制、采集时间管理、策略绑定与参数只读展示、RSS 目录/订阅 |
| `GET /notifications` | 通知 | 策略独立发送时间、全局硬限制、机器人群选择与只读预览 |
| `GET /ops` | 运维 | 分开采集/通知待处理、安全重试原操作、旧式补跑、⭐标记已入库（回执/兜底） |

全部页面都需 `service.yaml: view.enabled`；写操作额外需 `view.actions`。
自带 `contract_version`。**宿主不要解析 `<news_dir>/state/**`**：那是本项目的私有布局，
改一次布局就会让宿主静默读空（页面显示成「从未运行」，看着像没跑，其实是读错了地方）。

### 界面与样式：本地渐进增强、零外部资源

样式在 `src/newspipe/style.py`，表单由 Python 服务端渲染。唯一脚本是同源
`/assets/workbench.js`：防重复提交、就地反馈、保留失败输入、未保存导航提示；无脚本仍能提交和预览。
不加载 CDN、外部字体、图标字体或遥测，没有构建链。配置/schema 和 CLI 校验才是事实来源。
失败、演练或预览的普通表单用 PRG 返回本页，草稿仅保留于服务内存（最多 32 份、10 分钟、一次性），
URL 只有随机句柄，不含草稿或令牌；服务重启/过期后草稿消失。冲突不会自动换哈希或重试写入。

动效全部走**平台原生能力**，且一律是渐进增强（不支持的浏览器只是没动画，不会坏）：

| 能力 | 用途 | 支持情况（2026-10） |
|---|---|---|
| `@view-transition{navigation:auto}` | Tab 切换交叉淡入 | Chrome 126+ / Safari 18.2+（Firefox 退化为普通跳转） |
| `transition`（悬停/聚焦/边框） | 悬停底色、聚焦环、抽屉箭头旋转 | 全部 |
| `prefers-color-scheme` | 深浅色跟随系统 | 全部 |
| `prefers-reduced-motion` | 统一关断动效 | 全部 |

**刻意不做「入场动画」**：两种主流写法都有「把内容永久留在不可见」的失败模式，且都实测到了 ——
① `transition` + `@starting-style`：真实浏览器里那段过渡没跑起来，`.stat` 停在 `opacity:0; translate:0 8px`，
统计卡片整块看不见（视觉核对抓到的，字符串测试全绿拦不住）；
② `animation` + `fill:both`：动画时钟不推进时停在 `from` 帧，同样是 `opacity:0`（本机无头浏览器 4 个动画
全是 `playState:running, currentTime:0`，截图/打印/无渲染环境都会命中）。
同理**没做**滚动渐显（`animation-timeline:view()` 的 `both` 填充会让视口外的行停在 `opacity:0`）。
数据面板里「内容看不见」比「没有动效」糟得多 —— 保留的动效全都**不可能隐藏内容**。

`--nav-h` 是导航栏高度的**单一事实来源**：吸顶表头的 `top` 与导航的 `height` 共用它
（写死像素会错位 —— 实测导航真实高度 49.7px，写 `44px` 会让表头被盖住 5.7px）。
页面只有**一份外壳**（`_page()`），导航/标题/元信息/页脚都在那里。

### 条目页：终于能看见收进来的东西

驾驶舱只给**计数**（146 条、78 条、6%），看不到任何一条的标题 —— 收了几百条，页面上一条都读不到。
`/items` 补上这一层：

- 数据来自 `state/batches/<日期>/*.json`（**页面专用，不进 `/view` 契约**：那是给宿主的只读
  契约，形状要稳，也不该背几百条条目）
- 筛选全在**服务端**（`?q=&source=&status=&days=`）：无 JS，`curl` 也能筛，和「自包含」这条底线一致
- 标题链 **`original_url` 优先** —— 那才是真实出处；`url` 常是抓取侧的代理页
- 状态取值**从数据现取**，不硬编码清单（实测数据里出现过 `wiki` 这种没预料到的状态）
- 命中 0 条时给诚实的空状态，不回空表

**批次详情路由的输入直接来自 URL，所以两道路径校验**：形状（日期 `\d{4}-\d{2}-\d{2}`、
文件名 `[A-Za-z0-9_][A-Za-z0-9_.-]{0,80}`）+ 越界（`state.Store.load_batch_by_rel()` 会拒，
越界路径连 IO 都不会发生）。

### 驾驶舱：趋势与诊断抽屉

**趋势（近 14 天）** 的每一列都对应一个落盘文件，页面不发明数据：

| 列 | 来源 |
|---|---|
| 条目 / 已标记率 | `state/batches/<date>/` |
| 发卡 | `state/budget/<date>.json` |
| 入库 | `state/pushed/<源>.jsonl`（每行带 `date`） |
| AI 降级/调用 | `llm/usage/<date>.json` |
| 有产出的源 | **批次 ∪ 推送台账** |

**「有产出的源」必须是两路合并** —— 有些源根本不写批次文件（poll + `append_card` 直接追加进
当日实时卡）。只看 `state/batches/` 会把它们的产出算成「没有」：实测 `linuxdo_deals` 台账
13 条（09-28/29、10-02）却 0 个批次文件，页面于是显示「一天都没有 —— 源可能一直没拉到东西」。
台账那一路（`state.Store.pushed_by_day()`）加上之后才看见真相：09-27 起每天 74–167 条入库。

**诊断抽屉**（点开源展开）给：最近心跳 + 原因、最近一次计数、近 14 天有产出天数、
**生效中的闸**（卡片形态 / 静默时段 / 每日上限与最小间隔 / 条数上限与触发）、
顺延队列与去重台账条数。

诚实边界写在页面上：**历史拦截原因没有落盘**（`state/status/<源>.json` 只留最后一次心跳），
所以给的是「最近一次为什么这样」+「生效中的闸」，不是「历史上被哪道闸拦了几次」；
「各源历史成功率」同理不给。无历史时**不提产出统计** —— 「近 0 天产出 0 天」会被读成
「源一直没拉到东西」，而无历史只是「还没有历史」。

趋势条形是纯 CSS（`<span class="bar" style="width:N%">`）：不引图表库、不发外部请求、不用 JS。

### 配置页：信源增删改 + RSS 搜索订阅

**表单不是第二份 schema。** 字段、类型、枚举取值、默认值全部来自引擎的
`newspipe config describe --json`（`config.py: describe_schema()`）；编辑表单的当前值来自
`config.source_values()`。加一个配置字段只改 `config.py` 一处，页面自动跟上 ——
有测试钉住「schema 的每个 path 都能取到当前值」，因为漏一个字段的后果不是报错，
而是**编辑表单把那个字段按默认值写回去**（静默的数据损坏）。

| 动作 | 落盘路径 |
|---|---|
| 新增信源 | `newspipe source set <名> --from-json '<整段配置>' --create-only` |
| 修改信源 | `newspipe source patch <名> --from-json '<合并补丁>'`（+ `--dry-run` / `--base-hash`） |
| 复制信源 | `newspipe source copy <名> --new-name <新 ID>`（保留扩展，默认停用） |
| 删除信源 | `newspipe source remove <名>` |
| 启用/停用 | `newspipe source enable\|disable <名>` |

- 表单默认勾选**先演练**（`--dry-run`），落盘前先看 diff；取消勾选才是真写。
- 编辑使用合并补丁：未提交字段不动，`null` 删除字段，保留未知适配器扩展、卡片配置和显式覆盖。
  CLI `source set` 仍是整体替换。修改块内注释可能丢失，其他块保持原样；warning 原样显示。
- RSS 搜索两条路：① **关键词搜目录**（`src/newspipe/rss.py` 里的 `CATALOG`，收录历史
  RSSHub 验证记录，带当时的日期与状态码，**不代表当前实例可用**）；② **站点地址发现 feed**（抓页面里的
  `<link rel=alternate>`，没有再探 `/feed`、`/atom.xml` 等常见路径）。
  本地 RSSHub 没有 `/routes` 目录（实测 404），所以不做「全网 RSS 搜索」这种吹牛功能。
- 搜到的候选点「用这个新建信源」→ 跳到配置页并**预填**成 `rsshub` 适配器 + 该地址。

### RSSHub 可选配置与检测

`rsshub` 是兼容保留的适配器名称，后台显示为 **RSS / RSSHub**。完整 HTTP(S) RSS 地址
直接访问，不需要部署 RSSHub；`/sspai/matrix` 这样的相对路由才需要一个可用的 RSSHub 实例。
支持本机与远端 HTTP(S) 地址，也支持反向代理前缀，例如 `https://example.com/rsshub`。
不会自动发现、安装或启动本机 RSSHub，因为进程存在不等于用户配置的实例 / 路由可用。

基础地址优先级：`sources.yaml: rsshub.base_url` → 环境变量 `RSSHub_BASE` →
`http://127.0.0.1:1200`。后台 `/config` 的“RSSHub · 可选连接”显示生效地址、来源和
启用且依赖的信源。展开“设置地址与测试订阅”可保存覆盖地址；显式留空并保存恢复继承。
默认仅校验，取消勾选才写入。保存由 CLI 原子校验和乐观并发保护，后续采集无需重启即可使用。

```sh
newspipe rsshub show --json
newspipe rsshub set --base-url https://example.com/rsshub --dry-run --json
newspipe rsshub set --base-url '' --json           # 清除显式覆盖，恢复继承
newspipe rsshub probe --json                      # 只检测当前端点可达
newspipe rsshub probe --base-url https://example.com/rsshub --feed /sspai/matrix --json
newspipe rsshub probe --feed https://example.com/feed.xml --json
```

后台“测试连接”仅验证候选端点可达，不证明服务一定是 RSSHub，也不证明所有路由正常；
“测试订阅”检查 RSS 2.0 / Atom，包括合法的空订阅，RDF 会明确报暂不支持。
测试不会保存、采集入队或发送通知，不清除未保存提示、不更新表单版本；无 JavaScript 时
保留本次草稿并重定向，刷新不会再次检测。失败区分网络、鉴权、路由不存在与订阅格式问题，
不显示上游响应正文、原始异常或 URL 查询参数。页面和 `doctor` 只读元信息，**不自动联网检测**。
检测直接访问端点，不走配置的出网代理；单次请求无重试，响应上限 1 MiB、套接字超时
5 秒，DNS / TLS / HTTP 跳转可能增加总耗时。
运行时同一信源含多个订阅时，异常 XML 单独记录安全的失败日志，正常订阅继续采集；
全订阅失败则明确报错，不会伪装成没有新内容。推导出的来源名称不包含 URL 查询参数。

容器中的 `localhost` 指容器自己。访问宿主 RSSHub 请用 `http://host.docker.internal:1200`，
同一容器网络使用服务名，远端使用完整地址；不要把用户名、密码或密钥写进基础地址。
`compose.yaml` 显式传递可选 `RSSHub_BASE`，但不启动 RSSHub；模型用的回环别名不会自动改写 RSSHub 地址。

### 页面写操作：仪表盘变控制台（`view.actions`）

打开 `service.yaml: view.actions` 后，`/ops`、`/config` 与 `/notifications` 上出现按钮与表单。机制：

消息源和策略的创建、编辑、复制，以及全局 / RSSHub 设置统一在弹窗中完成。普通页面只展示摘要与清单；`?` 支持悬停、键盘聚焦和点击固定，移动端可点击查看。高级选项折叠但始终保留当前值。
默认按钮为“校验配置”；取消“仅校验，不应用”后变为“保存配置”。只有确认保存成功且提交期间没有新编辑时才关闭弹窗并刷新清单。关闭未保存弹窗会询问，草稿仅在当前页面保留，离开或刷新后丢失；多个草稿各自保留原始版本，其他配置保存不会替它们更新版本。
禁用 JavaScript 时编辑链接进入同一份服务端表单；预览、校验和失败通过一次性 PRG 恢复草稿，刷新不会重放写入。错误、删除后果和入库回执条件不会藏进提示图标。

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
- 配置保存、安全重试和旧式跑一轮默认只校验/试运行；采集与通知待处理按钮是明确的即时操作。
  “通知待处理”只读队列；越过汇总时钟需要勾选确认，仍不能越过暂停或预算。
- **没开 `view.actions` 就不渲染按钮** —— 给一个按下去只会 403 的按钮是骗人。
- 只读契约 `GET /view` 不含令牌；新增 `notification`（有效规则、来源、待处理原因、预算和安全回执）
  与同一字节快照的 `source_values` / `sources_hash`，供页面乐观并发。宿主继续读契约，不读私有状态。

### 可复用通知策略（单用户、单目标）

采集决定“收什么”，策略决定“何时打扰”。同一策略绑定多个源，但**每个源独立计数**；
顶层 `notifications` 是全部源共同受限的硬门禁，含旧式源和源级例外。更新已有卡片也计一次操作。
计数按确认成功的日期归属；新策略/全局最小间隔从最近确认时间跨日连续计算，不在午夜清零。
已确认远端成功但本地回执未完成的操作仍占用预算，防止另一源在修复之前穿透总上限。

```yaml
chat: oc_example
slots: {am: '08:15', pm: '18:35'}
notifications:
  paused: false
  quiet_hours: '23:00-07:30'
  max_cards_per_day: 24
  min_gap_min: 0
policies:
  reading:
    label: 早晚读报
    mode: digest
    times: {morning: '08:15', evening: '18:35'}
    form: card
    max_cards_per_day: 4
sources:
  my_feed:
    adapter: rsshub
    feeds: [https://example.com/feed.xml]
    fetch: {trigger: poll, interval_min: 10}
    notify:
      policy: reading
```

策略 `mode` 可选 `immediate`（有待处理即评估）、`digest`（指定多个发送时间）或 `collect`（本地批次，不发送）。
`card` 每批新卡，`append_card` 更新稳定卡片。消息源只选择策略，参数在源弹窗只读展示；
需要不同设置时，复制策略后绑定，不再提供源级覆盖。编辑一个策略，所有绑定源同步生效，其他策略不变。
`times` 的键是内部稳定标识，保留通知完成记录；管理后台隐藏标识，仅编辑时间。
旧同刻计划保留各自的完成记录；新建或改时不能引入重复时间，旧同刻计划可原样保留或消除重复。
顶层 `slots` 只管理定时采集计划，修改它不影响已独立配置 `times` 的策略。
后台在“消息源 → 管理采集时间”统一编辑采集时刻，不显示内部标识。
修改一个时间，所有引用它的消息源同步生效；引用详情含停用源，解除全部引用后才能移除。
旧通知策略仍引用采集时间时会明确警告并提供迁移入口，不会自动改变旧行为。
源弹窗按触发方式显示采集时间复选框、间隔或手动采集；切换方式不会清空未生效的配置。
采集与策略发送时间都按 `HH:MM` 保存，时间输入框的外观可能随浏览器地区设置显示 AM/PM。
添加/移除时间仅调整草稿；Enter 仅校验，真正写入仍需取消“仅校验，不应用”并点击保存。
全局上限未配置是不加硬上限，`0` 是不发送；源级例外只跳过该源的静默/上限/间隔，不能越过全局或时钟。
暂停通知继续采集；停用源停止新采集与新通知，队列保留。已确认远端成功的本地回执仍可修复。

既有源不会自动迁移。绑定/解绑先预览并确认：旧式预算是全局计数，模板预算是每源计数，转换不清零。
旧字段保留用于回退。切到 `collect` 后待处理内容消费为本地批次，切回通知不会重发这些内容。
旧策略 `slots` 引用与源 `notify.overrides` 仍兼容读取，通知页明确提示待迁移。
先执行 `newspipe notification migrate --dry-run --json` 查看变更，再带当前 `--base-hash` 执行迁移：
旧时间固定到各策略，源覆盖值保留为独立策略，已停用源也迁移，计数、队列和投递回执不变。
旧覆盖配置迁移前不能改绑定或复制源，避免丢值；单独修改源的采集或加工参数仍可保存。
从源弹窗创建策略后，本页保留源草稿。返回源草稿并选择新策略，点击“核对并同步最新配置版本”；
只有完整源配置未被其他操作改动才更新版本，之后再预览、保存。离开页面不保留草稿。
历史总计缺少源归属时，当天模板的源级限制保护性阻塞，不编造计数；次日或核对留存回执后再处理。
删除策略前先解除所有引用（含停用源）；ID 稳定不可改名，复制必须使用新 ID，新源默认停用。

```sh
newspipe policy set reading --from-json '{"mode":"immediate","form":"append_card"}' --create-only --dry-run --json
newspipe global set --from-json '{"notifications":{"paused":true}}' --dry-run --json
newspipe notification preview --source my_feed --json
newspipe collect my_feed --json       # 只采集，不强制发送
newspipe notify my_feed --dry --json   # 只评估已有待处理，不抓取
```

失败重试最多三次、至少间隔一分钟，新内容、配置修改和解绑不会重置未完成原操作。
发送 JSON 载荷持久化并精确复用；新增内容、标题/按钮配置不会偷偷改变该操作的重试载荷。
`notification retry <源> --operation-id <ID>` 仅在安全幂等窗口或明确未发送时重置同一身份的次数，不立刻发送。
远端结果未知且超过窗口时，按 `/ops` / `notification preview` 的目标、卡片 ID、操作 ID 和时间核对平台回执；
保持阻塞，不提供强制重发、不伪造成功、不建议编辑私有状态。本版本不含远端回执对账写入器。

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
newspipe queue list --json                                # 待入库台账（消费器入库后 ack 出队）
```

细节与硬规则见 [`AGENTS.md`](AGENTS.md)。

---

## 测试

```bash
python -m pip install -e '.[all]'
NEWSPIPE_REQUIRE_JS_TESTS=1 python -m unittest discover -s tests  # Node 24，缺少即失败
python3 scripts/scan_secrets.py             # 与 CI 使用同一密钥门禁
```

CI 对全部分支 push / PR 验证 Python 3.11/3.12/3.13，显式安装 Node 24。
浏览器测试是可选开发依赖、独立强制 CI job，不引入前端构建链：

```bash
python -m pip install -e '.[browser-test]'
python -m playwright install chromium webkit  # Linux 用 install --with-deps
NEWSPIPE_BROWSER_TESTS=1 python -m unittest discover -s tests -p test_browser_e2e.py
```

使用临时离线状态，覆盖两种引擎、手机/平板/桌面、保存失败及无 JS 表单；
未启用浏览器标志时单元测试有意跳过，启用后缺工具或引擎会失败。
失败截图/trace 写入被忽略的 `test-results/browser/`。
真实应用权限、消息回执和点击闭环仍需[受控飞书验收](docs/live-feishu-acceptance.md)，
离线通过不代表已实机通过，不能自动发卡或另起 WS 来补验收。

覆盖：四轴配置与校验、采集与去重、预算熔断与回执复用、卡片渲染与元素预算、
卡片交互（翻页/收藏/屏蔽）、**两种入站方式的装配**（含「handler 没注册到 SDK 上」这类
静默故障）、CLI 契约与退出码、状态原子写。

密钥扫描覆盖已跟踪与未忽略的工作区文件，只报告位置与规则，不打印命中值。
它不会读取被忽略的本地凭据，也不扫描 Git 历史；如果密钥曾经提交，仍需撤销或轮换。

---

## 文档

| 文档 | 内容 |
|---|---|
| [`AGENTS.md`](AGENTS.md) | 给 Agent 的使用说明：两种入站方式、CLI 契约、事件流、硬规则、排障 |
| [`docs/host-integration.md`](docs/host-integration.md) | 宿主接入协议 v1 + 两份参考实现（通用宿主 / 插件式宿主） |
| [`docs/live-feishu-acceptance.md`](docs/live-feishu-acceptance.md) | 离线验收命令、证据边界、受控实机验收与停止条件 |
| `examples/news/*.yaml` | 配置示例（信源 / 模型 / 服务 / 卡片按钮） |
| `newspipe api describe --json` | 命令面契约（运行时自取） |

---

## 边界与运维责任

独立运行意味着下面五件事从「宿主帮你做」变成「你自己做」：
**凭据存储、进程守护、投递重试、事件订阅、收藏入库消费**。前四件代码里都有对应实现，
但运维责任一并转移 —— 钥匙串/环境变量自己管、launchd/systemd 自己装、投递失败自己看日志、
事件订阅自己盯连接。第五件（入库消费）是设计上的留白：知识库是宿主侧的东西，newspipe
只记台账，消费器你自己提供（`/ops` 手动回执或一个几十行的轮询脚本）。
如果你已经有一个稳定的宿主 Agent，用「宿主转发」那条路会省掉大部分运维。

## 许可

MIT（见 `LICENSE`）。
