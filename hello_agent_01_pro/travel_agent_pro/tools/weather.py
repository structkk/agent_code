"""实时天气查询工具。"""

from __future__ import annotations

from urllib.parse import quote

import requests


def get_weather(city: str) -> str:
    """通过 wttr.in 查询指定城市当前天气。"""
    url = f"https://wttr.in/{quote(city, safe='')}?format=j1"
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        data = response.json()
        current = data["current_condition"][0]
        description = current["weatherDesc"][0]["value"]
        temperature = current["temp_C"]
        return f"{city}当前天气：{description}，气温{temperature}摄氏度"
    except requests.exceptions.RequestException as exc:
        return f"错误：查询天气时遇到网络问题 - {exc}"
    except (KeyError, IndexError, TypeError) as exc:
        return f"错误：解析天气数据失败，可能是城市名称无效 - {exc}"
