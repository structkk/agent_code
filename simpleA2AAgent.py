"""基于 a2a-sdk 1.x 的计算器 A2A 智能体示例。

默认执行本地技能测试并退出：
    python simpleA2AAgent.py

启动符合 A2A 1.0 协议的 HTTP 服务：
    python simpleA2AAgent.py --serve
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Callable

try:
    import uvicorn
    from starlette.applications import Starlette

    from a2a.helpers import new_text_message
    from a2a.server.agent_execution import AgentExecutor, RequestContext
    from a2a.server.events import EventQueue
    from a2a.server.request_handlers import DefaultRequestHandler
    from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
    from a2a.server.tasks import InMemoryTaskStore
    from a2a.types import (
        AgentCapabilities,
        AgentCard,
        AgentInterface,
        AgentSkill,
    )
except ImportError as exc:
    raise SystemExit(
        "A2A 1.x 服务依赖导入失败。请使用当前解释器执行：\n"
        "python -m pip install \"a2a-sdk[http-server]>=1.0,<2\" uvicorn starlette\n"
        f"原始错误：{exc}"
    ) from exc


def _format_number(value: float) -> str:
    """整数不显示多余的小数点，其他数值使用紧凑格式。"""
    return str(int(value)) if value.is_integer() else f"{value:g}"


def _parse_numbers(query: str, operator: str) -> list[float]:
    """从规范化表达式中解析操作数。"""
    expression = query.replace("计算", "").strip()
    parts = [part.strip() for part in expression.split(operator)]
    if len(parts) < 2 or any(not part for part in parts):
        raise ValueError(f"至少需要两个由 {operator} 分隔的数字")
    return [float(part) for part in parts]


@dataclass
class CalculatorAgent:
    """计算器业务逻辑，与 A2A 传输层解耦。"""

    name: str = "calculator-agent"

    @property
    def skill_names(self) -> list[str]:
        return ["add", "multiply", "info"]

    def add(self, query: str) -> str:
        """执行两个或多个数字的加法。"""
        try:
            normalized = query.replace("加上", "+").replace("加", "+")
            numbers = _parse_numbers(normalized, "+")
            expression = " + ".join(_format_number(number) for number in numbers)
            return f"计算结果: {expression} = {_format_number(sum(numbers))}"
        except ValueError as exc:
            return f"加法输入错误: {exc}。示例：计算 5 + 3"

    def multiply(self, query: str) -> str:
        """执行两个或多个数字的乘法。"""
        try:
            normalized = query.replace("乘以", "*").replace("×", "*")
            numbers = _parse_numbers(normalized, "*")
            result = 1.0
            for number in numbers:
                result *= number
            expression = " × ".join(_format_number(number) for number in numbers)
            return f"计算结果: {expression} = {_format_number(result)}"
        except ValueError as exc:
            return f"乘法输入错误: {exc}。示例：计算 5 * 3"

    def info(self, _query: str = "") -> str:
        """返回智能体能力信息。"""
        return f"我是 {self.name}，支持的技能: {', '.join(self.skill_names)}"

    def respond(self, query: str) -> str:
        """根据文本选择计算技能。"""
        text = query.strip()
        if not text:
            return "请输入任务，例如：计算 10 + 5 或 计算 6 * 7"
        if "信息" in text or text.lower() in {"info", "help", "帮助"}:
            return self.info(text)
        if "+" in text or "加" in text:
            return self.add(text)
        if "*" in text or "×" in text or "乘以" in text:
            return self.multiply(text)
        return "暂不支持该任务。可用示例：计算 10 + 5、计算 6 * 7、获取信息"


class CalculatorAgentExecutor(AgentExecutor):
    """将 A2A 请求转换为计算器调用，并向事件队列返回消息。"""

    def __init__(self, calculator: CalculatorAgent) -> None:
        self.calculator = calculator

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        query = context.get_user_input()
        response = self.calculator.respond(query)
        await event_queue.enqueue_event(new_text_message(response))

    async def cancel(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        del context, event_queue
        raise NotImplementedError("该计算器任务执行时间很短，不支持取消操作")


def build_agent_card(public_url: str) -> AgentCard:
    """构建 A2A 1.0 Agent Card。"""
    endpoint = public_url.rstrip("/") + "/"
    skills = [
        AgentSkill(
            id="add",
            name="数字加法",
            description="计算两个或多个数字之和",
            input_modes=["text/plain"],
            output_modes=["text/plain"],
            tags=["calculator", "math", "addition"],
            examples=["计算 10 + 5", "1.5 加上 2.5"],
        ),
        AgentSkill(
            id="multiply",
            name="数字乘法",
            description="计算两个或多个数字之积",
            input_modes=["text/plain"],
            output_modes=["text/plain"],
            tags=["calculator", "math", "multiplication"],
            examples=["计算 6 * 7", "2 乘以 8"],
        ),
        AgentSkill(
            id="info",
            name="能力说明",
            description="返回计算器智能体支持的技能",
            input_modes=["text/plain"],
            output_modes=["text/plain"],
            tags=["calculator", "metadata"],
            examples=["获取信息"],
        ),
    ]

    return AgentCard(
        name="calculator-agent",
        description="基于 A2A 1.0 协议提供加法和乘法能力的计算器智能体",
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC",
                protocol_version="1.0",
                url=endpoint,
            )
        ],
        skills=skills,
    )


def build_a2a_app(
    calculator: CalculatorAgent,
    public_url: str,
) -> tuple[Starlette, AgentCard]:
    """创建 Agent Card 路由和 JSON-RPC 路由。"""
    agent_card = build_agent_card(public_url)
    request_handler = DefaultRequestHandler(
        agent_executor=CalculatorAgentExecutor(calculator),
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
    )
    routes = [
        *create_agent_card_routes(agent_card),
        *create_jsonrpc_routes(request_handler, rpc_url="/"),
    ]
    return Starlette(routes=routes), agent_card


def run_self_test(calculator: CalculatorAgent) -> None:
    """执行不启动网络服务的本地技能测试。"""
    print(f"计算器智能体创建成功，支持技能: {calculator.skill_names}")
    print("\n测试智能体技能:")

    cases: list[tuple[str, Callable[[str], str]]] = [
        ("获取信息", calculator.respond),
        ("计算 10 + 5", calculator.respond),
        ("计算 6 * 7", calculator.respond),
    ]
    for query, handler in cases:
        print(f"  查询: {query}")
        print(f"  回复: {handler(query)}\n")

    print("本地技能测试完成")
    print("提示：使用 --serve 启动 A2A 1.0 HTTP 服务")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="A2A 1.x 计算器智能体")
    parser.add_argument(
        "--serve",
        action="store_true",
        help="启动 A2A HTTP 服务；默认只执行本地技能测试",
    )
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=5000, help="监听端口")
    parser.add_argument(
        "--public-url",
        default=None,
        help="Agent Card 对外公布的 URL，默认使用 http://127.0.0.1:<port>",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    calculator = CalculatorAgent()

    if not args.serve:
        run_self_test(calculator)
        return

    public_url = args.public_url or f"http://127.0.0.1:{args.port}"
    app, agent_card = build_a2a_app(calculator, public_url)

    print(f"A2A 1.0 计算器服务启动: {args.host}:{args.port}")
    print(f"Agent Card: {public_url.rstrip('/')}/.well-known/agent-card.json")
    print(f"JSON-RPC: {agent_card.supported_interfaces[0].url}")
    print(f"可用技能: {[skill.id for skill in agent_card.skills]}")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
