from __future__ import annotations

import json
import os
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AppSetting


DEFAULT_SETTINGS: dict[str, Any] = {
    "language": "zh-CN",
    "timezone": "Asia/Shanghai",
    "schedule_cron": "0 8 * * *",
    "max_items_per_source": 20,
    "max_total_items": 40,
    "topic_keywords": "LLM,large language model,AI infra,agent,benchmark,breakthrough,safety",
    "podcast_name": "AI Daily Podcast",
    "podcast_host_style": "专业、简洁、信息密度高",
    "llm_api_base": "https://api.openai.com/v1",
    "llm_api_key": "",
    "llm_model": "gpt-4o-mini",
    "llm_temperature": 0.2,
    "llm_summary_system_prompt": "你是专业 AI 播客编辑，擅长信息压缩与事实表达。",
    "llm_summary_prompt_template": (
        "请用 {language} 输出该条目的结构化摘要，长度控制在 130-220 字。"
        "输出严格 JSON 对象，字段必须为 summary, impact。"
        "impact 要解释该信息对 AI/LLM 研发或产业的意义。"
    ),
    "llm_episode_system_prompt": "你是 AI 资讯播客总编，能够组织逻辑清晰、节奏紧凑的音频节目。",
    "llm_episode_prompt_template": (
        "请用 {language} 生成播客脚本，主持风格为：{host_style}。"
        "输出严格 JSON 对象，字段 title, overview, script。"
        "script 是可以直接用于 TTS 的完整播报稿，长度 1200-2400 汉字。"
    ),
    "prompt_versions": [],
    "tts_enabled": True,
    "tts_provider": "edge_tts",
    "tts_api_base": "https://api.openai.com/v1",
    "tts_api_key": "",
    "tts_model": "gpt-4o-mini-tts",
    "tts_voice": "zh-CN-XiaoxiaoNeural",
    "tts_format": "mp3",
    "tts_api_mode": "auto",
    "tts_edge_proxy": "",
    "tts_edge_connect_timeout": 10,
    "tts_edge_receive_timeout": 60,
    "tts_audio_speed": 1.0,
    "telegram_enabled": True,
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "telegram_send_audio": True,
    "newsapi_global_key": "",
    "rsshub_base_url": os.getenv("RSSHUB_BASE_URL", "http://rsshub:1200"),
}


def _to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _from_json(raw: str) -> Any:
    return json.loads(raw)


def ensure_default_settings(db: Session) -> None:
    existing = {item.key for item in db.scalars(select(AppSetting)).all()}
    dirty = False

    for key, value in DEFAULT_SETTINGS.items():
        if key in existing:
            continue
        db.add(AppSetting(key=key, value_json=_to_json(value)))
        dirty = True

    if dirty:
        db.commit()


def get_settings(db: Session) -> dict[str, Any]:
    values = dict(DEFAULT_SETTINGS)
    rows = db.scalars(select(AppSetting)).all()
    for row in rows:
        values[row.key] = _from_json(row.value_json)
    return values


def set_settings(db: Session, values: dict[str, Any]) -> dict[str, Any]:
    if not values:
        return get_settings(db)

    for key, value in values.items():
        row = db.get(AppSetting, key)
        if row is None:
            db.add(AppSetting(key=key, value_json=_to_json(value)))
        else:
            row.value_json = _to_json(value)
    db.commit()
    return get_settings(db)
