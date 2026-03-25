from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx


class TelegramClient:
    def __init__(self, settings: dict[str, Any]) -> None:
        self.enabled = bool(settings.get("telegram_enabled", True))
        self.token = str(settings.get("telegram_bot_token", "")).strip()
        self.chat_id = str(settings.get("telegram_chat_id", "")).strip()
        self.send_audio_enabled = bool(settings.get("telegram_send_audio", True))

    def available(self) -> bool:
        return self.enabled and bool(self.token and self.chat_id)

    @property
    def base_url(self) -> str:
        return f"https://api.telegram.org/bot{self.token}"

    async def _send_text_once(
        self,
        text: str,
        parse_mode: str | None = None,
        disable_preview: bool = True,
    ) -> bool:
        endpoint = f"{self.base_url}/sendMessage"
        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": text[:4000],
            "disable_web_page_preview": disable_preview,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(endpoint, json=payload)
            return resp.status_code < 300

    @staticmethod
    def _split_message(text: str, chunk_size: int = 3500) -> list[str]:
        if len(text) <= chunk_size:
            return [text]

        parts: list[str] = []
        current = ""
        for block in text.split("\n\n"):
            candidate = f"{current}\n\n{block}".strip() if current else block
            if len(candidate) <= chunk_size:
                current = candidate
            else:
                if current:
                    parts.append(current)
                if len(block) <= chunk_size:
                    current = block
                else:
                    for idx in range(0, len(block), chunk_size):
                        parts.append(block[idx : idx + chunk_size])
                    current = ""
        if current:
            parts.append(current)
        return parts

    async def send_text(
        self,
        text: str,
        parse_mode: str | None = None,
        disable_preview: bool = True,
    ) -> bool:
        if not self.available():
            return False

        chunks = self._split_message(text)
        for chunk in chunks:
            ok = await self._send_text_once(chunk, parse_mode=parse_mode, disable_preview=disable_preview)
            if not ok:
                return False
        return True

    async def send_audio(self, audio_file: Path, caption: str = "") -> bool:
        if not self.available() or not self.send_audio_enabled:
            return False
        if not audio_file.exists():
            return False

        endpoint = f"{self.base_url}/sendAudio"
        async with httpx.AsyncClient(timeout=120) as client:
            with audio_file.open("rb") as file_obj:
                files = {"audio": (audio_file.name, file_obj, "audio/mpeg")}
                data = {"chat_id": self.chat_id, "caption": caption[:1024]}
                resp = await client.post(endpoint, data=data, files=files)
        return resp.status_code < 300

    async def send_document(self, document_file: Path, caption: str = "") -> bool:
        if not self.available():
            return False
        if not document_file.exists():
            return False

        endpoint = f"{self.base_url}/sendDocument"
        async with httpx.AsyncClient(timeout=120) as client:
            with document_file.open("rb") as file_obj:
                files = {"document": (document_file.name, file_obj, "text/markdown")}
                data = {"chat_id": self.chat_id, "caption": caption[:1024]}
                resp = await client.post(endpoint, data=data, files=files)
        return resp.status_code < 300
