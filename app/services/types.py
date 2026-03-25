from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class NormalizedItem:
    source_id: int
    source_name: str
    title: str
    link: str
    summary: str = ""
    content: str = ""
    author: str = ""
    published_at: datetime | None = None
    tags: list[str] = field(default_factory=list)

    def unique_key(self) -> str:
        return self.link.strip() or self.title.strip().lower()

    def as_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "source_name": self.source_name,
            "title": self.title,
            "link": self.link,
            "summary": self.summary,
            "content": self.content,
            "author": self.author,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "tags": self.tags,
        }

