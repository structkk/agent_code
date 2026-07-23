import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from ELIZA_pro import ElizaPro, swap_pronouns  # noqa: E402


class ElizaProTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = ElizaPro(seed=7)

    def test_name_is_remembered_and_recalled(self) -> None:
        acknowledgement = self.agent.respond("My name is Alice.")
        recall = self.agent.respond("What is my name?")

        self.assertIn("Alice", acknowledgement)
        self.assertEqual(recall, "You told me that your name is Alice.")

    def test_age_is_remembered_and_recalled(self) -> None:
        self.agent.respond("I am 28 years old.")

        self.assertEqual(
            self.agent.respond("How old am I?"),
            "You told me that you are 28 years old.",
        )

    def test_profession_is_remembered_and_used_in_work_context(self) -> None:
        self.agent.respond("I work as a teacher.")
        response = self.agent.respond("Work has been difficult lately.")

        self.assertEqual(self.agent.memory.profession, "teacher")
        self.assertEqual(self.agent.last_rule, "work")
        self.assertIn("work", response.lower())

    def test_multiple_facts_can_be_extracted_from_one_message(self) -> None:
        self.agent.respond(
            "My name is Bob, I am 35 years old, and I work as an engineer."
        )

        self.assertEqual(self.agent.memory.name, "Bob")
        self.assertEqual(self.agent.memory.age, 35)
        self.assertEqual(self.agent.memory.profession, "engineer")
        summary = self.agent.respond("What do you remember about me?")
        self.assertIn("Bob", summary)
        self.assertIn("35", summary)
        self.assertIn("engineer", summary)

    def test_memory_can_be_cleared(self) -> None:
        self.agent.respond("Call me Carol.")
        self.agent.memory.clear()

        self.assertEqual(
            self.agent.respond("What is my name?"),
            "You have not told me your name yet.",
        )

    def test_five_extended_topics_are_matched(self) -> None:
        cases = {
            "work": "Work has been demanding recently.",
            "study": "I have an exam next week.",
            "hobby": "I enjoy painting on weekends.",
            "stress": "I feel stressed about tomorrow.",
            "goals": "My goal is to travel next year.",
        }

        for expected_topic, user_input in cases.items():
            with self.subTest(topic=expected_topic):
                self.agent.respond(user_input)
                self.assertEqual(self.agent.last_rule, expected_topic)

    def test_pronoun_swap_preserves_punctuation(self) -> None:
        self.assertEqual(
            swap_pronouns("my friend understands me."),
            "your friend understands you.",
        )


if __name__ == "__main__":
    unittest.main()

