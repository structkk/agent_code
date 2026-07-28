from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from HelloAgentsLLM import HelloAgentsLLM


class HybridAgentError(RuntimeError):
    """混合智能体运行时异常。"""


class BudgetExceededError(HybridAgentError):
    """智能体调用预算耗尽。"""


def _as_string_list(value: Any) -> List[str]:
    """把模型返回的字符串或列表统一转换为字符串列表。"""
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _compact_json(value: Any, max_chars: int = 10000) -> str:
    """把状态转换成适合放入提示词的紧凑 JSON。"""
    text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...（内容过长，已截断）"


def _extract_json(text: str) -> Any:
    """从纯 JSON 或 Markdown 代码块中提取第一个 JSON 对象。"""
    if not text or not text.strip():
        raise ValueError("模型返回为空。")

    candidates = []
    fenced_blocks = re.findall(
        r"```(?:json)?\s*([\s\S]*?)```",
        text,
        flags=re.IGNORECASE,
    )
    candidates.extend(block.strip() for block in fenced_blocks)
    candidates.append(text.strip())

    decoder = json.JSONDecoder()
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

        for index, char in enumerate(candidate):
            if char not in "[{":
                continue
            try:
                value, _ = decoder.raw_decode(candidate[index:])
                return value
            except json.JSONDecodeError:
                continue

    raise ValueError("无法从模型响应中解析 JSON。")


@dataclass
class PlanStep:
    """Plan-and-Solve 生成的单个可执行步骤。"""

    step_id: str
    goal: str
    dependencies: List[str] = field(default_factory=list)
    tools: List[str] = field(default_factory=list)
    success_criteria: List[str] = field(default_factory=list)
    max_attempts: int = 2

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlanStep":
        max_attempts = data.get("max_attempts", 2)
        try:
            max_attempts = int(max_attempts)
        except (TypeError, ValueError):
            max_attempts = 2

        return cls(
            step_id=str(data.get("step_id", "")).strip(),
            goal=str(data.get("goal", "")).strip(),
            dependencies=_as_string_list(data.get("dependencies")),
            tools=_as_string_list(data.get("tools")),
            success_criteria=_as_string_list(data.get("success_criteria")),
            max_attempts=max(1, min(max_attempts, 3)),
        )


@dataclass
class StepResult:
    """ReAct 局部执行器返回的步骤结果。"""

    step_id: str
    status: str
    output: str
    observations: List[Dict[str, Any]] = field(default_factory=list)
    error: str = ""


@dataclass
class ReflectionDecision:
    """Reflection 对步骤结果给出的控制决策。"""

    decision: str
    reason: str
    feedback: str = ""
    missing_information: List[str] = field(default_factory=list)


@dataclass
class HybridAgentState:
    """三种范式共享的统一任务状态。"""

    task: str
    constraints: List[str] = field(default_factory=list)
    plan: List[PlanStep] = field(default_factory=list)
    plan_version: int = 1
    current_step: int = 0
    observations: List[Dict[str, Any]] = field(default_factory=list)
    step_results: Dict[str, StepResult] = field(default_factory=dict)
    reflections: List[Dict[str, Any]] = field(default_factory=list)
    step_attempts: Dict[str, int] = field(default_factory=dict)
    search_failures: Dict[str, int] = field(default_factory=dict)
    unavailable_targets: Dict[str, str] = field(default_factory=dict)
    tool_calls: int = 0
    llm_calls: int = 0
    retries: int = 0
    status: str = "planning"
    blocked_reason: str = ""


class LLMGateway:
    """统一管理 LLM 调用、调用预算和 JSON 修复。"""

    def __init__(self, llm_client: HelloAgentsLLM, max_calls: int = 30):
        self.llm_client = llm_client
        self.max_calls = max_calls
        self.call_count = 0

    def ask(self, prompt: str, temperature: float = 0) -> str:
        if self.call_count >= self.max_calls:
            raise BudgetExceededError(
                f"LLM 调用次数已达到上限 {self.max_calls}。"
            )

        self.call_count += 1
        response = self.llm_client.think(
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
        )
        if not response or not response.strip():
            raise HybridAgentError("LLM 未返回有效内容。")
        return response.strip()

    def ask_json(self, prompt: str, schema_hint: str) -> Dict[str, Any]:
        """请求结构化 JSON；第一次失败时允许模型修复一次格式。"""
        raw_response = self.ask(prompt)
        try:
            parsed = _extract_json(raw_response)
        except ValueError:
            repair_prompt = f"""
你是一个 JSON 格式修复器。请把下面的内容转换为合法 JSON。
不得补充解释，不得使用 Markdown 代码块。

目标结构：
{schema_hint}

待修复内容：
{raw_response}
"""
            parsed = _extract_json(self.ask(repair_prompt))

        if not isinstance(parsed, dict):
            raise HybridAgentError("模型返回的 JSON 顶层必须是对象。")
        return parsed


class ToolRegistry:
    """工具注册表，兼容本章 ToolExecutor 的注册习惯。"""

    def __init__(self):
        self.tools: Dict[str, Dict[str, Any]] = {}

    def register_tool(
        self,
        name: str,
        description: str,
        func: Callable[[str], Any],
    ) -> None:
        self.tools[name] = {
            "description": description,
            "func": func,
        }

    def registerTool(
        self,
        name: str,
        description: str,
        func: Callable[[str], Any],
    ) -> None:
        """兼容 React.py 中的驼峰命名接口。"""
        self.register_tool(name, description, func)

    def descriptions(self, allowed_tools: Optional[List[str]] = None) -> str:
        names = allowed_tools or list(self.tools)
        descriptions = []
        for name in names:
            if name in self.tools:
                descriptions.append(
                    f"- {name}: {self.tools[name]['description']}"
                )
        return "\n".join(descriptions) or "- 无可用工具"

    def execute(self, name: str, tool_input: str) -> Tuple[bool, str]:
        tool = self.tools.get(name)
        if tool is None:
            return False, f"错误：未注册工具 {name!r}。"

        try:
            result = tool["func"](tool_input)
            if result is None:
                return False, f"错误：工具 {name!r} 未返回结果。"
            text = str(result)
            if (
                text.startswith("错误:")
                or text.startswith("错误：")
                or text.startswith("搜索时发生错误")
            ):
                return False, text
            return True, text
        except Exception as exc:
            return False, f"错误：工具 {name!r} 执行失败：{exc}"


class Planner:
    """Plan-and-Solve：生成、修复或调整全局计划。"""

    PLAN_SCHEMA = """
{
  "steps": [
    {
      "step_id": "S1",
      "goal": "清晰且可执行的步骤目标",
      "dependencies": [],
      "tools": ["Search"],
      "success_criteria": ["可验证的成功标准"],
      "max_attempts": 2
    }
  ]
}
"""

    def __init__(self, gateway: LLMGateway, tools: ToolRegistry):
        self.gateway = gateway
        self.tools = tools

    def create_plan(
        self,
        task: str,
        constraints: List[str],
    ) -> List[PlanStep]:
        prompt = f"""
你是 Plan-and-Solve 规划器。请把复杂任务拆成 2 至 6 个按依赖关系排列的步骤。
计划必须覆盖事实收集、约束处理、结果综合；只有确实需要外部信息时才调用工具。
成功标准必须具体、可验证。只输出合法 JSON，不要输出解释或 Markdown。

用户任务：
{task}

约束：
{_compact_json(constraints)}

可用工具：
{self.tools.descriptions()}

输出结构：
{self.PLAN_SCHEMA}
"""
        payload = self.gateway.ask_json(prompt, self.PLAN_SCHEMA)
        return self._parse_steps(payload)

    def repair_plan(
        self,
        task: str,
        invalid_plan: List[PlanStep],
        issues: List[str],
        constraints: List[str],
    ) -> List[PlanStep]:
        prompt = f"""
你是 Plan-and-Solve 计划修复器。以下计划未通过验证，请修复全部问题。
只输出修复后的合法 JSON，不要解释，不要使用 Markdown。

原始任务：
{task}

约束：
{_compact_json(constraints)}

可用工具：
{self.tools.descriptions()}

原计划：
{_compact_json([asdict(step) for step in invalid_plan])}

验证问题：
{_compact_json(issues)}

输出结构：
{self.PLAN_SCHEMA}
"""
        payload = self.gateway.ask_json(prompt, self.PLAN_SCHEMA)
        return self._parse_steps(payload)

    def replan(
        self,
        state: HybridAgentState,
        failed_step: PlanStep,
        decision: ReflectionDecision,
    ) -> List[PlanStep]:
        completed = {
            step_id: result.output
            for step_id, result in state.step_results.items()
        }
        prompt = f"""
你是 Plan-and-Solve 重规划器。当前计划在执行中遇到问题。
请保留已经完成的结果，只生成尚未完成的剩余步骤。
新 step_id 不得与已完成 step_id 重复；dependencies 只能引用已完成步骤，
或者引用本次新计划中排在当前步骤之前的 step_id。
只输出合法 JSON，不要输出解释或 Markdown。

原始任务：
{state.task}

约束：
{_compact_json(state.constraints)}

已完成步骤：
{_compact_json(completed)}

失败步骤：
{_compact_json(asdict(failed_step))}

反思结论：
{_compact_json(asdict(decision))}

可用工具：
{self.tools.descriptions()}

输出结构：
{self.PLAN_SCHEMA}
"""
        payload = self.gateway.ask_json(prompt, self.PLAN_SCHEMA)
        return self._parse_steps(payload)

    @staticmethod
    def _parse_steps(payload: Dict[str, Any]) -> List[PlanStep]:
        raw_steps = payload.get("steps", [])
        if not isinstance(raw_steps, list):
            raise HybridAgentError("计划中的 steps 必须是列表。")
        return [
            PlanStep.from_dict(item)
            for item in raw_steps
            if isinstance(item, dict)
        ]


class PlanValidator:
    """在执行前检查计划结构、依赖关系和工具名称。"""

    def __init__(self, tools: ToolRegistry, max_steps: int = 8):
        self.tools = tools
        self.max_steps = max_steps

    def validate(
        self,
        plan: List[PlanStep],
        completed_ids: Optional[List[str]] = None,
    ) -> List[str]:
        issues = []
        completed = set(completed_ids or [])
        known_ids = set(completed)

        if not plan:
            return ["计划不能为空。"]
        if len(plan) > self.max_steps:
            issues.append(
                f"计划步骤数 {len(plan)} 超过上限 {self.max_steps}。"
            )

        for index, step in enumerate(plan, start=1):
            if not step.step_id:
                issues.append(f"第 {index} 步缺少 step_id。")
                continue
            if step.step_id in known_ids:
                issues.append(f"step_id {step.step_id!r} 重复。")
            if not step.goal:
                issues.append(f"步骤 {step.step_id!r} 缺少 goal。")
            if not step.success_criteria:
                issues.append(
                    f"步骤 {step.step_id!r} 缺少 success_criteria。"
                )

            unknown_dependencies = [
                dependency
                for dependency in step.dependencies
                if dependency not in known_ids
            ]
            if unknown_dependencies:
                issues.append(
                    f"步骤 {step.step_id!r} 引用了尚不存在的依赖："
                    f"{unknown_dependencies}。"
                )

            unknown_tools = [
                name for name in step.tools if name not in self.tools.tools
            ]
            if unknown_tools:
                issues.append(
                    f"步骤 {step.step_id!r} 使用了未注册工具："
                    f"{unknown_tools}。"
                )

            known_ids.add(step.step_id)

        return issues


class ReActStepExecutor:
    """ReAct：针对单个计划步骤执行 Thought-Action-Observation 循环。"""

    ACTION_SCHEMA = """
调用工具：
{
  "thought_summary": "一句话说明为什么采取该动作",
  "action": {
    "type": "tool",
    "tool_name": "Search",
    "tool_input": "查询内容",
    "search_target": "正在查询的地点或对象名称；使用搜索工具时必须填写"
  }
}

完成步骤：
{
  "thought_summary": "一句话说明为什么已有信息足够",
  "action": {
    "type": "finish",
    "answer": "该步骤的完整结论"
  }
}
"""

    def __init__(
        self,
        gateway: LLMGateway,
        tools: ToolRegistry,
        max_actions_per_step: int = 3,
        max_observation_chars: int = 6000,
    ):
        self.gateway = gateway
        self.tools = tools
        self.max_actions_per_step = max_actions_per_step
        self.max_observation_chars = max_observation_chars

    def execute(
        self,
        state: HybridAgentState,
        step: PlanStep,
        retry_feedback: str = "",
        remaining_tool_budget: int = 0,
    ) -> StepResult:
        # 重试当前步骤时继续使用之前已经取得的 Observation，避免重复搜索。
        local_observations = [
            observation
            for observation in state.observations
            if observation.get("step_id") == step.step_id
        ]
        seen_calls = {
            (
                str(observation.get("tool_name", "")),
                str(observation.get("tool_input", "")),
            )
            for observation in local_observations
            if observation.get("tool_name")
        }

        # 工具预算用尽后额外保留一次模型收尾机会，使其能够基于已有
        # Observation 输出 Finish，而不是直接把步骤判定为失败。
        action_limit = self.max_actions_per_step + 1
        for action_index in range(1, action_limit + 1):
            prompt = self._build_prompt(
                state=state,
                step=step,
                local_observations=local_observations,
                retry_feedback=retry_feedback,
                action_index=action_index,
                remaining_tool_budget=remaining_tool_budget,
            )
            payload = self.gateway.ask_json(prompt, self.ACTION_SCHEMA)
            action = payload.get("action", {})
            if not isinstance(action, dict):
                local_observations.append(
                    {
                        "step_id": step.step_id,
                        "action": "Invalid",
                        "observation": "action 必须是 JSON 对象。",
                        "success": False,
                    }
                )
                continue

            action_type = str(action.get("type", "")).strip().lower()
            thought_summary = str(
                payload.get("thought_summary", "")
            ).strip()
            print(
                f"\n[ReAct {step.step_id}] "
                f"Thought: {thought_summary or '未提供摘要'}"
            )

            if action_type == "finish":
                answer = str(action.get("answer", "")).strip()
                if answer:
                    return StepResult(
                        step_id=step.step_id,
                        status="success",
                        output=answer,
                        observations=local_observations,
                    )
                local_observations.append(
                    {
                        "step_id": step.step_id,
                        "action": "Finish",
                        "observation": "Finish 的 answer 不能为空。",
                        "success": False,
                    }
                )
                continue

            if action_type != "tool":
                local_observations.append(
                    {
                        "step_id": step.step_id,
                        "action": "Invalid",
                        "observation": (
                            "action.type 只能是 'tool' 或 'finish'。"
                        ),
                        "success": False,
                    }
                )
                continue

            tool_name = str(action.get("tool_name", "")).strip()
            tool_input = action.get("tool_input", "")
            if not isinstance(tool_input, str):
                tool_input = json.dumps(tool_input, ensure_ascii=False)
            tool_input = tool_input.strip()
            search_target = str(
                action.get("search_target", "")
            ).strip()

            target_key, target_name = self._get_search_target(
                tool_name=tool_name,
                search_target=search_target,
                step=step,
            )

            if (
                tool_name.casefold() == "search"
                and target_key in state.unavailable_targets
            ):
                local_observations.append(
                    {
                        "step_id": step.step_id,
                        "action": f"{tool_name}[{tool_input}]",
                        "tool_name": tool_name,
                        "tool_input": tool_input,
                        "search_target": target_name,
                        "observation": (
                            f"{target_name} 已连续两次查询不到，"
                            "不得继续搜索；请使用备选方案或完成当前步骤。"
                        ),
                        "success": False,
                    }
                )
                continue

            if step.tools and tool_name not in step.tools:
                local_observations.append(
                    {
                        "step_id": step.step_id,
                        "action": f"{tool_name}[{tool_input}]",
                        "observation": (
                            f"当前步骤只允许使用工具：{step.tools}。"
                        ),
                        "success": False,
                    }
                )
                continue

            if remaining_tool_budget <= 0:
                local_observations.append(
                    {
                        "step_id": step.step_id,
                        "action": f"{tool_name}[{tool_input}]",
                        "tool_name": tool_name,
                        "tool_input": tool_input,
                        "search_target": target_name,
                        "observation": (
                            "工具调用预算已耗尽。不得继续调用工具；"
                            "请利用已有 Observation 输出 Finish。"
                            "无法确认的内容必须标记为“待核实”。"
                        ),
                        "success": False,
                    }
                )
                continue

            call_signature = (tool_name, tool_input)
            if call_signature in seen_calls:
                local_observations.append(
                    {
                        "step_id": step.step_id,
                        "action": f"{tool_name}[{tool_input}]",
                        "observation": "禁止使用相同参数重复调用同一工具。",
                        "success": False,
                    }
                )
                continue
            seen_calls.add(call_signature)

            print(
                f"[ReAct {step.step_id}] "
                f"Action: {tool_name}[{tool_input}]"
            )
            success, observation = self.tools.execute(tool_name, tool_input)
            observation = observation[: self.max_observation_chars]
            print(
                f"[ReAct {step.step_id}] "
                f"Observation: {observation}"
            )

            record = {
                "step_id": step.step_id,
                "action": f"{tool_name}[{tool_input}]",
                "tool_name": tool_name,
                "tool_input": tool_input,
                "search_target": target_name,
                "observation": observation,
                "success": success,
            }
            local_observations.append(record)
            state.observations.append(record)
            state.tool_calls += 1
            remaining_tool_budget -= 1

            if tool_name.casefold() == "search":
                if self._is_no_result_observation(observation):
                    failure_count = (
                        state.search_failures.get(target_key, 0) + 1
                    )
                    state.search_failures[target_key] = failure_count
                    record["search_failure_count"] = failure_count

                    if failure_count >= 2:
                        state.unavailable_targets[target_key] = target_name
                        record["observation"] = (
                            f"{observation}\n\n"
                            f"降级结论：{target_name} 已连续两次搜索"
                            "不出有效结果，标记为“查询不到”，"
                            "当前步骤结束并进入下一步。"
                        )
                        return StepResult(
                            step_id=step.step_id,
                            status="unavailable",
                            output=(
                                f"{target_name}：连续两次搜索均未返回"
                                "有效结果，已标记为“查询不到”。"
                            ),
                            observations=local_observations,
                            error="",
                        )
                elif success:
                    # 一次有效结果即可清除该地点此前的无结果计数。
                    state.search_failures.pop(target_key, None)

        return StepResult(
            step_id=step.step_id,
            status="failed",
            output="",
            observations=local_observations,
            error=(
                f"在 {self.max_actions_per_step} 次 ReAct 动作内"
                "未完成当前步骤。"
            ),
        )

    def _build_prompt(
        self,
        state: HybridAgentState,
        step: PlanStep,
        local_observations: List[Dict[str, Any]],
        retry_feedback: str,
        action_index: int,
        remaining_tool_budget: int,
    ) -> str:
        completed = {
            step_id: result.output
            for step_id, result in state.step_results.items()
        }
        return f"""
你是 ReAct 局部执行器，只负责当前计划步骤。
每轮只能执行一个动作：调用一次工具，或结束当前步骤。
不得虚构工具结果。事实结论必须来自 Observation；无法确认时明确标注。
thought_summary 只能给出简短决策依据，不要输出冗长推理过程。
只输出合法 JSON，不要输出解释或 Markdown。

原始任务：
{state.task}

任务约束：
{_compact_json(state.constraints)}

当前步骤：
{_compact_json(asdict(step))}

已完成步骤：
{_compact_json(completed)}

当前步骤已有 Observation：
{_compact_json(local_observations)}

已经标记为“查询不到”的地点或对象：
{_compact_json(list(state.unavailable_targets.values()))}

上轮反思反馈：
{retry_feedback or "无"}

当前是本步骤第 {action_index} 个动作。
剩余工具调用预算：{remaining_tool_budget}

如果剩余工具调用预算为 0，不得再返回 tool 动作。你必须利用已有
Observation 返回 finish；无法确认的信息标记为“待核实”。
如果某地点已标记为“查询不到”，不得继续搜索该地点，应保留该标记并完成当前步骤。

当前步骤允许使用的工具：
{self.tools.descriptions(step.tools)}

输出格式二选一：
{self.ACTION_SCHEMA}
"""

    @staticmethod
    def _get_search_target(
        tool_name: str,
        search_target: str,
        step: PlanStep,
    ) -> Tuple[str, str]:
        """确定搜索对象；模型未填写时使用当前步骤作为稳定的回退键。"""
        if tool_name.casefold() != "search":
            return "", ""

        target_name = search_target or step.goal
        normalized = re.sub(
            r"[\W_]+",
            "",
            target_name,
            flags=re.UNICODE,
        ).casefold()
        if not normalized:
            normalized = f"step{step.step_id.casefold()}"
        return normalized, target_name

    @staticmethod
    def _is_no_result_observation(observation: str) -> bool:
        """识别搜索工具明确返回的“没有结果”，不把网络错误算入其中。"""
        normalized = observation.casefold()
        markers = (
            "没有找到",
            "未找到相关",
            "没有相关结果",
            "无相关结果",
            "no results",
            "did not match any documents",
            "0 results",
        )
        return any(marker in normalized for marker in markers)


class Reflector:
    """Reflection：校验步骤结果，并决定通过、重试或重规划。"""

    STEP_SCHEMA = """
{
  "decision": "pass | retry | replan | blocked",
  "reason": "判断依据",
  "feedback": "供下一次执行或重规划使用的具体建议",
  "missing_information": []
}
"""

    FINAL_SCHEMA = """
{
  "decision": "pass | revise | recover",
  "reason": "判断依据",
  "revision_instruction": "需要修改的内容；通过时为空字符串"
}
"""

    def __init__(self, gateway: LLMGateway):
        self.gateway = gateway

    def review_step(
        self,
        state: HybridAgentState,
        step: PlanStep,
        result: StepResult,
    ) -> ReflectionDecision:
        prompt = f"""
你是 Reflection 步骤审查器。请根据成功标准检查结果是否完整、可信、相关。

决策规则：
- pass：已满足全部成功标准。
- retry：计划仍正确，但需要更换查询、补充证据或修正工具动作。
- replan：当前步骤设计或剩余计划本身不合理。
- blocked：缺少用户信息、权限或外部服务，继续执行也无法恢复。

如果执行状态为 failed，不能判定为 pass。
只输出合法 JSON，不要输出解释或 Markdown。

原始任务：
{state.task}

当前步骤：
{_compact_json(asdict(step))}

执行结果：
{_compact_json(asdict(result))}

输出结构：
{self.STEP_SCHEMA}
"""
        payload = self.gateway.ask_json(prompt, self.STEP_SCHEMA)
        decision = str(payload.get("decision", "retry")).strip().lower()
        if decision not in {"pass", "retry", "replan", "blocked"}:
            decision = "retry"
        if result.status != "success" and decision == "pass":
            decision = "retry"

        return ReflectionDecision(
            decision=decision,
            reason=str(payload.get("reason", "")).strip(),
            feedback=str(payload.get("feedback", "")).strip(),
            missing_information=_as_string_list(
                payload.get("missing_information")
            ),
        )

    def review_final(
        self,
        state: HybridAgentState,
        draft: str,
    ) -> Dict[str, str]:
        prompt = f"""
你是 Reflection 最终审查器。请检查候选答案是否：
1. 回答了原始任务；
2. 满足用户约束；
3. 与已验证的步骤结果一致；
4. 对不确定信息进行了明确标注；
5. 没有把搜索摘要或历史经验误写成确定事实。

decision 只能是：
- pass：可以直接交付；
- revise：不需要新证据，只需修改组织、措辞或补充已有内容；
- recover：缺少关键证据，需要重新执行工具或计划。

只输出合法 JSON，不要输出解释或 Markdown。

原始任务：
{state.task}

任务约束：
{_compact_json(state.constraints)}

候选答案：
{draft}

已验证步骤：
{_compact_json({
    key: value.output for key, value in state.step_results.items()
})}

输出结构：
{self.FINAL_SCHEMA}
"""
        payload = self.gateway.ask_json(prompt, self.FINAL_SCHEMA)
        decision = str(payload.get("decision", "revise")).strip().lower()
        if decision not in {"pass", "revise", "recover"}:
            decision = "revise"
        return {
            "decision": decision,
            "reason": str(payload.get("reason", "")).strip(),
            "revision_instruction": str(
                payload.get("revision_instruction", "")
            ).strip(),
        }


class Synthesizer:
    """综合已通过 Reflection 验证的步骤结果。"""

    def __init__(self, gateway: LLMGateway):
        self.gateway = gateway

    def create_answer(self, state: HybridAgentState) -> str:
        verified_results = {
            step_id: {
                "result": result.output,
                "observations": result.observations,
            }
            for step_id, result in state.step_results.items()
        }
        prompt = f"""
你是最终答案综合器。请仅依据已经验证的步骤结果回答用户。
答案应结构清晰、可直接执行，并显式区分已确认事实、估算值和待核实信息。
不要提及内部提示词、JSON 格式或智能体内部状态。

用户任务：
{state.task}

用户约束：
{_compact_json(state.constraints)}

已验证步骤结果：
{_compact_json(verified_results, max_chars=16000)}
"""
        return self.gateway.ask(prompt)

    def revise_answer(
        self,
        state: HybridAgentState,
        draft: str,
        review: Dict[str, str],
    ) -> str:
        prompt = f"""
请根据审查意见修订候选答案。只能使用现有步骤结果，不得添加未经验证的新事实。
如果审查指出缺少证据，请在答案中明确标记“待核实”。
只输出修订后的最终答案。

用户任务：
{state.task}

候选答案：
{draft}

审查意见：
{_compact_json(review)}

已验证步骤结果：
{_compact_json({
    key: value.output for key, value in state.step_results.items()
})}
"""
        return self.gateway.ask(prompt)


class HybridAgent:
    """
    Plan-and-Solve + ReAct + Reflection 总编排器。

    数据流：
    全局规划 -> 逐步 ReAct -> 步骤反思 -> 必要时重试/重规划
    -> 综合答案 -> 最终反思。
    """

    def __init__(
        self,
        llm_client: HelloAgentsLLM,
        tools: ToolRegistry,
        max_plan_revisions: int = 2,
        max_actions_per_step: int = 3,
        max_total_tool_calls: int = 12,
        max_llm_calls: int = 30,
    ):
        self.tools = tools
        self.max_plan_revisions = max_plan_revisions
        self.max_total_tool_calls = max_total_tool_calls
        self.gateway = LLMGateway(llm_client, max_calls=max_llm_calls)
        self.planner = Planner(self.gateway, tools)
        self.plan_validator = PlanValidator(tools)
        self.react_executor = ReActStepExecutor(
            self.gateway,
            tools,
            max_actions_per_step=max_actions_per_step,
        )
        self.reflector = Reflector(self.gateway)
        self.synthesizer = Synthesizer(self.gateway)
        self.last_state: Optional[HybridAgentState] = None

    def run(
        self,
        task: str,
        constraints: Optional[List[str]] = None,
    ) -> str:
        state = HybridAgentState(
            task=task.strip(),
            constraints=constraints or [],
        )
        self.last_state = state

        try:
            self._create_valid_plan(state)

            while state.current_step < len(state.plan):
                step = state.plan[state.current_step]
                state.status = "executing"
                print(
                    f"\n{'=' * 64}\n"
                    f"执行步骤 {state.current_step + 1}/{len(state.plan)} "
                    f"[{step.step_id}]：{step.goal}"
                )

                outcome = self._run_step_with_reflection(state, step)
                if outcome == "passed":
                    state.current_step += 1
                    continue
                if outcome == "replanned":
                    continue
                return self._build_blocked_response(
                    state,
                    state.blocked_reason or "步骤执行后无法安全恢复。",
                )

            state.status = "synthesizing"
            draft = self.synthesizer.create_answer(state)

            state.status = "final_review"
            final_review = self.reflector.review_final(state, draft)
            state.reflections.append(
                {"scope": "final", **final_review}
            )

            if final_review["decision"] == "pass":
                answer = draft
            else:
                # recover 需要新证据，但预算内不再开启无限循环；
                # 修订时必须显式标记证据缺口。
                answer = self.synthesizer.revise_answer(
                    state,
                    draft,
                    final_review,
                )

            state.status = "done"
            state.llm_calls = self.gateway.call_count
            print(f"\n{'=' * 64}\n混合智能体执行完成。")
            return answer

        except (HybridAgentError, ValueError) as exc:
            state.status = "blocked"
            return self._build_blocked_response(state, str(exc))
        finally:
            # 无论成功、异常还是主动进入 blocked，都同步真实调用次数。
            state.llm_calls = self.gateway.call_count

    def _create_valid_plan(self, state: HybridAgentState) -> None:
        state.status = "planning"
        plan = self.planner.create_plan(state.task, state.constraints)

        for revision in range(self.max_plan_revisions + 1):
            issues = self.plan_validator.validate(plan)
            if not issues:
                state.plan = plan
                print("\n--- 已生成并验证全局计划 ---")
                for step in state.plan:
                    print(f"{step.step_id}: {step.goal}")
                return

            if revision >= self.max_plan_revisions:
                raise HybridAgentError(
                    "计划在修复后仍未通过验证：" + "；".join(issues)
                )

            print(f"计划验证失败，正在第 {revision + 1} 次修复：{issues}")
            plan = self.planner.repair_plan(
                state.task,
                plan,
                issues,
                state.constraints,
            )
            state.plan_version += 1

    def _run_step_with_reflection(
        self,
        state: HybridAgentState,
        step: PlanStep,
    ) -> str:
        retry_feedback = ""

        while True:
            attempt = state.step_attempts.get(step.step_id, 0) + 1
            state.step_attempts[step.step_id] = attempt

            result = self.react_executor.execute(
                state=state,
                step=step,
                retry_feedback=retry_feedback,
                remaining_tool_budget=(
                    self.max_total_tool_calls - state.tool_calls
                ),
            )

            if result.status == "unavailable":
                decision = ReflectionDecision(
                    decision="pass",
                    reason=(
                        "同一地点已连续两次搜索不到有效结果，"
                        "按降级规则标记为“查询不到”并继续后续步骤。"
                    ),
                    feedback="",
                    missing_information=[result.output],
                )
                state.reflections.append(
                    {
                        "scope": "step",
                        "step_id": step.step_id,
                        "attempt": attempt,
                        **asdict(decision),
                    }
                )
                state.step_results[step.step_id] = result
                print(
                    f"[Reflection {step.step_id}] "
                    f"pass: {decision.reason}"
                )
                return "passed"

            state.status = "reflecting"
            decision = self.reflector.review_step(state, step, result)
            state.reflections.append(
                {
                    "scope": "step",
                    "step_id": step.step_id,
                    "attempt": attempt,
                    **asdict(decision),
                }
            )
            print(
                f"[Reflection {step.step_id}] "
                f"{decision.decision}: {decision.reason}"
            )

            if decision.decision == "pass":
                state.step_results[step.step_id] = result
                return "passed"

            if (
                decision.decision == "retry"
                and attempt < step.max_attempts
            ):
                state.retries += 1
                retry_feedback = decision.feedback or decision.reason
                continue

            if decision.decision in {"retry", "replan"}:
                if state.plan_version > self.max_plan_revisions:
                    state.status = "blocked"
                    state.blocked_reason = (
                        f"步骤 {step.step_id} 未通过检查，且计划修订次数"
                        f"已达到上限 {self.max_plan_revisions}。"
                        f"最后一次反思：{decision.reason}"
                    )
                    return "blocked"

                replanned = self.planner.replan(state, step, decision)
                completed_ids = list(state.step_results)
                issues = self.plan_validator.validate(
                    replanned,
                    completed_ids=completed_ids,
                )
                if issues:
                    state.status = "blocked"
                    state.blocked_reason = (
                        "重规划结果未通过验证：" + "；".join(issues)
                    )
                    state.reflections.append(
                        {
                            "scope": "replan_validation",
                            "issues": issues,
                        }
                    )
                    return "blocked"

                completed_plan = state.plan[: state.current_step]
                state.plan = completed_plan + replanned
                state.current_step = len(completed_plan)
                state.plan_version += 1
                print(
                    f"已根据反思生成第 {state.plan_version} 版计划。"
                )
                return "replanned"

            state.status = "blocked"
            state.blocked_reason = (
                decision.reason
                or f"步骤 {step.step_id} 被 Reflection 判定为不可恢复。"
            )
            return "blocked"

    @staticmethod
    def _build_blocked_response(
        state: HybridAgentState,
        reason: str,
    ) -> str:
        state.status = "blocked"
        state.blocked_reason = reason
        completed = [
            f"- {step_id}: {result.output}"
            for step_id, result in state.step_results.items()
        ]
        completed_text = "\n".join(completed) if completed else "- 暂无"
        return (
            "任务未能完整完成。\n\n"
            f"停止原因：{reason}\n\n"
            f"已经完成的结果：\n{completed_text}\n\n"
            "建议检查模型配置、工具权限、网络状态或补充缺失信息后重试。"
        )


__all__ = [
    "HybridAgent",
    "HybridAgentState",
    "PlanStep",
    "ReflectionDecision",
    "StepResult",
    "ToolRegistry",
]
