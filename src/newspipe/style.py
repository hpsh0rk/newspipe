"""页面样式（纯 CSS）。

三条硬约束（由 `tests/test_history.py` / `tests/test_view.py` 钉死，改这里之前先读）：

1. **不许出现 `<script`** —— 页面零 JS。
2. **不许出现 `url(`** —— 所以没有外部字体、没有图标字体、没有 data-URI 背景；
   图标一律用字符或 CSS 画（`::before` 三角、渐变、`border` 技巧）。
3. **不许出现外部 `http://`** —— 自包含，断网/境内都能看。

动效因此全部走**平台原生能力**，并且都是渐进增强 —— 不支持的浏览器只是没有动画，不会坏：

- **View Transitions**（`@view-transition{navigation:auto}`）：跨文档的 Tab 切换交叉淡入。
  Chrome 126+ / Safari 18.2+ 原生支持；Firefox 未支持 ⇒ 退化成普通跳转（无破损态）。
- **`@starting-style`**：首屏卡片/行的入场过渡（Chrome 117+ / Safari 17.4+ / Firefox 129+）。
- **`animation-timeline:view()`**：长表格随滚动渐显（Chromium / Safari 26+；其余忽略该行）。
- 所有动效统一受 `@media (prefers-reduced-motion:reduce)` 关断。

配色走 `color-scheme:light dark` + `prefers-color-scheme`，跟随系统深浅色，不引第三方主题。
"""

CSS = """
/* ══ 1. 设计令牌 ═══════════════════════════════════════════════════════ */
:root{
  color-scheme:light dark;
  --bg:#f5f5f7; --surface:#fff; --surface-2:#fbfbfc; --sunken:#f0f0f3;
  --border:#e4e4e9; --border-strong:#cdcdd6;
  --text:#16161a; --text-2:#4b4b55; --muted:#6f6f7a; --faint:#9c9ca8;
  --accent:#0d7a6f; --accent-fg:#fff; --accent-soft:#e4f4f1; --accent-line:#9ed9d1;
  --ok:#14713f; --ok-soft:#e5f4ea;
  --warn:#8f5b06; --warn-soft:#fdf2e0;
  --danger:#ac1f16; --danger-soft:#fdebe9;
  --radius:10px; --radius-sm:6px; --pill:999px;
  --shadow-1:0 1px 2px rgba(16,24,40,.06);
  --shadow-2:0 8px 24px -10px rgba(16,24,40,.18);
  --font:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB",
         "Microsoft YaHei",Roboto,sans-serif;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
  --dur:.16s; --ease:cubic-bezier(.4,0,.2,1); --gap:12px;
  /* 导航栏高度**定死**：吸顶表头要贴在它下沿，靠字体度量算高度会错位（实测差 5.7px 就被盖住） */
  --nav-h:50px;
}
@media (prefers-color-scheme:dark){
  :root{
    --bg:#0b0b0d; --surface:#151518; --surface-2:#1a1a1e; --sunken:#212126;
    --border:#2b2b32; --border-strong:#3c3c45;
    --text:#ebebf0; --text-2:#b3b3bd; --muted:#8c8c97; --faint:#6a6a75;
    --accent:#4fd1c0; --accent-fg:#04211d; --accent-soft:#12302c; --accent-line:#2b6d64;
    --ok:#5fd39a; --ok-soft:#12291f;
    --warn:#e5ae62; --warn-soft:#2a2113;
    --danger:#f4857c; --danger-soft:#2c1614;
    --shadow-1:0 1px 2px rgba(0,0,0,.45);
    --shadow-2:0 10px 28px -12px rgba(0,0,0,.7);
  }
}

/* ══ 2. 基础 ══════════════════════════════════════════════════════════ */
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{
  font:14px/1.62 var(--font); margin:0 auto; padding:0 24px 64px; max-width:1120px;
  background:var(--bg); color:var(--text);
  -webkit-font-smoothing:antialiased; text-rendering:optimizeLegibility;
}
h1{font-size:22px; line-height:1.3; margin:26px 0 6px; letter-spacing:-.01em; text-wrap:balance}
h2{font-size:15px; margin:30px 0 10px; letter-spacing:-.005em; display:flex;
   align-items:baseline; gap:8px; flex-wrap:wrap}
h3{font-size:13px; margin:16px 0 6px}
p{margin:0 0 10px}
a{color:var(--accent); text-decoration:none; text-underline-offset:2px;
  transition:color var(--dur) var(--ease)}
a:hover{text-decoration:underline}
a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,
summary:focus-visible{outline:2px solid var(--accent); outline-offset:2px; border-radius:var(--radius-sm)}
code{font:12px/1.5 var(--mono); background:var(--sunken); border:1px solid var(--border);
     border-radius:var(--radius-sm); padding:1px 5px; overflow-wrap:anywhere}
::selection{background:var(--accent-soft); color:var(--text)}
.sub{color:var(--muted); font-size:12.5px; margin:0 0 18px; line-height:1.7}
.sub code{background:transparent; border:0; padding:0}
.note{font-size:12px; color:var(--muted); line-height:1.7}
.hint{font-size:12px; color:var(--faint); font-weight:400}
h2 .hint{margin-left:2px}

/* ══ 3. Tab 导航（吸顶）═══════════════════════════════════════════════ */
nav.tabs{
  position:sticky; top:0; z-index:20; display:flex; gap:2px; flex-wrap:wrap;
  height:var(--nav-h); align-items:flex-end; box-sizing:border-box;
  margin:0 -24px 0; padding:8px 24px 0; background:color-mix(in srgb,var(--bg) 88%,transparent);
  backdrop-filter:saturate(180%) blur(12px);
  border-bottom:1px solid var(--border); view-transition-name:tabs;
}
nav.tabs a.tab{
  position:relative; padding:9px 15px; color:var(--muted); font-size:14px;
  border-radius:var(--radius) var(--radius) 0 0; text-decoration:none;
  transition:color var(--dur) var(--ease),background var(--dur) var(--ease);
}
nav.tabs a.tab:hover{color:var(--text); background:var(--surface-2)}
nav.tabs a.tab.on{color:var(--accent); font-weight:600}
nav.tabs a.tab.on::after{
  content:""; position:absolute; inset:auto 12px -1px; height:2px;
  background:var(--accent); border-radius:2px 2px 0 0;
}

/* ══ 4. 统计块 / 卡片 ═════════════════════════════════════════════════ */
.stats{display:grid; grid-template-columns:repeat(auto-fit,minmax(104px,1fr));
       gap:var(--gap); margin:0 0 6px}
.stat{
  background:var(--surface); border:1px solid var(--border); border-radius:var(--radius);
  padding:12px 14px; box-shadow:var(--shadow-1);
  transition:border-color var(--dur) var(--ease),box-shadow var(--dur) var(--ease),
             translate var(--dur) var(--ease);
}
.stat:hover{border-color:var(--border-strong); box-shadow:var(--shadow-2); translate:0 -1px}
.stat b{display:block; font-size:22px; line-height:1.25; letter-spacing:-.02em;
        font-variant-numeric:tabular-nums}
.stat span{font-size:11.5px; color:var(--muted)}
.grid{display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:var(--gap)}
.card{background:var(--surface); border:1px solid var(--border); border-radius:var(--radius);
      padding:12px 14px; box-shadow:var(--shadow-1)}
.card h3{margin:0 0 8px}
.cand{background:var(--surface-2); border:1px solid var(--border); border-radius:var(--radius-sm);
      padding:9px 11px; margin:6px 0}
.cand code{word-break:break-all; background:transparent; border:0; padding:0}

/* ══ 5. 表格 ══════════════════════════════════════════════════════════ */
/* 注意：**不能**给 table 加 `overflow:hidden`（本来想用它裁圆角）——
   那会让表格自己成为滚动容器，`thead th` 的 `position:sticky` 就失去参照、彻底不生效。
   圆角改由首末单元格承担。 */
table{border-collapse:separate; border-spacing:0; width:100%; margin:0 0 4px;
      background:var(--surface); border:1px solid var(--border); border-radius:var(--radius);
      box-shadow:var(--shadow-1)}
thead th:first-child{border-top-left-radius:var(--radius)}
thead th:last-child{border-top-right-radius:var(--radius)}
tbody tr:last-child td:first-child{border-bottom-left-radius:var(--radius)}
tbody tr:last-child td:last-child{border-bottom-right-radius:var(--radius)}
th,td{text-align:left; padding:8px 11px; border-bottom:1px solid var(--border); vertical-align:top}
thead th{
  position:sticky; top:var(--nav-h); z-index:5; background:var(--surface-2);
  font-size:11.5px; font-weight:600; color:var(--muted); letter-spacing:.02em;
  text-transform:none; white-space:nowrap;
}
tbody tr{transition:background var(--dur) var(--ease)}
tbody tr:hover{background:var(--surface-2)}
tbody tr:last-child td{border-bottom:0}
td.note{font-size:12px; color:var(--text-2); max-width:46ch}
td .hint,td.hint{font-size:12px; color:var(--muted)}
.barcell{white-space:nowrap; font-variant-numeric:tabular-nums}

/* ══ 6. 趋势条（保持 .bar / .bar-n / .trend .zero 三个类名）═══════════ */
.bar{display:inline-block; height:9px; min-width:2px; border-radius:var(--pill);
     background:linear-gradient(90deg,var(--accent),color-mix(in srgb,var(--accent) 55%,transparent));
     vertical-align:middle}
.bar-n{font-size:11px; color:var(--muted); margin-left:6px; font-variant-numeric:tabular-nums}
.trend .zero{opacity:.35}
.trend td{font-variant-numeric:tabular-nums}

/* ══ 7. 胶囊 / 状态 ═══════════════════════════════════════════════════ */
.pill{display:inline-block; border-radius:var(--pill); padding:1px 9px; font-size:11.5px;
      line-height:1.7; white-space:nowrap; border:1px solid var(--border-strong);
      color:var(--text-2); background:var(--surface-2)}
.pill.ok{border-color:var(--ok); color:var(--ok); background:var(--ok-soft)}
.pill.warn{border-color:var(--warn); color:var(--warn); background:var(--warn-soft)}
.pill.danger{border-color:var(--danger); color:var(--danger); background:var(--danger-soft)}
.pill.muted{color:var(--muted); border-color:var(--border); background:transparent}

/* ══ 8. 表单 / 工具条 / 按钮 ══════════════════════════════════════════ */
input[type=text],input[type=number],select,textarea{
  font:inherit; font-size:12.5px; padding:5px 9px; border-radius:var(--radius-sm);
  border:1px solid var(--border-strong); background:var(--surface); color:var(--text);
  transition:border-color var(--dur) var(--ease),box-shadow var(--dur) var(--ease);
}
input[type=text]:hover,select:hover{border-color:var(--faint)}
input[type=text]:focus,select:focus{border-color:var(--accent); outline:0;
  box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 18%,transparent)}
input[type=checkbox]{accent-color:var(--accent); width:15px; height:15px}
.toolbar{
  display:flex; gap:12px; align-items:center; flex-wrap:wrap; margin:0 0 14px;
  background:var(--surface); border:1px solid var(--border); border-radius:var(--radius);
  padding:11px 13px; box-shadow:var(--shadow-1);
}
.toolbar label{font-size:12px; color:var(--muted); display:flex; gap:6px; align-items:center}
label.f{margin:0; font-size:12px; display:inline-flex; align-items:center; gap:5px; color:var(--muted)}
label.f input[type=text],label.f select,label.f input[type=number]{min-width:96px}
fieldset{border:1px solid var(--border); border-radius:var(--radius); margin:0 0 12px;
         padding:10px 13px; background:var(--surface)}
legend{font-size:12px; color:var(--muted); padding:0 6px}
form.inline{display:inline; margin:0 5px 0 0}
.btn{
  font:inherit; font-size:12px; padding:5px 11px; border-radius:var(--radius-sm); cursor:pointer;
  border:1px solid var(--border-strong); background:var(--surface); color:var(--text-2);
  transition:background var(--dur) var(--ease),border-color var(--dur) var(--ease),
             color var(--dur) var(--ease),translate var(--dur) var(--ease);
}
.btn:hover{border-color:var(--accent); color:var(--accent); background:var(--accent-soft)}
.btn:active{translate:0 1px}
.btn.primary{border-color:var(--accent); color:var(--accent-fg); background:var(--accent)}
.btn.primary:hover{background:color-mix(in srgb,var(--accent) 85%,#000); color:var(--accent-fg)}
.btn.danger{border-color:var(--danger); color:var(--danger); background:transparent}
.btn.danger:hover{background:var(--danger-soft)}
.actions{white-space:nowrap}

/* ══ 9. 诊断抽屉（`<details>`，纯 CSS 展开动画）═══════════════════════ */
details.drawer{
  background:var(--surface); border:1px solid var(--border); border-radius:var(--radius);
  padding:0 13px; margin:8px 0; box-shadow:var(--shadow-1);
}
details.drawer[open]{padding-bottom:12px}
details.drawer summary{
  cursor:pointer; font-size:12.5px; color:var(--text-2); padding:10px 0;
  list-style:none; display:flex; align-items:center; gap:7px;
  transition:color var(--dur) var(--ease);
}
details.drawer summary::-webkit-details-marker{display:none}
details.drawer summary::before{
  content:""; width:0; height:0; flex:none;
  border-left:5px solid currentColor; border-top:4px solid transparent;
  border-bottom:4px solid transparent; opacity:.65;
  transition:transform var(--dur) var(--ease);
}
details.drawer[open] summary::before{transform:rotate(90deg)}
details.drawer summary:hover{color:var(--accent)}
details.drawer[open] summary{border-bottom:1px solid var(--border); margin-bottom:10px}

/* ══ 10. 横幅 / 空态 / 页脚 ═══════════════════════════════════════════ */
.banner{border:1px solid var(--border-strong); border-radius:var(--radius);
        padding:11px 13px; margin:14px 0; font-size:12.5px; background:var(--surface)}
.banner.ok{border-color:var(--ok); background:var(--ok-soft); color:var(--ok)}
.banner.err{border-color:var(--danger); background:var(--danger-soft); color:var(--danger)}
.banner pre{white-space:pre-wrap; word-break:break-all; margin:6px 0 0; font-size:11.5px;
            font-family:var(--mono); opacity:.9}
.empty{color:var(--muted); font-size:12.5px; padding:14px 2px}
footer{margin-top:34px; padding-top:14px; border-top:1px solid var(--border);
       font-size:11.5px; color:var(--faint); line-height:1.8}
footer code{background:transparent; border:0; padding:0}

/* ══ 11. 动效（全部渐进增强，全部可关断）══════════════════════════════ */
/* 跨文档 Tab 切换：浏览器原生交叉淡入，Firefox 未支持时退化成普通跳转 */
@view-transition{navigation:auto}
::view-transition-group(tabs){animation-duration:.001s}
/* **不做「入场动画」** —— 两种主流写法都有「把内容永久留在不可见」的失败模式，
   且都不是理论风险，是实测：
     ① `transition` + `@starting-style`：真实浏览器里那段过渡没跑起来，
        `.stat` 停在 `opacity:0; translate:0 8px`，统计卡片整块看不见；
     ② `animation` + `fill:both`：动画时钟不推进时（截图、打印、无渲染环境）停在 `from` 帧，
        同样是 `opacity:0`。本机无头浏览器就是这样（4 个动画 `playState:running, currentTime:0`）。
   数据面板里「内容看不见」比「没有动效」糟得多，所以入场一律不做。
   保留的动效都**不可能隐藏内容**：Tab 交叉淡入、悬停底色、聚焦环、抽屉箭头旋转。 */
/* 容器的边框/阴影过渡（不动 opacity/translate，纯手感） */
.card,details.drawer,.toolbar,.banner,table{
  transition:border-color var(--dur) var(--ease),box-shadow var(--dur) var(--ease);
}
/* 长表格随滚动渐显：**不做**。`animation-timeline:view()` 的 `both` 填充会让视口外的行
   停在 `from` 帧（opacity:0）—— 数据面板里「内容看不见」比「没有动效」糟得多。
   行只保留悬停底色过渡。 */
tbody tr{transition:background var(--dur) var(--ease)}
@media (prefers-reduced-motion:reduce){
  html{scroll-behavior:auto}
  *,*::before,*::after{
    animation-duration:.001ms !important; animation-iteration-count:1 !important;
    transition-duration:.001ms !important; scroll-behavior:auto !important;
  }
}

/* ══ 12. 窄屏（手机上偶尔点）═════════════════════════════════════════ */
@media (max-width:640px){
  :root{--nav-h:44px}
  body{padding:0 14px 48px; font-size:13.5px}
  nav.tabs{margin:0 -14px; padding:6px 14px 0; overflow-x:auto; flex-wrap:nowrap}
  nav.tabs a.tab{padding:8px 12px; font-size:13.5px; white-space:nowrap}
  h1{font-size:19px}
  .stats{grid-template-columns:repeat(auto-fit,minmax(88px,1fr))}
  .toolbar{gap:8px}
  td.note{max-width:none}
}
"""
