"""资讯管线 v2 —— 类型化错误。

区分「缺配置」与「真故障」：前者记 `config_missing` 心跳（不告警刷屏），后者记 `error`
（stdout 非空 → no_agent 任务会把它投递给运维通道）。这条区分是 v1 用血的教训换来的：
把缺凭据混进故障告警，会让每 5 分钟的轮询变成告警轰炸。
"""
from __future__ import annotations


class NewsError(Exception):
    """资讯管线所有类型化错误的基类。"""


class ConfigError(NewsError):
    """配置缺失或非法（缺 chat / 未知适配器 / 枚举值拼错 / 缺凭据）。

    记 config_missing 心跳，不触发故障告警——需要人去改配置，重试没有意义。
    """


class DeliveryError(NewsError):
    """投递层失败（卡片实体创建/发送/更新被拒）。"""


class ModelError(NewsError):
    """模型调用失败（网络/鉴权/响应不可解析/超预算）。可降级：不加工继续发卡。"""
