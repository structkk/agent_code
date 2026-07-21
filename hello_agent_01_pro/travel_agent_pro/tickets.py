"""可替换的票务库存接口及本地模拟实现。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


VALID_STATUSES = {"available", "sold_out", "unknown"}


@dataclass(frozen=True, slots=True)
class TicketResult:
    attraction: str
    status: str
    source: str


class TicketInventory:
    """从 JSON 文件读取模拟票务状态。

    后续接入真实售票服务时，只需替换 check 方法的实现。
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.source = "local_mock"
        self.default_status = "unknown"
        self._inventory: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.source = str(payload.get("source", "local_mock"))
        default_status = str(payload.get("default_status", "unknown"))
        self.default_status = (
            default_status if default_status in VALID_STATUSES else "unknown"
        )
        raw_inventory = payload.get("attractions", {})
        if not isinstance(raw_inventory, dict):
            raise ValueError("票务文件中的 attractions 必须是对象。")
        self._inventory = {
            str(name).strip(): str(status)
            for name, status in raw_inventory.items()
            if str(status) in VALID_STATUSES
        }

    def check(self, attraction: str) -> TicketResult:
        """查询景点票务；支持常见的前后缀名称差异。"""
        normalized = attraction.strip()
        status = self._inventory.get(normalized)
        if status is None:
            for known_name, known_status in self._inventory.items():
                if known_name in normalized or normalized in known_name:
                    status = known_status
                    break
        return TicketResult(
            attraction=normalized,
            status=status or self.default_status,
            source=self.source,
        )
