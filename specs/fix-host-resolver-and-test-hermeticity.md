# Spec: 修复 model_host 注入回归 + 测试去环境依赖

> created: 2026-10-07
> status: implemented
> 背景：干净环境（无 `NEWSPIPE_HOST_HOME`/`HERMES_HOME`、无飞书凭据、日历翻页）下全量测试 6 例红。
> 起因：356 例中 6 例失败 —— 1 例真回归 + 5 例「只在作者机器上绿」的测试。

---

## Scope 边界

**改**：
- `src/newspipe/backends/model_host.py`：`HostModelResolver.resolve` 的宿主目录推导。
- `tests/test_news_pipeline.py`：`LlmTests.setUp` 注入 `NEWSPIPE_HOST_HOME` 指向用例临时目录。
- `tests/test_service.py`：`test_direct_channel_installed_when_configured` 注入哑密钥环境变量。
- `tests/test_items.py`：fixture 批次日期从写死改为相对 `today()` 生成。

**不改**：`llm.chat_json` 对外签名、`credentials.resolve_feishu` 的解析顺序、
`view`/`inbound` 的日期窗口语义、`ports.py` 契约。

## Contract 接口契约

- `ModelResolver.resolve(capability, models_cfg, *, host_config_path=None, env_path=None)`：
  显式传入 `host_config_path` 或 `env_path` 时，home 从该路径的父目录推导，
  **不得**要求 `NEWSPIPE_HOST_HOME`/`HERMES_HOME`；两者都缺省时维持现状
  （`host_home(required=True)`，没配就报可执行的错）。
- 错误提示中引用的配置路径 / dotenv 路径必须与实际读取的路径一致（含注入场景）。
- 测试**不赌环境**：凭据、宿主目录一律用例内注入（`patch.dict`），日历相关日期一律相对 `today()` 生成。

## Acceptance Criteria 验收标准

1. 干净 shell（unset `NEWSPIPE_HOST_HOME`/`HERMES_HOME`/飞书相关变量）下
   `python -m unittest discover -s tests` 全绿（≥356 例）。
2. 以下 6 例全部转绿：
   `LlmTests.test_host_follows_current_model_and_prefers_env_key`、
   `LlmTests.test_describe_never_leaks_key`、
   `LlmTests.test_persistent_empty_content_records_reason_code`、
   `LlmTests.test_reasoning_model_empty_content_retries_with_bigger_budget`、
   `ChannelWiringTests.test_direct_channel_installed_when_configured`、
   `BatchRouteTests.test_items_route_filters_from_the_query_string`。
3. 把系统日期语义考虑在内：fixture 批次日 = `today()`，窗口过滤断言不再随日历翻车。
4. `python3 scripts/scan_secrets.py` 保持 0 输出；本次改动不含任何真实凭据/个人路径。
5. 生产行为不变：未注入路径时，缺宿主目录仍报 `ConfigError`（文案不变）。
