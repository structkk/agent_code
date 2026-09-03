"""在 Agent 中使用天气 MCP 服务器。"""

import sys
from pathlib import Path

from dotenv import load_dotenv
from hello_agents import SimpleAgent, HelloAgentsLLM
from hello_agents.tools import MCPTool

load_dotenv()


def create_weather_assistant():
    """创建天气助手"""
    llm = HelloAgentsLLM()

    assistant = SimpleAgent(
        name="天气助手",
        llm=llm,
        system_prompt="""你是天气助手，可以查询城市天气。
使用 mcp_get_weather 工具查询天气，支持中文城市名。
"""
    )

    # 添加天气 MCP 工具
    server_script = Path(__file__).resolve().with_name("mcp_weather_server.py")
    if not server_script.is_file():
        raise FileNotFoundError(f"天气 MCP 服务器脚本不存在: {server_script}")

    # 明确使用当前 Conda 环境的 Python，避免调用到系统中的其他解释器。
    weather_tool = MCPTool(
        server_command=[sys.executable, str(server_script)],
    )

    # 显式展开并注册 MCP 子工具
    expanded_tools = weather_tool.get_expanded_tools()
    if not expanded_tools:
        raise RuntimeError("未发现天气 MCP 子工具，请检查服务脚本、依赖和启动日志。")

    for expanded_tool in expanded_tools:
        assistant.add_tool(expanded_tool)

    return assistant


def demo():
    """演示"""
    assistant = create_weather_assistant()

    print("\n查询北京天气：")
    response = assistant.run("北京今天天气怎么样？")
    print(f"回答: {response}\n")


def interactive():
    """交互模式"""
    assistant = create_weather_assistant()

    while True:
        user_input = input("\n你: ").strip()
        if user_input.lower() in ['quit', 'exit']:
            break
        response = assistant.run(user_input)
        print(f"助手: {response}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        demo()
    else:
        interactive()
