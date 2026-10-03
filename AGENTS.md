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
2. **密钥永不进仓库、永不打印。** 凭据只从 显式配置 → 进程环境 → dotenv → 系统钥匙串 读
   （见 `src/newspipe/credentials.py`）。任何探测/自检命令只输出「有没有、够不够」，不输出值。
3. **人环不能省。** `favorite`（⭐ 入库）这类事件只**进队列**（`state/events` + `queue`），
   是否真的写进用户的知识库必须由人确认。Agent 可以展示、可以建议，不许自动落库。

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

响应 `{"code": 0}` = 处理成功；`{"code": 0, "toast": {...}}` = 顺便回一个提示给用户；
非 0 或超时 = 宿主可以回一句「服务未响应」。**3 秒预算**：不要在转发路径上跑 LLM 或长任务。

完整协议、注册表格式与两份参考实现（通用宿主 / 插件式宿主）见
[`docs/host-integration.md`](docs/host-integration.md)。

### 两种方式都要能自证

```sh
newspipe doctor --json                             # 当前模式 + 冲突检测
newspipe probe-channel --json                      # 真发一张卡（输出不含任何密钥）
```

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
| 自检 | `doctor` / `status` / `probe-model` / `probe-channel` |
| 信源 | `source list\|show\|set\|enable\|remove`（写操作支持 `--dry-run`） |
| 卡片按钮 | `hooks list\|add\|remove` |
| 事件 | `events list --unconsumed` / `events ack <id> --by <who>` / `events prune` |
| 队列 | `queue list` / `queue ack <id>` |
| 跑一轮 | `run --slot am` / `run --poll` / `run --source <id>` / `run --poll --dry` |
| 服务 | `serve`（常驻：自带调度 + 入站） |

**约定**：`set/remove/add/ack` 这类写操作都「先校验再落盘」，被拒绝时 `ok=false` 且退出码 `4`，
同时给出 `hint`。Agent 不要绕过 CLI 直接改 YAML —— 校验与原子写都在 CLI 里。

---

## 3. 事件流与队列（Agent 怎么消费）

```
<news_dir>/state/events/<YYYY-MM-DD>.jsonl    一行一个事件
<news_dir>/state/events/acks.jsonl            消费确认
```

事件类型：`delivered`（发卡成功）/ `clicked`（点了）/ `favorite`（⭐）/ `muted`（🔕）/
`degraded`（AI 加工降级）/ `failed`（投递失败）。

```sh
newspipe events list --unconsumed --json       # 还没被消费的
newspipe events ack ev_xxx --by myagent --json # 消费掉（幂等：区分 acked/already/unknown）
newspipe queue list --json                     # 只看「待入库」的收藏
newspipe queue ack ev_xxx --by myagent --json  # 人确认入库之后才调
```

实时性靠 hook（事件发生当下就把 payload 推给宿主的脚本），补漏靠轮询 `events list`。
**两者都要有**：hook 会失败（fail-soft），事件流才是真相。

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
        r = httpx.post(target, json={"domain": domain, "tag": event.action.tag, "value": value},
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

---

## 6. 改本项目时的约定

- **不 import 宿主，也不被宿主 import**：两边只通过 CLI 契约、hooks 声明、事件流说话。
  这是防腐层，不是风格偏好。
- **默认值要能独立跑**：任何「需要宿主」的能力都必须显式配置才启用（例：`NEWSPIPE_HOST_HOME`），
  且没配时报可执行的错 —— 猜一个路径会导致静默错配。
- **失败要出声**：入站、投递、hook 的失败都必须留痕（事件流 / 日志），
  「静默 + 重试」是最贵的故障形态。
- **发布前跑一次**：`python3 scripts/scan_secrets.py`（0 = 干净）与
  `python -m unittest discover -s tests`。
- **发卡相关改动**：跑 `newspipe doctor --json` 确认元素预算没被顶破。
