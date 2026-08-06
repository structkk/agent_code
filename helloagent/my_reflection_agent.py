DEFAULT_PROMPTS = {
    "initial": """
请根据以下要求完成任务:

任务: {task}

请提供一个完整、准确的回答。
""",
    "reflect": """
请仔细审查以下回答，并找出可能的问题或改进空间:

# 原始任务:
{task}

# 当前回答:
{content}

请分析这个回答的质量，指出不足之处，并提出具体的改进建议。
如果回答已经很好，请回答"无需改进"。
""",
    "refine": """
请根据反馈意见改进你的回答:

# 原始任务:
{task}

# 上一轮回答:
{last_attempt}

# 反馈意见:
{feedback}

请提供一个改进后的回答。
"""
}

from typing import Dict, Optional

from hello_agents import Config, HelloAgentsLLM, Message, ReflectionAgent


class MyReflectionAgent(ReflectionAgent):

    def __init__(
        self,
        name: str,
        llm: HelloAgentsLLM,
        system_prompt: Optional[str] = None,
        config: Optional[Config] = None,
        max_reflections: int = 3,
        custom_prompts: Optional[Dict[str, str]] = None,
    ):
        # 合并提示词，允许用户只覆盖其中一部分
        prompts = DEFAULT_PROMPTS.copy()

        if custom_prompts:
            prompts.update(custom_prompts)

        super().__init__(
            name=name,
            llm=llm,
            system_prompt=system_prompt,
            config=config,
            max_iterations=max_reflections,
            custom_prompts=prompts,
        )

        self.max_reflections = max_reflections
        self.prompts = prompts

        print(
            f"✅ {name} 初始化完成，"
            f"最大反思次数: {max_reflections}"
        )
    def run(self, task: str, **kwargs) -> str:
        content = self.llm.invoke([
            {
                "role": "user",
                "content": self.prompts["initial"].format(task=task),
            }
        ], **kwargs)

        for _ in range(self.max_reflections):
            feedback = self.llm.invoke([
                {
                    "role": "user",
                    "content": self.prompts["reflect"].format(
                        task=task,
                        content=content,
                    ),
                }
            ], **kwargs)

            if "无需改进" in feedback:
                break

            content = self.llm.invoke([
                {
                    "role": "user",
                    "content": self.prompts["refine"].format(
                        task=task,
                        last_attempt=content,
                        feedback=feedback,
                    ),
                }
            ], **kwargs)

        self.add_message(Message(task, "user"))
        self.add_message(Message(content, "assistant"))
        return content
