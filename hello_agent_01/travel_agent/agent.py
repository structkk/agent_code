"""Agent 主循环以及模型动作解析。"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Callable, Mapping, Protocol


Tool = Callable[..., str]


class LLMClient(Protocol):
    """Agent 所需的最小模型客户端接口。"""

    def generate(self, prompt: str, system_prompt: str) -> str:
        """根据提示生成文本。"""


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


def normalize_model_output(output: str) -> str:
    """只保留模型输出中的第一组 Thought-Action。"""
    match = _THOUGHT_ACTION_PATTERN.search(output)
    return match.group(1).strip() if match else output.strip()


def parse_action(output: str) -> ParsedAction:
    """安全解析 Finish 或工具调用，不执行模型生成的 Python 代码。"""
    action_match = _ACTION_PATTERN.search(output)
    if not action_match:
        raise ValueError("未找到 Action 字段。")

    action_text = action_match.group(1).strip()
    finish_match = _FINISH_PATTERN.fullmatch(action_text)
    if finish_match:
        return FinishAction(answer=finish_match.group(1).strip())
    if action_text.startswith("Finish"):
        raise ValueError("Finish 格式无效，应使用 Finish[最终答案]。")

    try:
        expression = ast.parse(action_text, mode="eval").body
    except SyntaxError as exc:
        raise ValueError("工具调用不是有效表达式。") from exc

    if not isinstance(expression, ast.Call) or not isinstance(
        expression.func, ast.Name
    ):
        raise ValueError("Action 必须是直接的函数调用。")
    if expression.args:
        raise ValueError("工具调用只支持具名参数。")

    arguments: dict[str, str] = {}
    for keyword in expression.keywords:
        if keyword.arg is None:
            raise ValueError("工具调用不支持 **kwargs 展开。")
        try:
            value = ast.literal_eval(keyword.value)
        except (ValueError, SyntaxError) as exc:
            raise ValueError(f"参数 {keyword.arg} 必须是字符串常量。") from exc
        if not isinstance(value, str):
            raise ValueError(f"参数 {keyword.arg} 必须是字符串。")
        arguments[keyword.arg] = value

    return ToolAction(name=expression.func.id, arguments=arguments)


class TravelAgent:
    """协调模型推理、工具执行和观察结果回传。"""

    def __init__(
        self,
        llm: LLMClient,
        tools: Mapping[str, Tool],
        system_prompt: str,
        max_steps: int = 5,
    ) -> None:
        self.llm = llm
        self.tools = dict(tools)
        self.system_prompt = system_prompt
        self.max_steps = max_steps

    def run(self, user_prompt: str) -> str | None:
        """运行 Agent，成功时返回最终答案，耗尽步骤时返回 None。"""
        prompt_history = [f"用户请求: {user_prompt}"]
        print(f"用户输入: {user_prompt}\n" + "=" * 40)

        for step in range(1, self.max_steps + 1):
            print(f"--- 循环 {step} ---\n")
            full_prompt = "\n".join(prompt_history)

            try:
                raw_output = self.llm.generate(
                    full_prompt,
                    system_prompt=self.system_prompt,
                )
            except Exception as exc:
                print(f"调用 LLM 失败：{exc}")
                return None

            llm_output = normalize_model_output(raw_output)
            if llm_output != raw_output.strip():
                print("已截断多余的 Thought-Action 对")
            print(f"模型输出:\n{llm_output}\n")
            prompt_history.append(llm_output)

            try:
                action = parse_action(llm_output)
            except ValueError as exc:
                self._record_observation(
                    prompt_history,
                    f"错误：{exc} 请严格遵循 Thought/Action 格式。",
                )
                continue

            if isinstance(action, FinishAction):
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
            return f"错误：工具 '{action.name}' 的参数不正确 - {exc}"
        except Exception as exc:
            return f"错误：工具 '{action.name}' 执行失败 - {exc}"

    @staticmethod
    def _record_observation(history: list[str], observation: str) -> None:
        observation_text = f"Observation: {observation}"
        print(f"{observation_text}\n" + "=" * 40)
        history.append(observation_text)
