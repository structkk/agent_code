"""不调用外部 API 的核心行为测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from travel_agent_pro.agent import FinishAction, ToolAction, parse_action
from travel_agent_pro.memory import PreferenceMemory
from travel_agent_pro.state import ConversationState
from travel_agent_pro.tickets import TicketInventory


class MemoryTests(unittest.TestCase):
    def test_preferences_persist_across_instances(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.json"
            memory = PreferenceMemory(path)
            self.assertTrue(memory.remember("兴趣", "历史文化景点"))
            self.assertTrue(memory.remember("预算", "每人200元以内"))

            reloaded = PreferenceMemory(path)
            self.assertEqual(reloaded.as_dict()["兴趣"], ["历史文化景点"])
            self.assertIn("每人200元以内", reloaded.summary())


class TicketFallbackTests(unittest.TestCase):
    def test_sold_out_attraction_cannot_be_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tickets.json"
            path.write_text(
                json.dumps(
                    {
                        "source": "test",
                        "default_status": "unknown",
                        "attractions": {"热门博物馆": "sold_out"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            inventory = TicketInventory(path)
            state = ConversationState()
            result = inventory.check("热门博物馆")
            state.record_ticket_status(result.attraction, result.status)

            self.assertEqual(result.status, "sold_out")
            self.assertIn("热门博物馆", state.excluded_attractions)
            with self.assertRaises(ValueError):
                state.record_recommendation("热门博物馆")


class ReflectionTests(unittest.TestCase):
    def test_three_rejections_require_strategy_reflection(self) -> None:
        state = ConversationState()
        for attraction in ("景点A", "景点B", "景点C"):
            state.record_ticket_status(attraction, "available")
            state.record_recommendation(attraction)
            state.start_turn("不喜欢这个，换一个")

        self.assertTrue(state.needs_reflection)
        self.assertEqual(state.consecutive_rejections, 3)
        state.apply_reflection("避开热门景点，改为小众历史街区并降低预算。")
        self.assertFalse(state.needs_reflection)
        self.assertEqual(state.reflection_count, 1)
        self.assertIn("小众历史街区", state.current_strategy)


class ParserTests(unittest.TestCase):
    def test_tool_and_finish_actions(self) -> None:
        tool = parse_action(
            'Thought: 保存预算\nAction: remember_preference(category="预算", value="200元")'
        )
        self.assertIsInstance(tool, ToolAction)
        self.assertEqual(tool.arguments["value"], "200元")

        finish = parse_action("Thought: 完成\nAction: Finish[推荐完成]")
        self.assertIsInstance(finish, FinishAction)
        self.assertEqual(finish.answer, "推荐完成")


if __name__ == "__main__":
    unittest.main()
