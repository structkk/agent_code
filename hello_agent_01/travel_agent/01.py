"""智能旅行助手的命令行入口。"""

from __future__ import annotations

import sys

from travel_agent.agent import TravelAgent
from travel_agent.config import ConfigurationError, Settings
from travel_agent.llm_client import OpenAICompatibleClient
from travel_agent.prompts import AGENT_SYSTEM_PROMPT
from travel_agent.tools import build_available_tools


DEFAULT_USER_PROMPT = (
    "你好，请帮我查询一下今天西安的天气，然后根据天气推荐一个合适的旅游景点。"
)


def main() -> int:
    """加载配置并运行旅行助手。"""
    try:
        settings = Settings.from_source()
    except ConfigurationError as exc:
        print(f"配置错误：{exc}")
        return 2

    user_prompt = " ".join(sys.argv[1:]).strip() or DEFAULT_USER_PROMPT
    llm = OpenAICompatibleClient(
        model=settings.model_id,
        api_key=settings.api_key,
        base_url=settings.base_url,
    )
    agent = TravelAgent(
        llm=llm,
        tools=build_available_tools(settings.tavily_api_key),
        system_prompt=AGENT_SYSTEM_PROMPT,
        max_steps=settings.max_steps,
    )

    return 0 if agent.run(user_prompt) is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
