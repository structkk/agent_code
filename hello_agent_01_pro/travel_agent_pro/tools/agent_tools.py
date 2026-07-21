"""将记忆、票务、搜索和反思能力注册为 Agent 工具。"""

from __future__ import annotations

from collections.abc import Callable

from ..memory import PreferenceMemory
from ..state import ConversationState
from ..tickets import TicketInventory
from .attractions import AttractionSearch
from .weather import get_weather


Tool = Callable[..., str]


class AgentToolbox:
    """持有运行时依赖，并为模型提供无隐式参数的工具接口。"""

    def __init__(
        self,
        memory: PreferenceMemory,
        state: ConversationState,
        attraction_search: AttractionSearch,
        ticket_inventory: TicketInventory,
    ) -> None:
        self.memory = memory
        self.state = state
        self.attraction_search = attraction_search
        self.ticket_inventory = ticket_inventory

    def registry(self) -> dict[str, Tool]:
        return {
            "remember_preference": self.remember_preference,
            "get_weather": get_weather,
            "get_attraction": self.get_attraction,
            "check_ticket_availability": self.check_ticket_availability,
            "record_recommendation": self.record_recommendation,
            "reflect_strategy": self.reflect_strategy,
        }

    def remember_preference(self, category: str, value: str) -> str:
        created = self.memory.remember(category, value)
        self.state.mark_preference_saved()
        if created:
            return f"已记住用户偏好：{category} = {value}。"
        return f"该偏好已经存在：{category} = {value}。"

    def get_attraction(self, city: str, weather: str) -> str:
        return self.attraction_search.search(
            city=city,
            weather=weather,
            preferences=self.memory.summary(),
            excluded=self.state.excluded_attractions,
            strategy=self.state.current_strategy,
        )

    def check_ticket_availability(self, attraction: str) -> str:
        result = self.ticket_inventory.check(attraction)
        self.state.record_ticket_status(result.attraction, result.status)

        if result.status == "sold_out":
            return (
                f"ticket_status=sold_out；{result.attraction}门票已售罄。"
                "该景点已自动加入排除列表，必须重新调用 get_attraction 搜索备选方案。"
            )
        if result.status == "available":
            return (
                f"ticket_status=available；{result.attraction}在模拟库存中显示有票，"
                "可以记录为本轮推荐。"
            )
        return (
            f"ticket_status=unknown；未获得{result.attraction}的实时票务状态。"
            "可以继续推荐，但最终答复必须提醒用户前往官方渠道复核。"
        )

    def record_recommendation(self, attraction: str, reason: str) -> str:
        if not reason.strip():
            return "错误：推荐理由不能为空。"
        try:
            self.state.record_recommendation(attraction)
        except ValueError as exc:
            return f"错误：{exc}"
        status = self.state.ticket_status_for(attraction)
        return (
            f"已记录最终候选：{attraction}；票务状态={status}；"
            f"推荐理由={reason}。下一步可以使用 Finish 输出答复。"
        )

    def reflect_strategy(self, reason: str, new_strategy: str) -> str:
        try:
            self.state.apply_reflection(new_strategy)
        except ValueError as exc:
            return f"错误：{exc}"
        excluded = "、".join(self.state.excluded_attractions) or "无"
        return (
            f"反思完成。失败原因：{reason}。"
            f"新策略：{self.state.current_strategy}。"
            f"后续不得推荐：{excluded}。现在可以重新搜索候选景点。"
        )
