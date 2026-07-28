from __future__ import annotations

import ast
import operator
import sys
from datetime import date
from typing import Callable, Dict, Type

from HelloAgentsLLM import HelloAgentsLLM
from HybridAgent import HybridAgent, ToolRegistry
from React import search


_BINARY_OPERATORS: Dict[Type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPERATORS: Dict[Type[ast.unaryop], Callable[[float], float]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def calculate(expression: str) -> str:
    """安全计算只包含数字和基本运算符的算术表达式。"""

    def evaluate(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError("只允许数字常量。")
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
            left = evaluate(node.left)
            right = evaluate(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 10:
                raise ValueError("指数绝对值不能超过 10。")
            return _BINARY_OPERATORS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
            return _UNARY_OPERATORS[type(node.op)](evaluate(node.operand))
        raise ValueError("表达式包含不允许的语法。")

    if len(expression) > 100:
        return "错误：表达式过长。"

    try:
        parsed = ast.parse(expression, mode="eval")
        value = evaluate(parsed)
        return f"{value:g}"
    except (SyntaxError, TypeError, ValueError, ZeroDivisionError) as exc:
        return f"错误：无法计算表达式：{exc}"


def build_agent() -> HybridAgent:
    """创建带网页搜索和安全计算器的混合智能体。"""
    llm_client = HelloAgentsLLM()
    tools = ToolRegistry()
    tools.register_tool(
        "Search",
        (
            "网页搜索工具。用于查询景点官网、预约方式、开放时间、"
            "门票政策等可能变化的信息。输入应是一条明确的搜索语句。"
        ),
        search,
    )
    tools.register_tool(
        "Calculator",
        (
            "安全算术计算器。用于汇总门票、餐饮和市内交通预算；"
            "输入示例：120 + 60 * 2 + 200。"
        ),
        calculate,
    )
    return HybridAgent(
        llm_client=llm_client,
        tools=tools,
        max_plan_revisions=2,
        max_actions_per_step=3,
        max_total_tool_calls=12,
        max_llm_calls=30,
    )


def default_scenario() -> str:
    """返回用于演示三种范式协作的具体旅行规划任务。"""
    today = date.today().isoformat()
    return (
        f"当前日期为 {today}。一位游客计划周末在西安游玩 2 天，"
        "预算为每人 1500 元（不含往返西安的大交通），偏好历史文化景点，"
        "希望尽量避开长时间排队。请查询关键景点当前的开放时间、门票和预约要求，"
        "给出按时间段排列的两日行程、费用估算、预约提醒，并为可能售罄或临时闭馆的"
        "景点提供同类型备选方案。无法从搜索结果确认的信息必须标记为“待核实”。"
    )


def main() -> None:
    question = " ".join(sys.argv[1:]).strip() or default_scenario()
    constraints = [
        "总预算不超过每人 1500 元，不含往返西安的大交通。",
        "优先历史文化类景点，并减少不必要的跨城区往返。",
        "涉及开放时间、门票和预约政策时，应优先搜索官方信息。",
        "搜索结果无法交叉确认时，必须标记为“待核实”。",
        "必须为售罄、闭馆或预约失败提供备选方案。",
    ]

    print("具体应用场景：西安历史文化主题两日旅行规划")
    print(f"用户任务：{question}")

    try:
        agent = build_agent()
        answer = agent.run(question, constraints=constraints)
        print("\n--- 最终答案 ---")
        print(answer)

        if agent.last_state is not None:
            state = agent.last_state
            print("\n--- 运行统计 ---")
            print(f"状态：{state.status}")
            print(f"计划版本：{state.plan_version}")
            print(f"LLM 调用：{state.llm_calls}")
            print(f"工具调用：{state.tool_calls}")
            print(f"步骤重试：{state.retries}")
    except ValueError as exc:
        print(f"配置错误：{exc}")
        print(
            "请检查 .env 中的 MODEL_NAME、OPENAI_API_KEY、"
            "OPENAI_BASE_URL 和 SERPAPI_API_KEY。"
        )
    except KeyboardInterrupt:
        print("\n用户已终止运行。")


if __name__ == "__main__":
    main()
