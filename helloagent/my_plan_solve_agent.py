"""自定义 Plan-and-Solve Agent。"""

import ast
import re
from typing import Dict, List, Optional

from hello_agents import Config, HelloAgentsLLM, Message
from hello_agents.core.agent import Agent


DEFAULT_PLANNER_PROMPT = """
你是一个顶级的AI规划专家。你的任务是将用户提出的复杂问题分解成一个由多个简单步骤组成的行动计划。
请确保计划中的每个步骤都是一个独立的、可执行的子任务，并且严格按照逻辑顺序排列。
你的输出必须是一个Python列表，其中每个元素都是一个描述子任务的字符串。

问题: {question}

请严格按照以下格式输出你的计划:
```python
["步骤1", "步骤2", "步骤3", ...]
```
"""


DEFAULT_EXECUTOR_PROMPT = """
你是一位顶级的AI执行专家。你的任务是严格按照给定的计划，一步步地解决问题。
你将收到原始问题、完整的计划、以及到目前为止已经完成的步骤和结果。
请你专注于解决"当前步骤"，并仅输出该步骤的最终答案，不要输出任何额外的解释或对话。

# 原始问题:
{question}

# 完整计划:
{plan}

# 历史步骤与结果:
{history}

# 当前步骤:
{current_step}

请仅输出针对"当前步骤"的回答:
"""


class MyPlanAndSolveAgent(Agent):
    """先规划、再逐步执行的自定义智能体。"""

    def __init__(
        self,
        name: str,
        llm: HelloAgentsLLM,
        system_prompt: Optional[str] = None,
        config: Optional[Config] = None,
        max_steps: int = 10,
        custom_prompts: Optional[Dict[str, str]] = None,
    ):
        super().__init__(name, llm, system_prompt, config)

        if max_steps < 1:
            raise ValueError("max_steps 必须大于或等于 1")

        prompts = {
            "planner": DEFAULT_PLANNER_PROMPT,
            "executor": DEFAULT_EXECUTOR_PROMPT,
        }
        if custom_prompts:
            prompts.update(custom_prompts)

        self.planner_prompt = prompts["planner"]
        self.executor_prompt = prompts["executor"]
        self.max_steps = max_steps
        self.execution_history: List[Dict[str, str]] = []

        print(f"✅ {name} 初始化完成，最大计划步骤数: {max_steps}")

    def _build_messages(self, prompt: str) -> List[Dict[str, str]]:
        """构造包含可选系统提示词的消息列表。"""
        messages: List[Dict[str, str]] = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.append({"role": "user", "content": prompt})
        return messages

    def _parse_plan(self, response: str) -> List[str]:
        """从模型响应中安全解析非空的 Python 字符串列表。"""
        if not response or not response.strip():
            raise ValueError("Planner 返回了空响应")

        cleaned = response.strip()

        # 优先提取 Markdown 代码块；如果不存在，则截取第一个完整列表。
        code_block = re.search(
            r"```(?:python)?\s*(.*?)```",
            cleaned,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if code_block:
            cleaned = code_block.group(1).strip()
        else:
            start = cleaned.find("[")
            end = cleaned.rfind("]")
            if start != -1 and end > start:
                cleaned = cleaned[start:end + 1]

        try:
            plan = ast.literal_eval(cleaned)
        except (SyntaxError, ValueError) as exc:
            raise ValueError(f"无法解析 Planner 输出: {exc}") from exc

        if not isinstance(plan, list):
            raise ValueError("Planner 输出必须是 Python 列表")

        normalized_plan = [
            step.strip()
            for step in plan
            if isinstance(step, str) and step.strip()
        ]
        if len(normalized_plan) != len(plan) or not normalized_plan:
            raise ValueError("计划必须是由非空字符串组成的列表")

        if len(normalized_plan) > self.max_steps:
            raise ValueError(
                f"计划包含 {len(normalized_plan)} 个步骤，"
                f"超过最大限制 {self.max_steps}"
            )

        return normalized_plan

    def _format_history(self) -> str:
        """将已完成步骤格式化为执行器可读的上下文。"""
        if not self.execution_history:
            return "无"

        return "\n\n".join(
            f"步骤 {index}: {record['step']}\n结果: {record['result']}"
            for index, record in enumerate(self.execution_history, start=1)
        )

    def _save_result(self, question: str, answer: str) -> None:
        """将本轮输入和最终答案写入 Agent 对话历史。"""
        self.add_message(Message(question, "user"))
        self.add_message(Message(answer, "assistant"))

    def run(self, question: str, **kwargs) -> str:
        """生成计划，按顺序执行步骤并返回最后一步结果。"""
        print(f"\n🤖 {self.name} 开始处理问题: {question}")
        self.execution_history = []

        planner_prompt = self.planner_prompt.format(question=question)
        print("\n--- 正在生成计划 ---")
        plan_response = self.llm.invoke(
            self._build_messages(planner_prompt),
            **kwargs,
        ) or ""

        try:
            plan = self._parse_plan(plan_response)
        except ValueError as exc:
            final_answer = f"无法生成有效的行动计划：{exc}"
            print(f"❌ {final_answer}")
            self._save_result(question, final_answer)
            return final_answer

        print(f"✅ 计划生成成功，共 {len(plan)} 个步骤:")
        for index, step in enumerate(plan, start=1):
            print(f"  {index}. {step}")

        print("\n--- 正在执行计划 ---")
        for index, step in enumerate(plan, start=1):
            print(f"\n-> 正在执行步骤 {index}/{len(plan)}: {step}")
            executor_prompt = self.executor_prompt.format(
                question=question,
                plan=plan,
                history=self._format_history(),
                current_step=step,
            )
            result = self.llm.invoke(
                self._build_messages(executor_prompt),
                **kwargs,
            ) or ""

            self.execution_history.append({"step": step, "result": result})
            print(f"✅ 步骤 {index} 完成: {result}")

        final_answer = self.execution_history[-1]["result"]
        self._save_result(question, final_answer)
        print(f"\n--- 任务完成 ---\n最终答案: {final_answer}")
        return final_answer

