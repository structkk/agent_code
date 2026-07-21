"""应用配置。

这是教学项目，直接在本文件顶部填写服务商配置即可。
"""

from __future__ import annotations

from dataclasses import dataclass


# 请根据教程以及你使用的模型服务商填写以下配置。
API_KEY = "YOUR_API_KEY"
BASE_URL = "YOUR_BASE_URL"
MODEL_ID = "YOUR_MODEL_ID"
TAVILY_API_KEY = "YOUR_TAVILY_API_KEY"
MAX_STEPS = 5


class ConfigurationError(ValueError):
    """配置缺失或无效。"""


@dataclass(frozen=True, slots=True)
class Settings:
    """旅行助手运行所需的配置。"""

    api_key: str
    base_url: str
    model_id: str
    tavily_api_key: str
    max_steps: int = 5

    @classmethod
    def from_source(cls) -> "Settings":
        """读取本模块顶部的源码配置，并对必填项进行校验。"""
        required = {
            "API_KEY": API_KEY.strip(),
            "BASE_URL": BASE_URL.strip(),
            "MODEL_ID": MODEL_ID.strip(),
            "TAVILY_API_KEY": TAVILY_API_KEY.strip(),
        }
        unconfigured = [
            name
            for name, value in required.items()
            if not value or value.startswith("YOUR_")
        ]
        if unconfigured:
            raise ConfigurationError(
                "请在 travel_agent/config.py 中配置："
                + ", ".join(unconfigured)
            )

        if not isinstance(MAX_STEPS, int) or MAX_STEPS < 1:
            raise ConfigurationError("MAX_STEPS 必须是大于 0 的整数。")

        return cls(
            api_key=required["API_KEY"],
            base_url=required["BASE_URL"].rstrip("/"),
            model_id=required["MODEL_ID"],
            tavily_api_key=required["TAVILY_API_KEY"],
            max_steps=MAX_STEPS,
        )
