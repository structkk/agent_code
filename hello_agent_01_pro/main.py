"""增强版旅行助手命令行入口。"""

from __future__ import annotations

import sys
from pathlib import Path

from travel_agent_pro.agent import TravelAgentPro
from travel_agent_pro.config import ConfigurationError, Settings
from travel_agent_pro.llm_client import OpenAICompatibleClient
from travel_agent_pro.memory import PreferenceMemory
from travel_agent_pro.prompts import AGENT_SYSTEM_PROMPT
from travel_agent_pro.state import ConversationState
from travel_agent_pro.tickets import TicketInventory
from travel_agent_pro.tools import AgentToolbox, AttractionSearch


PROJECT_ROOT = Path(__file__).resolve().parent
MEMORY_FILE = PROJECT_ROOT / "data" / "user_memory.json"
TICKET_FILE = PROJECT_ROOT / "data" / "ticket_inventory.json"


def build_agent(settings: Settings) -> tuple[TravelAgentPro, PreferenceMemory, ConversationState]:
    """组装增强版 Agent 及其运行时依赖。"""
    memory = PreferenceMemory(MEMORY_FILE)
    state = ConversationState()
    toolbox = AgentToolbox(
        memory=memory,
        state=state,
        attraction_search=AttractionSearch(settings.tavily_api_key),
        ticket_inventory=TicketInventory(TICKET_FILE),
    )
    llm = OpenAICompatibleClient(
        model=settings.model_id,
        api_key=settings.api_key,
        base_url=settings.base_url,
    )
    agent = TravelAgentPro(
        llm=llm,
        tools=toolbox.registry(),
        memory=memory,
        state=state,
        system_prompt=AGENT_SYSTEM_PROMPT,
        max_steps=settings.max_steps,
    )
    return agent, memory, state


def main() -> int:
    try:
        settings = Settings.from_source()
        agent, memory, state = build_agent(settings)
    except (ConfigurationError, OSError, ValueError) as exc:
        print(f"初始化失败：{exc}")
        return 2

    if len(sys.argv) > 1:
        user_input = " ".join(sys.argv[1:]).strip()
        return 0 if agent.run_turn(user_input) is not None else 1

    print("增强版智能旅行助手已启动。")
    print("命令：/memory 查看记忆，/state 查看状态，/clear-memory 清空记忆，/quit 退出。")
    print("示例：我喜欢历史文化景点，预算每人200元，请推荐西安景点。\n")

    while True:
        try:
            user_input = input("你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已退出。")
            return 0
        if not user_input:
            continue
        if user_input == "/quit":
            print("已退出。")
            return 0
        if user_input == "/memory":
            print("长期偏好记忆：\n" + memory.summary())
            continue
        if user_input == "/state":
            print("当前会话状态：\n" + state.summary())
            continue
        if user_input == "/clear-memory":
            memory.clear()
            print("长期偏好记忆已清空。")
            continue
        agent.run_turn(user_input)
        print()


if __name__ == "__main__":
    raise SystemExit(main())
