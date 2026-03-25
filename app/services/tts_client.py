from __future__ import annotations

import base64
import json
import math
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

try:
    import edge_tts
except Exception:  # pragma: no cover - 运行时依赖问题通过测试接口提示
    edge_tts = None


class TTSClient:
    def __init__(self, settings: dict[str, Any]) -> None:
        self.provider = str(settings.get("tts_provider", "edge_tts")).strip().lower() or "edge_tts"
        if self.provider not in {"edge_tts", "custom_api"}:
            self.provider = "edge_tts"

        self.enabled = bool(settings.get("tts_enabled", True))
        self.base_url = self._normalize_base_url(str(settings.get("tts_api_base", "https://api.openai.com/v1")).strip())
        self.api_key = str(settings.get("tts_api_key", "")).strip()
        self.model = str(settings.get("tts_model", "gpt-4o-mini-tts")).strip()
        self.voice = str(settings.get("tts_voice", "zh-CN-XiaoxiaoNeural")).strip()
        self.audio_format = str(settings.get("tts_format", "mp3")).strip().lower()
        self.api_mode = "auto"
        self.edge_proxy = str(settings.get("tts_edge_proxy", "")).strip() or None

        try:
            self.edge_connect_timeout = int(settings.get("tts_edge_connect_timeout", 10))
        except Exception:
            self.edge_connect_timeout = 10
        try:
            self.edge_receive_timeout = int(settings.get("tts_edge_receive_timeout", 60))
        except Exception:
            self.edge_receive_timeout = 60
        self.edge_connect_timeout = max(3, min(self.edge_connect_timeout, 120))
        self.edge_receive_timeout = max(10, min(self.edge_receive_timeout, 600))
        try:
            self.audio_speed = float(settings.get("tts_audio_speed", 1.0))
        except Exception:
            self.audio_speed = 1.0

        if (not math.isfinite(self.audio_speed)) or self.audio_speed <= 0:
            self.audio_speed = 1.0
        self.audio_speed = max(0.25, min(self.audio_speed, 4.0))

        self.provider_host = urlparse(self.base_url).netloc.lower()
        self.is_xiaomimimo = "xiaomimimo.com" in self.provider_host

        if self.provider == "custom_api" and self.is_xiaomimimo:
            if not self.model:
                self.model = "mimo-v2-tts"
            elif self.model.lower().startswith("mimo-"):
                self.model = self.model.lower()

        if self.provider == "custom_api" and self.is_xiaomimimo and (not self.voice or self.voice == "alloy"):
            self.voice = "default_zh"
        if self.provider == "custom_api" and self.is_xiaomimimo and self.voice in {"mimo_default", "default", "default-cn"}:
            self.voice = "default_zh"

        if self.provider == "edge_tts" and not self.voice:
            self.voice = "zh-CN-XiaoxiaoNeural"

    @staticmethod
    def _normalize_base_url(raw_base: str) -> str:
        base = (raw_base or "https://api.openai.com/v1").strip().rstrip("/")
        parsed = urlparse(base)
        if not (parsed.scheme and parsed.netloc):
            return base

        path = (parsed.path or "").rstrip("/")
        for suffix in ("/chat/completions", "/audio/speech"):
            if path.endswith(suffix):
                path = path[: -len(suffix)]
                break

        host = parsed.netloc.lower()
        if "xiaomimimo.com" in host and path in {"", "/"}:
            path = "/v1"

        return f"{parsed.scheme}://{parsed.netloc}{path}"

    def available(self) -> bool:
        if not self.enabled:
            return False
        if self.provider == "edge_tts":
            return bool(self.voice)
        return bool(self.api_key and self.model)

    def _edge_rate(self) -> str:
        percent = int(round((self.audio_speed - 1.0) * 100))
        percent = max(-75, min(percent, 100))
        return f"{percent:+d}%"

    async def _request_edge_tts(self, text: str) -> tuple[bool, bytes, str]:
        if edge_tts is None:
            return False, b"", "edge-tts 依赖未安装"
        if not text.strip():
            return False, b"", "edge-tts 文本为空"

        try:
            communicate = edge_tts.Communicate(
                text=text,
                voice=self.voice,
                rate=self._edge_rate(),
                proxy=self.edge_proxy,
                connect_timeout=self.edge_connect_timeout,
                receive_timeout=self.edge_receive_timeout,
            )
            audio_parts: list[bytes] = []
            async for chunk in communicate.stream():
                if chunk.get("type") == "audio":
                    data = chunk.get("data")
                    if isinstance(data, bytes) and data:
                        audio_parts.append(data)

            if not audio_parts:
                return False, b"", "edge-tts 未返回音频数据"

            return True, b"".join(audio_parts), "edge-tts 模式成功"
        except Exception as exc:
            error_text = str(exc)
            if "403" in error_text:
                error_text = (
                    f"{error_text}；当前网络/IP 可能被 Microsoft TTS 网关拒绝，"
                    "可尝试配置 edge proxy 或切换 custom_api"
                )
            return False, b"", f"edge-tts 模式失败：{error_text}"

    @staticmethod
    def _extract_error_text(resp: httpx.Response) -> str:
        try:
            payload = resp.json()
            if isinstance(payload, dict):
                error = payload.get("error")
                if isinstance(error, dict):
                    message = str(error.get("message") or "").strip()
                    if message:
                        return message
            return str(payload)
        except Exception:
            return (resp.text or "").strip()

    @staticmethod
    def _need_chat_compat(error_text: str) -> bool:
        low = error_text.lower()
        return (
            "assistant role" in low
            or ("messages" in low and "tts" in low)
            or "messages must contain" in low
            or "invalid request" in low
        )

    @staticmethod
    def _decode_b64(raw: str) -> bytes:
        if not raw:
            return b""
        padded = raw + "=" * ((4 - len(raw) % 4) % 4)
        try:
            return base64.b64decode(padded)
        except Exception:
            return b""

    def _extract_audio_from_chat_payload(self, payload: dict[str, Any]) -> tuple[bool, bytes, str]:
        candidates = [
            payload.get("choices", [{}])[0].get("message", {}).get("audio", {}).get("data", ""),
            payload.get("choices", [{}])[0].get("audio", {}).get("data", ""),
            payload.get("audio", {}).get("data", ""),
        ]

        for raw in candidates:
            if not raw:
                continue
            audio_bytes = self._decode_b64(str(raw))
            if audio_bytes:
                return True, audio_bytes, "chat_compat 模式成功"
        return False, b"", "chat_compat 模式失败：未找到可解析的音频字段"

    async def _request_speech(self, text: str) -> tuple[bool, bytes, str]:
        endpoint = f"{self.base_url}/audio/speech"
        payload = {
            "model": self.model,
            "voice": self.voice,
            "input": text,
            "response_format": self.audio_format,
            "format": self.audio_format,
            "speed": self.audio_speed,
        }

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                endpoint,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=payload,
            )

        if resp.status_code >= 300:
            return False, b"", f"speech 模式失败：HTTP {resp.status_code} - {self._extract_error_text(resp)}"
        if not resp.content:
            return False, b"", "speech 模式失败：返回音频为空"
        return True, resp.content, "speech 模式成功"

    def _chat_payload_variants(self, text: str) -> list[dict[str, Any]]:
        base_audio = {
            "voice": self.voice,
            "format": self.audio_format,
            "speed": self.audio_speed,
        }

        return [
            {
                "model": self.model,
                "messages": [{"role": "assistant", "content": text}],
                "audio": base_audio,
            },
            {
                "model": self.model,
                "messages": [{"role": "user", "content": text}],
                "audio": base_audio,
            },
            {
                "model": self.model,
                "messages": [{"role": "assistant", "content": text}],
                "modalities": ["text", "audio"],
                "audio": base_audio,
                "stream": False,
            },
        ]

    async def _request_chat_compat(self, text: str) -> tuple[bool, bytes, str]:
        endpoint = f"{self.base_url}/chat/completions"
        last_error = ""

        for index, payload in enumerate(self._chat_payload_variants(text), start=1):
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    endpoint,
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json=payload,
                )

            if resp.status_code >= 300:
                error_text = self._extract_error_text(resp)
                last_error = f"尝试{index}失败：HTTP {resp.status_code} - {error_text}"
                continue

            try:
                data = resp.json()
            except json.JSONDecodeError:
                last_error = f"尝试{index}失败：响应不是 JSON"
                continue

            ok, audio_bytes, message = self._extract_audio_from_chat_payload(data)
            if ok:
                return True, audio_bytes, f"{message}（尝试{index}）"
            last_error = f"尝试{index}失败：{message}"

        return False, b"", f"chat_compat 模式失败：{last_error or '未知错误'}"

    async def _synthesize_bytes(self, text: str) -> tuple[bool, bytes, str]:
        if self.provider == "edge_tts":
            return await self._request_edge_tts(text)

        if self.api_mode == "speech":
            return await self._request_speech(text)
        if self.api_mode == "chat_compat":
            return await self._request_chat_compat(text)

        if self.is_xiaomimimo:
            chat_ok, chat_audio, chat_message = await self._request_chat_compat(text)
            if chat_ok:
                return True, chat_audio, f"provider({self.provider_host}) 优先 chat_compat：{chat_message}"

            speech_ok, speech_audio, speech_message = await self._request_speech(text)
            if speech_ok:
                return True, speech_audio, f"provider({self.provider_host}) chat_compat 失败后 speech 成功"
            return False, b"", f"provider({self.provider_host}) chat_compat 与 speech 均失败：{chat_message} | {speech_message}"

        ok, audio, message = await self._request_speech(text)
        if ok:
            return ok, audio, message

        if self._need_chat_compat(message):
            fallback_ok, fallback_audio, fallback_message = await self._request_chat_compat(text)
            if fallback_ok:
                return True, fallback_audio, f"{message}；自动切换 {fallback_message}"
            return False, b"", f"{message}；自动切换失败：{fallback_message}"

        fallback_ok, fallback_audio, fallback_message = await self._request_chat_compat(text)
        if fallback_ok:
            return True, fallback_audio, f"speech 失败，自动切换 {fallback_message}"
        return False, b"", f"speech 与 chat_compat 均失败：{message} | {fallback_message}"

    async def synthesize(self, text: str, output_path: Path) -> bool:
        if not self.available():
            return False

        output_path.parent.mkdir(parents=True, exist_ok=True)
        ok, audio_bytes, _ = await self._synthesize_bytes(text)
        if not ok or not audio_bytes:
            return False

        output_path.write_bytes(audio_bytes)
        return output_path.exists() and output_path.stat().st_size > 0

    async def test_connection(self) -> tuple[bool, str]:
        if not self.available():
            return False, "TTS API 未配置完整或未启用"

        try:
            ok, audio_bytes, message = await self._synthesize_bytes("你好，这是一段测试语音，请用温柔的女声说出来。")
            if not ok:
                if self.provider == "edge_tts":
                    return False, f"TTS 连接失败：{message}（provider=edge_tts）"
                return False, f"TTS 连接失败：{message}（provider=custom_api, base={self.base_url}）"
            if not audio_bytes:
                return False, "TTS 连接失败：返回为空"
            if self.provider == "edge_tts":
                return True, (
                    f"TTS 连接成功，provider=edge_tts，voice：{self.voice}，speed：{self.audio_speed}，"
                    f"{message}"
                )
            return True, (
                f"TTS 连接成功，provider=custom_api，base：{self.base_url}，模型：{self.model}，voice：{self.voice}，"
                f"speed：{self.audio_speed}，mode：{self.api_mode}，{message}"
            )
        except Exception as exc:
            return False, f"TTS 连接失败：{exc}"
