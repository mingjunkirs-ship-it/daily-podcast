from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.config import AUDIO_DIR, FEEDS_DIR, NOTES_DIR
from app.database import SessionLocal
from app.models import Episode, Source
from app.services.llm_client import LLMClient
from app.services.rss import build_rss_xml
from app.services.settings import get_settings
from app.services.source_adapters import fetch_and_transform_source
from app.services.telegram_client import TelegramClient
from app.services.tts_client import TTSClient
from app.services.types import NormalizedItem


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _filter_by_keywords(items: list[NormalizedItem], keywords_text: str) -> list[NormalizedItem]:
    keywords = [item.strip().lower() for item in keywords_text.split(",") if item.strip()]
    if not keywords:
        return items

    filtered: list[NormalizedItem] = []
    for item in items:
        haystack = " ".join([item.title, item.summary, item.content, " ".join(item.tags)]).lower()
        if any(keyword in haystack for keyword in keywords):
            filtered.append(item)
    return filtered


def _deduplicate(items: list[NormalizedItem]) -> list[NormalizedItem]:
    seen: set[str] = set()
    output: list[NormalizedItem] = []
    for item in items:
        key = item.unique_key().strip().lower()
        if not key:
            continue
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def _write_source_feed(source_id: int, xml: str) -> Path:
    path = FEEDS_DIR / f"source-{source_id}.xml"
    path.write_text(xml, encoding="utf-8")
    return path


def _write_aggregate_feed(items: list[NormalizedItem], owner_username: str) -> Path:
    xml = build_rss_xml(
        feed_title="AI Podcast Aggregated Feed",
        feed_link="https://localhost/internal/aggregate",
        feed_description="Aggregated normalized feed across all enabled sources.",
        items=items,
    )
    safe_owner = str(owner_username or "admin").strip() or "admin"
    path = FEEDS_DIR / f"aggregated-{safe_owner}.xml"
    path.write_text(xml, encoding="utf-8")
    return path


def _write_notes_file(episode_id: int, title: str, summaries: list[dict[str, str]]) -> Path:
    lines = [f"# {title}", "", "## 参考内容"]
    for index, item in enumerate(summaries, start=1):
        lines.extend(
            [
                f"### {index}. {item['title']}",
                f"- 来源: {item['source']}",
                f"- 链接: {item['link']}",
                f"- 摘要: {item['summary']}",
                f"- 影响: {item['impact']}",
                "",
            ]
        )
    path = NOTES_DIR / f"episode-{episode_id}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _build_telegram_digest_html(title: str, overview: str, summaries: list[dict[str, str]]) -> str:
    lines = [
        f"<b>🎙️ {escape(title)}</b>",
        "",
        f"<i>{escape(overview)}</i>",
        "",
        "<b>📌 今日重点</b>",
    ]

    for index, row in enumerate(summaries[:8], start=1):
        item_title = escape(row.get("title", "Untitled"))
        source_name = escape(row.get("source", "Unknown"))
        summary_text = escape((row.get("summary") or "")[:220])
        link = row.get("link") or ""
        safe_link = escape(link, quote=True)

        lines.extend(
            [
                f"{index}. <b>{item_title}</b>",
                f"来源：{source_name}",
                f"摘要：{summary_text}",
                f"<a href=\"{safe_link}\">原文链接</a>" if safe_link else "",
                "",
            ]
        )

    return "\n".join(lines).strip()


class PipelineRunner:
    def __init__(self, session_factory=SessionLocal) -> None:
        self.session_factory = session_factory

    @staticmethod
    def _read_payload(episode: Episode) -> dict[str, Any]:
        try:
            parsed = json.loads(episode.payload_json or "{}")
        except Exception:
            return {}
        if isinstance(parsed, dict):
            return parsed
        return {}

    @staticmethod
    def _write_payload(episode: Episode, payload: dict[str, Any]) -> None:
        episode.payload_json = json.dumps(payload, ensure_ascii=False)

    def _set_progress(
        self,
        db: Session,
        episode: Episode,
        *,
        stage: str,
        percent: int,
        message: str,
        status: str = "running",
        extra_payload: dict[str, Any] | None = None,
        commit: bool = True,
    ) -> None:
        payload = self._read_payload(episode)
        payload["progress"] = {
            "stage": stage,
            "percent": max(0, min(100, int(percent))),
            "message": message,
            "updated_at": _utcnow().isoformat(),
        }
        if extra_payload:
            payload.update(extra_payload)
        self._write_payload(episode, payload)
        episode.status = status
        episode.overview = message
        if commit:
            db.commit()

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        text = str(exc or "").strip()
        if text:
            return text
        return exc.__class__.__name__

    def _create_episode(self, db: Session, trigger: str, owner_username: str) -> Episode:
        episode = Episode(owner_username=owner_username, status="pending", trigger_type=trigger, overview="任务排队中")
        db.add(episode)
        db.commit()
        db.refresh(episode)
        self._set_progress(
            db,
            episode,
            stage="queued",
            percent=1,
            message="任务已创建，等待执行",
            status="pending",
        )
        return episode

    async def run_existing_episode(self, episode_id: int) -> dict:
        with self.session_factory() as db:
            episode = db.get(Episode, episode_id)
            if not episode:
                raise RuntimeError(f"episode not found: {episode_id}")

            try:
                result = await self._execute_episode(db, episode)
                return result
            except Exception as exc:
                message = self._safe_error(exc)
                episode.status = "failed"
                episode.error_message = message
                episode.completed_at = _utcnow()
                self._set_progress(
                    db,
                    episode,
                    stage="failed",
                    percent=100,
                    message=f"任务失败：{message}",
                    status="failed",
                    commit=False,
                )
                db.commit()
                raise

    async def queue_once(self, trigger: str = "manual", owner_username: str = "admin") -> int:
        with self.session_factory() as db:
            episode = self._create_episode(db, trigger, owner_username)
            episode_id = episode.id
        asyncio.create_task(self._run_existing_episode_task(episode_id))
        return episode_id

    async def _run_existing_episode_task(self, episode_id: int) -> None:
        try:
            await self.run_existing_episode(episode_id)
        except Exception:
            return

    async def rebuild_source_feeds(self, owner_username: str = "admin") -> dict:
        with self.session_factory() as db:
            settings = get_settings(db, username=owner_username)
            sources = db.scalars(
                select(Source)
                .where(Source.owner_username == owner_username, Source.enabled.is_(True))
                .order_by(Source.id)
            ).all()
            written = 0
            for source in sources:
                try:
                    items, xml = await fetch_and_transform_source(source, settings)
                    _write_source_feed(source.id, xml)
                    source.last_sync_at = _utcnow()
                    source.last_error = ""
                    written += 1
                except Exception as exc:
                    source.last_error = str(exc)
            db.commit()
        return {"sources": written}

    async def run_once(self, trigger: str = "manual", owner_username: str = "admin") -> dict:
        with self.session_factory() as db:
            episode = self._create_episode(db, trigger, owner_username)
            try:
                result = await self._execute_episode(db, episode)
                return result
            except Exception as exc:
                message = self._safe_error(exc)
                episode.status = "failed"
                episode.error_message = message
                episode.completed_at = _utcnow()
                self._set_progress(
                    db,
                    episode,
                    stage="failed",
                    percent=100,
                    message=f"任务失败：{message}",
                    status="failed",
                    commit=False,
                )
                db.commit()
                raise

    async def _execute_episode(self, db: Session, episode: Episode) -> dict:
        self._set_progress(db, episode, stage="init", percent=3, message="初始化任务参数")

        owner_username = str(episode.owner_username or "admin").strip() or "admin"
        settings = get_settings(db, username=owner_username)
        language = str(settings.get("language", "zh-CN"))
        podcast_name = str(settings.get("podcast_name", "AI Daily Podcast"))
        host_style = str(settings.get("podcast_host_style", "专业、简洁、信息密度高"))
        max_total_items = int(settings.get("max_total_items", 40))

        sources = db.scalars(
            select(Source)
            .where(Source.owner_username == owner_username, Source.enabled.is_(True))
            .order_by(Source.id)
        ).all()
        if not sources:
            raise RuntimeError("没有启用的 Source，请先在管理台配置")

        all_items: list[NormalizedItem] = []
        source_results: list[dict[str, Any]] = []
        self._set_progress(
            db,
            episode,
            stage="collecting",
            percent=8,
            message=f"开始抓取 {len(sources)} 个来源",
            extra_payload={"source_results": source_results},
        )

        for source in sources:
            current_index = len(source_results) + 1
            collecting_percent = 8 + int(24 * max(0, current_index - 1) / max(1, len(sources)))
            self._set_progress(
                db,
                episode,
                stage="collecting",
                percent=collecting_percent,
                message=f"抓取来源 {current_index}/{len(sources)}：{source.name}",
                extra_payload={"source_results": source_results},
            )
            try:
                items, source_rss_xml = await fetch_and_transform_source(source, settings)
                _write_source_feed(source.id, source_rss_xml)
                source.last_sync_at = _utcnow()
                source.last_error = ""
                all_items.extend(items)
                source_results.append(
                    {
                        "id": source.id,
                        "name": source.name,
                        "ok": True,
                        "item_count": len(items),
                        "error": "",
                    }
                )
            except Exception as exc:
                source_error = self._safe_error(exc)
                source.last_error = source_error
                source_results.append(
                    {
                        "id": source.id,
                        "name": source.name,
                        "ok": False,
                        "item_count": 0,
                        "error": source_error,
                    }
                )

        db.commit()
        self._set_progress(
            db,
            episode,
            stage="collecting",
            percent=35,
            message=(
                f"抓取完成：成功 {sum(1 for row in source_results if row.get('ok'))}/{len(source_results)} 来源，"
                f"原始条目 {len(all_items)} 条"
            ),
            extra_payload={"source_results": source_results},
        )

        all_items = _deduplicate(all_items)
        dedup_count = len(all_items)
        all_items = _filter_by_keywords(all_items, str(settings.get("topic_keywords", "")))
        filtered_count = len(all_items)
        all_items.sort(key=lambda item: item.published_at or _utcnow(), reverse=True)
        all_items = all_items[: max(1, max_total_items)]

        self._set_progress(
            db,
            episode,
            stage="filtering",
            percent=45,
            message=(
                f"过滤完成：去重后 {dedup_count} 条，关键词命中 {filtered_count} 条，"
                f"截断后保留 {len(all_items)} 条"
            ),
            extra_payload={
                "source_results": source_results,
                "stats": {
                    "raw_item_count": sum(int(row.get("item_count") or 0) for row in source_results),
                    "deduplicated_count": dedup_count,
                    "keyword_matched_count": filtered_count,
                    "final_item_count": len(all_items),
                },
            },
        )

        if not all_items:
            source_errors = [f"{row['name']}: {row['error']}" for row in source_results if (not row.get("ok")) and row.get("error")]
            topic_keywords = str(settings.get("topic_keywords", "")).strip()
            if source_errors:
                raise RuntimeError(
                    "抓取后无可用内容。来源错误："
                    + " | ".join(source_errors[:6])
                    + ("。请先修复来源连接后再试。" if len(source_errors) <= 6 else "。请优先修复失败来源。")
                )
            raise RuntimeError(
                f"抓取成功但关键词过滤后为 0 条。当前关键词：{topic_keywords or '（空）'}，建议放宽后重试"
            )

        _write_aggregate_feed(all_items, owner_username)

        async def _summary_progress(index: int, total: int, item: Any) -> None:
            self._set_progress(
                db,
                episode,
                stage="summarizing",
                percent=55 + int(12 * max(0, index - 1) / max(1, total)),
                message=f"正在总结 {index}/{total}：{str(getattr(item, 'title', 'Untitled'))[:80]}",
            )

        settings["_summary_progress_hook"] = _summary_progress
        llm = LLMClient(settings)
        self._set_progress(db, episode, stage="summarizing", percent=55, message=f"正在总结 {len(all_items)} 条内容")
        try:
            summaries = await llm.summarize_items(all_items, language=language)
        finally:
            settings.pop("_summary_progress_hook", None)
        self._set_progress(db, episode, stage="scripting", percent=70, message="正在生成播客脚本")
        script_package = await llm.compose_episode(
            summaries=summaries,
            language=language,
            podcast_name=podcast_name,
            host_style=host_style,
        )

        episode.title = script_package["title"]
        episode.overview = script_package["overview"]
        episode.script_text = script_package["script"]
        episode.item_count = len(all_items)

        notes_path = _write_notes_file(episode.id, episode.title, summaries)
        episode.notes_file = notes_path.name

        payload = {
            "items": [item.as_dict() for item in all_items],
            "summaries": summaries,
            "source_results": source_results,
        }
        episode.payload_json = json.dumps(payload, ensure_ascii=False)

        telegram = TelegramClient(settings)
        telegram_error_text = ""

        self._set_progress(db, episode, stage="delivery_text", percent=80, message="正在推送 Telegram 文本与材料")
        if telegram.available():
            try:
                digest_html = _build_telegram_digest_html(episode.title, episode.overview, summaries)
                sent = await telegram.send_text(digest_html, parse_mode="HTML", disable_preview=True)
                if not sent:
                    fallback_lines = [f"🎙️ {episode.title}", "", episode.overview, "", "📚 主要条目:"]
                    for row in summaries[:8]:
                        fallback_lines.append(f"- {row['title']}\n  {row['link']}")
                    await telegram.send_text("\n".join(fallback_lines), parse_mode=None, disable_preview=True)
                await telegram.send_document(notes_path, caption=f"{episode.title} 参考材料")
            except Exception as exc:
                telegram_error_text = f"文本推送失败：{self._safe_error(exc)}"

        tts = TTSClient(settings)
        audio_ext = str(settings.get("tts_format", "mp3")).lower()
        audio_path = AUDIO_DIR / f"episode-{episode.id}.{audio_ext}"

        self._set_progress(db, episode, stage="tts", percent=88, message="正在生成音频")
        audio_ok = await tts.synthesize(episode.script_text, audio_path)
        if audio_ok:
            episode.audio_file = audio_path.name
            if telegram.available():
                try:
                    await telegram.send_audio(audio_path, caption=episode.title)
                except Exception as exc:
                    audio_send_err = self._safe_error(exc)
                    if telegram_error_text:
                        telegram_error_text = f"{telegram_error_text}；音频推送失败：{audio_send_err}"
                    else:
                        telegram_error_text = f"音频推送失败：{audio_send_err}"
            self._set_progress(db, episode, stage="tts", percent=96, message="音频生成完成")
        else:
            self._set_progress(db, episode, stage="tts", percent=96, message="音频生成失败（文本已推送）")

        episode.status = "completed"
        episode.completed_at = _utcnow()
        self._set_progress(
            db,
            episode,
            stage="completed",
            percent=100,
            message=("任务完成" if not telegram_error_text else f"任务完成（{telegram_error_text}）"),
            status="completed",
            extra_payload={
                "items": [item.as_dict() for item in all_items],
                "summaries": summaries,
                "source_results": source_results,
                "telegram_error": telegram_error_text,
            },
            commit=False,
        )
        db.commit()

        return {
            "episode_id": episode.id,
            "title": episode.title,
            "item_count": episode.item_count,
            "audio_file": episode.audio_file,
        }

    @staticmethod
    def latest_episodes(db: Session, limit: int = 20) -> list[Episode]:
        return db.scalars(select(Episode).order_by(desc(Episode.created_at)).limit(limit)).all()
