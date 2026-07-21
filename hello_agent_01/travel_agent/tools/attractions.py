"""旅游景点搜索工具。"""

from __future__ import annotations

from tavily import TavilyClient


def get_attraction(city: str, weather: str, *, api_key: str) -> str:
    """结合城市与天气，通过 Tavily 搜索合适的旅游景点。"""
    query = f"'{city}' 在'{weather}'天气下最值得去的旅游景点推荐及理由"

    try:
        tavily = TavilyClient(api_key=api_key)
        response = tavily.search(
            query=query,
            search_depth="basic",
            include_answer=True,
        )

        if response.get("answer"):
            return str(response["answer"])

        formatted_results = [
            f"- {result['title']}: {result['content']}"
            for result in response.get("results", [])
            if result.get("title") and result.get("content")
        ]
        if not formatted_results:
            return "抱歉，没有找到相关的旅游景点推荐。"

        return "根据搜索，为您找到以下信息：\n" + "\n".join(formatted_results)
    except Exception as exc:
        return f"错误：执行 Tavily 搜索时出现问题 - {exc}"
