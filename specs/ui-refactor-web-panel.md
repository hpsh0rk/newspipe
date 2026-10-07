# Spec: Web 面板简化重构 —— 「看 / 读 / 改 / 修」四页各司其职

> created: 2026-10-07
> status: implemented
> 上游: `specs/ui-audit/2026-10-07-web-panel-ux-audit.md`（Approved）
> 设计契约: `specs/design/DESIGN_SYSTEM.md`（方案 A「Ocean Minimalist」，tokens 已锁定）

---

## Scope 边界

**只动渲染层**：`view.py` 的 `*_html()` 系列与 `style.py`、`inbound.py` 的页面装配函数
（`_recent_page` 加只读计数）。
**不动**：`view.build()` / `items_snapshot` / `action_snapshot` 返回结构、`TABS` 常量与全部
路由 URL、`actions.py` 的 7 个动作白名单与表单字段名、CLI、事件流、卡片渲染。

## Contract 接口契约

- `view.as_html(view, *, flash=None, page=None)` = **驾驶舱**（只读总览）；`cockpit_html` 转发。
- `view.ops_html(view, *, actions=None, flash=None)` = **运维**（动作面），不再复用驾驶舱正文；
  无 token 时渲染诚实的「写操作未开启」空态（这是唯一允许出现该文案的页面）。
- 驾驶舱 KPI 4 个：今日条目 / 已发卡 / ⭐待确认（>0 时链接 `/ops#queue`）/ AI 降级（仅 >0 渲染）。
- 信源实体单点化：驾驶舱 = 单行健康条（异常置顶，行内 `<details>` 展开诊断抽屉）；
  运维 = 动作表（源 / 心跳 / 操作）；完整运行态明细只在诊断抽屉里。
- 趋势表与今日批次收进驾驶舱「深入诊断」折叠区；运维页不再重复渲染它们。
- 运维页**下线**未消费事件按钮墙（`events-ack` 动作保留在白名单，仅去 UI 入口）。
- `config_html`：编辑表单（有 `?edit=` 时）→「添加信源」（RSS 搜索 + 新增表单合并置顶）→ 信源清单。
- `_page()` 页首元信息行（生成于/通道/入站/投递群）移入页脚；页脚不再输出架构自证文字。
- CSS 硬约束不变（有测试钉着）：`--nav-h` 单一来源、table 无 overflow/transform、
  无 `@starting-style` / 入场动画 / `opacity:0` 关键帧、零外部资源、诚实边界文案全保留。

## Acceptance Criteria 验收标准

1. 全量测试绿（含按新行为显式更新的断言：`/` 不再含「本页只读」自我说明）。
2. `test_history` 的诚实边界断言（生效中的闸 / 没有落盘 / 一天都没有 / 还没有历史 / 只算落盘过的）
   **不改一行**仍绿。
3. 行为保持清单（审计 §5）逐项核过：URL、契约、动作白名单、表单字段名不变。
4. 交互成本量化（审计 §3）达成：驾驶舱 1 屏 ≤3 决策；⭐ 入库 ≤2 步；加信源 ≤3 步。
5. 视觉落 `specs/design` tokens（Ocean Minimalist）；每 Tab 一个独立可回退提交。
