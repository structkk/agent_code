#!/usr/bin/env python3
"""测试天气查询 MCP 服务器"""

import asyncio
import json
from pathlib import Path

from hello_agents.protocols.mcp.client import MCPClient


async def test_weather_server():
    server_script = Path(__file__).resolve().with_name("mcp_weather_server.py")
    if not server_script.is_file():
        raise FileNotFoundError(f"天气 MCP 服务器脚本不存在: {server_script}")

    # 直接传入脚本路径，MCPClient 会使用当前 Python 解释器启动服务。
    client = MCPClient(str(server_script))

    try:
        async with client:
            # 测试1: 获取服务器信息
            info = json.loads(await client.call_tool("get_server_info", {}))
            print(f"服务器: {info['name']} v{info['version']}")

            # 测试2: 列出支持的城市
            cities = json.loads(await client.call_tool("list_supported_cities", {}))
            print(f"支持城市: {cities['count']} 个")

            # 测试3: 查询北京天气
            weather = json.loads(await client.call_tool("get_weather", {"city": "北京"}))
            if "error" not in weather:
                print(f"\n西安天气: {weather['temperature']}°C, {weather['condition']}")

            # 测试4: 查询深圳天气
            weather = json.loads(await client.call_tool("get_weather", {"city": "深圳"}))
            if "error" not in weather:
                print(f"深圳天气: {weather['temperature']}°C, {weather['condition']}")

            print("\n✅ 所有测试完成！")

    except Exception as e:
        print(f"❌ 测试失败: {e}")


if __name__ == "__main__":
    asyncio.run(test_weather_server())
