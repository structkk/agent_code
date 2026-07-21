"""单次程序运行期间的会话状态。"""

from __future__ import annotations

from dataclasses import dataclass, field


REJECTION_PHRASES = (
    "不喜欢",
    "不想去",
    "不合适",
    "换一个",
    "换个",
    "还有别的",
    "拒绝",
    "不满意",
    "不要这个",
)
ACCEPTANCE_PHRASES = ("就这个", "接受", "满意", "不错", "可以", "好的")


@dataclass(frozen=True, slots=True)
class TurnEvent:
    """用户输入对会话状态造成的事件。"""

    rejected_attraction: str | None = None
    accepted: bool = False


@dataclass(slots=True)
class ConversationState:
    """记录推荐、拒绝、票务检查和反思策略。"""

    last_recommendation: str | None = None
    consecutive_rejections: int = 0
    rejected_attractions: list[str] = field(default_factory=list)
    sold_out_attractions: list[str] = field(default_factory=list)
    ticket_checks: dict[str, str] = field(default_factory=dict)
    current_strategy: str = "优先匹配用户已记录的兴趣、预算和出行约束。"
    reflection_count: int = 0
    recommendation_ready: bool = False
    preference_saved_this_turn: bool = False

    def start_turn(self, user_input: str) -> TurnEvent:
        """开始新一轮用户交互，并识别接受或拒绝反馈。"""
        self.recommendation_ready = False
        self.preference_saved_this_turn = False
        normalized = user_input.strip()

        is_rejection = any(phrase in normalized for phrase in REJECTION_PHRASES)
        if is_rejection and self.last_recommendation:
            rejected = self.last_recommendation
            self._append_unique(self.rejected_attractions, rejected)
            self.consecutive_rejections += 1
            self.last_recommendation = None
            return TurnEvent(rejected_attraction=rejected)

        is_acceptance = any(phrase in normalized for phrase in ACCEPTANCE_PHRASES)
        if is_acceptance:
            self.consecutive_rejections = 0
            return TurnEvent(accepted=True)
        return TurnEvent()

    @property
    def needs_reflection(self) -> bool:
        """连续三次拒绝后必须先反思再继续推荐。"""
        return self.consecutive_rejections >= 3

    @property
    def excluded_attractions(self) -> list[str]:
        """返回已拒绝或已售罄景点的去重列表。"""
        return list(dict.fromkeys(
            self.rejected_attractions + self.sold_out_attractions
        ))

    def mark_preference_saved(self) -> None:
        self.preference_saved_this_turn = True

    def record_ticket_status(self, attraction: str, status: str) -> None:
        normalized = attraction.strip()
        self.ticket_checks[normalized] = status
        if status == "sold_out":
            self._append_unique(self.sold_out_attractions, normalized)

    def ticket_status_for(self, attraction: str) -> str | None:
        return self.ticket_checks.get(attraction.strip())

    def record_recommendation(self, attraction: str) -> None:
        normalized = attraction.strip()
        status = self.ticket_status_for(normalized)
        if status is None:
            raise ValueError("推荐前必须先调用 check_ticket_availability。")
        if status == "sold_out":
            raise ValueError("该景点门票已售罄，不能记录为最终推荐。")
        self.last_recommendation = normalized
        self.recommendation_ready = True

    def apply_reflection(self, new_strategy: str) -> None:
        if not self.needs_reflection:
            raise ValueError("当前未达到连续拒绝三次的反思条件。")
        normalized = new_strategy.strip()
        if not normalized:
            raise ValueError("新策略不能为空。")
        self.current_strategy = normalized
        self.reflection_count += 1
        self.consecutive_rejections = 0

    def summary(self) -> str:
        excluded = "、".join(self.excluded_attractions) or "无"
        last = self.last_recommendation or "无"
        return (
            f"最近推荐: {last}\n"
            f"连续拒绝次数: {self.consecutive_rejections}\n"
            f"排除景点: {excluded}\n"
            f"当前策略: {self.current_strategy}\n"
            f"累计反思次数: {self.reflection_count}"
        )

    @staticmethod
    def _append_unique(values: list[str], value: str) -> None:
        if value and value not in values:
            values.append(value)
