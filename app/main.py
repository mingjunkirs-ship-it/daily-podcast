from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from apscheduler.triggers.cron import CronTrigger
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.config import AUDIO_DIR, FEEDS_DIR, NOTES_DIR
from app.database import get_db, init_db
from app.models import Episode, Source
from app.schemas import (
    AddRssHubSourceRequest,
    AddRssSourceRequest,
    AuthMeResponse,
    ChangePasswordRequest,
    ConnectionTestResponse,
    CronTestRequest,
    CronTestResponse,
    EdgeVoicePreviewRequest,
    EpisodeRead,
    ImportPresetsRequest,
    ImportPresetsResponse,
    LoginRequest,
    PromptVersionCreateRequest,
    PromptVersionRead,
    RSSHubTemplateRead,
    RunNowResponse,
    SettingsRead,
    SourceConnectivityTestResponse,
    SettingsUpdate,
    SourceCreate,
    SourcePresetRead,
    SourceRead,
    SourceUpdate,
)
from app.services.pipeline import PipelineRunner
from app.services.scheduler import SchedulerService, _parse_cron
from app.services.auth import (
    SESSION_COOKIE_NAME,
    auth_cookie_secure,
    authenticate_admin,
    create_session_token,
    ensure_default_admin,
    get_admin_by_username,
    parse_session_token,
    session_ttl_seconds,
    update_admin_password,
)
from app.services.llm_client import LLMClient
from app.services.rsshub_templates import list_rsshub_templates
from app.services.settings import ensure_default_settings, get_settings, set_settings
from app.services.source_adapters import fetch_and_transform_source
from app.services.source_presets import import_presets, list_presets
from app.services.tts_client import TTSClient

try:
    import edge_tts
except Exception:
    edge_tts = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    db = next(get_db())
    try:
        ensure_default_settings(db)
        ensure_default_admin(db)
        settings = get_settings(db)
    finally:
        db.close()

    runner = PipelineRunner()
    scheduler = SchedulerService(runner)
    scheduler.start(settings)

    app.state.runner = runner
    app.state.scheduler = scheduler

    yield

    scheduler.shutdown()


app = FastAPI(title="AI Podcast Builder", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.mount("/media/audio", StaticFiles(directory=str(AUDIO_DIR)), name="audio")
app.mount("/media/notes", StaticFiles(directory=str(NOTES_DIR)), name="notes")


def _username_from_cookie(request: Request) -> str | None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    payload = parse_session_token(token)
    if not payload:
        return None
    username = str(payload.get("u", "")).strip()
    return username or None


def _is_public_path(path: str) -> bool:
    if path.startswith("/static"):
        return True
    public_exact = {"/login", "/api/auth/login", "/api/health"}
    return path in public_exact


def _default_source_name_from_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.strip().replace("www.", "")
    if host:
        return f"RSS {host}"[:120]
    return "RSS Source"


PROMPT_VERSION_KEY = "prompt_versions"
PROMPT_SETTING_KEYS = [
    "llm_summary_system_prompt",
    "llm_summary_prompt_template",
    "llm_episode_system_prompt",
    "llm_episode_prompt_template",
]

_EDGE_VOICE_ALIASES: dict[str, dict[str, str]] = {
    "zh-CN": {
        "Xiaoxiao": "晓晓", "Yunxi": "云希", "Yunjian": "云健", "Xiaoyi": "晓伊",
        "Yunyang": "云扬", "Xiaomo": "晓墨", "Xiaorui": "晓睿", "Xiaoshuang": "晓双",
        "Xiaohan": "晓涵", "Xiaochen": "晓辰",
    },
    "zh-TW": {"HsiaoChen": "曉臻", "YunJhe": "雲哲", "HsiaoYu": "曉雨"},
    "zh-HK": {"HiuGaai": "曉佳", "WanLung": "雲龍", "HiuMaan": "曉曼"},
    "ja-JP": {"Nanami": "七海", "Keita": "圭太", "Aoi": "葵", "Daichi": "大地"},
    "ko-KR": {"SunHi": "선희", "InJoon": "인준", "JiMin": "지민", "SeoHyeon": "서현"},
    "ru-RU": {"Svetlana": "Светлана", "Dmitry": "Дмитрий"},
    "es-ES": {"Elvira": "Elvira", "Alvaro": "Álvaro", "Ximena": "Ximena"},
    "fr-FR": {"Denise": "Denise", "Eloise": "Éloïse", "Henri": "Henri"},
    "de-DE": {"Katja": "Katja", "Conrad": "Conrad", "Florian": "Florian"},
    "pt-BR": {"Francisca": "Francisca", "Antonio": "Antônio", "Brenda": "Brenda"},
    "en-US": {"Aria": "Aria", "Jenny": "Jenny", "Guy": "Guy", "Sara": "Sara", "Davis": "Davis"},
    "en-GB": {"Sonia": "Sonia", "Ryan": "Ryan", "Maisie": "Maisie", "Thomas": "Thomas"},
}


def _edge_voice_locale(voice: str) -> str:
    parts = str(voice or "").strip().split("-")
    if len(parts) >= 2:
        return f"{parts[0]}-{parts[1]}"
    return ""


def _edge_voice_char_name(voice: str) -> str:
    raw = str(voice or "").strip()
    if not raw:
        return ""

    char_name = raw.rsplit("-", 1)[-1] if "-" in raw else raw
    char_name = re.sub(r"Neural$", "", char_name).strip()
    return char_name


def _edge_voice_alias(locale: str, char_name: str) -> str:
    local = _EDGE_VOICE_ALIASES.get(locale, {})
    if char_name in local:
        return local[char_name]

    lang = locale.split("-")[0] if locale else ""
    if lang:
        for key, mapping in _EDGE_VOICE_ALIASES.items():
            if key.startswith(f"{lang}-") and char_name in mapping:
                return mapping[char_name]
    return char_name


def _edge_voice_alias_from_short_name(short_name: str, locale: str | None = None) -> str:
    parsed_locale = (locale or _edge_voice_locale(short_name)).strip()
    char_name = _edge_voice_char_name(short_name)
    if not char_name:
        return str(short_name or "").strip()
    if not parsed_locale:
        return char_name
    return _edge_voice_alias(parsed_locale, char_name)


def _edge_preview_text_for_voice(short_name: str) -> str:
    alias = _edge_voice_alias_from_short_name(short_name)
    return f"我是{alias}" if alias else "我是语音助手"


def _prompt_snapshot(values: dict) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for key in PROMPT_SETTING_KEYS:
        snapshot[key] = str(values.get(key, "")).strip()
    return snapshot


def _normalize_prompt_versions(values: dict) -> list[dict]:
    rows = values.get(PROMPT_VERSION_KEY, [])
    if not isinstance(rows, list):
        return []

    output: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        version_id = str(row.get("id") or "").strip()
        name = str(row.get("name") or "").strip()
        created_at = str(row.get("created_at") or "").strip()
        prompts = row.get("prompts", {})
        if not (version_id and name and created_at and isinstance(prompts, dict)):
            continue
        normalized_prompts: dict[str, str] = {}
        for key in PROMPT_SETTING_KEYS:
            normalized_prompts[key] = str(prompts.get(key, "")).strip()
        output.append(
            {
                "id": version_id,
                "name": name,
                "created_at": created_at,
                "prompts": normalized_prompts,
            }
        )
    return output


def _edge_version_key(raw: str) -> tuple[int, ...]:
    cleaned = str(raw or "").strip().lstrip("vV")
    if not cleaned:
        return ()
    parts: list[int] = []
    for seg in cleaned.split("."):
        m = re.match(r"(\d+)", seg)
        if not m:
            break
        parts.append(int(m.group(1)))
    return tuple(parts)


def _is_edge_update_available(installed: str, latest: str) -> bool:
    installed_key = _edge_version_key(installed)
    latest_key = _edge_version_key(latest)
    if not installed_key or not latest_key:
        return False

    size = max(len(installed_key), len(latest_key))
    installed_key += (0,) * (size - len(installed_key))
    latest_key += (0,) * (size - len(latest_key))
    return latest_key > installed_key


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path

    if _is_public_path(path):
        return await call_next(request)

    username = _username_from_cookie(request)
    if username:
        request.state.current_username = username
        return await call_next(request)

    if path.startswith("/api/"):
        return JSONResponse(status_code=401, content={"detail": "unauthorized"})
    return RedirectResponse(url="/login", status_code=307)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    html_path = Path("app/static/index.html")
    return html_path.read_text(encoding="utf-8")


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> Response:
    if _username_from_cookie(request):
        return RedirectResponse(url="/", status_code=307)
    html_path = Path("app/static/login.html")
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/auth/login", response_model=AuthMeResponse)
def api_auth_login(payload: LoginRequest, db: Session = Depends(get_db)) -> Response:
    user = authenticate_admin(db, payload.username, payload.password)
    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    token = create_session_token(user.username)
    response = JSONResponse(content=AuthMeResponse(authenticated=True, username=user.username).model_dump())
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=auth_cookie_secure(),
        samesite="lax",
        max_age=session_ttl_seconds(),
        path="/",
    )
    return response


@app.post("/api/auth/logout")
def api_auth_logout() -> Response:
    response = JSONResponse(content={"message": "logged out"})
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return response


@app.get("/api/auth/me", response_model=AuthMeResponse)
def api_auth_me(request: Request) -> AuthMeResponse:
    username = _username_from_cookie(request)
    if not username:
        raise HTTPException(status_code=401, detail="unauthorized")
    return AuthMeResponse(authenticated=True, username=username)


@app.post("/api/auth/change-password")
def api_auth_change_password(payload: ChangePasswordRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    username = _username_from_cookie(request)
    if not username:
        raise HTTPException(status_code=401, detail="unauthorized")

    user = get_admin_by_username(db, username)
    if not user:
        raise HTTPException(status_code=404, detail="管理员不存在")
    if not authenticate_admin(db, username, payload.current_password):
        raise HTTPException(status_code=400, detail="当前密码错误")
    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=400, detail="新密码不能与当前密码相同")

    ok = update_admin_password(db, username, payload.new_password)
    if not ok:
        raise HTTPException(status_code=500, detail="修改密码失败")
    return {"message": "密码修改成功"}


@app.get("/api/settings", response_model=SettingsRead)
def api_get_settings(db: Session = Depends(get_db)) -> SettingsRead:
    return SettingsRead(values=get_settings(db))


@app.put("/api/settings", response_model=SettingsRead)
def api_set_settings(payload: SettingsUpdate, db: Session = Depends(get_db)) -> SettingsRead:
    values = set_settings(db, payload.values)
    app.state.scheduler.reschedule(values)
    return SettingsRead(values=values)


@app.get("/api/prompt-versions", response_model=list[PromptVersionRead])
def api_list_prompt_versions(db: Session = Depends(get_db)) -> list[PromptVersionRead]:
    values = get_settings(db)
    versions = _normalize_prompt_versions(values)
    return [PromptVersionRead.model_validate(row) for row in versions]


@app.post("/api/prompt-versions", response_model=PromptVersionRead)
def api_create_prompt_version(payload: PromptVersionCreateRequest, db: Session = Depends(get_db)) -> PromptVersionRead:
    values = get_settings(db)
    versions = _normalize_prompt_versions(values)

    row = {
        "id": uuid4().hex[:12],
        "name": payload.name.strip(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "prompts": _prompt_snapshot(values),
    }
    versions.insert(0, row)
    versions = versions[:80]
    set_settings(db, {PROMPT_VERSION_KEY: versions})
    return PromptVersionRead.model_validate(row)


@app.post("/api/prompt-versions/{version_id}/apply", response_model=SettingsRead)
def api_apply_prompt_version(version_id: str, db: Session = Depends(get_db)) -> SettingsRead:
    values = get_settings(db)
    versions = _normalize_prompt_versions(values)
    target = next((row for row in versions if row.get("id") == version_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="prompt version not found")

    updated = set_settings(db, target["prompts"])
    app.state.scheduler.reschedule(updated)
    return SettingsRead(values=updated)


@app.delete("/api/prompt-versions/{version_id}")
def api_delete_prompt_version(version_id: str, db: Session = Depends(get_db)) -> dict:
    values = get_settings(db)
    versions = _normalize_prompt_versions(values)
    remained = [row for row in versions if row.get("id") != version_id]
    if len(remained) == len(versions):
        raise HTTPException(status_code=404, detail="prompt version not found")

    set_settings(db, {PROMPT_VERSION_KEY: remained})
    return {"deleted": version_id}


@app.post("/api/test/llm", response_model=ConnectionTestResponse)
async def api_test_llm_connection(db: Session = Depends(get_db)) -> ConnectionTestResponse:
    settings = get_settings(db)
    client = LLMClient(settings)
    ok, message = await client.test_connection()
    return ConnectionTestResponse(ok=ok, message=message)


@app.post("/api/test/tts", response_model=ConnectionTestResponse)
async def api_test_tts_connection(db: Session = Depends(get_db)) -> ConnectionTestResponse:
    settings = get_settings(db)
    client = TTSClient(settings)
    ok, message = await client.test_connection()
    return ConnectionTestResponse(ok=ok, message=message)


@app.post("/api/test/edge-voice")
async def api_test_edge_voice(payload: EdgeVoicePreviewRequest, db: Session = Depends(get_db)) -> Response:
    voice = str(payload.voice or "").strip()
    if not voice:
        raise HTTPException(status_code=400, detail="voice 不能为空")

    sample_text = _edge_preview_text_for_voice(voice)

    settings = get_settings(db)
    preview_settings = dict(settings)
    preview_settings["tts_enabled"] = True
    preview_settings["tts_provider"] = "edge_tts"
    preview_settings["tts_voice"] = voice
    if payload.audio_speed is not None:
        preview_settings["tts_audio_speed"] = float(payload.audio_speed)

    tts = TTSClient(preview_settings)
    ok, audio_bytes, message = await tts._request_edge_tts(sample_text)
    if not ok or not audio_bytes:
        raise HTTPException(status_code=400, detail=f"音色试听失败：{message}")

    return Response(content=audio_bytes, media_type="audio/mpeg")


@app.post("/api/test/cron", response_model=CronTestResponse)
def api_test_cron(payload: CronTestRequest) -> CronTestResponse:
    cron = str(payload.schedule_cron or "").strip()
    if not cron:
        raise HTTPException(status_code=400, detail="Cron 表达式不能为空")

    timezone_name = str(payload.timezone or "Asia/Shanghai").strip() or "Asia/Shanghai"
    timezone_note = ""
    try:
        zone = ZoneInfo(timezone_name)
    except Exception:
        zone = timezone.utc
        timezone_note = f"（时区 {timezone_name} 不可用，已按 UTC 测试）"

    try:
        trigger_args = _parse_cron(cron)
        trigger = CronTrigger(timezone=zone, **trigger_args)
        now = datetime.now(zone)

        next_runs: list[str] = []
        previous = None
        for _ in range(3):
            nxt = trigger.get_next_fire_time(previous, now if previous is None else previous)
            if not nxt:
                break
            next_runs.append(nxt.isoformat())
            previous = nxt

        if not next_runs:
            return CronTestResponse(ok=True, message="Cron 可解析，但未计算到下一次触发时间", next_runs=[])

        return CronTestResponse(
            ok=True,
            message=f"Cron 配置有效（时区：{timezone_name}）{timezone_note}",
            next_runs=next_runs,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Cron 配置无效：{exc}")


@app.get("/api/tts/edge-version")
async def api_edge_tts_version() -> dict:
    installed = "unknown"
    try:
        installed = package_version("edge-tts")
    except PackageNotFoundError:
        installed = "not-installed"
    except Exception:
        installed = "unknown"

    latest = "unknown"
    message = ""
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            resp = await client.get("https://api.github.com/repos/rany2/edge-tts/releases/latest")
            if resp.status_code >= 300:
                raise RuntimeError(f"GitHub API HTTP {resp.status_code}")
            payload = resp.json()
            latest = str(payload.get("tag_name") or payload.get("name") or "").strip().lstrip("vV") or "unknown"
    except Exception as exc:
        message = f"检查更新失败：{exc}"

    update_available = _is_edge_update_available(installed, latest)
    if not message:
        if installed == "not-installed":
            message = "edge-tts 未安装"
        elif latest == "unknown":
            message = "已读取本地版本，暂未获取到 GitHub 最新版本"
        elif update_available:
            message = f"检测到新版本 {latest}，当前为 {installed}"
        else:
            message = f"当前已是最新版本（{installed}）"

    return {
        "ok": True,
        "installed_version": installed,
        "latest_version": latest,
        "update_available": update_available,
        "message": message,
        "repo": "https://github.com/rany2/edge-tts",
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/tts/edge-voices")
async def api_edge_tts_voices() -> dict:
    fallback = {
        "ok": False,
        "message": "edge-tts voices unavailable",
        "languages": [
            {
                "code": "zh-CN",
                "name": "中文（简体）",
                "voices": [
                    {"name": "zh-CN-XiaoxiaoNeural", "label": "晓晓 (女声，温暖自然)"},
                    {"name": "zh-CN-YunxiNeural", "label": "云希 (男声，沉稳)"},
                    {"name": "zh-CN-YunjianNeural", "label": "云健 (男声，阳刚)"},
                    {"name": "zh-CN-XiaoyiNeural", "label": "晓伊 (女声，活泼)"},
                ],
            },
            {
                "code": "en-US",
                "name": "English (US)",
                "voices": [
                    {"name": "en-US-AriaNeural", "label": "Aria (Female, Warm)"},
                    {"name": "en-US-JennyNeural", "label": "Jenny (Female, Friendly)"},
                    {"name": "en-US-GuyNeural", "label": "Guy (Male, Casual)"},
                ],
            },
            {
                "code": "ja-JP",
                "name": "日本語",
                "voices": [
                    {"name": "ja-JP-NanamiNeural", "label": "七海 (女性、明るい)"},
                    {"name": "ja-JP-KeitaNeural", "label": "圭太 (男性、落ち着き)"},
                ],
            },
        ],
    }

    if edge_tts is None:
        return fallback

    try:
        rows = await edge_tts.list_voices()

        # Locale → native language name mapping
        _locale_names: dict[str, str] = {
            "zh-CN": "中文（简体）", "zh-TW": "中文（繁體）", "zh-HK": "中文（香港）",
            "en-US": "English (US)", "en-GB": "English (UK)", "en-AU": "English (AU)",
            "en-IN": "English (IN)", "en-CA": "English (CA)",
            "ja-JP": "日本語", "ko-KR": "한국어",
            "fr-FR": "Français (FR)", "fr-CA": "Français (CA)",
            "de-DE": "Deutsch", "es-ES": "Español (ES)", "es-MX": "Español (MX)",
            "pt-BR": "Português (BR)", "pt-PT": "Português (PT)",
            "it-IT": "Italiano", "ru-RU": "Русский",
            "ar-SA": "العربية", "hi-IN": "हिन्दी", "th-TH": "ไทย",
            "vi-VN": "Tiếng Việt", "id-ID": "Bahasa Indonesia",
            "nl-NL": "Nederlands", "pl-PL": "Polski", "sv-SE": "Svenska",
            "tr-TR": "Türkçe", "uk-UA": "Українська", "cs-CZ": "Čeština",
            "da-DK": "Dansk", "fi-FI": "Suomi", "el-GR": "Ελληνικά",
            "he-IL": "עברית", "hu-HU": "Magyar", "nb-NO": "Norsk",
            "ro-RO": "Română", "sk-SK": "Slovenčina",
            "ms-MY": "Bahasa Melayu", "fil-PH": "Filipino",
        }

        # Gender labels per locale prefix
        def _gender_label(locale: str, gender: str) -> str:
            g = gender.lower()
            lang = locale.split("-")[0]
            if lang == "zh":
                return "女声" if g == "female" else "男声"
            if lang == "ja":
                return "女性" if g == "female" else "男性"
            if lang == "ko":
                return "여성" if g == "female" else "남성"
            if lang == "ru":
                return "Женский" if g == "female" else "Мужской"
            if lang == "es":
                return "Femenina" if g == "female" else "Masculina"
            if lang == "fr":
                return "Féminine" if g == "female" else "Masculine"
            if lang == "de":
                return "Weiblich" if g == "female" else "Männlich"
            if lang == "pt":
                return "Feminina" if g == "female" else "Masculina"
            return "Female" if g == "female" else "Male"

        def _build_label(short_name: str, locale: str, gender: str) -> str:
            char_name = _edge_voice_char_name(short_name)
            alias = _edge_voice_alias_from_short_name(short_name, locale)
            g = _gender_label(locale, gender) if gender else ""

            if alias != char_name:
                base = f"{alias} ({char_name})"
            else:
                base = char_name

            return f"{base} · {g}" if g else base

        grouped: dict[str, dict] = {}
        for item in rows:
            if not isinstance(item, dict):
                continue
            short_name = str(item.get("ShortName") or "").strip()
            locale = str(item.get("Locale") or "").strip()
            if not (short_name and locale):
                continue

            gender = str(item.get("Gender") or "").strip()
            label = _build_label(short_name, locale, gender)

            locale_name = _locale_names.get(locale, locale)
            group = grouped.setdefault(
                locale,
                {
                    "code": locale,
                    "name": locale_name,
                    "voices": [],
                },
            )
            group["voices"].append({"name": short_name, "label": label})

        languages = []
        for key in sorted(grouped.keys()):
            voices = grouped[key]["voices"]
            voices.sort(key=lambda row: row["name"])
            languages.append(
                {
                    "code": grouped[key]["code"],
                    "name": grouped[key]["name"],
                    "voices": voices,
                }
            )

        if not languages:
            return fallback
        return {
            "ok": True,
            "message": f"loaded {sum(len(item['voices']) for item in languages)} voices",
            "languages": languages,
        }
    except Exception as exc:
        fallback["message"] = f"edge-tts voices unavailable: {exc}"
        return fallback


@app.get("/api/rsshub/templates", response_model=list[RSSHubTemplateRead])
def api_list_rsshub_templates() -> list[RSSHubTemplateRead]:
    return [RSSHubTemplateRead.model_validate(item) for item in list_rsshub_templates()]


@app.get("/api/sources", response_model=list[SourceRead])
def api_list_sources(db: Session = Depends(get_db)) -> list[SourceRead]:
    rows = db.scalars(select(Source).order_by(desc(Source.created_at))).all()
    result: list[SourceRead] = []
    for row in rows:
        result.append(
            SourceRead(
                id=row.id,
                name=row.name,
                source_type=row.source_type,
                enabled=row.enabled,
                config=json.loads(row.config_json or "{}"),
                last_sync_at=row.last_sync_at,
                last_error=row.last_error,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
        )
    return result


@app.post("/api/sources/{source_id}/test", response_model=SourceConnectivityTestResponse)
async def api_test_source_connectivity(source_id: int, db: Session = Depends(get_db)) -> SourceConnectivityTestResponse:
    source = db.get(Source, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="source not found")

    settings = get_settings(db)
    try:
        items, _ = await fetch_and_transform_source(source, settings)
        source.last_sync_at = datetime.now(timezone.utc)
        source.last_error = ""
        db.commit()
        return SourceConnectivityTestResponse(
            source_id=source.id,
            source_name=source.name,
            ok=True,
            item_count=len(items),
            message=f"来源连接成功，抓取到 {len(items)} 条",
        )
    except Exception as exc:
        source.last_error = str(exc)
        db.commit()
        return SourceConnectivityTestResponse(
            source_id=source.id,
            source_name=source.name,
            ok=False,
            item_count=0,
            message=f"来源连接失败：{exc}",
        )


@app.post("/api/sources", response_model=SourceRead)
def api_create_source(payload: SourceCreate, db: Session = Depends(get_db)) -> SourceRead:
    row = Source(
        name=payload.name,
        source_type=payload.source_type,
        enabled=payload.enabled,
        config_json=json.dumps(payload.config, ensure_ascii=False),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return SourceRead(
        id=row.id,
        name=row.name,
        source_type=row.source_type,
        enabled=row.enabled,
        config=payload.config,
        last_sync_at=row.last_sync_at,
        last_error=row.last_error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@app.post("/api/sources/rss", response_model=SourceRead)
def api_create_rss_source(payload: AddRssSourceRequest, db: Session = Depends(get_db)) -> SourceRead:
    url = payload.url.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=400, detail="RSS URL 必须以 http:// 或 https:// 开头")

    name = (payload.name or "").strip() or _default_source_name_from_url(url)
    config = {"url": url}
    row = Source(
        name=name,
        source_type="rss",
        enabled=payload.enabled,
        config_json=json.dumps(config, ensure_ascii=False),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return SourceRead(
        id=row.id,
        name=row.name,
        source_type=row.source_type,
        enabled=row.enabled,
        config=config,
        last_sync_at=row.last_sync_at,
        last_error=row.last_error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@app.post("/api/sources/rsshub", response_model=SourceRead)
async def api_create_rsshub_source(payload: AddRssHubSourceRequest, db: Session = Depends(get_db)) -> SourceRead:
    route = payload.route.strip()
    if not route.startswith("/"):
        route = f"/{route}"

    settings = get_settings(db)
    base_url = str(settings.get("rsshub_base_url", "http://rsshub:1200")).strip().rstrip("/")
    if not (base_url.startswith("http://") or base_url.startswith("https://")):
        raise HTTPException(status_code=400, detail="rsshub_base_url 配置无效")

    rss_url = f"{base_url}{route}"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(rss_url)
        if resp.status_code >= 400:
            raise HTTPException(status_code=400, detail=f"RSSHub 路由不可用，HTTP {resp.status_code}")
        text = resp.text.lower()
        if "<rss" not in text and "<feed" not in text:
            raise HTTPException(status_code=400, detail="RSSHub 返回内容不是有效 RSS/Atom")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"访问 RSSHub 失败：{exc}") from exc

    default_name = f"RSSHub {route}"[:120]
    name = (payload.name or "").strip() or default_name
    config = {"url": rss_url, "rsshub_route": route}
    row = Source(
        name=name,
        source_type="rss",
        enabled=payload.enabled,
        config_json=json.dumps(config, ensure_ascii=False),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return SourceRead(
        id=row.id,
        name=row.name,
        source_type=row.source_type,
        enabled=row.enabled,
        config=config,
        last_sync_at=row.last_sync_at,
        last_error=row.last_error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@app.get("/api/source-presets", response_model=list[SourcePresetRead])
def api_source_presets() -> list[SourcePresetRead]:
    return [SourcePresetRead.model_validate(row) for row in list_presets()]


@app.post("/api/sources/import-defaults", response_model=ImportPresetsResponse)
def api_import_default_sources(
    payload: ImportPresetsRequest,
    db: Session = Depends(get_db),
) -> ImportPresetsResponse:
    result = import_presets(
        db,
        preset_ids=payload.preset_ids,
        overwrite_existing=payload.overwrite_existing,
    )
    return ImportPresetsResponse.model_validate(result)


@app.put("/api/sources/{source_id}", response_model=SourceRead)
def api_update_source(source_id: int, payload: SourceUpdate, db: Session = Depends(get_db)) -> SourceRead:
    row = db.get(Source, source_id)
    if not row:
        raise HTTPException(status_code=404, detail="source not found")

    if payload.name is not None:
        row.name = payload.name
    if payload.enabled is not None:
        row.enabled = payload.enabled
    if payload.config is not None:
        row.config_json = json.dumps(payload.config, ensure_ascii=False)

    db.commit()
    db.refresh(row)
    return SourceRead(
        id=row.id,
        name=row.name,
        source_type=row.source_type,
        enabled=row.enabled,
        config=json.loads(row.config_json or "{}"),
        last_sync_at=row.last_sync_at,
        last_error=row.last_error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@app.delete("/api/sources/{source_id}")
def api_delete_source(source_id: int, db: Session = Depends(get_db)) -> dict:
    row = db.get(Source, source_id)
    if not row:
        raise HTTPException(status_code=404, detail="source not found")
    db.delete(row)
    db.commit()
    return {"deleted": source_id}


@app.post("/api/run-now", response_model=RunNowResponse)
async def api_run_now() -> RunNowResponse:
    episode_id = await app.state.runner.queue_once(trigger="manual")
    return RunNowResponse(message=f"任务已启动：episode #{episode_id}", episode_id=episode_id)


@app.post("/api/rebuild-feeds")
async def api_rebuild_feeds() -> dict:
    result = await app.state.runner.rebuild_source_feeds()
    return {"ok": True, **result}


def _remove_episode_files(episode: Episode) -> None:
    if episode.audio_file:
        audio_path = AUDIO_DIR / episode.audio_file
        if audio_path.is_file():
            audio_path.unlink(missing_ok=True)
    if episode.notes_file:
        notes_path = NOTES_DIR / episode.notes_file
        if notes_path.is_file():
            notes_path.unlink(missing_ok=True)


@app.get("/api/episodes", response_model=list[EpisodeRead])
def api_list_episodes(db: Session = Depends(get_db)) -> list[EpisodeRead]:
    rows = db.scalars(select(Episode).order_by(desc(Episode.created_at)).limit(30)).all()
    return [EpisodeRead.model_validate(row) for row in rows]


@app.get("/api/episodes/{episode_id}", response_model=EpisodeRead)
def api_get_episode(episode_id: int, db: Session = Depends(get_db)) -> EpisodeRead:
    row = db.get(Episode, episode_id)
    if not row:
        raise HTTPException(status_code=404, detail="episode not found")
    return EpisodeRead.model_validate(row)


@app.delete("/api/episodes/{episode_id}")
def api_delete_episode(episode_id: int, db: Session = Depends(get_db)) -> dict:
    row = db.get(Episode, episode_id)
    if not row:
        raise HTTPException(status_code=404, detail="episode not found")

    status = str(row.status or "").lower()
    if status in {"pending", "running"}:
        raise HTTPException(status_code=400, detail="episode 正在执行，暂不允许删除")

    _remove_episode_files(row)
    db.delete(row)
    db.commit()
    return {"ok": True, "deleted": episode_id}


@app.delete("/api/episodes")
def api_clear_episodes(db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(select(Episode)).all()
    running = [row.id for row in rows if str(row.status or "").lower() in {"pending", "running"}]
    if running:
        raise HTTPException(status_code=400, detail=f"存在执行中任务，暂不能清空：{running[:5]}")

    deleted = 0
    for row in rows:
        _remove_episode_files(row)
        db.delete(row)
        deleted += 1
    db.commit()
    return {"ok": True, "deleted": deleted}


@app.get("/rss/sources/{source_id}.xml")
def api_source_feed(source_id: int) -> Response:
    path = FEEDS_DIR / f"source-{source_id}.xml"
    if not path.exists():
        raise HTTPException(status_code=404, detail="feed not found")
    return Response(path.read_text(encoding="utf-8"), media_type="application/rss+xml")


@app.get("/rss/aggregated.xml")
def api_aggregated_feed() -> Response:
    path = FEEDS_DIR / "aggregated.xml"
    if not path.exists():
        raise HTTPException(status_code=404, detail="feed not found")
    return Response(path.read_text(encoding="utf-8"), media_type="application/rss+xml")


@app.get("/media/audio/file/{filename}")
def api_audio_file(filename: str) -> FileResponse:
    path = AUDIO_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="audio not found")
    return FileResponse(path)


@app.get("/media/notes/file/{filename}")
def api_notes_file(filename: str) -> FileResponse:
    path = NOTES_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="notes not found")
    return FileResponse(path)
