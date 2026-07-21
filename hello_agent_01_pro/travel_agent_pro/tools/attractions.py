"""结合记忆、排除列表和反思策略搜索候选景点。"""

from __future__ import annotations

from tavily import TavilyClient


class AttractionSearch:
    """Tavily 景点搜索客户端。"""

    def __init__(self, api_key: str) -> None:
        self.client = TavilyClient(api_key=api_key)

    def search(
        self,
        city: str,
        weather: str,
        preferences: str,
        excluded: list[str],
        strategy: str,
    ) -> str:
        excluded_text = "、".join(excluded) or "无"
        query = (
            f"为前往{city}的游客推荐3个适合'{weather}'天气的景点。"
            f"用户长期偏好：{preferences}。"
            f"当前推荐策略：{strategy}。"
            f"不得推荐以下已拒绝或已售罄景点：{excluded_text}。"
            "请给出景点准确名称、推荐理由、适合人群和大致费用，便于后续查询门票。"
        )
        try:
            response = self.client.search(
                query=query,
                search_depth="basic",
                include_answer=True,
            )
            if response.get("answer"):
                return str(response["answer"])

            results = [
                f"- {item['title']}: {item['content']}"
                for item in response.get("results", [])
                if item.get("title") and item.get("content")
            ]
            if not results:
                return "没有搜索到符合条件的新景点，请调整约束后重试。"
            return "候选景点：\n" + "\n".join(results)
        except Exception as exc:
            return f"错误：执行 Tavily 景点搜索失败 - {exc}"
