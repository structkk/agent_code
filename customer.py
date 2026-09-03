"""基于 a2a-sdk 1.x 的多智能体客服示例。

默认使用本地规则接待员，程序可直接运行且不会调用外部大模型：
    python customer.py

使用 HelloAgentsLLM 作为接待员，由模型选择技术或销售工具：
    python customer.py --mode llm

本示例会在当前进程中启动两个 A2A 1.0 服务：
    - 技术专家：http://127.0.0.1:6000
    - 销售顾问：http://127.0.0.1:6001
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any

try:
    import uvicorn
    from starlette.applications import Starlette

    from a2a.client import ClientConfig, create_client
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
        Message,
        Part,
        Role,
        SendMessageRequest,
        StreamResponse,
    )
except ImportError as exc:
    raise SystemExit(
        "A2A 1.x 依赖导入失败。请使用当前解释器执行：\n"
        'python -m pip install "a2a-sdk[http-server]>=1.0,<2" uvicorn starlette\n'
        f"原始错误：{exc}"
    ) from exc


TECH_URL = "http://127.0.0.1:6000"
SALES_URL = "http://127.0.0.1:6001"

# a2a-sdk 1.1.2 在非流式短任务完成后可能输出无害的事件分发器清理警告。
# 该提示不会影响响应结果，这里仅降低该模块的日志噪声。
logging.getLogger("a2a.server.events.event_queue_v2").setLevel(logging.ERROR)


@dataclass(frozen=True)
class ExpertAgent:
    """专家智能体的业务逻辑，与 A2A 网络传输层解耦。"""

    name: str
    description: str
    category: str

    def answer(self, question: str) -> str:
        question = question.strip()
        if not question:
            return "问题不能为空，请补充您的咨询内容。"

        if self.category == "technical":
            return (
                f"技术专家答复：关于“{question}”，API 通常通过 HTTPS 请求调用。"
                "请先获取 API Key，再根据接口文档配置 Base URL、模型名称和请求参数；"
                "Python 项目可使用官方 SDK 或 requests/httpx 接入。"
            )

        return (
            f"销售顾问答复：关于“{question}”，企业版价格通常取决于调用量、"
            "并发需求、服务等级和部署方式。请提供预计用量，我们可以进一步给出报价。"
        )


class ExpertAgentExecutor(AgentExecutor):
    """把 A2A 请求转换为专家业务调用。"""

    def __init__(self, expert: ExpertAgent) -> None:
        self.expert = expert

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        answer = self.expert.answer(context.get_user_input())
        await event_queue.enqueue_event(new_text_message(answer))

    async def cancel(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        del context, event_queue
        raise NotImplementedError("该示例任务会立即完成，不支持取消操作")


def build_agent_card(expert: ExpertAgent, public_url: str) -> AgentCard:
    """为专家构建符合 A2A 1.0 的 Agent Card。"""
    endpoint = public_url.rstrip("/") + "/"
    skill = AgentSkill(
        id="answer",
        name="回答客户问题",
        description=expert.description,
        input_modes=["text/plain"],
        output_modes=["text/plain"],
        tags=["customer-service", expert.category],
        examples=(
            ["你们的 API 如何调用？", "如何集成到 Python 项目中？"]
            if expert.category == "technical"
            else ["企业版价格是多少？", "是否有批量调用优惠？"]
        ),
    )

    return AgentCard(
        name=expert.name,
        description=expert.description,
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
        skills=[skill],
    )


def build_a2a_app(
    expert: ExpertAgent,
    public_url: str,
) -> tuple[Starlette, AgentCard]:
    """创建 Agent Card 路由与 JSON-RPC 路由。"""
    agent_card = build_agent_card(expert, public_url)
    request_handler = DefaultRequestHandler(
        agent_executor=ExpertAgentExecutor(expert),
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
    )
    routes = [
        *create_agent_card_routes(agent_card),
        *create_jsonrpc_routes(request_handler, rpc_url="/"),
    ]
    return Starlette(routes=routes), agent_card


@dataclass
class RunningServer:
    """保存 Uvicorn 服务及其后台线程，便于统一关闭。"""

    name: str
    server: uvicorn.Server
    thread: threading.Thread

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)


def _ensure_port_available(host: str, port: int) -> None:
    """在启动前检查端口，避免后台线程静默失败。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError as exc:
            raise RuntimeError(
                f"端口 {port} 已被占用，请先关闭占用该端口的程序。"
            ) from exc


def start_a2a_server(
    expert: ExpertAgent,
    host: str,
    port: int,
) -> RunningServer:
    """在后台线程启动一个 A2A 1.0 服务并等待其就绪。"""
    _ensure_port_available(host, port)
    public_url = f"http://{host}:{port}"
    app, _ = build_a2a_app(expert, public_url)
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="error",
        access_log=False,
        lifespan="off",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(
        target=server.run,
        name=f"{expert.name}-server",
        daemon=True,
    )
    thread.start()

    deadline = time.monotonic() + 8
    while not server.started:
        if not thread.is_alive():
            raise RuntimeError(f"A2A 服务 {expert.name} 启动失败")
        if time.monotonic() >= deadline:
            server.should_exit = True
            raise TimeoutError(f"等待 A2A 服务 {expert.name} 启动超时")
        time.sleep(0.05)

    print(f"[服务就绪] {expert.name}: {public_url}")
    print(f"[Agent Card] {public_url}/.well-known/agent-card.json")
    return RunningServer(expert.name, server, thread)


def _part_text(part: Part) -> str:
    return part.text if part.WhichOneof("content") == "text" else ""


def _message_text(message: Message) -> str:
    return "".join(_part_text(part) for part in message.parts).strip()


def _response_text(response: StreamResponse) -> str:
    """从新版 SDK 的不同响应载荷中提取文本。"""
    payload = response.WhichOneof("payload")
    if payload == "message":
        return _message_text(response.message)
    if payload == "task":
        if response.task.status.HasField("message"):
            text = _message_text(response.task.status.message)
            if text:
                return text
        return "".join(
            _part_text(part)
            for artifact in response.task.artifacts
            for part in artifact.parts
        ).strip()
    if payload == "status_update" and response.status_update.status.HasField(
        "message"
    ):
        return _message_text(response.status_update.status.message)
    if payload == "artifact_update":
        return "".join(
            _part_text(part)
            for part in response.artifact_update.artifact.parts
        ).strip()
    return ""


async def ask_a2a_agent(agent_url: str, question: str) -> str:
    """通过 a2a-sdk 1.x 客户端发现 Agent Card 并发送消息。"""
    config = ClientConfig(
        streaming=False,
        supported_protocol_bindings=["JSONRPC"],
    )
    client = await create_client(agent_url, client_config=config)
    request = SendMessageRequest(
        message=new_text_message(question, role=Role.ROLE_USER)
    )

    answers: list[str] = []
    async with client:
        async for response in client.send_message(request):
            text = _response_text(response)
            if text:
                answers.append(text)

    if not answers:
        raise RuntimeError(f"{agent_url} 未返回可识别的文本结果")
    return answers[-1]


SALES_KEYWORDS = {
    "价格",
    "费用",
    "报价",
    "购买",
    "优惠",
    "套餐",
    "企业版",
    "合同",
    "付款",
}


def choose_expert(query: str) -> tuple[str, str]:
    """默认接待员：根据问题关键词选择专家。"""
    if any(keyword in query for keyword in SALES_KEYWORDS):
        return "销售顾问", SALES_URL
    return "技术专家", TECH_URL


async def run_rule_receptionist(queries: list[str]) -> None:
    """无需外部 LLM 的可复现接待流程。"""
    for query in queries:
        expert_name, agent_url = choose_expert(query)
        print(f"\n客户咨询：{query}")
        print(f"接待员路由：{expert_name}")
        answer = await ask_a2a_agent(agent_url, query)
        print(f"客服回复：{answer}")
        print("=" * 60)


def build_llm_receptionist() -> Any:
    """按需创建 HelloAgents 接待员，避免默认模式依赖 API Key。"""
    try:
        from dotenv import load_dotenv
        from hello_agents import HelloAgentsLLM, SimpleAgent
        from hello_agents.tools.base import Tool, ToolParameter
    except ImportError as exc:
        raise RuntimeError(
            "LLM 模式依赖 hello-agents 和 python-dotenv，请先安装对应依赖。"
        ) from exc

    class A2ASDKTool(Tool):
        """用新版 a2a-sdk 实现的 HelloAgents 工具适配器。"""

        def __init__(self, agent_url: str, name: str, description: str) -> None:
            super().__init__(name=name, description=description)
            self.agent_url = agent_url

        def run(self, parameters: dict[str, Any]) -> str:
            question = str(
                parameters.get("question") or parameters.get("input") or ""
            ).strip()
            if not question:
                return "错误：question 参数不能为空"
            return asyncio.run(ask_a2a_agent(self.agent_url, question))

        def get_parameters(self) -> list[Any]:
            return [
                ToolParameter(
                    name="question",
                    type="string",
                    description="需要交给该专家回答的完整客户问题",
                    required=True,
                )
            ]

    load_dotenv(override=True)
    receptionist = SimpleAgent(
        name="接待员",
        llm=HelloAgentsLLM(),
        system_prompt="""你是客服接待员。请分析客户问题并且必须调用一个专家工具：
1. API、代码、集成、故障等问题调用 tech_expert。
2. 价格、购买、套餐、合同等问题调用 sales_advisor。
3. 工具调用必须使用格式 [TOOL_CALL:工具名:question=完整问题]。
4. 获得专家结果后，整理为礼貌、简洁的最终答复。""",
    )
    receptionist.add_tool(
        A2ASDKTool(TECH_URL, "tech_expert", "技术专家，回答技术相关问题")
    )
    receptionist.add_tool(
        A2ASDKTool(
            SALES_URL,
            "sales_advisor",
            "销售顾问，回答价格、购买和套餐相关问题",
        )
    )
    return receptionist


def run_llm_receptionist(queries: list[str]) -> None:
    """由 HelloAgentsLLM 自主选择新版 A2A 工具。"""
    receptionist = build_llm_receptionist()
    for query in queries:
        print(f"\n客户咨询：{query}")
        print("=" * 60)
        response = receptionist.run(query)
        print(f"客服回复：{response}")
        print("=" * 60)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="A2A 1.x 多智能体客服示例")
    parser.add_argument(
        "--mode",
        choices=("rule", "llm"),
        default="rule",
        help="接待模式：rule 不调用外部模型；llm 使用 HelloAgentsLLM",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    host = "127.0.0.1"
    experts = [
        (
            ExpertAgent("tech-expert", "技术专家，回答 API 和代码问题", "technical"),
            6000,
        ),
        (
            ExpertAgent("sales-advisor", "销售顾问，回答购买和价格问题", "sales"),
            6001,
        ),
    ]
    queries = [
        "你们的API如何调用？",
        "企业版的价格是多少？",
        "如何集成到我的Python项目中？",
    ]

    running_servers: list[RunningServer] = []
    try:
        print("正在启动 A2A 1.0 专家服务...")
        for expert, port in experts:
            running_servers.append(start_a2a_server(expert, host, port))

        if args.mode == "llm":
            print("\n接待模式：HelloAgentsLLM")
            run_llm_receptionist(queries)
        else:
            print("\n接待模式：本地规则路由（不调用外部 LLM）")
            asyncio.run(run_rule_receptionist(queries))
    finally:
        for running_server in reversed(running_servers):
            running_server.stop()
        if running_servers:
            print("\nA2A 专家服务已关闭。")


if __name__ == "__main__":
    main()
