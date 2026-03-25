from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx
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
from app.services.scheduler import SchedulerService
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
                    {"name": "zh-CN-XiaoxiaoNeural", "label": "zh-CN-XiaoxiaoNeural"},
                    {"name": "zh-CN-YunxiNeural", "label": "zh-CN-YunxiNeural"},
                    {"name": "zh-CN-YunjianNeural", "label": "zh-CN-YunjianNeural"},
                    {"name": "zh-CN-XiaoyiNeural", "label": "zh-CN-XiaoyiNeural"},
                ],
            },
            {
                "code": "en-US",
                "name": "English (US)",
                "voices": [
                    {"name": "en-US-AriaNeural", "label": "en-US-AriaNeural"},
                    {"name": "en-US-JennyNeural", "label": "en-US-JennyNeural"},
                    {"name": "en-US-GuyNeural", "label": "en-US-GuyNeural"},
                ],
            },
            {
                "code": "ja-JP",
                "name": "日本語",
                "voices": [
                    {"name": "ja-JP-NanamiNeural", "label": "ja-JP-NanamiNeural"},
                    {"name": "ja-JP-KeitaNeural", "label": "ja-JP-KeitaNeural"},
                ],
            },
        ],
    }

    if edge_tts is None:
        return fallback

    try:
        rows = await edge_tts.list_voices()
        grouped: dict[str, dict] = {}
        for item in rows:
            if not isinstance(item, dict):
                continue
            short_name = str(item.get("ShortName") or "").strip()
            locale = str(item.get("Locale") or "").strip()
            if not (short_name and locale):
                continue

            gender = str(item.get("Gender") or "").strip()
            friendly = str(item.get("FriendlyName") or "").strip()
            label_parts = [short_name]
            if gender:
                label_parts.append(gender)
            if friendly:
                label_parts.append(friendly)

            group = grouped.setdefault(
                locale,
                {
                    "code": locale,
                    "name": locale,
                    "voices": [],
                },
            )
            group["voices"].append({"name": short_name, "label": " | ".join(label_parts)})

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
