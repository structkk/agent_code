"""带记忆、回退和反思门控的 Thought-Action-Observation 循环。"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Callable, Mapping, Protocol

from .memory import PreferenceMemory
from .state import ConversationState, TurnEvent


Tool = Callable[..., str]


class LLMClient(Protocol):
    def generate(self, prompt: str, system_prompt: str) -> str:
        """根据提示生成一组 Thought 和 Action。"""


@dataclass(frozen=True, slots=True)
class FinishAction:
    answer: str


@dataclass(frozen=True, slots=True)
class ToolAction:
    name: str
    arguments: dict[str, str]


ParsedAction = FinishAction | ToolAction

_THOUGHT_ACTION_PATTERN = re.compile(
    r"(Thought:.*?Action:.*?)(?=\n\s*(?:Thought:|Action:|Observation:)|\Z)",
    re.DOTALL,
)
_ACTION_PATTERN = re.compile(r"Action:\s*(.+)", re.DOTALL)
_FINISH_PATTERN = re.compile(r"Finish\[(.*)\]", re.DOTALL)

_PREFERENCE_SIGNALS = (
    "我喜欢",
    "我更喜欢",
    "我不喜欢",
    "偏好",
    "预算",
    "请记住",
    "我倾向",
)
_RECOMMENDATION_SIGNALS = ("推荐", "景点", "去哪", "旅游", "换一个", "换个")


def normalize_model_output(output: str) -> str:
    """只保留模型输出中的第一组 Thought-Action。"""
    match = _THOUGHT_ACTION_PATTERN.search(output)
    return match.group(1).strip() if match else output.strip()


def parse_action(output: str) -> ParsedAction:
    """使用 AST 安全解析动作，不执行模型生成的代码。"""
    action_match = _ACTION_PATTERN.search(output)
    if not action_match:
        raise ValueError("未找到 Action 字段。")

    action_text = action_match.group(1).strip()
    finish_match = _FINISH_PATTERN.fullmatch(action_text)
    if finish_match:
        return FinishAction(answer=finish_match.group(1).strip())
    if action_text.startswith("Finish"):
        raise ValueError("Finish 格式无效，应使用 Finish[最终答复]。")

    try:
        expression = ast.parse(action_text, mode="eval").body
    except SyntaxError as exc:
        raise ValueError("工具调用不是有效表达式。") from exc

    if not isinstance(expression, ast.Call) or not isinstance(
        expression.func, ast.Name
    ):
        raise ValueError("Action 必须是直接函数调用。")
    if expression.args:
        raise ValueError("工具调用只支持具名参数。")

    arguments: dict[str, str] = {}
    for keyword in expression.keywords:
        if keyword.arg is None:
            raise ValueError("工具调用不支持 **kwargs。")
        try:
            value = ast.literal_eval(keyword.value)
        except (ValueError, SyntaxError) as exc:
            raise ValueError(f"参数 {keyword.arg} 必须是字符串常量。") from exc
        if not isinstance(value, str):
            raise ValueError(f"参数 {keyword.arg} 必须是字符串。")
        arguments[keyword.arg] = value
    return ToolAction(name=expression.func.id, arguments=arguments)


class TravelAgentPro:
    """在每轮用户输入中执行受约束的 Agent Loop。"""

    def __init__(
        self,
        llm: LLMClient,
        tools: Mapping[str, Tool],
        memory: PreferenceMemory,
        state: ConversationState,
        system_prompt: str,
        max_steps: int = 12,
    ) -> None:
        self.llm = llm
        self.tools = dict(tools)
        self.memory = memory
        self.state = state
        self.system_prompt = system_prompt
        self.max_steps = max_steps

    def run_turn(self, user_input: str) -> str | None:
        """处理一轮用户输入，并保留跨轮会话状态与长期记忆。"""
        event = self.state.start_turn(user_input)
        preference_required = (
            event.rejected_attraction is None
            and any(signal in user_input for signal in _PREFERENCE_SIGNALS)
        )
        recommendation_required = (
            event.rejected_attraction is not None
            or any(signal in user_input for signal in _RECOMMENDATION_SIGNALS)
        )

        prompt_history = [
            f"用户本轮请求: {user_input}",
            "长期偏好记忆:\n" + self.memory.summary(),
            "当前会话状态:\n" + self.state.summary(),
        ]
        self._append_turn_event(prompt_history, event)
        if self.state.needs_reflection:
            prompt_history.append(
                "Observation: 系统检测到用户已连续拒绝3个推荐。"
                "下一步强制调用 reflect_strategy，反思前禁止搜索或推荐。"
            )

        print(f"用户输入: {user_input}\n" + "=" * 56)
        for step in range(1, self.max_steps + 1):
            print(f"--- Agent 循环 {step} ---\n")
            try:
                raw_output = self.llm.generate(
                    "\n".join(prompt_history),
                    system_prompt=self.system_prompt,
                )
            except Exception as exc:
                print(f"调用 LLM 失败：{exc}")
                return None

            output = normalize_model_output(raw_output)
            print(f"模型输出:\n{output}\n")
            prompt_history.append(output)

            try:
                action = parse_action(output)
            except ValueError as exc:
                self._record_observation(
                    prompt_history,
                    f"错误：{exc} 请严格遵循 Thought/Action 格式。",
                )
                continue

            if self.state.needs_reflection and not (
                isinstance(action, ToolAction)
                and action.name == "reflect_strategy"
            ):
                self._record_observation(
                    prompt_history,
                    "策略门控：连续拒绝已达到3次，必须先调用 reflect_strategy。",
                )
                continue

            if isinstance(action, FinishAction):
                if preference_required and not self.state.preference_saved_this_turn:
                    self._record_observation(
                        prompt_history,
                        "记忆门控：用户表达了长期偏好，必须先调用 remember_preference。",
                    )
                    continue
                if recommendation_required and not self.state.recommendation_ready:
                    self._record_observation(
                        prompt_history,
                        "推荐门控：必须完成候选搜索、票务检查和 record_recommendation 后才能 Finish。",
                    )
                    continue
                print(f"任务完成，最终答案: {action.answer}")
                return action.answer

            observation = self._execute_tool(action)
            self._record_observation(prompt_history, observation)

        print(f"任务未在 {self.max_steps} 个步骤内完成。")
        return None

    def _execute_tool(self, action: ToolAction) -> str:
        tool = self.tools.get(action.name)
        if tool is None:
            return f"错误：未定义的工具 '{action.name}'"
        try:
            return tool(**action.arguments)
        except TypeError as exc:
            return f"错误：工具 '{action.name}' 参数不正确 - {exc}"
        except Exception as exc:
            return f"错误：工具 '{action.name}' 执行失败 - {exc}"

    @staticmethod
    def _append_turn_event(history: list[str], event: TurnEvent) -> None:
        if event.rejected_attraction:
            history.append(
                "Observation: 用户拒绝了上一推荐："
                f"{event.rejected_attraction}。该景点已加入排除列表。"
            )
        elif event.accepted:
            history.append("Observation: 用户接受了当前建议，连续拒绝计数已清零。")

    @staticmethod
    def _record_observation(history: list[str], observation: str) -> None:
        text = f"Observation: {observation}"
        print(f"{text}\n" + "=" * 56)
        history.append(text)
