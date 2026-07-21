"""用户长期偏好记忆。"""

from __future__ import annotations

import json
from pathlib import Path


class MemoryStoreError(RuntimeError):
    """记忆文件无法读取或写入。"""


class PreferenceMemory:
    """将用户偏好按类别持久化到 JSON 文件。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._preferences: dict[str, list[str]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MemoryStoreError(f"无法读取记忆文件：{exc}") from exc

        raw_preferences = payload.get("preferences", {})
        if not isinstance(raw_preferences, dict):
            raise MemoryStoreError("记忆文件中的 preferences 必须是对象。")

        self._preferences = {
            str(category): [str(value) for value in values]
            for category, values in raw_preferences.items()
            if isinstance(values, list)
        }

    def remember(self, category: str, value: str) -> bool:
        """保存一项偏好；返回 False 表示该偏好已存在。"""
        normalized_category = category.strip() or "其他"
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("偏好内容不能为空。")

        values = self._preferences.setdefault(normalized_category, [])
        if normalized_value in values:
            return False
        values.append(normalized_value)
        self._save()
        return True

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = {"preferences": self._preferences}
        try:
            temporary_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary_path.replace(self.path)
        except OSError as exc:
            raise MemoryStoreError(f"无法保存记忆文件：{exc}") from exc

    def clear(self) -> None:
        """清空全部长期偏好。"""
        self._preferences.clear()
        if self.path.exists():
            try:
                self.path.unlink()
            except OSError as exc:
                raise MemoryStoreError(f"无法清空记忆文件：{exc}") from exc

    def summary(self) -> str:
        """返回适合注入 Prompt 的自然语言摘要。"""
        if not self._preferences:
            return "暂无已记录的用户偏好。"
        lines = [
            f"- {category}: {'；'.join(values)}"
            for category, values in sorted(self._preferences.items())
        ]
        return "\n".join(lines)

    def as_dict(self) -> dict[str, list[str]]:
        """返回记忆副本，便于展示与测试。"""
        return {
            category: list(values)
            for category, values in self._preferences.items()
        }
