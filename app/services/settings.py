from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AppSetting, UserSetting


DEFAULT_SETTINGS: dict[str, Any] = {
    "language": "zh-CN",
    "timezone": "Asia/Shanghai",
    "schedule_enabled": True,
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
    "llm_summary_system_prompt": "你是专业的 AI 资讯编辑，擅长从技术文章中提炼核心信息。你的受众是忙碌的 AI 从业者，输出内容将用于后续播客脚本生成，需要信息密度高、事实准确、逻辑清晰。",
    "llm_summary_prompt_template": (
        """请用 {language} 对以下内容进行结构化摘要。

来源：{source}（第 {index}/{total} 条）
标题：{title}

要求：
- 输出严格 JSON 对象，字段为 summary、impact、key_facts
- summary：核心事件或观点，130–200 字，去除冗余修饰
- impact：该信息对 AI/LLM 研发或产业的实质影响，重点说明"谁会受影响、怎么受影响"
- key_facts：数组，提取 2–4 条可直接引用的关键数据或结论（数字、名称、时间等）
- 禁止输出 JSON 以外的任何内容"""
    ),
    "llm_episode_system_prompt": (
        """你是一档 AI 科技日报播客的主编，节目面向通勤中的 AI 从业者，每期时长 5–8 分钟。
你的核心职责：将多条 AI 资讯整合为一期逻辑连贯、节奏流畅的音频节目。
输出的脚本必须满足：① 可直接交给 TTS 引擎朗读，② 无 Markdown/符号/表情，③ 段落间有自然的口语过渡。"""
    ),
    "llm_episode_prompt_template": (
        """请用 {language} 为播客「{podcast_name}」生成本期脚本，共 {count} 条资讯，主持风格：{host_style}。

输出严格 JSON 对象，字段如下：
- title：本期标题，15 字以内，吸引通勤听众
- overview：开场白，60–80 字，概括本期亮点，语气自然口语化
- script：正文脚本，1200–2400 字，要求如下：
  （1）按资讯重要性排序，开头放最重磅的一条
  （2）每条资讯之间用口语化过渡句衔接，如"说完这个，再来看一条关于……的消息"
  （3）重要数字或术语后加简短解释，方便听众"只听不看"也能理解
  （4）结尾有 30–50 字的收尾语，可预告下期或鼓励听众
  （5）全文无任何 Markdown 符号、括号注释、列表符号，纯口语化书面语
- duration_estimate：预估朗读时长（分钟，按每分钟 200 字计算）
- 禁止输出 JSON 以外的任何内容"""
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
    "auth_blocked_usernames": [],
}

GLOBAL_ONLY_SETTING_KEYS = {"auth_blocked_usernames"}
USER_SCOPED_SETTING_KEYS = set(DEFAULT_SETTINGS.keys()) - GLOBAL_ONLY_SETTING_KEYS


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


def migrate_legacy_global_user_settings(db: Session, username: str) -> bool:
    target_username = str(username or "").strip()
    if not target_username:
        return False

    changed = False
    for key in USER_SCOPED_SETTING_KEYS:
        app_row = db.get(AppSetting, key)
        if app_row is None:
            continue

        try:
            value = _from_json(app_row.value_json)
        except Exception:
            continue

        if value == DEFAULT_SETTINGS.get(key):
            continue

        exists = db.scalar(select(UserSetting).where(UserSetting.username == target_username, UserSetting.key == key))
        if exists is not None:
            continue

        db.add(UserSetting(username=target_username, key=key, value_json=app_row.value_json))
        changed = True

    if changed:
        db.commit()
    return changed


def get_settings(db: Session, username: str | None = None) -> dict[str, Any]:
    values = dict(DEFAULT_SETTINGS)
    rows = db.scalars(select(AppSetting)).all()

    if username:
        for row in rows:
            if row.key in GLOBAL_ONLY_SETTING_KEYS:
                values[row.key] = _from_json(row.value_json)

        user_rows = db.scalars(select(UserSetting).where(UserSetting.username == username)).all()
        for row in user_rows:
            values[row.key] = _from_json(row.value_json)
        return values

    for row in rows:
        values[row.key] = _from_json(row.value_json)
    return values


def set_settings(db: Session, values: dict[str, Any], username: str | None = None) -> dict[str, Any]:
    if not values:
        return get_settings(db, username=username)

    if username:
        for key, value in values.items():
            if key in GLOBAL_ONLY_SETTING_KEYS:
                continue
            row = db.scalar(select(UserSetting).where(UserSetting.username == username, UserSetting.key == key))
            if row is None:
                db.add(UserSetting(username=username, key=key, value_json=_to_json(value)))
            else:
                row.value_json = _to_json(value)
        db.commit()
        return get_settings(db, username=username)

    for key, value in values.items():
        row = db.get(AppSetting, key)
        if row is None:
            db.add(AppSetting(key=key, value_json=_to_json(value)))
        else:
            row.value_json = _to_json(value)
    db.commit()
    return get_settings(db)
