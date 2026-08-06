"""MyPlanAndSolveAgent 综合测试。"""

from dotenv import load_dotenv
from hello_agents import HelloAgentsLLM

from my_plan_solve_agent import MyPlanAndSolveAgent


# 覆盖 Windows/PyCharm 中可能遗留的同名旧环境变量。
load_dotenv(override=True)

llm = HelloAgentsLLM()

agent = MyPlanAndSolveAgent(
    name="我的规划执行助手",
    llm=llm,
)

question = (
    "一个水果店周一卖出了15个苹果。周二卖出的苹果数量是周一的两倍。"
    "周三卖出的数量比周二少了5个。请问这三天总共卖出了多少个苹果？"
)

result = agent.run(question)
print(f"\n最终结果: {result}")
print(f"对话历史: {len(agent.get_history())} 条消息")

