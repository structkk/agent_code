"""旅行助手可调用工具的注册入口。"""

from __future__ import annotations

from functools import partial

from .attractions import get_attraction
from .weather import get_weather


def build_available_tools(tavily_api_key: str):
    """构建工具白名单，并将密钥绑定到景点搜索工具。"""
    return {
        "get_weather": get_weather,
        "get_attraction": partial(get_attraction, api_key=tavily_api_key),
    }


__all__ = ["build_available_tools", "get_attraction", "get_weather"]
