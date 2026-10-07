# newspipe - 智能体构建载荷 (Agent Build Payload)

> **读取时机**: 只在**从零生成新页面或新组件**时读; 日常改样式 / 调间距**不必读**本文件 (契约正文见 `DESIGN_SYSTEM.md`)。
> **分层依据**: 本文件是**派生载荷**, 与契约正文分离以守住常驻读取成本 (specs/design-contract-context-budget-2026-09-29.md)。
> **方案**: 方案 A: 现代通透极简 (Ocean Minimalist) · 契约版本 `1.0` (Ocean Minimalist)

---

## 1. 色彩速查 (Quick Color Reference)

```
页面背景 (Background)       #F8FAFC   # 全局画布底色
卡片/面板表面 (Surface)       #FFFFFF   # 承载内容的抬升面
悬停表面 (Surface Hover)    #F1F5F9   # 列表行 / 卡片 hover 态
默认边框 (Border)           #E2E8F0   # 1px 发丝边框与分割线
强边框 (Border Hover)      #CBD5E1   # 输入框 / 卡片 hover 边框
主文字 (Text Primary)      #0F172A   # 标题与正文
次文字 (Text Secondary)    #64748B   # 辅助说明、次级信息
弱文字 (Text Tertiary)     #94A3B8   # 占位符、禁用态
主 CTA (Primary)         #2563EB   # 主按钮与核心强调 (一屏只给一个)
CTA 悬停 (Primary Hover)  #1D4ED8   # 主按钮 hover
主色上的文字 (On Primary)     #FFFFFF   # 主按钮文字
焦点环 (Focus Ring)        #93C5FD   # 键盘焦点可见描边
成功 (Success)            #065F46   # 就绪 / 成功文本
告警 (Warning)            #92400E   # 告警文本
错误 (Error)              #991B1B   # 错误 / 危险文本
```

## 2. 档位速查 (Spacing / Radius / Type / Motion)

```
间距 spacing    xxs=4px  xs=8px  sm=12px  md=16px  lg=24px  xl=32px  xxl=48px  section=96px
圆角 rounded    xs=4px  sm=6px  md=8px  lg=12px  xl=16px  xxl=24px  pill=9999px  full=9999px
正文 body       16px / 字重 400 / 行高 1.5
卡片标题        18px / 字重 600
页面主标题      24px / 字重 700
时长 motion     fast=120ms  base=200ms  slow=320ms
缓动 easing     standard=cubic-bezier(0.2, 0, 0, 1)
响应通道        hover=color  focus=ring
数字 numeric    tnum 1, lnum 1 (font-variant-numeric: tabular-nums)  对齐=right
层叠 zIndex     dropdown=1000  sticky=1020  modal=1050  toast=1070
栅格 grid       12 列  gutter=24px  maxWidth=1200px
遮罩 scrim      color=rgba(0,0,0,0.45)  blur=8px
对比度红线      正文/次级/主色链接 ≥4.5:1 (AA)  ·  按钮瞬态态 ≥3:1  ·  占位符/禁用态豁免
```

## 3. 构建顺序 (Build Order)

按此顺序落地新页面, 每一步只解决一件事:

1. **铺底** — 背景 `#F8FAFC`, 主文字 `#0F172A`, 次文字 `#64748B`。
2. **建结构** — 容器用 `#FFFFFF` + 1px `#E2E8F0` 发丝边框; **不靠投影分层**。
3. **排文字** — 取 §2 排版档位; 正文一律常规字重, 强调靠层级阶梯而非加粗一切。
4. **点强调** — 主 CTA 用 `#2563EB`, hover 走 `#1D4ED8`, 文字 `#FFFFFF`; **一屏只给一个强调**。
5. **调间距** — 只取 spacing 档位 (卡片内边距 `24px`, 区块间距 `32px`, 分区间距 `96px`)。
6. **收细节** — 焦点态用 `#93C5FD` 可见描边; 语义色 (成功 `#065F46` / 告警 `#92400E` / 错误 `#991B1B`) **只用于状态, 不挪作装饰**。

## 4. 示例 Prompt (Example Prompts)

> 以下片段已内联解析后的色值, **可直接作为提示词使用**, 无需再查表推导。

**落地页特性区块 (Landing Section):**
"创建一个特性区块: 背景 `#F8FAFC`, 主标题 30px/字重 700/颜色 `#0F172A`; 副标题 16px/颜色 `#64748B`; 下方一个主按钮 (背景 `#2563EB`, 文字 `#FFFFFF`, 圆角 `8px`, 内边距 `12px 16px`, 悬停背景转 `#1D4ED8`); 区块上下留白 `96px`。"

**卡片网格 (Card Grid):**
"构建 3 列卡片网格 (容器最大宽 1200px): 每张卡片背景 `#FFFFFF`、1px `#E2E8F0` 边框、圆角 `12px`、内边距 `24px`; 卡片标题 18px/字重 600/颜色 `#0F172A`; 正文 14px/颜色 `#64748B`; 悬停时边框转 `#CBD5E1`, 其余通道保持静止。"

**表单区块 (Form Section):**
"设计一个表单: 输入框背景 `#FFFFFF`、边框 `#CBD5E1`、圆角 `8px`、内边距 `12px 16px`、占位文字 `#94A3B8`; 聚焦时边框转 `#2563EB` 并加光晕 `0 0 0 3px rgba(37,99,235,0.15)`; 提交按钮背景 `#2563EB`; 字段下方辅助文字 12px/颜色 `#94A3B8`。"

**顶部导航 (Navigation Bar):**
"创建顶部导航: 背景 `#F8FAFC`、底部 1px `#E2E8F0` 分隔、高度 56px; 左侧品牌字 16px/字重 600/颜色 `#0F172A`; 导航链接 14px/颜色 `#64748B`, 悬停转 `#2563EB`; 最右一个主 CTA 按钮 (背景 `#2563EB`, 文字 `#FFFFFF`)。吸顶时 `z-index: 1020`。"

**指标区与数据表格 (Metrics & Data Table):**
"构建指标区: 每张指标卡 = caption 标签 (12px/`#94A3B8`) + 大数 (30px/`#0F172A`/**等宽数字 `font-variant-numeric: tnum 1, lnum 1`**) + 同比说明; 数值列右对齐 (`text-align: right`), 单位与数值同字号仅弱化为 `#64748B`。表格行分隔用 1px `#E2E8F0`, 表头 14px/`#64748B`; 表格容器若吸顶则 `z-index: 1020`。"

**浮层与通知 (Modal / Toast):**
"模态框: 遮罩 `rgba(0,0,0,0.45)` + `z-index: 1040`; 面板背景 `#FFFFFF`、圆角 `12px`、内边距 `24px`、`z-index: 1050`; 模态内的气泡提示用 `z-index: 1060`。全局通知固定右下, `z-index: 1070` (永远最上层)。**严禁现编 z-index**, 一律取上表档位。"

**招牌组件 `stat-tile`:**
"按契约 §10 的 `stat-tile` 规格实现 —— 单指标大数卡: caption 标签 + 3xl 数字 + 同比说明。边界: 只用于仪表盘顶部一排 3~4 张, 不要塞进普通列表。"

## 5. 招牌组件速览 (Signature Components)

- `stat-tile` — 单指标大数卡: caption 标签 + 3xl 数字 + 同比说明
  - 🚧 边界: 只用于仪表盘顶部一排 3~4 张, 不要塞进普通列表
- `task-row` — 任务行: 复选框 + 主文案 + 状态 badge
  - 🚧 边界: 悬停整行转 surfaceHover, 严禁给行加边框
- `empty-state` — 空态: lg 图标 + 一行说明 + 一个主 CTA
  - 🚧 边界: 只在列表零数据时出现, 严禁复用为错误态

## 6. 组件态速查 (Component States Quick Ref)

> 常规改动读到这里就够了; **完整 18 族矩阵见 `DESIGN_COMPONENTS.md`** (构建/修改组件时读)。

```
按钮 Button     default=#2563EB  hover=#1D4ED8  active=#1E40AF  disabled=#E2E8F0  文字=#FFFFFF
输入框 Input    default边框=#CBD5E1  focus边框=#2563EB  error边框=#EF4444
选择 Checkbox   未选=#FFFFFF  选中=#2563EB  半选=#2563EB  禁用=#F1F5F9
链接 Link       默认=#2563EB  hover=#1D4ED8 (+下划线)
卡片 Card       底=#FFFFFF  边框=#E2E8F0  hover边框=#CBD5E1
徽章 Badge      成功=#065F46  告警=#92400E  错误=#991B1B
表格 Table      表头底=#F1F5F9  行hover底=#F1F5F9  行选中底=#EFF6FF
导航 Nav        底=#F8FAFC  链接hover=#2563EB  z=1020
模态 Modal      面板=#FFFFFF  遮罩=rgba(0,0,0,0.45)  z=1050
通知 Toast      底=#FFFFFF  成功底=#ECFDF5  z=1070
标签页 Tabs     active=#2563EB (下划线 #2563EB)
空态 Empty      图标=#94A3B8  标题=#0F172A
骨架 Skeleton   底=#F1F5F9
```

## 7. 交付前自查 (Pre-flight Checklist)

- [ ] 色值是否全部来自 §1 (无现编色)?
- [ ] 间距 / 圆角是否全部来自 §2 档位?
- [ ] 语义色是否只出现在状态位置 (未挪作装饰)?
- [ ] 悬停是否只用了 `color` 通道 (未叠加变色+变边框+位移)?
- [ ] 一屏是否只有一个视觉焦点?
- [ ] 数值是否等宽对齐 (`font-variant-numeric: tabular-nums` + 右对齐)?
- [ ] 浮层是否取 `{zIndex.*}` 档位而非现编数字?
- [ ] 遮罩是否取 `{scrim.*}` (未硬编码 rgba)? 栅格是否取 `{grid.*}`?
- [ ] 文字对背景是否 ≥4.5:1 (深色主题亮填充配白字不达标时, 是否已改深色文字)?
- [ ] 招牌组件是否越界复用 (对照 §5 边界)?
- [ ] 技术栈是否与 §8 契约一致 (框架/组件库/样式/动效未擅自替换)?
## 8. 技术栈 (Tech Stack)

框架=Python http.server 服务端模板 · 组件库=纯 CSS 设计系统(零依赖) · 样式=原生 CSS 单文件 · 动效=CSS 渐进增强(零JS)
来源: 用户拍板
