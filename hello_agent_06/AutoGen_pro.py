from __future__ import annotations

import ast
import asyncio
import json
import os
import re
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Sequence

from dotenv import load_dotenv

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.conditions import (
    MaxMessageTermination,
    TextMentionTermination,
)
from autogen_agentchat.teams import SelectorGroupChat
from autogen_agentchat.ui import Console
from autogen_ext.models.openai import OpenAIChatCompletionClient


ENV_FILE = Path(__file__).with_name(".env")
load_dotenv(ENV_FILE, override=True)

ROLE_NAMES = {
    "ProductManager",
    "Engineer",
    "CodeReviewer",
    "QualityAssurance",
    "QualityMonitor",
}

ROUTE_PATTERN = re.compile(
    r"\[ROUTE:(ProductManager|Engineer|CodeReviewer|"
    r"QualityAssurance|QualityMonitor)\]",
    re.IGNORECASE,
)

DEVELOPMENT_TASK = """我们需要开发一个比特币价格显示应用，具体要求如下：

核心功能：
- 实时显示比特币当前价格（USD）
- 显示24小时价格变化趋势（涨跌幅和涨跌额）
- 提供价格刷新功能

技术要求：
- 使用 Streamlit 框架创建 Web 应用
- 界面简洁美观，用户友好
- 添加适当的错误处理、请求超时和加载状态
- 不得在代码中硬编码 API Key

协作要求：
- 产品经理负责需求版本和验收标准
- 工程师负责完整代码实现
- 代码审查员判断问题属于代码缺陷还是需求变更
- 测试工程师在代码审查通过后调用自动化静态测试工具
- 质量监控员负责发现偏题、重复循环和路由异常

请团队协作完成这个任务，从需求分析到自动化质量检查。"""


def create_openai_model_client() -> OpenAIChatCompletionClient:
    """创建支持工具调用的 OpenAI 兼容模型客户端。"""
    config = {
        "model": os.getenv("LLM_MODEL_ID", "").strip(),
        "api_key": os.getenv("LLM_API_KEY", "").strip(),
        "base_url": os.getenv("LLM_BASE_URL", "").strip(),
    }
    missing = [name for name, value in config.items() if not value]
    if missing:
        env_names = {
            "model": "LLM_MODEL_ID",
            "api_key": "LLM_API_KEY",
            "base_url": "LLM_BASE_URL",
        }
        missing_env = [env_names[name] for name in missing]
        raise ValueError(
            "缺少环境变量："
            + ", ".join(missing_env)
            + f"。请检查 {ENV_FILE}"
        )

    return OpenAIChatCompletionClient(
        model=config["model"],
        api_key=config["api_key"],
        base_url=config["base_url"],
        max_tokens=4096,
        # Qwen 思考模式不能与强制工具调用稳定组合；QA 需要调用测试工具，
        # 因此在 OpenAI 兼容请求中显式关闭思考模式。
        extra_body={"enable_thinking": False},
        model_info={
            "vision": False,
            "function_calling": True,
            "json_output": False,
            "structured_output": False,
            "family": "unknown",
        },
    )


def _extract_python_source(text: str) -> str:
    """从 Markdown 回复中提取最长的 Python 代码块。"""
    blocks = re.findall(
        r"```(?:python|py)?\s*([\s\S]*?)```",
        text,
        flags=re.IGNORECASE,
    )
    if blocks:
        return max(blocks, key=len).strip()
    return text.strip()


def _call_name(node: ast.AST) -> str:
    """返回函数调用的点分名称，例如 requests.get。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def run_python_static_tests(code: str) -> str:
    """
    对工程师提交的 Python 代码执行受控自动化检查。

    该工具只解析和编译代码，不运行模型生成的程序，不访问网络，也不写入文件。
    返回 JSON，包含是否通过、检查项和问题列表。
    """
    source = _extract_python_source(code)
    checks: list[dict[str, Any]] = []
    issues: list[str] = []

    if not source:
        return json.dumps(
            {
                "passed": False,
                "checks": [],
                "issues": ["没有收到可测试的 Python 代码。"],
            },
            ensure_ascii=False,
        )

    try:
        tree = ast.parse(source)
        compile(tree, "<qa_candidate>", "exec")
        checks.append({"name": "syntax_and_compile", "passed": True})
    except SyntaxError as exc:
        checks.append(
            {
                "name": "syntax_and_compile",
                "passed": False,
                "detail": f"第 {exc.lineno} 行：{exc.msg}",
            }
        )
        issues.append(f"Python 语法错误：第 {exc.lineno} 行 {exc.msg}。")
        return json.dumps(
            {
                "passed": False,
                "checks": checks,
                "issues": issues,
            },
            ensure_ascii=False,
        )

    imports = set()
    call_names = set()
    string_literals = []
    has_exception_handler = False
    has_request_timeout = False

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            call_names.add(_call_name(node.func))
            if any(keyword.arg == "timeout" for keyword in node.keywords):
                has_request_timeout = True
        elif isinstance(node, ast.Try) and node.handlers:
            has_exception_handler = True
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            string_literals.append(node.value.casefold())

    lowered_source = source.casefold()
    joined_strings = " ".join(string_literals)

    has_streamlit = "streamlit" in imports
    has_http_client = bool(
        {"requests", "httpx", "urllib", "aiohttp"} & imports
    )
    has_refresh_control = (
        any(name.endswith(".button") for name in call_names)
        and any(
            word in joined_strings
            for word in ("刷新", "refresh", "重新加载")
        )
    )
    has_price_fields = any(
        marker in lowered_source
        for marker in (
            "current_price",
            "price_change_24h",
            "price_change_percentage_24h",
            "usd",
        )
    )

    required_checks = [
        ("streamlit_import", has_streamlit, "缺少 Streamlit 导入。"),
        ("http_client", has_http_client, "缺少 HTTP 请求客户端。"),
        (
            "exception_handling",
            has_exception_handler,
            "缺少 try/except 异常处理。",
        ),
        (
            "request_timeout",
            has_request_timeout,
            "网络请求没有设置 timeout。",
        ),
        (
            "refresh_control",
            has_refresh_control,
            "没有检测到明确的价格刷新按钮。",
        ),
        (
            "price_and_24h_fields",
            has_price_fields,
            "没有检测到价格或 24 小时变化字段。",
        ),
    ]

    for name, passed, failure_message in required_checks:
        checks.append({"name": name, "passed": passed})
        if not passed:
            issues.append(failure_message)

    dangerous_calls = sorted(
        name
        for name in call_names
        if name
        in {
            "eval",
            "exec",
            "compile",
            "os.system",
            "subprocess.call",
            "subprocess.Popen",
            "subprocess.run",
        }
    )
    if dangerous_calls:
        issues.append(
            "检测到不允许的高风险调用：" + ", ".join(dangerous_calls)
        )
    checks.append(
        {
            "name": "dangerous_calls",
            "passed": not dangerous_calls,
            "detail": dangerous_calls,
        }
    )

    hardcoded_secret = bool(
        re.search(
            r"(?i)(api[_-]?key|token|secret)\s*=\s*"
            r"['\"][^'\"]{12,}['\"]",
            source,
        )
    )
    if hardcoded_secret:
        issues.append("检测到疑似硬编码密钥或令牌。")
    checks.append(
        {
            "name": "hardcoded_secret",
            "passed": not hardcoded_secret,
        }
    )

    return json.dumps(
        {
            "passed": not issues,
            "checks": checks,
            "issues": issues,
            "note": (
                "这是受控的语法与静态质量检查，未执行代码，"
                "不等同于真实浏览器、网络和端到端测试。"
            ),
        },
        ensure_ascii=False,
    )


def _message_text(message: Any) -> str:
    """兼容不同 AutoGen 消息类型并提取可比较文本。"""
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(item) for item in content)
    return str(content or "")


def _message_source(message: Any) -> str:
    return str(getattr(message, "source", "") or "")


def _normalize_text(text: str) -> str:
    text = re.sub(r"```[\s\S]*?```", "<CODE>", text)
    text = re.sub(r"\s+", " ", text).strip().casefold()
    return text[:5000]


def _extract_route(text: str) -> str | None:
    match = ROUTE_PATTERN.search(text)
    if not match:
        return None
    canonical_names = {
        name.casefold(): name
        for name in ROLE_NAMES
    }
    return canonical_names.get(match.group(1).casefold())


class ConversationQualityController:
    """检测偏题、重复循环和协议异常，并决定下一位发言者。"""

    def __init__(
        self,
        monitor_interval: int = 4,
        similarity_threshold: float = 0.93,
    ):
        self.monitor_interval = monitor_interval
        self.similarity_threshold = similarity_threshold
        self.last_monitored_count = 0
        self.last_trigger_reason = ""
        self.topic_keywords = {
            "比特币",
            "bitcoin",
            "价格",
            "price",
            "24",
            "streamlit",
            "刷新",
            "refresh",
            "api",
            "timeout",
            "异常",
            "error",
            "测试",
            "test",
            "代码",
            "code",
            "需求",
            "requirement",
        }

    def selector_func(self, messages: Sequence[Any]) -> str:
        """
        确定性路由函数。

        优先级：
        质量异常 -> 显式 ROUTE -> 角色默认路由。
        """
        participant_messages = [
            message
            for message in messages
            if _message_source(message) in ROLE_NAMES
            and _message_text(message).strip()
        ]

        if not participant_messages:
            return "ProductManager"

        last_message = participant_messages[-1]
        last_source = _message_source(last_message)
        last_text = _message_text(last_message)

        # 质量监控员完成干预后，严格执行它给出的恢复路由。
        if last_source == "QualityMonitor":
            route = _extract_route(last_text)
            return route if route and route != "QualityMonitor" else "ProductManager"

        anomaly = self._detect_anomaly(participant_messages)
        current_count = len(participant_messages)
        periodic_check = (
            current_count - self.last_monitored_count
            >= self.monitor_interval
        )

        if anomaly or periodic_check:
            self.last_monitored_count = current_count
            self.last_trigger_reason = anomaly or "定期质量检查"
            print(
                "\n[质量监控触发] "
                f"{self.last_trigger_reason}"
            )
            return "QualityMonitor"

        explicit_route = _extract_route(last_text)
        if explicit_route:
            return explicit_route

        # 模型偶尔遗漏路由标签时进入监控员，而不是随机选择发言者。
        self.last_monitored_count = current_count
        self.last_trigger_reason = (
            f"{last_source} 未输出合法的 [ROUTE:角色] 标签"
        )
        print(
            "\n[质量监控触发] "
            f"{self.last_trigger_reason}"
        )
        return "QualityMonitor"

    def _detect_anomaly(self, messages: Sequence[Any]) -> str | None:
        non_monitor = [
            message
            for message in messages
            if _message_source(message) != "QualityMonitor"
        ]
        recent = non_monitor[-6:]

        # 同一角色连续提交几乎相同的内容，通常意味着修改没有生效。
        by_source: dict[str, list[str]] = {}
        for message in recent:
            source = _message_source(message)
            text = _normalize_text(_message_text(message))
            if len(text) >= 80:
                by_source.setdefault(source, []).append(text)

        for source, texts in by_source.items():
            if len(texts) < 2:
                continue
            similarity = SequenceMatcher(
                None,
                texts[-2],
                texts[-1],
            ).ratio()
            if similarity >= self.similarity_threshold:
                return (
                    f"检测到 {source} 连续输出高度相似内容"
                    f"（相似度 {similarity:.2f}）"
                )

        # 检测 A-B-A-B 式重复回退。
        sources = [_message_source(message) for message in recent]
        if (
            len(sources) >= 6
            and sources[-6] == sources[-4] == sources[-2]
            and sources[-5] == sources[-3] == sources[-1]
        ):
            return (
                "检测到两个角色重复往返："
                f"{sources[-2]} ↔ {sources[-1]}"
            )

        # 最近两个长回复都不包含任务关键词时，标记为可能偏题。
        long_recent_texts = [
            _normalize_text(_message_text(message))
            for message in recent[-2:]
            if len(_message_text(message)) >= 100
        ]
        if len(long_recent_texts) == 2:
            if all(
                not any(keyword in text for keyword in self.topic_keywords)
                for text in long_recent_texts
            ):
                return "最近两个回复与原始开发任务缺少关键词关联，可能偏题"

        # 相同角色转换出现三次以上，说明流程可能没有收敛。
        transitions = [
            (sources[index - 1], sources[index])
            for index in range(1, len(sources))
        ]
        if transitions:
            transition, count = Counter(transitions).most_common(1)[0]
            if count >= 3:
                return (
                    f"角色转换 {transition[0]} → {transition[1]} "
                    f"已重复 {count} 次"
                )

        return None


def create_product_manager(
    model_client: OpenAIChatCompletionClient,
) -> AssistantAgent:
    return AssistantAgent(
        name="ProductManager",
        description="维护需求版本、验收标准并处理需求变更。",
        model_client=model_client,
        system_message="""你是产品经理，负责维护需求基线和验收标准。

首次发言时：
1. 输出“需求版本 V1”。
2. 明确功能、非功能需求、边界条件和可测试的验收标准。
3. 将开发任务交给工程师。

当代码审查员或测试工程师以 [ROUTE:ProductManager] 回退时：
1. 判断反馈是需求变更、需求歧义还是原需求遗漏。
2. 将需求版本递增，例如 V1 -> V2。
3. 列出“变更前、变更后、影响范围、更新后的验收标准”。
4. 不要直接编写实现代码。

每次回复最后必须且只能给出一个路由标签：
[ROUTE:Engineer]
""",
    )


def create_engineer(
    model_client: OpenAIChatCompletionClient,
) -> AssistantAgent:
    return AssistantAgent(
        name="Engineer",
        description="按照最新需求实现代码并处理审查或测试缺陷。",
        model_client=model_client,
        system_message="""你是资深 Python 与 Streamlit 工程师。

工作规则：
1. 始终以最近一版产品需求和验收标准为准。
2. 首次开发时提供完整、可以保存为 app.py 的代码。
3. 收到代码审查或测试失败反馈后，逐项说明修复内容并重新提供完整代码。
4. 网络请求必须设置 timeout，并处理超时、HTTP 错误和数据缺失。
5. 不得硬编码 API Key，不得使用 eval、exec、os.system 或 subprocess。
6. 回复中只保留一个最终版本的完整 Python 代码块，避免 QA 测试到旧版本。

回复结尾必须是：
[ROUTE:CodeReviewer]
""",
    )


def create_code_reviewer(
    model_client: OpenAIChatCompletionClient,
) -> AssistantAgent:
    return AssistantAgent(
        name="CodeReviewer",
        description="审查代码并区分实现缺陷和需求变更。",
        model_client=model_client,
        system_message="""你是代码审查员。请依据最新需求版本和验收标准审查工程师代码。

必须从三种结论中选择一种：

1. 代码满足要求：
[REVIEW:APPROVED]
[ROUTE:QualityAssurance]

2. 需求没有变化，但代码实现存在缺陷：
[REVIEW:CODE_CHANGES]
列出带优先级的具体缺陷和修复标准。
[ROUTE:Engineer]

3. 新反馈改变了功能边界、验收标准或产品决策：
[REVIEW:REQUIREMENT_CHANGE]
解释为什么这不是单纯代码缺陷，并列出需要产品经理决策的问题。
[ROUTE:ProductManager]

不得直接修改完整代码，不得跳过测试工程师，不得输出 TERMINATE。
""",
    )


def create_quality_assurance(
    model_client: OpenAIChatCompletionClient,
) -> AssistantAgent:
    return AssistantAgent(
        name="QualityAssurance",
        description="代码审查通过后执行自动化静态测试并作出发布判断。",
        model_client=model_client,
        tools=[run_python_static_tests],
        reflect_on_tool_use=True,
        max_tool_iterations=2,
        system_message="""你是测试工程师（Quality Assurance）。

只有代码审查员输出 [REVIEW:APPROVED] 后你才能测试。

测试流程：
1. 从最近一条 Engineer 消息提取完整 Python 代码。
2. 必须调用 run_python_static_tests，不能凭印象宣称测试通过。
3. 将工具结果映射到最新验收标准，给出通过项、失败项和剩余风险。
4. 不得声称已经运行 Streamlit、访问真实网络或完成浏览器端到端测试；
   当前工具只执行语法、编译和静态质量检查。

测试全部通过：
[QA:PASSED]
说明静态检查范围和仍需人工验证的项目。
TERMINATE

代码问题导致失败：
[QA:FAILED_CODE]
列出可复现的失败项和修复标准。
[ROUTE:Engineer]

需求歧义导致无法判断：
[QA:FAILED_REQUIREMENT]
列出需要重新确认的验收标准。
[ROUTE:ProductManager]
""",
    )


def create_quality_monitor(
    model_client: OpenAIChatCompletionClient,
) -> AssistantAgent:
    return AssistantAgent(
        name="QualityMonitor",
        description="监控对话是否偏题、重复、违规路由或缺乏收敛。",
        model_client=model_client,
        system_message="""你是独立的对话质量监控员，不负责编码或测试。

请检查最近对话：
1. 是否仍围绕原始比特币 Streamlit 应用。
2. 当前角色是否履行职责。
3. 是否出现相同内容重复、Engineer 与 Reviewer 往返却没有实质变化。
4. 是否存在需求版本不一致或验收标准漂移。
5. 上一角色建议的下一步是否合理。

如果流程健康：
[MONITOR:OK]
用一句话说明依据。
[ROUTE:合理的下一角色]

如果需要干预：
[MONITOR:INTERVENE]
指出偏题、循环或协议问题，并给出一条可执行纠正要求。
需求或验收标准问题路由 ProductManager；
实现问题路由 Engineer；
审查问题路由 CodeReviewer；
已审查通过的代码路由 QualityAssurance。
[ROUTE:目标角色]

不得输出 TERMINATE，不得编写完整代码。
""",
    )


async def run_dynamic_software_team() -> Any:
    """运行支持动态回退、自动 QA 和质量监控的团队。"""
    print("[初始化] 正在创建 OpenAI 兼容模型客户端...")
    model_client = create_openai_model_client()

    quality_controller = ConversationQualityController(
        monitor_interval=4,
        similarity_threshold=0.93,
    )

    participants = [
        create_product_manager(model_client),
        create_engineer(model_client),
        create_code_reviewer(model_client),
        create_quality_assurance(model_client),
        create_quality_monitor(model_client),
    ]

    termination = (
        TextMentionTermination(
            "TERMINATE",
            sources=["QualityAssurance"],
        )
        | MaxMessageTermination(45)
    )

    team = SelectorGroupChat(
        participants=participants,
        model_client=model_client,
        selector_func=quality_controller.selector_func,
        termination_condition=termination,
        max_turns=30,
        allow_repeated_speaker=False,
    )

    print("[团队] 已创建：产品经理、工程师、代码审查员、测试工程师、质量监控员")
    print("[启动] 开始支持动态回退的软件开发协作...")
    print("=" * 72)

    try:
        result = await Console(team.run_stream(task=DEVELOPMENT_TASK))
        print("\n" + "=" * 72)
        print("[完成] 协作结束")
        print(f"- 停止原因：{getattr(result, 'stop_reason', '未知')}")
        print(f"- 消息数量：{len(getattr(result, 'messages', []))}")
        return result
    finally:
        await model_client.close()


if __name__ == "__main__":
    try:
        asyncio.run(run_dynamic_software_team())
    except KeyboardInterrupt:
        print("\n用户已终止运行。")
    except ValueError as exc:
        print(f"[配置错误] {exc}")
        raise SystemExit(1)
    except Exception as exc:
        print(f"[运行错误] {exc}")
        import traceback

        traceback.print_exc()
        raise SystemExit(1)
