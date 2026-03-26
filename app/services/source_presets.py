from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Source


CURATED_SOURCE_PRESETS: list[dict[str, Any]] = [
    {
        "preset_id": "arxiv-llm-daily",
        "name": "arXiv LLM Daily",
        "source_type": "arxiv",
        "enabled": True,
        "description": "arXiv 上最新 LLM / NLP / AI 论文",
        "config": {
            "query": "cat:cs.CL OR cat:cs.AI OR ti:\"large language model\" OR abs:\"large language model\"",
            "max_results": 40,
            "sort_by": "submittedDate",
            "sort_order": "descending",
        },
    },
    {
        "preset_id": "arxiv-agent-reasoning",
        "name": "arXiv Agent & Reasoning",
        "source_type": "arxiv",
        "enabled": True,
        "description": "arXiv 上 Agent、推理与工具使用方向论文",
        "config": {
            "query": "all:agent OR all:reasoning OR all:tool use OR all:function calling",
            "max_results": 30,
            "sort_by": "submittedDate",
            "sort_order": "descending",
        },
    },
    {
        "preset_id": "newsapi-ai-global",
        "name": "NewsAPI AI Global",
        "source_type": "newsapi",
        "enabled": True,
        "description": "全球 AI / LLM 相关新闻（需配置 NewsAPI Key）",
        "config": {
            "query": "(AI OR LLM OR \"artificial intelligence\" OR \"AI infra\")",
            "language": "en",
            "page_size": 40,
            "sort_by": "publishedAt",
        },
    },
    {
        "preset_id": "hf-blog-rss",
        "name": "HuggingFace Blog",
        "source_type": "rss",
        "enabled": False,
        "description": "Hugging Face 博客 RSS（可按需开启）",
        "config": {
            "url": "https://huggingface.co/blog/feed.xml",
        },
    },
]


def list_presets() -> list[dict[str, Any]]:
    return CURATED_SOURCE_PRESETS


def import_presets(
    db: Session,
    owner_username: str,
    preset_ids: list[str] | None = None,
    overwrite_existing: bool = False,
) -> dict[str, int]:
    selected_ids = set(preset_ids or [preset["preset_id"] for preset in CURATED_SOURCE_PRESETS])
    selected_presets = [preset for preset in CURATED_SOURCE_PRESETS if preset["preset_id"] in selected_ids]

    existing_sources = db.scalars(select(Source).where(Source.owner_username == owner_username)).all()
    existing_by_name_type = {(source.name.strip().lower(), source.source_type.strip().lower()): source for source in existing_sources}

    created = 0
    updated = 0
    skipped = 0

    for preset in selected_presets:
        key = (preset["name"].strip().lower(), preset["source_type"].strip().lower())
        config_json = json.dumps(preset["config"], ensure_ascii=False)
        target = existing_by_name_type.get(key)

        if target is None:
            db.add(
                Source(
                    name=preset["name"],
                    owner_username=owner_username,
                    source_type=preset["source_type"],
                    enabled=bool(preset.get("enabled", True)),
                    config_json=config_json,
                )
            )
            created += 1
            continue

        if overwrite_existing:
            target.enabled = bool(preset.get("enabled", True))
            target.config_json = config_json
            updated += 1
        else:
            skipped += 1

    db.commit()

    return {
        "selected": len(selected_presets),
        "created": created,
        "updated": updated,
        "skipped": skipped,
    }
