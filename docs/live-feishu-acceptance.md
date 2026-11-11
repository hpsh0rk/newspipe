# 受控飞书验收

## 已验证与未验证

2026-11-11，本机 macOS arm64 使用 Playwright 1.65.0 的 Chromium 158 / WebKit 27.2
完成离线验收。9 个测试均在两个引擎上运行；5 个页面和 2 个编辑器分别覆盖
360 / 768 / 1440px，共 42 个页面/尺寸组合。覆盖标签、键盘可见焦点、减少动态效果、
预览/校验/保存/复制、冲突与断网保留编辑、保存期间继续编辑、无 JS 失败草稿与 PRG 防重放。

复现并修复：折叠区非法字段无法定位、通知槽位组缺少错误关联、隐藏继承值阻塞校验、
HTTP 表单丢弃显式空值，以及无 JS 时折叠非法输入阻塞提交。夹具固定回环绑定，
关闭前排空请求线程，防止安全拦截提前撤销。测试使用临时状态、假适配器和双侧网络阻断，
不读生产凭据，不运行调度/入站事件、不发真实消息。

最终复核后，Python 3.11/3.12/3.13 + Node 24.2.0 各通过 569 项 Python/Node 测试；
普通测试发现的另 9 项浏览器测试有意跳过，已在独立强制浏览器门禁运行并通过。
密钥扫描 144 个文件无发现，`git diff --check` 无问题。独立复核的两项 Important
发现（无 JS 校验退化、关闭请求线程前撤销拦截）均经过失败/通过回归验证，无遗留 Minor。
Python 3.13 同时启用 `NEWSPIPE_REQUIRE_JS_TESTS=1` 和 `NEWSPIPE_BROWSER_TESTS=1`
后，578 项完整测试全部通过，没有跳过项。

**这不是飞书实机验收。** 没有验证真实应用权限、宿主转发、平台回执与去重、真实用户点击，
也不等同于 iOS Safari/Android 真机或屏幕阅读器验收。GitHub Ubuntu runner 的结果需推送后看 CI，
不能用本机通过替代。结果未知操作的人工对账写入能力仍未实现，不能绕过阻塞。

## 可重复的离线门禁

在仓库根目录，使用 Python 3.11+ 和 Node 24：

```sh
python -m pip install -e '.[all,browser-test]'
NEWSPIPE_REQUIRE_JS_TESTS=1 python -m unittest discover -s tests
python3 scripts/scan_secrets.py
python -m playwright install chromium webkit
NEWSPIPE_BROWSER_TESTS=1 python -m unittest discover -s tests -p test_browser_e2e.py
```

Linux 首次安装引擎时用 `python -m playwright install --with-deps chromium webkit`。
普通单元测试有意跳过浏览器用例；第二条门禁明确启用它们，缺 Playwright 或任一引擎会失败，
不会跳过。CI 对所有分支 push / PR 跑 Python 3.11/3.12/3.13 + 必需的 Node 测试，
另有独立 Chromium/WebKit job。运行引擎需要下载外部测试工具，但被测服务只访问离线夹具。

失败截图/trace 在被 Git 忽略的 `test-results/browser/`，CI 保存 7 天；仅包含合成数据和
测试进程的临时表单令牌。可用 `python -m playwright show-trace <trace.zip>` 排查。
人工离线查看可运行 `python tests/preview_workbench.py`，打开输出的回环 URL，Ctrl-C 关闭；
不要给这个夹具添加生产配置或凭据，它不替代真实 runner。

## 实机验收前置条件

下面步骤有外部副作用，**需要用户明确授权，不能由 CI 或 Agent 自动执行**。

- 优先专用飞书应用 + 测试群 + 独立 `NEWSPIPE_NEWS_DIR`；不要复用生产队列或复制其私有状态。
- 若必须复用应用，先约定独占窗口、消息条数和目标群，确认宿主/服务谁持长连接。
  不能为了验收再启动第二个 WS client；`doctor` 的冲突检查不能替代人工确认全部 runner。
- 确认应用已进测试群、事件订阅/卡片交互权限已生效。凭据只能走显式配置、环境、dotenv
  或钥匙串，不进截图、报告、Git 或命令行回显。
- 只选容器或 launchd 一个 runner。检查端口和心跳；容器切换时清理旧 launchd plist，
  防止下次登录复活双跑。不要为了检查启动/重启生产宿主。
- 确认当前配置的唯一通知目标就是批准的测试群；`probe-channel --chat` 不改变日常通知目标。
- 测试收藏不得进入生产入库消费器。使用独立事件目录/测试消费者，或明确暂停该测试目录的消费。
  本项目仍只记入库意图，不能替用户写知识库，也不能伪造 `queue ack`。

先在操作记录填好：验收人/批准窗口、提交 SHA、数据目录、应用身份、测试群、WS 唯一所有者、
runner/PID/端口、心跳、入站方式。任何一项不明确，就停止实机步骤。

## 分阶段执行

以下命令都是供验收人确认后执行的模板；先让 `NEWSPIPE_NEWS_DIR` 指向批准的独立验收目录。
配置写入走 CLI 或页面，不直接编辑 `state/**`。

1. **只读自检**：运行 `newspipe doctor --json`、`newspipe status --json`、
   `newspipe view --json`，确认应用/群/入站/预算与记录一致、服务端口和心跳正常。
   如需模型验收，再单独批准模型调用；`probe-model` 只核对解析配置，不证明模型加工成功。
2. **一张探针卡**：执行 `newspipe probe-channel --chat <测试群ID> --json`。
   记录真实 `card_id`、`message_id`、目标群和时间；必须在测试群看到且只看到一张卡。
   探针只是直连通道检查，不证明日常通知后端和完整策略链路通过，也不要把探针重复发送当重试。
3. **日常通知链路**：探针不带业务按钮，点击验收需要真实的测试资讯卡。
   通过 CLI/页面创建一个手动采集、关闭 AI 的测试源，绑定批准的策略、
   受控 feed 和测试群。先 `newspipe collect <测试源ID> --json`，再
   `newspipe notification preview --source <测试源ID> --json`，确认待处理内容与限制。
   明确批准后执行一次 `newspipe notify <测试源ID> --json`，记录操作身份及平台回执。
   汇总策略只在约定槽位执行；确需越过时钟时另行批准 `--bypass-clock`，它不越过暂停或预算。
4. **点击闭环**：在测试资讯卡上验证翻页等无破坏性交互，确认实际 WS 所有者接收。
   HTTP 模式复用该宿主转发到已存在的 endpoint，核对 toast、卡片变化和失败日志；
   没有新的 WS 进程。收藏单独按第 6 步批准，不混进通道探针。
5. **实体更新与去重**：若验收追加卡，再采集第二条受控内容并通知，检查更新同一实体、
   序列递增、仅有一次有效更新；再次通知没有新内容时不重复投递。核对 `view/status`
   的待处理计数、预算和真实群里的消息，不用“接口返回成功”替代肉眼及平台回执。
6. **收藏与故障边界**：只有已批准测试消费方式后才点收藏。用
   `newspipe events list --unconsumed --json` 和 `newspipe queue list --json` 验证台账。
   只有宿主真实入库成功且能给出路径时才能 `queue ack <id> --note <路径> --by <消费者>`。
   单纯验证按钮不需要出队。断网/限流等故障注入只在专用环境做，禁止扰动生产网络。

## 停止与回执

出现应用/群不符、双 runner、重复卡、点击没有可解释回显、密钥进入日志或结果未知时，
立即停止后续写操作，保留证据，先判断远端有没有成功。结果未知且超幂等窗口必须保持阻塞；
禁止换操作 ID 重发、强制重发、改私有状态或“手工标记成功”。现有安全重试只处理允许的原操作。

每阶段记录：命令（不含密钥）、时间、CLI `ok/changed/error`、来源 ID、目标群、操作 ID、
card/message ID、可用的平台请求回执、群内实际条数、点击/toast/事件 ID，以及通过/失败/未执行。
缺少证据的项目只能标“未验证”。完成后确认测试配置停用、批准的临时 runner 已关闭、
生产唯一所有者/端口/心跳仍正常；保留需消费的事件，不为了清场伪造 ack。
