"""页面样式（纯 CSS，零外部请求；本地 JS 只增强交互）。

**设计契约**：`specs/design/DESIGN_SYSTEM.md`（方案 A「Ocean Minimalist」，AI 五方案选优
97.55 分）+ `specs/design/tokens.json`。气质 = 轻盈通透：浅色画布 + 高饱和科技蓝**单点**
强调（强调色几乎只出现在主 CTA、活动 Tab 与链接上），中性色全走 slate 阶，留白优先。

四条硬约束（由 `tests/test_history.py` / `tests/test_view.py` 钉死，改这里之前先读）：

1. **唯一脚本是代码拥有的 `/assets/workbench.js`**；禁止内联/外部/用户输入脚本。
2. **不许出现 `url(`** —— 没有外部字体/图标字体/data-URI；图标一律字符或 CSS 画。
3. **不许出现外部 `http://`** —— 自包含，断网/境内都能看。
4. **不许有「内容不可见」的入场动效** —— 无 `@starting-style`、关键帧不得从 `opacity:0`
   开始（两种写法实测都会让卡片/行停在不可见态；截图/打印必命中）。
   `table{}` 首条规则禁 `overflow/transform`（否则吸顶表头彻底失效，实测踩过）。

配色走 `color-scheme:light dark` + `prefers-color-scheme`，跟随系统深浅色；深色是同一套
slate 阶的反转（非简单反色），强调蓝提到 `#60A5FA` 保证对比度。动效只剩「不可能隐藏内容」
的三类：跨文档 Tab 交叉淡入（View Transitions）、悬停底色/边框过渡、聚焦环；
全部受 `@media (prefers-reduced-motion:reduce)` 关断。
"""

CSS = """
/* ══ 1. Design Tokens（Ocean Minimalist，specs/design/tokens.json）══════ */
:root{
  color-scheme:light dark;
  /* 中性：slate 阶 */
  --bg:#f8fafc; --surface:#fff; --surface-2:#f1f5f9; --surface-hover:#f8fafc;
  --border:#e2e8f0; --border-strong:#cbd5e1; --divider:#f1f5f9;
  --text:#0f172a; --text-2:#475569; --muted:#64748b; --faint:#94a3b8;
  /* 强调：高饱和科技蓝，只给主 CTA / 活动 Tab / 链接 */
  --accent:#2563eb; --accent-hover:#1d4ed8; --accent-active:#1e40af;
  --accent-soft:#eff6ff; --accent-fg:#fff; --focus-ring:rgba(37,99,235,.28);
  /* 语义（软底色 + 深字，pill / 横幅共用） */
  --ok:#065f46; --ok-soft:#ecfdf5; --ok-line:#a7f3d0;
  --warn:#92400e; --warn-soft:#fffbeb; --warn-line:#fde68a;
  --danger:#991b1b; --danger-soft:#fef2f2; --danger-line:#fecaca;
  --danger-solid:#dc2626;
  /* 形状 / 阴影 / 动效 */
  --radius:12px; --radius-sm:8px; --radius-xs:6px; --pill:999px;
  --shadow-1:0 1px 2px rgba(15,23,42,.05);
  --shadow-2:0 4px 12px -2px rgba(15,23,42,.10);
  --dur:.18s; --ease:cubic-bezier(.2,0,0,1);
  --font:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB",
         "Microsoft YaHei",Roboto,sans-serif;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
  --gap:16px;
  /* 导航栏高度**定死**：吸顶表头要贴在它下沿，靠字体度量算高度会错位（实测差 5.7px 就被盖住） */
  --nav-h:50px;
}
@media (prefers-color-scheme:dark){
  :root{
    --bg:#0b1220; --surface:#111a2c; --surface-2:#16213a; --surface-hover:#16213a;
    --border:#1f2b45; --border-strong:#2d3b5c; --divider:#182338;
    --text:#e8edf6; --text-2:#b6c2d6; --muted:#8fa0b8; --faint:#66788f;
    --accent:#60a5fa; --accent-hover:#93c5fd; --accent-active:#3b82f6;
    --accent-soft:#12233f; --accent-fg:#0b1220; --focus-ring:rgba(96,165,250,.32);
    --ok:#6ee7b7; --ok-soft:#0c2419; --ok-line:#1d4d38;
    --warn:#fcd34d; --warn-soft:#2a2110; --warn-line:#5c4a17;
    --danger:#fca5a5; --danger-soft:#2c1414; --danger-line:#5c2323;
    --danger-solid:#f87171;
    --shadow-1:0 1px 2px rgba(0,0,0,.4);
    --shadow-2:0 6px 16px -4px rgba(0,0,0,.5);
  }
}

/* ══ 2. 基础 ═══════════════════════════════════════════════════════════ */
*{box-sizing:border-box}
[hidden]{display:none!important}
html{scroll-behavior:smooth}
body{
  font:14px/1.65 var(--font); margin:0 auto; padding:0 24px 72px; max-width:1200px;
  background:var(--bg); color:var(--text);
  -webkit-font-smoothing:antialiased; text-rendering:optimizeLegibility;
}
h1{font-size:22px; line-height:1.3; margin:28px 0 8px; letter-spacing:-.015em; font-weight:650}
h2{font-size:15px; margin:34px 0 10px; letter-spacing:-.005em; font-weight:600;
   display:flex; align-items:baseline; gap:8px; flex-wrap:wrap}
h3{font-size:13px; margin:18px 0 8px; font-weight:600}
p{margin:0 0 10px}
a{color:var(--accent); text-decoration:none; text-underline-offset:2px;
  transition:color var(--dur) var(--ease)}
a:hover{text-decoration:underline}
a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,
summary:focus-visible{outline:2px solid var(--accent); outline-offset:2px; border-radius:var(--radius-xs)}
code{font:12px/1.5 var(--mono); background:var(--surface-2); border:1px solid var(--divider);
     border-radius:var(--radius-xs); padding:1px 5px; overflow-wrap:anywhere}
::selection{background:var(--accent-soft); color:var(--text)}
.sub{color:var(--muted); font-size:12.5px; margin:0 0 18px; line-height:1.7}
.sub code{background:transparent; border:0; padding:0}
.note{font-size:12px; color:var(--text-2); line-height:1.7; margin:4px 0}
.hint{font-size:12px; color:var(--faint); font-weight:400}
h2 .hint,h3 .hint{margin-left:2px}

/* ══ 3. Tab 导航（吸顶，毛玻璃）════════════════════════════════════════ */
nav.tabs{
  position:sticky; top:0; z-index:20; display:flex; gap:4px; flex-wrap:wrap;
  height:var(--nav-h); align-items:flex-end; box-sizing:border-box;
  margin:0 -24px 0; padding:10px 24px 0; background:color-mix(in srgb,var(--bg) 86%,transparent);
  backdrop-filter:saturate(160%) blur(14px); -webkit-backdrop-filter:saturate(160%) blur(14px);
  border-bottom:1px solid var(--border); view-transition-name:tabs;
}
nav.tabs a.tab{
  position:relative; padding:9px 14px 10px; color:var(--muted); font-size:13.5px; font-weight:500;
  border-radius:var(--radius-sm) var(--radius-sm) 0 0; text-decoration:none;
  transition:color var(--dur) var(--ease),background var(--dur) var(--ease);
}
nav.tabs a.tab:hover{color:var(--text); background:var(--surface-2)}
nav.tabs a.tab.on{color:var(--accent); font-weight:600}
nav.tabs a.tab.on::after{
  content:""; position:absolute; inset:auto 12px -1px; height:2px;
  background:var(--accent); border-radius:2px 2px 0 0;
}

/* ══ 4. KPI 统计块 / 卡片 ══════════════════════════════════════════════ */
.stats{display:grid; grid-template-columns:repeat(auto-fit,minmax(118px,1fr));
       gap:var(--gap); margin:0 0 8px}
.stat{
  background:var(--surface); border:1px solid var(--border); border-radius:var(--radius);
  padding:14px 16px; box-shadow:var(--shadow-1);
  transition:border-color var(--dur) var(--ease),box-shadow var(--dur) var(--ease);
}
.stat:hover{border-color:var(--border-strong); box-shadow:var(--shadow-2)}
.stat b{display:block; font-size:24px; line-height:1.25; letter-spacing:-.02em;
        font-weight:650; font-variant-numeric:tabular-nums}
.stat span{font-size:11.5px; color:var(--muted)}
.stat.attn{border-color:var(--accent); background:var(--accent-soft)}
.stat.attn b,.stat.attn span{color:var(--accent)}
.stat.danger{border-color:var(--danger-line); background:var(--danger-soft)}
.stat.danger b,.stat.danger span{color:var(--danger)}
a.statlink{text-decoration:none; display:block}
a.statlink:hover{text-decoration:none}
a.statlink:hover .stat{border-color:var(--accent); box-shadow:var(--shadow-2)}
.grid{display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:var(--gap)}
.card{background:var(--surface); border:1px solid var(--border); border-radius:var(--radius);
      padding:14px 16px; box-shadow:var(--shadow-1)}
.card h3{margin:0 0 8px}
.cand{background:var(--surface); border:1px solid var(--border); border-radius:var(--radius-sm);
      padding:10px 12px; margin:8px 0}
.cand code{word-break:break-all; background:transparent; border:0; padding:0}

/* ══ 5. 表格 ═══════════════════════════════════════════════════════════ */
/* 注意：**不能**给 table 加 `overflow:hidden`（裁圆角的错误做法）——
   那会让表格自己成为滚动容器，`thead th` 的 `position:sticky` 失去参照、彻底不生效。
   圆角由首末单元格承担；也不许 transform/translate（祖先位移会推走吸顶表头）。 */
table{border-collapse:separate; border-spacing:0; width:100%; margin:0 0 4px;
      background:var(--surface); border:1px solid var(--border); border-radius:var(--radius);
      box-shadow:var(--shadow-1)}
thead th:first-child{border-top-left-radius:var(--radius)}
thead th:last-child{border-top-right-radius:var(--radius)}
tbody tr:last-child td:first-child{border-bottom-left-radius:var(--radius)}
tbody tr:last-child td:last-child{border-bottom-right-radius:var(--radius)}
th,td{text-align:left; padding:9px 12px; border-bottom:1px solid var(--divider); vertical-align:top}
thead th{
  position:sticky; top:var(--nav-h); z-index:5; background:var(--surface-2);
  font-size:11.5px; font-weight:600; color:var(--muted); letter-spacing:.02em;
  white-space:nowrap;
}
tbody tr{transition:background var(--dur) var(--ease)}
tbody tr:hover{background:var(--surface-hover)}
tbody tr:last-child td{border-bottom:0}
td.note{font-size:12px; color:var(--text-2); max-width:46ch}
td .hint,td.hint{font-size:12px; color:var(--muted)}
.barcell{white-space:nowrap; font-variant-numeric:tabular-nums}

/* ══ 6. 趋势条（.bar / .bar-n / .trend .zero 类名被测试钉住）═══════════ */
.bar{display:inline-block; height:8px; min-width:2px; border-radius:var(--pill);
     background:linear-gradient(90deg,var(--accent),color-mix(in srgb,var(--accent) 45%,transparent));
     vertical-align:middle}
.bar-n{font-size:11px; color:var(--muted); margin-left:6px; font-variant-numeric:tabular-nums}
.trend .zero{opacity:.35}
.trend td{font-variant-numeric:tabular-nums}

/* ══ 7. 胶囊 / 状态 ════════════════════════════════════════════════════ */
.pill{display:inline-block; border-radius:var(--pill); padding:1px 9px; font-size:11.5px;
      line-height:1.7; white-space:nowrap; border:1px solid var(--border-strong);
      color:var(--text-2); background:var(--surface-2)}
.pill.ok{border-color:var(--ok-line); color:var(--ok); background:var(--ok-soft)}
.pill.warn{border-color:var(--warn-line); color:var(--warn); background:var(--warn-soft)}
.pill.danger{border-color:var(--danger-line); color:var(--danger); background:var(--danger-soft)}
.pill.muted{color:var(--muted); border-color:var(--border); background:transparent}
.srcrow summary .pill{margin:0 8px}

/* ══ 8. 表单 / 工具条 / 按钮 ═══════════════════════════════════════════ */
input:not([type]),input[type=text],input[type=number],input[type=time],select,textarea{
  font:inherit; font-size:12.5px; padding:6px 10px; border-radius:var(--radius-xs);
  border:1px solid var(--border-strong); background:var(--surface); color:var(--text);
  transition:border-color var(--dur) var(--ease),box-shadow var(--dur) var(--ease);
}
input[type=text]:hover,input[type=time]:hover,select:hover{border-color:var(--faint)}
input[type=text]:focus,input[type=time]:focus,select:focus,textarea:focus{border-color:var(--accent); outline:0;
  box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 15%,transparent)}
select:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
input[type=checkbox]{accent-color:var(--accent); width:15px; height:15px}
.toolbar{
  display:flex; gap:12px; align-items:center; flex-wrap:wrap; margin:0 0 16px;
  background:var(--surface); border:1px solid var(--border); border-radius:var(--radius);
  padding:12px 14px; box-shadow:var(--shadow-1);
}
.toolbar label{font-size:12px; color:var(--muted); display:flex; gap:6px; align-items:center}
label.f{margin:0; font-size:12px; display:inline-flex; align-items:center; gap:5px; color:var(--muted)}
label.f input[type=text],label.f select,label.f input[type=number]{min-width:96px}
fieldset{border:1px solid var(--border); border-radius:var(--radius); margin:0 0 14px;
         padding:12px 14px; background:var(--surface)}
legend{font-size:12px; color:var(--muted); padding:0 6px}
form.inline{display:inline-flex;align-items:center;gap:8px;margin:0}
.btn{
  display:inline-flex;align-items:center;justify-content:center;align-self:center;box-sizing:border-box;
  min-height:36px;white-space:nowrap;text-decoration:none;
  font:inherit; font-size:12.5px;line-height:20px; padding:6px 12px; border-radius:var(--radius-xs); cursor:pointer;
  border:1px solid var(--border-strong); background:var(--surface); color:var(--text-2);
  transition:background var(--dur) var(--ease),border-color var(--dur) var(--ease),
             color var(--dur) var(--ease),box-shadow var(--dur) var(--ease);
}
.btn:hover{border-color:var(--accent); color:var(--accent); background:var(--accent-soft)}
.btn:active{transform:translateY(1px)}
.btn:focus-visible{box-shadow:0 0 0 3px var(--focus-ring)}
.btn.primary{border-color:transparent; color:var(--accent-fg); background:var(--accent);
             box-shadow:var(--shadow-1)}
.btn.primary:hover{background:var(--accent-hover); color:var(--accent-fg)}
.btn.primary:active{background:var(--accent-active)}
.btn.danger{border-color:var(--danger-line); color:var(--danger); background:transparent}
.btn.danger:hover{background:var(--danger-soft); border-color:var(--danger-solid); color:var(--danger-solid)}
.actions{white-space:nowrap}

/* ══ 9. 折叠区 / 诊断抽屉（`<details>`，纯 CSS 箭头旋转）═══════════════ */
details.drawer,details.srcrow{
  background:var(--surface); border:1px solid var(--border); border-radius:var(--radius);
  padding:0 14px; margin:8px 0; box-shadow:var(--shadow-1);
}
details.drawer[open],details.srcrow[open]{padding-bottom:12px}
details.drawer summary,details.srcrow summary{
  cursor:pointer; font-size:12.5px; color:var(--text-2); padding:11px 0;
  list-style:none; display:flex; align-items:center; gap:7px;
  transition:color var(--dur) var(--ease);
}
details.drawer summary::-webkit-details-marker,
details.srcrow summary::-webkit-details-marker{display:none}
details.drawer summary::before,details.srcrow summary::before{
  content:""; width:0; height:0; flex:none;
  border-left:5px solid currentColor; border-top:4px solid transparent;
  border-bottom:4px solid transparent; opacity:.55;
  transition:transform var(--dur) var(--ease);
}
details.drawer[open] summary::before,details.srcrow[open] summary::before{transform:rotate(90deg)}
details.drawer summary:hover,details.srcrow summary:hover{color:var(--accent)}
details.drawer[open] summary,details.srcrow[open] summary{
  border-bottom:1px solid var(--divider); margin-bottom:10px}
/* 信源单行健康条：summary 里 code 与 pill 紧排，hint 补一句人话 */
details.srcrow summary{color:var(--text)}
details.srcrow summary code{background:transparent; border:0; padding:0; font-size:13px}
details.srcrow .note{margin:6px 0}
details.drawer.deep summary{font-weight:600; color:var(--text-2)}

/* ══ 10. 横幅 / 空态 / 条目摘要 / 页脚 ═════════════════════════════════ */
.banner{border:1px solid var(--border-strong); border-radius:var(--radius);
        padding:12px 14px; margin:0 0 16px; font-size:12.5px; background:var(--surface)}
.banner.ok{border-color:var(--ok-line); background:var(--ok-soft); color:var(--ok)}
.banner.err{border-color:var(--danger-line); background:var(--danger-soft); color:var(--danger)}
.banner pre{white-space:pre-wrap; word-break:break-all; margin:6px 0 0; font-size:11.5px;
            font-family:var(--mono); opacity:.9}
.empty{color:var(--muted); font-size:12.5px; padding:14px 2px}
.clamp2{display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden;
        margin-top:2px; max-width:60ch}
footer{margin-top:40px; padding-top:14px; border-top:1px solid var(--border);
       font-size:11.5px; color:var(--faint); line-height:1.8}
footer code{background:transparent; border:0; padding:0}

/* ══ 11. 动效（全部渐进增强、全部可关断、全部**不可能隐藏内容**）══════ */
/* 跨文档 Tab 切换：浏览器原生交叉淡入，Firefox 未支持时退化成普通跳转 */
@view-transition{navigation:auto}
::view-transition-group(tabs){animation-duration:.001s}
/* 不做入场动画：`transition + @starting-style` 与 `animation + fill:both` 两种写法
   实测都会把内容**永久**留在不可见态（动画时钟不推进 ⇒ 停在 from 帧）。数据面板里
   「内容看不见」比「没有动效」糟得多 —— 所以只保留悬停/聚焦/抽屉箭头这类状态过渡。 */
.card,details.drawer,details.srcrow,.toolbar,.banner,table,.stat{
  transition:border-color var(--dur) var(--ease),box-shadow var(--dur) var(--ease);
}
@media (prefers-reduced-motion:reduce){
  html{scroll-behavior:auto}
  *,*::before,*::after{
    animation-duration:.001ms !important; animation-iteration-count:1 !important;
    transition-duration:.001ms !important; scroll-behavior:auto !important;
  }
}

/* ══ 12. 窄屏（手机上偶尔点）═══════════════════════════════════════════ */
@media (max-width:640px){
  :root{--nav-h:44px}
  body{padding:0 14px 56px; font-size:13.5px}
  nav.tabs{margin:0 -14px; padding:8px 14px 0; overflow-x:auto; flex-wrap:nowrap}
  nav.tabs a.tab{padding:8px 12px; font-size:13.5px; white-space:nowrap}
  h1{font-size:19px}
  .stats{grid-template-columns:repeat(auto-fit,minmax(96px,1fr))}
  .toolbar{gap:8px}
  td.note{max-width:none}
  .clamp2{max-width:none}
}
/* Workbench: shared hierarchy, distinct collection/delivery controls. */
body{background:radial-gradient(ellipse at 95% 0,var(--accent-soft),transparent 45%),var(--bg)}
.eyebrow{font:11px/1.5 var(--mono);letter-spacing:.14em;color:var(--accent);margin:0 0 8px}
.lead{font-size:18px;line-height:1.6;color:var(--text-2);max-width:720px}
.page-intro,.section-heading{display:flex;align-items:center;justify-content:space-between;gap:16px;margin:24px 0}
.section-heading h2{margin:0}
.source-filters{display:flex;align-items:end;flex-wrap:wrap;gap:12px;margin:24px 0}
.source-filters label{display:grid;gap:4px;color:var(--text-2);font-size:12px;flex:1;min-width:130px}
.source-filters input,.source-filters select{width:100%;min-width:0}
.source-list{display:grid;gap:12px}
.source-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:16px;padding:20px 24px;border:1px solid var(--border);background:var(--surface);border-radius:var(--radius)}
.source-row h3{font-size:16px;margin:0 0 4px}
.source-row p{overflow-wrap:anywhere;font-size:13px}
.source-state{display:grid;align-content:start;justify-items:end;gap:8px;font-size:12px;color:var(--muted)}
.row-actions{display:flex;align-items:center;flex-wrap:wrap;gap:8px;grid-column:1/-1}
dialog{max-width:min(480px,calc(100vw - 40px));box-sizing:border-box;padding:24px;border:1px solid var(--border);border-radius:var(--radius);background:var(--surface);color:var(--text);box-shadow:var(--shadow-1)}
dialog::backdrop{background:rgb(0 0 0 / .4)}
dialog h2{margin-top:0}
dialog p{overflow-wrap:anywhere}
dialog .row-actions{margin-top:20px}
#editor-dialog{width:min(1040px,calc(100vw - 40px));max-width:1040px;max-height:calc(100dvh - 40px);padding:0;overflow:auto}
#editor-dialog:is([data-editor-kind=policy],[data-editor-kind=global],[data-editor-kind=rsshub],[data-editor-kind=acquisition]){width:min(760px,calc(100vw - 40px))}
#editor-dialog[data-editor-kind=source-copy]{width:min(520px,calc(100vw - 40px))}
.editor-panel{margin:20px 0;min-width:0}
#editor-dialog .editor-panel{margin:0;padding:20px 24px}
.editor-heading{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:16px;position:sticky;top:0;background:var(--surface);z-index:2;padding:8px 0}
.editor-heading h2{margin:0;font-size:20px}
.editor-cancel{margin-top:16px}
#editor-dialog .workbench-grid{grid-template-columns:minmax(0,3fr) minmax(210px,1fr)}
#editor-dialog .rule-aside{top:70px}
.source-editor,.policy-editor,.rss-panel{margin-top:28px}
.source-editor>summary,.policy-editor>summary,.rss-panel>summary{font-size:16px;font-weight:600}
.workbench-grid{display:grid;grid-template-columns:minmax(0,3fr) minmax(240px,1fr);gap:24px;margin-top:20px}
.workbench-grid>*{min-width:0}
.editor{min-width:0;padding:20px 24px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius)}
.editor fieldset{border:0;padding:16px 0;margin:0 0 12px;min-width:0}
.editor legend{font-weight:600;font-size:15px;color:var(--text)}
.editor details{margin:12px 0;padding:8px 0;border-top:1px solid var(--divider)}
.field-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.field-grid>details{grid-column:1/-1}
.field-grid-link{grid-column:1/-1}
.boolean-field{align-content:start}
.boolean-field .field-label{min-height:38px}
.boolean-field input[type=checkbox]{flex:none;margin:0}
.acquisition-panel{border:1px solid var(--border);border-radius:var(--radius);background:var(--surface);margin:16px 0}
.field{display:grid;gap:6px;min-width:0;margin:0 0 12px;color:var(--text-2);font-size:13px}
.field input:not([type=checkbox]),.field select{width:100%;min-width:0;font-size:14px}
.field small{font-size:12px;color:var(--muted);line-height:1.6}
.field-error{color:var(--danger)!important}
[aria-invalid=true]{border-color:var(--danger)!important}
.check{display:flex;align-items:center;gap:8px;font-size:13px;line-height:1.6}
.slot-options{display:flex;flex-wrap:wrap;gap:12px}
.override{padding:12px 0;border-bottom:1px solid var(--divider)}
.override>label{display:flex;align-items:center;justify-content:space-between;gap:12px;font-size:13px}
.override [data-custom]{margin-top:12px}
.form-actions{display:flex;align-items:center;flex-wrap:wrap;gap:12px;margin:24px 0 0;padding-top:16px;border-top:1px solid var(--border)}
.form-actions .check{margin-right:auto}
.save-actual{display:none}
form:has([name=dry]:not(:checked)) .save-dry{display:none}
form:has([name=dry]:not(:checked)) .save-actual{display:inline}
.rule-aside{position:sticky;top:calc(var(--nav-h) + 20px);align-self:start;padding:24px;border-left:2px solid var(--accent);background:var(--accent-soft);border-radius:var(--radius-sm)}
.rule-aside h3{font-size:16px;margin:0 0 12px}
.global-panel{padding:24px;background:linear-gradient(130deg,var(--accent-soft),var(--surface));border:1px solid var(--border);border-radius:var(--radius);margin-top:28px}
.global-panel h2{margin-top:0}
.global-panel .editor{background:var(--surface);margin-top:16px}
.rsshub-panel{padding:20px 24px;margin:24px 0;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius)}
.rsshub-panel h2{margin-top:0}
.rsshub-panel .receipt{margin:16px 0}
.rsshub-panel details{margin-top:16px}
.rsshub-panel summary{font-weight:600;color:var(--accent)}
.rsshub-panel .editor{margin-top:16px;background:var(--surface-2)}
.rsshub-panel .banner{overflow-wrap:anywhere}
.summary-strip{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:16px 20px}
.summary-strip>div{min-width:0}
.summary-strip h2{margin:0 0 6px}
.summary-strip p{margin:4px 0}
.field-label{display:flex;align-items:center;gap:6px}
.help,.editor .help{display:inline-block;position:relative;margin:0 0 0 6px;padding:0;border:0;vertical-align:middle;font-weight:400}
.help>button{display:inline-flex;align-items:center;justify-content:center;width:24px;height:24px;padding:0;border:1px solid var(--border-strong);border-radius:50%;background:var(--surface);color:var(--muted);font:12px var(--font);cursor:help}
.help>button:hover{color:var(--accent);border-color:var(--accent)}
.help>[role=tooltip]{position:fixed;inset:auto;left:max(8px,calc(50vw - 160px));bottom:16px;margin:0;width:min(320px,calc(100vw - 16px));max-height:calc(100dvh - 16px);overflow:auto;padding:12px 14px;border:1px solid var(--border-strong);border-radius:var(--radius-sm);background:var(--text);color:var(--surface);box-shadow:var(--shadow-2);font:12px/1.7 var(--font);white-space:normal;overflow-wrap:anywhere;z-index:50}
.field .help [role=tooltip]{color:var(--surface)}
.policy-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px}
.policy-card{padding:24px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius)}
.policy-card h3{font-size:16px;margin-top:0}
.policy-card p{margin:12px 0;font-size:13px;overflow-wrap:anywhere}
.notice{padding:16px;background:var(--warn-soft);border:1px solid var(--warn-line);border-radius:var(--radius-sm);color:var(--warn)}
.slot-edit-row{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:end;gap:8px 12px;padding:12px 0;border-bottom:1px solid var(--divider)}
.slot-edit-row label{display:grid;gap:4px;font-size:12px}
.slot-edit-row input{width:100%;min-width:0}
.slot-references{grid-column:1/-1;margin:0!important;padding:0!important;border:0!important}
.slot-references summary{font-size:12px;color:var(--muted)}
.slot-options .check{padding:8px 12px;border:1px solid var(--border);border-radius:var(--radius-sm)}
.slot-options .check:has(:checked){background:var(--accent-soft);border-color:var(--accent)}
.time-edit-row{display:flex;align-items:center;gap:8px;margin:8px 0}
.time-edit-row input{flex:1;min-width:0;width:100%}
input[type=time]{min-height:38px}
.policy-parameters{display:grid;grid-template-columns:minmax(120px,1fr) 1.4fr;gap:6px 16px;margin:12px 0}
.policy-parameters dt{color:var(--muted);font-size:12px}
.policy-parameters dd{margin:0;overflow-wrap:anywhere}
[data-digest-times]{grid-column:1/-1}
[data-editor-drafts]{display:flex;flex-wrap:wrap;align-items:center;gap:8px}
.feed-result{display:grid;gap:4px;padding:16px;margin:8px 0;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-sm)}
.feed-result span,.feed-result small{color:var(--muted);font-size:12px}
.feed-result code{overflow-wrap:anywhere;white-space:normal}
#action-result:focus{outline:2px solid var(--accent);outline-offset:3px}
#action-result h3{margin-top:0}
.editor[aria-busy=true]{opacity:.75}
.notification-status,.operation-row,.preview-result{padding:20px 24px;margin:20px 0;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius)}
.notification-status h2,.operation-row h3,.preview-result h2{margin-top:0}
.notify-form{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:12px 0}
.receipt{display:grid;grid-template-columns:auto minmax(0,1fr);gap:8px 16px;font-size:12px}
.receipt dd{margin:0;overflow-wrap:anywhere}
.receipt code{white-space:normal}
.preview-result article{border-top:1px solid var(--border);padding:12px 0}
@media(max-width:900px){.workbench-grid,#editor-dialog .workbench-grid{grid-template-columns:1fr}.rule-aside{position:static}}
@media(max-width:640px){
 .btn{min-height:44px}
 #editor-dialog,#editor-dialog[data-editor-kind]{width:calc(100vw - 16px);max-height:calc(100dvh - 16px)}
 #editor-dialog .editor-panel{padding:12px}
 nav.tabs{padding:8px 8px 0;gap:0;overflow:visible}
 nav.tabs a.tab{font-size:12.5px;padding:8px;flex:1;text-align:center}
 .source-row{grid-template-columns:minmax(0,1fr);padding:16px;gap:8px}
 .source-state{justify-items:start;display:flex;align-items:center}
 .page-intro,.section-heading{align-items:start;flex-wrap:wrap}
 .editor,.global-panel,.policy-card,.rsshub-panel{padding:16px}
 .field-grid{grid-template-columns:1fr}
 .policy-grid{grid-template-columns:minmax(0,1fr)}
 .summary-strip{align-items:start;flex-wrap:wrap}
 .override>label{align-items:start;flex-direction:column}
 td,th{overflow-wrap:anywhere}
 .lead{font-size:16px}
}
"""
