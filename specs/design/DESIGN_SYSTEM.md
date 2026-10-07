# newspipe - 高保真前端设计系统规范 (Design System Contract)

> **设计方案**: 方案 A: 现代通透极简 (Ocean Minimalist)
> **契约版本**: `1.0` (Ocean Minimalist) · **生成时间**: 2026-10-07 04:52:27
> **约束级别**: **物理门禁强制执行 (Design-First Redline)**

---

## 1. 视觉主题与氛围 (Visual Theme & Atmosphere)

轻盈通透的浅色画布 + 高饱和科技蓝单点强调。留白充裕、层级分明, 读起来是「干净、可靠、不喧哗」的效率工具气质 —— 信息密度中等, 视觉呼吸感优先, 强调色稀缺到几乎只出现在主 CTA 上。

- **气质词表**: 轻盈 · 通透 · 可靠 · 不喧哗 · 呼吸感
- **信息密度**: 面向 通用 SaaS、效率管理、日常学习与协作工具

**用词纪律** (数值给编译器看, 气质给生成器看, 两者必须并列):
- 描述「感觉」时用自然语言, 不要直接抄 CSS 值 —— 说「几乎不圆的角」而非 `border-radius: 4px`
- 把气质与数值并列: 说「分区之间画廊般的静默」比 `margin-bottom: 128px` 更能约束生成结果
- 本方案的用词一律取 description 与 feel 词表, 不要自造同义气质词 (会稀释设计语言的辨识度)

---

## 2. 基础色彩系统 (Color Tokens)

在任何前端样式中，**严禁使用硬编码色值**，必须严格遵循以下色彩变量：

| 变量名称 | 色值 | 用途与语义说明 | 预览 |
| :--- | :--- | :--- | :--- |
| `colors.primary` | `#2563EB` | 核心品牌主色、主按钮背景、核心选中高亮 | `[■]` |
| `colors.primaryHover` | `#1D4ED8` | 按钮悬停 (Hover) 态背景 | `[■]` |
| `colors.primaryActive` | `#1E40AF` | 按钮按压 (Active) 态背景 | `[■]` |
| `colors.secondary` | `#0284C7` | 次级操作、辅助标识 | `[■]` |
| `colors.background` | `#F8FAFC` | 全局页面背景底色 | `[■]` |
| `colors.surface` | `#FFFFFF` | 卡片、面板、弹窗表面底色 | `[■]` |
| `colors.textPrimary` | `#0F172A` | 主要正文、标题颜色 | `[■]` |
| `colors.textSecondary` | `#64748B` | 辅助文字、表单提示文字 | `[■]` |
| `colors.border` | `#E2E8F0` | 默认边框、卡片分割线 | `[■]` |
| `colors.onPrimary` | `#FFFFFF` | 主色之上的文字 (主按钮文字) | `[■]` |
| `colors.focusRing` | `#93C5FD` | 键盘焦点可见描边 | `[■]` |

### 语义与状态颜色 (Semantic Feedback)
- **Success (成功)**: 背景 `#ECFDF5` | 边框 `#A7F3D0` | 文本 `#065F46`
- **Warning (告警)**: 背景 `#FFFBEB` | 边框 `#FDE68A` | 文本 `#92400E`
- **Error (错误)**: 背景 `#FEF2F2` | 边框 `#FECACA` | 文本 `#991B1B`
- **Info (提示)**: 背景 `#EFF6FF` | 边框 `#BFDBFE` | 文本 `#1E40AF`

### 对比度红线 (Contrast Redline)

> 文字可读性是**硬红线而非评分项**。正文/次级文字/主色链接对背景与表面须达 WCAG 2.1 AA **≥4.5:1**; 按钮瞬态态 (hover/active) 下限 **≥3:1**。由 `design-gatekeeper.js` 机械校验。
> 豁免: 占位符与禁用态 (`textTertiary`) 不设阈 —— WCAG 对禁用控件豁免。

---

## 3. 字体与排版阶梯 (Typography)

- **字体族 (Font Family)**: `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", sans-serif`
- **代码字体 (Mono)**: `ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace`

| 阶梯 | 像素大小 | 行高 | 字重 | 适用场景 |
| :--- | :--- | :--- | :--- | :--- |
| **xs** | `12px` | 1.25 | 400 | 微小标签、版权信息、辅助状态元数据 |
| **sm** | `14px` | 1.5 | 400/500 | 表单说明、表格次要列、次要按钮文字 |
| **base** | `16px` | 1.5 | 400/500 | 页面常规正文、默认输入框内容 |
| **lg** | `18px` | 1.5 | 600 | 卡片标题、模态框标题 |
| **xl** | `20px` | 1.25 | 600 | 模块一级标题 |
| **2xl** | `24px` | 1.25 | 700 | 页面主标题 (Page Header) |
| **3xl** | `30px` | 1.25 | 700 | 核心大数指标看板 (Hero Display) |

### 字体替代 (Font Substitutes)

若产品未获授权使用专有字体, 按下列开源替代落地, **并保持上方阶梯的字号/行高不变**:
- **主字体替代**: `Inter` 或 `Geist Sans` (若为 system-ui 栈则无需替代);
- **等宽替代**: `JetBrains Mono` 或 `Geist Mono`;
- 替代字体的字重映射保持 400/500/600/700, 不得因替代而改用 300 或 800 (会破坏层级阶梯)。

### 数字与表格排版 (Numeric & Tabular Figures)

> 指标、价格、计数、ID 一律用**等宽数字 (tabular figures)** —— 否则数值列会随位数变化左右跳动, 这是数据类界面最扎眼的失序。

- **数字字体**: `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", sans-serif` (可与正文同族, 关键是开启等宽数字特性)
- **CSS 特性**: `font-variant-numeric: tnum 1, lnum 1` —— 对应 CSS 写法 `font-variant-numeric: tabular-nums lining-nums`
- **数值列对齐**: `right` (右对齐, 让个位对齐个位)
- **单位与数值同字号同字重**, 单位用 `#64748B` 弱化, 不得缩小字号
- **大数指标** 用 `30px` 档 + tabular; **严禁**对数字使用比例数字 (proportional), 也不要用 letter-spacing 撑开数字
- **时长/百分比/金额** 一律保留固定小数位 (如 `12.50%`), 不要因数值大小改变精度

---

## 4. 图标系统标准 (Iconography)

- **推荐图标库**: `Lucide Icons / Heroicons`
- **描边规格 (Stroke Width)**: `1.75px`
- **尺寸规格**:
  - `sm`: `16px` (内嵌在文字或小型按钮中)
  - `md`: `20px` (标准导航栏与操作按钮)
  - `lg`: `24px` (卡片头部与提示标识)
  - `xl`: `32px` (空状态插图或重点主图)
- **纪律**: 图标颜色一律继承相邻文字色; 严禁给图标单独现编颜色。

---

## 5. 间距与圆角阶梯 (Spacing & Radius Scale)

> 间距与圆角一律取档位 token, **严禁现编数值** —— 现编是「间距无系统」的直接来源。

| 档位 | 间距 (spacing) | 圆角 (rounded) | 典型用途 |
| :--- | :--- | :--- | :--- |
| xxs | `4px` | `4px` | 图标与文字内联微间隙 / 状态小徽章 |
| xs | `8px` | `6px` | 紧凑控件内边距 / 行内标签 |
| sm | `12px` | `8px` | 表单内边距 / 全部按钮与输入框 |
| md | `16px` | `12px` | 卡片内边距 / 列表项间距 |
| lg | `24px` | `16px` | 区块内元素间距 / 面板容器 |
| xl | `32px` | `24px` | 卡片间距 / 大型容器 |
| xxl | `48px` | `9999px` | 区块外间距 / 胶囊按钮与标签 |
| section | `96px` | `9999px` | 页面级分区间距 / 头像圆形 |

---

## 6. 布局与网格 (Layout: Grid & Container)

- **栅格**: `12` 列, 列间距 `24px` (解析值 `24px`), 容器最大宽 `1200px` (内容居中); 全出血区块不受此限。
- **卡片网格降级**: 桌面 3 列 → 平板 (≤1024px) 2 列 → 移动 (≤768px) 1 列; 严禁固定列宽。
- **区块内元素间距**: `24px`; **区块之间**: `96px`; **卡片内边距**: `24px`。
- **留白哲学**: 分组靠留白与对齐 (Gestalt 邻近性), **不靠给每块画边框卡片**。留白本身承担分隔职责 —— 先加间距, 间距不够再考虑边框。

---

## 7. 图片与插画几何 (Photography & Illustration Geometry)

- **Hero 大图**: 全出血、不圆角 (圆角只给内联卡片图); 保持宽高比, 严禁裁切主体。
- **卡片图**: `12px` 圆角 + 保持宽高比; 图文间距取 `16px`。
- **头像/圆形元素**: `9999px`; 尺寸 32–40px。
- **加载**: 一律 `loading="lazy"` + 响应式 `srcset`/`sizes`; 图片容器预留尺寸, 避免加载抖动 (CLS)。
- **纪律**: 图片是内容不是装饰; 严禁用装饰性插画填充空白来「显得丰富」。

---

## 8. 层级与深度 (Elevation & Decorative Depth)

| 层级 | 处理手法 | 用途 |
| :--- | :--- | :--- |
| L0 (平面) | 无投影、无边框 | 正文、标题、页脚等非容器内容 |
| L1 (基础抬升) | 表面色 `#FFFFFF` + 1px `#E2E8F0` + 投影 `0 1px 3px rgba(0,0,0,0.05)` | 默认卡片、面板 |
| L2 (悬停抬升) | 边框转 `#CBD5E1` + 投影 `0 4px 6px -1px rgba(0,0,0,0.1)` | 悬停卡片、可点击列表项 |
| L3 (焦点环) | 2px `#93C5FD` 描边 | 聚焦输入框与按钮 (无障碍可见焦点) |

- 深度优先靠**表面色 + 发丝边框**建立, 投影只作辅助; 深色主题尤其忌讳重投影。
- **装饰深度默认关闭**: 本契约不引入装饰性渐变、聚光灯卡片或顶边高光; 若确需引入, 必须在任务 spec 中登记信息依据 (装饰必须承载信息, 否则删除)。
- **遮罩与背景模糊 (Scrim)**: 模态/抽屉遮罩取 `{scrim.color}` (`rgba(0,0,0,0.45)`), backdrop 模糊取 `{scrim.blur}` (`8px`); 吸顶导航同样走 `{scrim.blur}`。**严禁硬编码遮罩色** —— 它是全局一致性的一环。

### 层叠顺序 (Z-Index Ladder)

> 层叠冲突是前端最常见的「玄学 bug」。**严禁在业务组件里现编 z-index** —— 新增浮层必须先登记到本阶梯。

| 层 | z-index | 用途 |
| :--- | :--- | :--- |
| `base` | `0` | 常规文档流 |
| `dropdown` | `1000` | 下拉菜单、选择器面板 |
| `sticky` | `1020` | 吸顶导航、粘性表头 |
| `banner` | `1030` | 顶部横幅 / 公告条 (位于吸顶导航**之上**) |
| `overlay` | `1040` | 遮罩层 (模态与抽屉共用) |
| `modal` | `1050` | 模态框、侧边抽屉 |
| `popover` | `1060` | 模态之上的气泡提示 |
| `toast` | `1070` | 全局通知 (永远在最上层) |

- 数值必须**严格递增** (由 `design-gatekeeper.js` 机械校验), 保证任意两层顺序无歧义。
- 同一层内**严禁**靠 +1 微调抢占; 需要更高层就新增一层并登记。

---

## 9. 公共组件矩阵 (Component Matrix → 见 DESIGN_COMPONENTS.md)

> **完整组件矩阵已外移**至同目录 `DESIGN_COMPONENTS.md` (18 个组件族 × 全状态) —— 它体积翻倍会撑破本文件预算。
> **读取时机**: 构建或修改组件时读 `DESIGN_COMPONENTS.md`; 常规取色 / 取档位读 `AGENT_PROMPTS.md`; 本文件用于评审与追溯。
> **纪律**: 组件不重复字面量, 改 `colors.*` 即全局跟随; 组件状态**严禁**在业务里现编 (由 `design-gatekeeper.js` 按族校验齐备)。

---

## 10. 招牌组件 (Signature Components)

> 本方案特有的、命名了的组件。每个都带「只在 X 场景用」的边界 —— **越界复用即破坏设计语言的辨识度**。

**`stat-tile`** — 单指标大数卡: caption 标签 + 3xl 数字 + 同比说明
  - 🚧 边界: 只用于仪表盘顶部一排 3~4 张, 不要塞进普通列表

**`task-row`** — 任务行: 复选框 + 主文案 + 状态 badge
  - 🚧 边界: 悬停整行转 surfaceHover, 严禁给行加边框

**`empty-state`** — 空态: lg 图标 + 一行说明 + 一个主 CTA
  - 🚧 边界: 只在列表零数据时出现, 严禁复用为错误态

---

## 11. 动效与交互响应 (Motion & Interaction)

- **时长阶梯**: `fast` 120ms · `base` 200ms · `slow` 320ms
- **缓动曲线**: `standard` `cubic-bezier(0.2, 0, 0, 1)` · `emphasized` `cubic-bezier(0.3, 0, 0, 1.15)`

### 交互响应通道 (Interaction Channels)

- **悬停响应通道**: `color` —— 悬停反馈**只走这一个通道**。
- **焦点响应通道**: `ring` —— 键盘焦点反馈只走这一个通道。

> 🚧 **一态一通道铁律**: 同一个状态**严禁**同时变色 + 变边框 + 位移 + 加投影。
> 多通道叠加会让交互显得廉价且难以预测; 若悬停通道已定为 `color`, 其余通道保持静止。
> 状态过渡一律用 `base` 时长 + `standard` 缓动; 严禁对布局属性 (width/height/margin) 做过渡。

---

## 12. 响应式行为 (Responsive Behavior)

| 断点 | 宽度 | 关键变化 |
| :--- | :--- | :--- |
| Desktop-XL | 1440px | 默认桌面布局, 卡片网格 3–4 列 |
| Desktop | 1280px | 内容最大宽度收敛, 网格维持 |
| Tablet | 1024px | 卡片网格降为 2 列, 侧栏折叠 |
| Mobile-Lg | 768px | 导航转汉堡菜单, 表格转卡片/手风琴 |
| Mobile | 480px | 单列布局, `30px` 主标题降档至 `20px` |

- **触控目标**: 主要 CTA 与表单控件保持 ≥44px 点击高度。
- **图片行为**: 产品截图保持宽高比, 严禁裁切。

---

## 13. Do's and Don'ts (设计禁止清单)

> 设计层的坏味道纪律 —— 与代码坏味道门禁同构。**条目一律绑定到具体 token 路径**, 便于逐条核对。
> 其中「引用漂移 / 间距圆角缺失 / 语义色换色相 / 品牌色相超 3 家族 / 纯黑画布」由 `design-gatekeeper.js` 机械拦截。

### ✅ Do
- **强调稀缺**: `{colors.primary}` (`#2563EB`) 只用于主 CTA、焦点环、品牌标; 一屏只给一个强调 (Von Restorff)。
- **一屏一焦点**: 每屏必须有且只有一个视觉主角; 找不到主角即层级失败。
- **分组靠留白**: 用 `{spacing.lg}` (`24px`) / `{spacing.section}` (`96px`) 建立从属关系, 不给每块内容画边框卡片。
- **层级靠对比**: 靠尺寸 + 字重 + 颜色对比建立层级, 不靠加框与加装饰。
- **引用式取色**: 所有颜色一律走 `{colors.*}`, 组件内严禁出现字面色值。
- **语义专色专用**: `{colors.semantic.success.text}` (`#065F46`) / `{colors.semantic.warning.text}` (`#92400E`) / `{colors.semantic.error.text}` (`#991B1B`) 只出现在状态位置。
- **档位化间距圆角**: 间距只取 `{spacing.*}`, 圆角只取 `{rounded.*}` (按钮与输入框用 `{rounded.md}` (`8px`))。
- **数字等宽对齐**: 数值列用 `{numeric.fontFeature}` (`tnum 1, lnum 1`) + 右对齐 (`right`), 个位对齐个位。
- **对比度达标**: 文字对背景必须 ≥4.5:1 (WCAG AA); 深色主题若亮填充配白字不达标, 改用**深色文字** (本方案 `{colors.onPrimary}` = `{colors.onPrimary}` (`#FFFFFF`))。

### 🚫 Don't
- **不要滥用主色**: 严禁把 `{colors.primary}` (`#2563EB`) 用作区块背景或卡片填充 (它只属于主操作与焦点)。
- **不要挪用语义色**: 严禁把 `{colors.semantic.*}` 挪作装饰、品牌或分类着色 —— 通道一旦混用, 状态感知即失效。
- **不要用纯黑画布**: 本方案画布为 `{colors.background}` (`#F8FAFC`); 深色主题禁用 `#000000` (会带来死黑与光晕)。
- **不要随手 pill 圆角**: CTA 圆角必须落在 `{rounded.*}` 档位内, 严禁临时指定。
- **不要加装饰性渐变/高光**: 装饰必须承载信息, 否则删除 (见 §8)。
- **不要硬编码间距**: 严禁在组件里现编 px 值 —— 一律取 `{spacing.*}`。
- **不要多通道叠加响应**: 悬停只走 `color`, 焦点只走 `ring` (见 §11)。
- **不要现编 z-index**: 浮层一律取 `{zIndex.*}` (吸顶 `1020` / 模态 `1050` / 通知 `1070`), 严禁 +1 微调抢层。
- **不要对数字用比例字体**: 指标 / 价格 / 计数 / ID 必须 `font-variant-numeric: tabular-nums`, 否则数值列随位数跳动。
- **不要硬编码遮罩色**: 遮罩取 `{scrim.color}`, 模糊取 `{scrim.blur}`; 严禁在业务里写 `rgba(0,0,0,.5)` 这类字面量。
- **不要自创栅格**: 列数 / 列间距 / 容器宽一律取 `{grid.*}` (本方案 12 列 / 1200px)。

---

## 14. 高保真效果图资产索引 (Mockup Previews)

- **核心页面 1 (主控仪表盘)**: `specs/design/mockups/selected/index_dashboard.svg`
- **核心页面 2 (工作台与详情)**: `specs/design/mockups/selected/detail_workspace.svg`
- **全量组件全状态效果图**: `specs/design/mockups/selected/component_catalog.svg`

> 前端开发人员与智能体在构建 UI 时，应以以上效果图和 Tokens 为唯一视觉验收基准。
> 需要「从零搭新页面」时, 改读同目录的 `AGENT_PROMPTS.md` (色彩速查 + 示例 prompt + 构建顺序), 不要重复读本文件。

---

## 15. 迭代指南 (Iteration Guide)

1. **一次只改一个组件**, 并用 `components.*` 的 token 名指代它。
2. 引入新区块前, 先决定它落在哪一级 surface / elevation。
3. 正文默认 `16px` / 字重 400; 强调靠层级阶梯, 不靠加粗一切。
4. 改完执行 `bash framework/scripts/design-gate.sh check <目录>` —— 漂移与设计坏味道必须归零。
5. 新增变体作为独立 `components.*` 条目登记, 不覆盖既有条目。

---

## 16. 已知边界 (Known Gaps)

- 本契约由 `design-gate.sh generate` 机械生成, 覆盖色彩 / 排版 / 图标 / 间距 / 圆角 / 布局 / 图片 / 深度 / 组件 / 动效;
  **不覆盖**具体业务页面的信息架构与内容密度 —— 那属于 spec 与效果图评审范围。
- 组件错误态文案、空状态未在本契约内定义 (加载态见 §9 按钮 Loading), 需在具体页面 spec 中补充。
- 字体族为系统栈或开源替代 (见 §3 字体替代); 若产品使用专有字体, 需在此显式登记替代方案与降级栈。
- 招牌组件 (§10) 为本方案的典型组合, 非穷举; 业务特有组件在页面 spec 中登记。
