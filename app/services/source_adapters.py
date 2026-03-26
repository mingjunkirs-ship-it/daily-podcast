from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import feedparser
from dateutil import parser as date_parser

from app.models import Source
from app.services.rss import build_rss_xml
from app.services.types import NormalizedItem


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return date_parser.parse(raw)
    except Exception:
        return None


def _normalize_text(raw: Any, limit: int = 4000) -> str:
    if raw is None:
        return ""
    text = str(raw).strip()
    return text[:limit]


def _from_rss(source: Source, config: dict[str, Any]) -> list[NormalizedItem]:
    url = str(config.get("url", "")).strip()
    if not url:
        raise ValueError(f"Source[{source.id}] RSS 缺少 url")

    parsed = feedparser.parse(url)
    items: list[NormalizedItem] = []
    for entry in parsed.entries:
        content = ""
        if getattr(entry, "content", None):
            first = entry.content[0]
            content = _normalize_text(first.get("value", ""))
        items.append(
            NormalizedItem(
                source_id=source.id,
                source_name=source.name,
                title=_normalize_text(getattr(entry, "title", "Untitled"), 400),
                link=_normalize_text(getattr(entry, "link", ""), 500),
                summary=_normalize_text(getattr(entry, "summary", ""), 1200),
                content=content,
                author=_normalize_text(getattr(entry, "author", ""), 120),
                published_at=_parse_date(getattr(entry, "published", None)),
                tags=[tag.term for tag in getattr(entry, "tags", []) if getattr(tag, "term", None)],
            )
        )
    return items


def _from_arxiv(source: Source, config: dict[str, Any]) -> list[NormalizedItem]:
    query = str(config.get("query", "cat:cs.CL OR cat:cs.AI"))
    max_results = int(config.get("max_results", 30))
    sort_by = str(config.get("sort_by", "submittedDate"))
    sort_order = str(config.get("sort_order", "descending"))

    params = {
        "search_query": query,
        "start": 0,
        "max_results": max(1, min(max_results, 100)),
        "sortBy": sort_by,
        "sortOrder": sort_order,
    }
    url = f"https://export.arxiv.org/api/query?{urlencode(params)}"
    parsed = feedparser.parse(url)

    items: list[NormalizedItem] = []
    for entry in parsed.entries:
        summary = _normalize_text(getattr(entry, "summary", ""), 2000)
        items.append(
            NormalizedItem(
                source_id=source.id,
                source_name=source.name,
                title=_normalize_text(getattr(entry, "title", "Untitled"), 400),
                link=_normalize_text(getattr(entry, "link", ""), 500),
                summary=summary,
                content=summary,
                author=", ".join(
                    [author.name for author in getattr(entry, "authors", []) if getattr(author, "name", None)]
                )[:200],
                published_at=_parse_date(getattr(entry, "published", None)),
                tags=[tag.term for tag in getattr(entry, "tags", []) if getattr(tag, "term", None)],
            )
        )
    return items


def _filter_by_keywords(items: list[NormalizedItem], keywords_text: str) -> list[NormalizedItem]:
    """Per-source keyword filtering (same logic as pipeline global filter)."""
    keywords = [kw.strip().lower() for kw in keywords_text.split(",") if kw.strip()]
    if not keywords:
        return items
    filtered: list[NormalizedItem] = []
    for item in items:
        haystack = " ".join([item.title, item.summary, item.content, " ".join(item.tags)]).lower()
        if any(kw in haystack for kw in keywords):
            filtered.append(item)
    return filtered


async def fetch_and_transform_source(source: Source, settings: dict[str, Any]) -> tuple[list[NormalizedItem], str]:
    config = {}
    if source.config_json:
        import json

        config = json.loads(source.config_json)

    source_type = source.source_type.lower().strip()
    if source_type == "rss":
        items = _from_rss(source, config)
    elif source_type == "arxiv":
        items = _from_arxiv(source, config)
    else:
        raise ValueError(f"不支持的 source_type: {source_type}")

    # Per-source keywords filtering (config.keywords overrides global)
    source_keywords = str(config.get("keywords", "")).strip()
    if source_keywords:
        items = _filter_by_keywords(items, source_keywords)

    # Per-source max_items (config.max_items overrides global)
    max_items = int(config.get("max_items") or settings.get("max_items_per_source", 20))
    items = items[: max(1, max_items)]

    rss_xml = build_rss_xml(
        feed_title=f"{source.name} Converted Feed",
        feed_link="https://localhost/internal",
        feed_description=f"Converted feed for source #{source.id}",
        items=items,
    )
    return items, rss_xml
