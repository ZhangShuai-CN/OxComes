"""事件数据模型（fingerprint 去重模式移植自老项目 event_calendar_task）。"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

CATEGORY_LABELS = {
    "earnings": "财报",
    "ipo": "IPO",
    "investor_day": "投资者日",
    "macro": "宏观数据",
    "conference": "大会",
}


@dataclass(frozen=True)
class Event:
    name: str
    event_date: str          # YYYY-MM-DD
    category: str
    importance: str = "medium"
    description: str = ""
    url: str = ""
    source: str = "unknown"

    @property
    def fingerprint(self) -> str:
        raw = f"{self.event_date}|{self.category}|{self.name[:30]}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]

    @property
    def category_label(self) -> str:
        return CATEGORY_LABELS.get(self.category, self.category)

    def to_row(self) -> tuple:
        return (self.fingerprint, self.name, self.event_date, self.category,
                self.importance, self.description, self.url, self.source)
