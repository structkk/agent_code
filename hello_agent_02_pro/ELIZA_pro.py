"""增强版 ELIZA：主题规则、会话内上下文记忆与可解释响应。

本程序仅用于人工智能教学，不具备心理咨询、诊断或危机干预能力。
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass


@dataclass
class ConversationMemory:
    """保存当前会话中由显式句式提取出的用户信息。"""

    name: str | None = None
    age: int | None = None
    profession: str | None = None

    def clear(self) -> None:
        self.name = None
        self.age = None
        self.profession = None

    def summary(self) -> str:
        facts: list[str] = []
        if self.name:
            facts.append(f"your name is {self.name}")
        if self.age is not None:
            facts.append(f"you are {self.age} years old")
        if self.profession:
            facts.append(f"you work as {_profession_phrase(self.profession)}")

        if not facts:
            return "You have not shared your name, age, or profession with me yet."
        return f"I remember that {_join_phrases(facts)}."


@dataclass(frozen=True)
class Rule:
    """一条按顺序匹配的对话规则。"""

    topic: str
    pattern: re.Pattern[str]
    responses: tuple[str, ...]


def _join_phrases(parts: list[str]) -> str:
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return f"{', '.join(parts[:-1])}, and {parts[-1]}"


def _profession_phrase(profession: str) -> str:
    """为简单的英文职业名称补充不定冠词。"""

    if profession.lower().startswith(("a ", "an ")):
        return profession
    article = "an" if profession[:1].lower() in "aeiou" else "a"
    return f"{article} {profession}"


PRONOUN_SWAP = {
    "i": "you",
    "you": "i",
    "me": "you",
    "my": "your",
    "am": "are",
    "are": "am",
    "was": "were",
    "i'd": "you would",
    "i've": "you have",
    "i'll": "you will",
    "yours": "mine",
    "mine": "yours",
}

_PRONOUN_PATTERN = re.compile(
    r"\b(" + "|".join(
        re.escape(word)
        for word in sorted(PRONOUN_SWAP, key=len, reverse=True)
    ) + r")\b",
    re.IGNORECASE,
)


def swap_pronouns(phrase: str) -> str:
    """转换第一、第二人称，并保留单词后的标点。"""

    return _PRONOUN_PATTERN.sub(
        lambda match: PRONOUN_SWAP[match.group(0).lower()],
        phrase,
    )


_FACT_END = r"(?=\s*(?:,|\band\b|\bbut\b|[.!?;]*$))"

NAME_PATTERNS = (
    re.compile(
        r"\bmy name is\s+([A-Za-z][A-Za-z' -]{0,40}?)" + _FACT_END,
        re.IGNORECASE,
    ),
    re.compile(
        r"\bcall me\s+([A-Za-z][A-Za-z' -]{0,40}?)" + _FACT_END,
        re.IGNORECASE,
    ),
)

AGE_PATTERNS = (
    re.compile(
        r"\b(?:i am|i'm)\s+(\d{1,3})\s+years?\s+old\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bmy age is\s+(\d{1,3})\b",
        re.IGNORECASE,
    ),
)

PROFESSION_PATTERNS = (
    re.compile(
        r"\bi (?:work|am working) as\s+(?:an?\s+)?"
        r"([A-Za-z][A-Za-z0-9/&' -]{0,60}?)" + _FACT_END,
        re.IGNORECASE,
    ),
    re.compile(
        r"\bmy (?:job|profession|occupation) is\s+(?:an?\s+)?"
        r"([A-Za-z][A-Za-z0-9/&' -]{0,60}?)" + _FACT_END,
        re.IGNORECASE,
    ),
    re.compile(
        r"\bi am\s+(?:an?\s+)?"
        r"(student|teacher|doctor|engineer|nurse|designer|developer|"
        r"programmer|researcher|manager|writer|lawyer|chef|artist|"
        r"accountant|consultant)\b",
        re.IGNORECASE,
    ),
)


RULES = (
    # 五类新增场景规则。具体主题规则放在基础句式和兜底规则之前。
    Rule(
        "work",
        re.compile(
            r".*\b(?:work|job|career|boss|colleague|coworker)\b.*",
            re.IGNORECASE,
        ),
        (
            "{address}how do you feel about your work {work_context}?",
            "{address}what part of your work is most satisfying to you?",
            "{address}what would you most like to change about your work?",
        ),
    ),
    Rule(
        "study",
        re.compile(
            r".*\b(?:study|studying|learn|learning|school|course|exam|"
            r"homework)\b.*",
            re.IGNORECASE,
        ),
        (
            "{address}what are you hoping to learn from that?",
            "{address}which part of studying feels most challenging?",
            "{address}what kind of support would help you make progress?",
        ),
    ),
    Rule(
        "hobby",
        re.compile(
            r".*\b(?:hobby|hobbies|enjoy|like|love|music|sports|reading|"
            r"gaming)\b.*",
            re.IGNORECASE,
        ),
        (
            "{address}what do you enjoy most about that?",
            "{address}how did you become interested in that hobby?",
            "{address}how does that activity make you feel?",
        ),
    ),
    Rule(
        "stress",
        re.compile(
            r".*\b(?:stress|stressed|pressure|overwhelmed|anxious)\b.*",
            re.IGNORECASE,
        ),
        (
            "{address}what seems to be the main source of that pressure?",
            "{address}when do you notice this feeling most strongly?",
            "{address}what has helped you handle similar pressure before?",
        ),
    ),
    Rule(
        "goals",
        re.compile(
            r".*\b(?:future|goal|goals|plan|plans|dream|dreams)\b.*",
            re.IGNORECASE,
        ),
        (
            "{address}what would reaching that goal mean to you?",
            "{address}what is one small step you could take toward that?",
            "{address}what might make that plan difficult to achieve?",
        ),
    ),
    # 原始 ELIZA 规则。
    Rule(
        "need",
        re.compile(r"I need (.*)", re.IGNORECASE),
        (
            "Why do you need {0}?",
            "Would it really help you to get {0}?",
            "Are you sure you need {0}?",
        ),
    ),
    Rule(
        "why_not_you",
        re.compile(r"Why don't you (.*)\?", re.IGNORECASE),
        (
            "Do you really think I don't {0}?",
            "Perhaps eventually I will {0}.",
            "Do you really want me to {0}?",
        ),
    ),
    Rule(
        "why_cant_i",
        re.compile(r"Why can't I (.*)\?", re.IGNORECASE),
        (
            "Do you think you should be able to {0}?",
            "If you could {0}, what would you do?",
            "I don't know -- why can't you {0}?",
        ),
    ),
    Rule(
        "i_am",
        re.compile(r"I am (.*)", re.IGNORECASE),
        (
            "Did you come to me because you are {0}?",
            "How long have you been {0}?",
            "How do you feel about being {0}?",
        ),
    ),
    Rule(
        "mother",
        re.compile(r".*\bmother\b.*", re.IGNORECASE),
        (
            "Tell me more about your mother.",
            "What was your relationship with your mother like?",
            "How do you feel about your mother?",
        ),
    ),
    Rule(
        "father",
        re.compile(r".*\bfather\b.*", re.IGNORECASE),
        (
            "Tell me more about your father.",
            "How did your father make you feel?",
            "What has your father taught you?",
        ),
    ),
    Rule(
        "fallback",
        re.compile(r".*", re.IGNORECASE),
        (
            "Please tell me more.",
            "Let's change focus a bit... Tell me about your family.",
            "Can you elaborate on that?",
        ),
    ),
)


class ElizaPro:
    """带有会话内结构化记忆的规则式聊天机器人。"""

    def __init__(self, seed: int | None = None) -> None:
        self.memory = ConversationMemory()
        self.last_rule: str | None = None
        self._random = random.Random(seed)

    @staticmethod
    def _clean_text(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip(" \t\r\n.,!?;:")

    def _extract_facts(self, user_input: str) -> dict[str, str | int]:
        updates: dict[str, str | int] = {}

        for pattern in NAME_PATTERNS:
            match = pattern.search(user_input)
            if match:
                name = self._clean_text(match.group(1)).title()
                if name:
                    updates["name"] = name
                break

        for pattern in AGE_PATTERNS:
            match = pattern.search(user_input)
            if match:
                age = int(match.group(1))
                if 1 <= age <= 120:
                    updates["age"] = age
                break

        for pattern in PROFESSION_PATTERNS:
            match = pattern.search(user_input)
            if match:
                profession = self._clean_text(match.group(1)).lower()
                if profession:
                    updates["profession"] = profession
                break

        return updates

    def _store_facts(self, updates: dict[str, str | int]) -> str:
        remembered: list[str] = []
        for key, value in updates.items():
            setattr(self.memory, key, value)
            if key == "name":
                remembered.append(f"your name is {value}")
            elif key == "age":
                remembered.append(f"you are {value} years old")
            elif key == "profession":
                remembered.append(
                    f"you work as {_profession_phrase(str(value))}"
                )

        self.last_rule = "memory_store"
        return f"I'll remember that {_join_phrases(remembered)}."

    def _answer_memory_query(self, user_input: str) -> str | None:
        if re.search(
            r"\b(?:what(?:'s| is) my name|do you remember my name)\b",
            user_input,
            re.IGNORECASE,
        ):
            self.last_rule = "memory_name"
            if self.memory.name:
                return f"You told me that your name is {self.memory.name}."
            return "You have not told me your name yet."

        if re.search(
            r"\b(?:how old am i|do you remember my age|what(?:'s| is) my age)\b",
            user_input,
            re.IGNORECASE,
        ):
            self.last_rule = "memory_age"
            if self.memory.age is not None:
                return f"You told me that you are {self.memory.age} years old."
            return "You have not told me your age yet."

        if re.search(
            r"\b(?:what do i do|what(?:'s| is) my "
            r"(?:job|profession|occupation)|do you remember my "
            r"(?:job|profession|occupation))\b",
            user_input,
            re.IGNORECASE,
        ):
            self.last_rule = "memory_profession"
            if self.memory.profession:
                profession = _profession_phrase(self.memory.profession)
                return f"You told me that you work as {profession}."
            return "You have not told me your profession yet."

        if re.search(
            r"\b(?:what do you remember about me|do you remember me|who am i)\b",
            user_input,
            re.IGNORECASE,
        ):
            self.last_rule = "memory_summary"
            return self.memory.summary()

        return None

    def _render_rule(self, rule: Rule, user_input: str) -> str:
        match = rule.pattern.search(user_input)
        if match is None:
            raise ValueError("The selected rule must match the user input.")

        captured = match.group(1) if match.groups() and match.group(1) else ""
        captured = swap_pronouns(captured)

        address = f"{self.memory.name}, " if self.memory.name else ""
        work_context = (
            f"in your role as {_profession_phrase(self.memory.profession)}"
            if self.memory.profession
            else "right now"
        )
        template = self._random.choice(rule.responses)
        response = template.format(
            captured,
            address=address,
            work_context=work_context,
        ).strip()
        return response[:1].upper() + response[1:]

    def respond(self, user_input: str) -> str:
        """根据记忆查询、事实抽取和规则库依次生成回复。"""

        user_input = user_input.strip()
        if not user_input:
            self.last_rule = "empty"
            return "Please say something so we can continue."

        memory_answer = self._answer_memory_query(user_input)
        if memory_answer is not None:
            return memory_answer

        updates = self._extract_facts(user_input)
        if updates:
            return self._store_facts(updates)

        for rule in RULES:
            if rule.pattern.search(user_input):
                self.last_rule = rule.topic
                return self._render_rule(rule, user_input)

        # RULES 最后一项是通配规则，正常情况下不会到达这里。
        self.last_rule = "fallback"
        return "Please tell me more."


HELP_TEXT = """Commands:
  /memory        Show remembered name, age, and profession
  /clear-memory  Clear the current session memory
  /help          Show this help
  quit | exit | bye
                  End the conversation
"""


def main() -> None:
    agent = ElizaPro()
    print("Therapist: Hello! I am ELIZA Pro. How can I help you today?")
    print("Therapist: I can remember your name, age, and profession in this session.")
    print("Therapist: Type /help to view commands.")

    while True:
        try:
            user_input = input("You: ")
        except (EOFError, KeyboardInterrupt):
            print("\nTherapist: Goodbye. It was nice talking to you.")
            break

        command = user_input.strip().lower()
        if command in {"quit", "exit", "bye"}:
            print("Therapist: Goodbye. It was nice talking to you.")
            break
        if command == "/memory":
            print(f"Memory: {agent.memory.summary()}")
            continue
        if command == "/clear-memory":
            agent.memory.clear()
            print("Memory: The current session memory has been cleared.")
            continue
        if command == "/help":
            print(HELP_TEXT)
            continue

        print(f"Therapist: {agent.respond(user_input)}")


if __name__ == "__main__":
    main()
