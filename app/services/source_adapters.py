from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import feedparser
import httpx
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


async def _from_newsapi(source: Source, config: dict[str, Any], settings: dict[str, Any]) -> list[NormalizedItem]:
    api_key = str(config.get("api_key") or settings.get("newsapi_global_key") or "").strip()
    if not api_key:
        raise ValueError(f"Source[{source.id}] NewsAPI 缺少 api_key")

    query = str(config.get("query", "AI OR LLM OR artificial intelligence"))
    language = str(config.get("language", "en"))
    page_size = int(config.get("page_size", 30))
    endpoint = str(config.get("endpoint", "https://newsapi.org/v2/everything"))
    sort_by = str(config.get("sort_by", "publishedAt"))

    params = {
        "q": query,
        "language": language,
        "pageSize": max(1, min(page_size, 100)),
        "sortBy": sort_by,
    }

    timeout_sec = int(settings.get("source_request_timeout_sec", 25))
    async with httpx.AsyncClient(timeout=timeout_sec) as client:
        resp = await client.get(endpoint, params=params, headers={"X-Api-Key": api_key})
        resp.raise_for_status()
        payload = resp.json()

    articles = payload.get("articles", [])
    items: list[NormalizedItem] = []
    for article in articles:
        source_name = article.get("source", {}).get("name") or source.name
        description = _normalize_text(article.get("description", ""), 1200)
        content = _normalize_text(article.get("content", ""), 1800)
        items.append(
            NormalizedItem(
                source_id=source.id,
                source_name=f"{source.name}:{source_name}"[:140],
                title=_normalize_text(article.get("title", "Untitled"), 400),
                link=_normalize_text(article.get("url", ""), 500),
                summary=description,
                content=content,
                author=_normalize_text(article.get("author", ""), 120),
                published_at=_parse_date(article.get("publishedAt")),
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
    elif source_type == "newsapi":
        items = await _from_newsapi(source, config, settings)
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

