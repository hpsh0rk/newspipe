# newspipe - 组件全状态矩阵 (Component Matrix)

> **读取时机**: **构建或修改任何组件时读**。常规取色 / 取档位 / 查构建顺序读 `AGENT_PROMPTS.md` 即可;
> 契约规则与纪律见 `DESIGN_SYSTEM.md`。本文件是 `tokens.json.components` 的可读视图, 二者同源。
> **契约版本**: `1.0` (Ocean Minimalist) · 共 18 个组件族

> 🔒 **组件状态严禁在业务里现编** —— 每个族的全状态集由 `design-gatekeeper.js` 机械校验齐备。
> 表中「引用 → 解析值」体现单一真相源: 组件不重复字面量, 改 `colors.*` 即全局跟随。

---

## 按钮 (Button)

> 六态含 loading 提交态; 一屏只给一个 primary 强调

- **default**: bg `{colors.primary}` → `#2563EB` | text `{colors.onPrimary}` → `#FFFFFF` | border `transparent` | shadow `0 1px 2px rgba(0,0,0,0.05)`
- **hover**: bg `{colors.primaryHover}` → `#1D4ED8` | text `{colors.onPrimary}` → `#FFFFFF` | border `transparent` | shadow `0 2px 4px rgba(37,99,235,0.2)`
- **active**: bg `{colors.primaryActive}` → `#1E40AF` | text `{colors.onPrimary}` → `#FFFFFF` | border `transparent` | shadow `none`
- **focus**: bg `{colors.primary}` → `#2563EB` | text `{colors.onPrimary}` → `#FFFFFF` | border `{colors.focusRing}` → `#93C5FD` | ring `0 0 0 3px rgba(37,99,235,0.3)`
- **disabled**: bg `{colors.border}` → `#E2E8F0` | text `{colors.textTertiary}` → `#94A3B8` | border `transparent` | cursor `not-allowed`
- **loading**: bg `{colors.primary}` → `#2563EB` | text `{colors.onPrimary}` → `#FFFFFF` | border `transparent` | cursor `progress` | spinner `{colors.onPrimary}` → `#FFFFFF`

---

## 输入框 (Input)

> 五态含 error; 聚焦走 focusRing 描边

- **default**: bg `{colors.surface}` → `#FFFFFF` | border `{colors.borderHover}` → `#CBD5E1` | text `{colors.textPrimary}` → `#0F172A` | placeholder `{colors.textTertiary}` → `#94A3B8`
- **hover**: bg `{colors.surface}` → `#FFFFFF` | border `{colors.textTertiary}` → `#94A3B8` | text `{colors.textPrimary}` → `#0F172A` | placeholder `{colors.textSecondary}` → `#64748B`
- **focus**: bg `{colors.surface}` → `#FFFFFF` | border `{colors.primary}` → `#2563EB` | text `{colors.textPrimary}` → `#0F172A` | ring `0 0 0 3px rgba(37,99,235,0.15)`
- **error**: bg `{colors.semantic.error.bg}` → `#FEF2F2` | border `#EF4444` | text `{colors.semantic.error.text}` → `#991B1B` | ring `0 0 0 3px rgba(239,68,68,0.15)`
- **disabled**: bg `{colors.surfaceHover}` → `#F1F5F9` | border `{colors.border}` → `#E2E8F0` | text `{colors.textTertiary}` → `#94A3B8` | cursor `not-allowed`

---

## 选择控件 (Checkbox / Radio)

> indeterminate 半选是父级多选必备态

- **default**: bg `{colors.surface}` → `#FFFFFF` | border `{colors.borderHover}` → `#CBD5E1` | text `{colors.textPrimary}` → `#0F172A`
- **checked**: bg `{colors.primary}` → `#2563EB` | border `{colors.primary}` → `#2563EB` | text `{colors.onPrimary}` → `#FFFFFF`
- **indeterminate**: bg `{colors.primary}` → `#2563EB` | border `{colors.primary}` → `#2563EB` | text `{colors.onPrimary}` → `#FFFFFF`
- **disabled**: bg `{colors.surfaceHover}` → `#F1F5F9` | border `{colors.border}` → `#E2E8F0` | text `{colors.textTertiary}` → `#94A3B8` | cursor `not-allowed`

---

## 文本链接 (Text Link)

> hover 通道常与按钮不同 (下划线而非底色)

- **default**: text `{colors.primary}` → `#2563EB` | decoration `none`
- **hover**: text `{colors.primaryHover}` → `#1D4ED8` | decoration `underline`
- **active**: text `{colors.primaryActive}` → `#1E40AF` | decoration `underline`

---

## 卡片 (Card)

> 容器件; 靠表面色 + 发丝边框分层, 投影只作辅助

- **default**: bg `{colors.surface}` → `#FFFFFF` | border `{colors.border}` → `#E2E8F0` | shadow `0 1px 3px rgba(0,0,0,0.05)`
- **hover**: bg `{colors.surface}` → `#FFFFFF` | border `{colors.borderHover}` → `#CBD5E1` | shadow `0 4px 6px -1px rgba(0,0,0,0.1)`

---

## 状态徽章 (Badge)

> 语义色只在此处出现, 严禁挪作装饰

- **default**: bg `{colors.surfaceHover}` → `#F1F5F9` | text `#475569` | border `{colors.border}` → `#E2E8F0`
- **success**: bg `{colors.semantic.success.bg}` → `#ECFDF5` | text `{colors.semantic.success.text}` → `#065F46` | border `{colors.semantic.success.border}` → `#A7F3D0`
- **warning**: bg `{colors.semantic.warning.bg}` → `#FFFBEB` | text `{colors.semantic.warning.text}` → `#92400E` | border `{colors.semantic.warning.border}` → `#FDE68A`
- **error**: bg `{colors.semantic.error.bg}` → `#FEF2F2` | text `{colors.semantic.error.text}` → `#991B1B` | border `{colors.semantic.error.border}` → `#FECACA`

---

## 顶部导航 (Top Nav)

> 吸顶时取 sticky 层; 激活项用 primary 指示条

- **default**: bg `{colors.background}` → `#F8FAFC` | text `{colors.textSecondary}` → `#64748B` | border `{colors.border}` → `#E2E8F0` | height `56px` | zIndex `{zIndex.sticky}` → `1020`
- **linkHover**: text `{colors.primary}` → `#2563EB` | decoration `none`
- **linkActive**: text `{colors.textPrimary}` → `#0F172A` | indicator `{colors.primary}` → `#2563EB`
- **sticky**: bg `{colors.surface}` → `#FFFFFF` | border `{colors.border}` → `#E2E8F0` | zIndex `{zIndex.sticky}` → `1020` | backdropFilter `{scrim.blur}` → `8px`

---

## 页脚 (Footer)

> 深色页面也可用浅色页脚收尾 (marketing reset)

- **default**: bg `{colors.background}` → `#F8FAFC` | text `{colors.textSecondary}` → `#64748B` | border `{colors.border}` → `#E2E8F0`
- **linkHover**: text `{colors.textPrimary}` → `#0F172A` | decoration `underline`

---

## 标签页 (Tabs)

> 激活态用 primary 下划线, 不做整块填充

- **default**: text `{colors.textSecondary}` → `#64748B` | border `transparent`
- **hover**: text `{colors.textPrimary}` → `#0F172A` | border `transparent`
- **active**: text `{colors.primary}` → `#2563EB` | border `{colors.primary}` → `#2563EB`

---

## 模态框 (Modal)

> 遮罩 + 面板; 层叠走 overlay → modal

- **overlay**: bg `{scrim.color}` → `rgba(0,0,0,0.45)` | backdropFilter `{scrim.blur}` → `8px` | zIndex `{zIndex.overlay}` → `1040`
- **default**: bg `{colors.surface}` → `#FFFFFF` | text `{colors.textPrimary}` → `#0F172A` | border `{colors.border}` → `#E2E8F0` | rounded `{rounded.lg}` → `12px` | zIndex `{zIndex.modal}` → `1050`
- **close**: text `{colors.textSecondary}` → `#64748B` | hoverText `{colors.textPrimary}` → `#0F172A`

---

## 侧边抽屉 (Drawer)

> 与模态共用 overlay 层

- **overlay**: bg `{scrim.color}` → `rgba(0,0,0,0.45)` | backdropFilter `{scrim.blur}` → `8px` | zIndex `{zIndex.overlay}` → `1040`
- **default**: bg `{colors.surface}` → `#FFFFFF` | text `{colors.textPrimary}` → `#0F172A` | border `{colors.border}` → `#E2E8F0` | zIndex `{zIndex.modal}` → `1050`

---

## 气泡提示 (Tooltip)

> 反色 (文字色作底), 走 popover 层

- **default**: bg `{colors.textPrimary}` → `#0F172A` | text `{colors.background}` → `#F8FAFC` | rounded `{rounded.sm}` → `6px` | zIndex `{zIndex.popover}` → `1060`

---

## 全局通知 (Toast)

> 四语义态; 永远在最上层 (toast 层)

- **default**: bg `{colors.surface}` → `#FFFFFF` | text `{colors.textPrimary}` → `#0F172A` | border `{colors.border}` → `#E2E8F0` | rounded `{rounded.md}` → `8px` | zIndex `{zIndex.toast}` → `1070`
- **success**: bg `{colors.semantic.success.bg}` → `#ECFDF5` | text `{colors.semantic.success.text}` → `#065F46` | border `{colors.semantic.success.border}` → `#A7F3D0` | zIndex `{zIndex.toast}` → `1070`
- **warning**: bg `{colors.semantic.warning.bg}` → `#FFFBEB` | text `{colors.semantic.warning.text}` → `#92400E` | border `{colors.semantic.warning.border}` → `#FDE68A` | zIndex `{zIndex.toast}` → `1070`
- **error**: bg `{colors.semantic.error.bg}` → `#FEF2F2` | text `{colors.semantic.error.text}` → `#991B1B` | border `{colors.semantic.error.border}` → `#FECACA` | zIndex `{zIndex.toast}` → `1070`

---

## 数据表格 (Data Table)

> 行分隔只用 border; 数值列必须等宽右对齐

- **header**: bg `{colors.surfaceHover}` → `#F1F5F9` | text `{colors.textSecondary}` → `#64748B` | border `{colors.border}` → `#E2E8F0`
- **rowDefault**: bg `{colors.surface}` → `#FFFFFF` | text `{colors.textPrimary}` → `#0F172A` | border `{colors.border}` → `#E2E8F0`
- **rowHover**: bg `{colors.surfaceHover}` → `#F1F5F9` | text `{colors.textPrimary}` → `#0F172A` | border `{colors.border}` → `#E2E8F0`
- **rowSelected**: bg `{colors.primarySubtle}` → `#EFF6FF` | text `{colors.textPrimary}` → `#0F172A` | border `{colors.primary}` → `#2563EB`

---

## 头像 (Avatar)

> 圆形; 无图时用 fallback 态

- **default**: bg `{colors.surfaceHover}` → `#F1F5F9` | text `{colors.textSecondary}` → `#64748B` | rounded `{rounded.full}` → `9999px`
- **fallback**: bg `{colors.primarySubtle}` → `#EFF6FF` | text `{colors.primary}` → `#2563EB` | rounded `{rounded.full}` → `9999px`

---

## 代码块 (Code Block)

> 等宽字体; 不做语法高亮外的装饰

- **default**: bg `{colors.surfaceHover}` → `#F1F5F9` | text `{colors.textPrimary}` → `#0F172A` | border `{colors.border}` → `#E2E8F0` | rounded `{rounded.md}` → `8px` | font `{typography.monoFontFamily}` → `ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace`

---

## 骨架屏 (Skeleton)

> 加载占位; 严禁用 spinner 替代骨架

- **default**: bg `{colors.surfaceHover}` → `#F1F5F9` | rounded `{rounded.sm}` → `6px`
- **shimmer**: from `{colors.surfaceHover}` → `#F1F5F9` | to `{colors.border}` → `#E2E8F0`

---

## 空态 (Empty State)

> 图标 + 标题 + 说明; 只在零数据时出现, 不复用为错误态

- **default**: icon `{colors.textTertiary}` → `#94A3B8` | title `{colors.textPrimary}` → `#0F172A` | body `{colors.textSecondary}` → `#64748B`
