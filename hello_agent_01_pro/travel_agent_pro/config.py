"""增强版旅行助手的本地配置。

这是教学项目，可直接在本文件顶部填写密钥。本文件已被 .gitignore 忽略。
"""

from __future__ import annotations

from dataclasses import dataclass


API_KEY = "YOUR_API_KEY"
BASE_URL = "YOUR_BASE_URL"
MODEL_ID = "YOUR_MODEL_ID"
TAVILY_API_KEY = "YOUR_TAVILY_API_KEY"
MAX_STEPS = 12


class ConfigurationError(ValueError):
    """配置缺失或无效。"""


@dataclass(frozen=True, slots=True)
class Settings:
    """增强版旅行助手运行所需的配置。"""

    api_key: str
    base_url: str
    model_id: str
    tavily_api_key: str
    max_steps: int = 12

    @classmethod
    def from_source(cls) -> "Settings":
        """读取源码配置并校验必填项。"""
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
                "请在 travel_agent_pro/config.py 中配置："
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
