from __future__ import annotations

import json
import re
from inspect import isawaitable
from typing import Any

import httpx

from app.services.types import NormalizedItem


def _extract_json_object(raw: str) -> dict[str, Any] | None:
    raw = raw.strip()
    try:
        return json.loads(raw)
    except Exception:
        pass

    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        return None

    try:
        return json.loads(match.group(0))
    except Exception:
        return None


def _render_prompt(template: str, values: dict[str, Any]) -> str:
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace(f"{{{key}}}", str(value))
    return rendered


class LLMClient:
    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings
        self.base_url = str(settings.get("llm_api_base", "https://api.openai.com/v1")).rstrip("/")
        self.api_key = str(settings.get("llm_api_key", "")).strip()
        self.model = str(settings.get("llm_model", "gpt-4o-mini")).strip()
        self.temperature = float(settings.get("llm_temperature", 0.2))

    def available(self) -> bool:
        return bool(self.api_key and self.model)

    async def _chat(self, messages: list[dict[str, str]], max_tokens: int = 1200) -> str:
        if not self.available():
            raise RuntimeError("LLM API 未配置")

        endpoint = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": max_tokens,
        }

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                endpoint,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        return data["choices"][0]["message"]["content"].strip()

    async def test_connection(self) -> tuple[bool, str]:
        if not self.available():
            return False, "LLM API 未配置完整（base/key/model）"
        try:
            content = await self._chat(
                messages=[
                    {"role": "system", "content": "You are a concise assistant."},
                    {"role": "user", "content": "Reply with: ok"},
                ],
                max_tokens=16,
            )
            return True, f"LLM 连接成功，模型可用：{self.model}，返回：{content[:40]}"
        except Exception as exc:
            return False, f"LLM 连接失败：{exc}"

    async def summarize_items(self, items: list[NormalizedItem], language: str) -> list[dict[str, str]]:
        summaries: list[dict[str, str]] = []
        summary_system_prompt = str(
            self.settings.get(
                "llm_summary_system_prompt",
                "你是专业 AI 播客编辑，擅长信息压缩与事实表达。",
            )
        )
        summary_template = str(
            self.settings.get(
                "llm_summary_prompt_template",
                "请用 {language} 输出该条目的结构化摘要，长度控制在 130-220 字。"
                "输出严格 JSON 对象，字段必须为 summary, impact。"
                "impact 要解释该信息对 AI/LLM 研发或产业的意义。",
            )
        )
        for index, item in enumerate(items, start=1):
            progress_hook = self.settings.get("_summary_progress_hook")
            if callable(progress_hook):
                try:
                    maybe = progress_hook(index, len(items), item)
                    if isawaitable(maybe):
                        await maybe
                except Exception:
                    pass

            if not self.available():
                fallback = (item.summary or item.content or "")[:260]
                summaries.append(
                    {
                        "title": item.title,
                        "link": item.link,
                        "source": item.source_name,
                        "summary": fallback if fallback else "（无摘要）",
                        "impact": "请配置 LLM API 以生成高质量影响分析。",
                    }
                )
                continue

            prompt = _render_prompt(
                summary_template,
                {
                    "language": language,
                    "index": index,
                    "total": len(items),
                    "source": item.source_name,
                    "title": item.title,
                },
            )
            user_content = (
                f"[条目 {index}/{len(items)}]\n"
                f"标题: {item.title}\n"
                f"来源: {item.source_name}\n"
                f"链接: {item.link}\n"
                f"摘要: {item.summary}\n"
                f"正文片段: {item.content[:2200]}"
            )

            try:
                raw = await self._chat(
                    messages=[
                        {"role": "system", "content": summary_system_prompt},
                        {"role": "user", "content": f"{prompt}\n\n{user_content}"},
                    ],
                    max_tokens=400,
                )
                parsed = _extract_json_object(raw) or {}
                summary = str(parsed.get("summary") or "").strip() or (item.summary or item.content)[:220]
                impact = str(parsed.get("impact") or "").strip() or "该信息值得持续跟踪。"
            except Exception:
                summary = (item.summary or item.content or "")[:220]
                impact = "LLM 总结失败，建议重试。"

            summaries.append(
                {
                    "title": item.title,
                    "link": item.link,
                    "source": item.source_name,
                    "summary": summary,
                    "impact": impact,
                }
            )
        return summaries

    async def compose_episode(
        self,
        summaries: list[dict[str, str]],
        language: str,
        podcast_name: str,
        host_style: str,
    ) -> dict[str, str]:
        if not summaries:
            return {"title": f"{podcast_name} - Empty", "overview": "无可用内容", "script": "今天暂无可播报内容。"}

        if not self.available():
            lines = [f"欢迎收听 {podcast_name}。今天的 AI 快报如下："]
            for idx, item in enumerate(summaries, start=1):
                lines.append(f"第 {idx} 条：{item['title']}。{item['summary']}。")
            lines.append("以上就是今天的 AI 简报，我们明天见。")
            return {
                "title": f"{podcast_name} | {summaries[0]['title'][:60]}",
                "overview": " | ".join(item["title"] for item in summaries[:3]),
                "script": "\n".join(lines),
            }

        episode_template = str(
            self.settings.get(
                "llm_episode_prompt_template",
                "请用 {language} 生成播客脚本，主持风格为：{host_style}。"
                "输出严格 JSON 对象，字段 title, overview, script。"
                "script 是可以直接用于 TTS 的完整播报稿，长度 1200-2400 汉字。",
            )
        )
        prompt = _render_prompt(
            episode_template,
            {
                "language": language,
                "host_style": host_style,
                "podcast_name": podcast_name,
                "count": len(summaries),
            },
        )
        episode_system_prompt = str(
            self.settings.get(
                "llm_episode_system_prompt",
                "你是 AI 资讯播客总编，能够组织逻辑清晰、节奏紧凑的音频节目。",
            )
        )

        digest = "\n\n".join(
            [
                f"#{idx+1} 标题: {item['title']}\n来源: {item['source']}\n摘要: {item['summary']}\n影响: {item['impact']}\n链接: {item['link']}"
                for idx, item in enumerate(summaries)
            ]
        )

        try:
            raw = await self._chat(
                messages=[
                    {
                        "role": "system",
                        "content": episode_system_prompt,
                    },
                    {"role": "user", "content": f"{prompt}\n\n素材:\n{digest}"},
                ],
                max_tokens=2200,
            )
        except Exception:
            lines = [f"欢迎收听 {podcast_name}，以下是今天的 AI 重点："]
            for idx, item in enumerate(summaries, start=1):
                lines.append(f"第 {idx} 条：{item['title']}。{item['summary']}。")
                lines.append(f"这条信息的意义是：{item['impact']}。")
            lines.append("以上是今天的播报，感谢收听。")
            return {
                "title": f"{podcast_name} | 自动降级稿",
                "overview": "LLM 长文本生成超时，已自动使用降级脚本",
                "script": "\n".join(lines),
            }

        parsed = _extract_json_object(raw) or {}
        title = str(parsed.get("title") or f"{podcast_name} 今日 AI 观察").strip()
        overview = str(parsed.get("overview") or "AI 重点资讯综述").strip()
        script = str(parsed.get("script") or raw).strip()

        return {"title": title, "overview": overview, "script": script}
