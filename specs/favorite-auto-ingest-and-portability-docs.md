# Spec: 收藏去人审（自动入库）+ 可移植性文档（独立跑 / 接 Hermes）
> 状态: Done (gate)

> created: 2026-10-07
> status: implemented
> 背景：作者决定 ⭐ 收藏不再需要人审 —— 收藏后由宿主侧自动化直接走 Knowledge vault 的
> wiki-C-compile 流程（propose → 自审 → approve → promote，mode A 自动晋升）编译入库。
> 同时回答可移植性问题：不用 Hermes 能不能用？用 Hermes 要做哪些操作？（README 给人看，
> AGENTS.md 给 AI 看。）

---

## Scope 边界

**改（newspipe，语义与文档，不含流程代码）**：
- `src/newspipe/interaction.py`：模块 docstring 的动作表/边界段、收藏分支注释、⭐ toast 文案。
- `src/newspipe/cli.py`：`queue ack` 子命令摘要（api describe 契约面）。
- `src/newspipe/view.py`：`/ops` 页 ⭐ 队列区文案（「人环放行」→「入库回执/手动兜底」）。
- `AGENTS.md`：硬规则 3 重写（人环不能省 → 入库动作发生在宿主侧+必须留回执）、§3 事件流
  消费描述、§2.1 /ops 行、§4 补「不用宿主 / 用 Hermes」的最小操作清单。
- `README.md`：「不做什么」首条、/ops Tab 行、queue list 示例注释、新增
  「部署形态：独立跑 vs 接 Hermes」一节、「边界与运维责任」补入库消费一项。

**不改**：
- 事件流与队列机制本身（`favorite` 仍只进 `state/events`，队列 = 未 ack 的 favorite）；
- `interaction.handle()` 的核心动作语义（状态、审计、卡片更新、事件）全部保持；
- `actions.py` 白名单、三道门禁、CLI 校验与原子写；
- 任何「newspipe 直接写知识库」的路径 —— **一条都不加**（防腐层不变）。

**不改（跨仓库，另行落地）**：Knowledge vault 侧的 `sch_news-favorite-ingest`
Hermes 定时任务（消费队列 → wiki-C-compile → `queue ack`）在 vault 的
`_meta/automation/schedules.md` 登记并 apply，属于 vault 自己的权威流程，不在本仓库。

## Contract 接口契约

- `favorite` 事件的语义从「待人确认的入库请求」改为「入库意图的台账记录」：
  newspipe 只负责**可靠记录**，入库的执行方是宿主侧消费器（自动化或人工，部署自选）。
- 队列契约不变：`queue list` = 未 ack 的 favorite（时间升序）；`queue ack --note <路径>`
  = 入库回执（幂等，acked/already/unknown）；`/ops` 的确认入库按钮 = 手动兜底回执。
- 用户可见文案不再承诺「人确认」：toast 与页面措辞改为「已提交入库 / 自动编译」口径；
  对没有任何消费器的部署，README/AGENTS 必须如实说明「需要自己提供消费器」。
- api describe（`cli.py: COMMANDS`）里 `queue ack` 的 summary 随语义更新，其余字段不动。

## Acceptance Criteria 验收标准

1. [x] `python -m unittest discover -s tests` 全绿（356 例，含更新后的 toast 回执断言）。
2. [x] `python3 scripts/scan_secrets.py` 退出码 0。
3. [x] 仓库内 grep 不再出现「人确认后再入库 / 人环放行」这类与新人审口径矛盾的用户可见文案
   （历史 spec/审计文档除外）。
4. [x] README 有「独立跑（ws）完全可用」与「接 Hermes 的操作清单」两段，步骤可照做；
   AGENTS.md 有同等内容且对 AI 可执行（含自检命令）。

## 落地记录（2026-10-07）

- vault 侧消费器已上线：`_meta/automation/schedules.md` 登记并 apply 为 Hermes job
  `7c0e25702a9f`（`*/15`，零 pin），冒烟端到端走通：队列中真实收藏
  `ev_20261006T084940_def5a2` → `.raw/` 捕获 → promote 为
  `wiki/ai/数据中心资源消耗与信息披露.md` → `queue ack` 回执页面路径，队列归零。
- 队列语义未变（= 未 ack 的 favorite）；`/ops` 的「标记已入库」保留为手动兜底。
