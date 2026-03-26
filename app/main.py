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
from app.models import AdminUser, Episode, PendingRegistration, Source
from app.schemas import (
    AddRssHubSourceRequest,
    AddRssSourceRequest,
    AuthMeResponse,
    BatchRssSourceItem,
    ChangePasswordRequest,
    ConnectionTestResponse,
    CronTestRequest,
    CronTestResponse,
    CronNaturalRequest,
    CronNaturalResponse,
    EdgeVoicePreviewRequest,
    EpisodeRead,
    ImportPresetsRequest,
    ImportPresetsResponse,
    ImportRssBatchRequest,
    ImportRssBatchResponse,
    LoginRequest,
    PendingRegistrationRead,
    PromptVersionCreateRequest,
    PromptVersionRead,
    RegisterOptionsResponse,
    RegisterRequest,
    RSSHubTemplateRead,
    RunNowResponse,
    SettingsRead,
    SourceConnectivityTestResponse,
    SettingsUpdate,
    SourceCreate,
    SourcePresetRead,
    SourceRead,
    SourceUpdate,
    UserRead,
    UserSetDisabledRequest,
    UserResetPasswordRequest,
)
from app.services.pipeline import PipelineRunner
from app.services.scheduler import SchedulerService, _parse_cron
from app.services.auth import (
    SESSION_COOKIE_NAME,
    auth_allow_register,
    auth_cookie_secure,
    authenticate_user,
    create_session_token,
    ensure_default_admin,
    get_user_by_username,
    hash_password,
    is_admin_username,
    parse_session_token,
    auth_register_require_admin_approval,
    session_ttl_seconds,
    update_user_password,
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
    public_exact = {
        "/login",
        "/api/auth/login",
        "/api/auth/register",
        "/api/auth/register-options",
        "/api/health",
    }
    return path in public_exact


def _current_username(request: Request) -> str | None:
    username = getattr(request.state, "current_username", None)
    if username:
        return str(username)
    return _username_from_cookie(request)


def _ensure_admin(request: Request) -> str:
    username = _current_username(request)
    if not username:
        raise HTTPException(status_code=401, detail="unauthorized")
    if not is_admin_username(username):
        raise HTTPException(status_code=403, detail="仅管理员可执行该操作")
    return username


def _default_source_name_from_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.strip().replace("www.", "")
    if host:
        return f"RSS {host}"[:120]
    return "RSS Source"


def _validate_rss_url(url: str) -> str:
    value = str(url or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="RSS URL 不能为空")
    if not (value.startswith("http://") or value.startswith("https://")):
        raise HTTPException(status_code=400, detail="RSS URL 必须以 http:// 或 https:// 开头")
    return value


def _normalize_rss_keywords(value: str | list[str] | None) -> str:
    if isinstance(value, list):
        keywords = [str(item).strip() for item in value if str(item).strip()]
        return ", ".join(keywords)
    if value is None:
        return ""
    return str(value).strip()


def _build_rss_source_config(item: BatchRssSourceItem) -> dict:
    config: dict[str, object] = {"url": item.url.strip()}
    keywords = _normalize_rss_keywords(item.keywords)
    if keywords:
        config["keywords"] = keywords
    if item.max_items is not None:
        config["max_items"] = int(item.max_items)
    return config


def _normalize_blocked_usernames(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    output: list[str] = []
    seen: set[str] = set()
    for item in value:
        username = str(item or "").strip()
        if not username or username in seen or is_admin_username(username):
            continue
        seen.add(username)
        output.append(username)
    return output


def _blocked_usernames_from_settings(values: dict) -> list[str]:
    return _normalize_blocked_usernames(values.get("auth_blocked_usernames", []))


def _is_user_blocked(db: Session, username: str) -> bool:
    if not username or is_admin_username(username):
        return False
    values = get_settings(db)
    blocked = _blocked_usernames_from_settings(values)
    return username in set(blocked)


def _set_user_blocked(db: Session, username: str, disabled: bool) -> list[str]:
    values = get_settings(db)
    blocked = _blocked_usernames_from_settings(values)
    blocked_set = set(blocked)

    if disabled:
        blocked_set.add(username)
    else:
        blocked_set.discard(username)

    updated = sorted(item for item in blocked_set if item and not is_admin_username(item))
    set_settings(db, {"auth_blocked_usernames": updated})
    return updated


def _parse_time_from_text(text: str) -> tuple[int, int]:
    source = text.strip().lower()

    m_hm = re.search(r"(\d{1,2})\s*[:：点时]\s*(\d{1,2})", source)
    if m_hm:
        hour = int(m_hm.group(1))
        minute = int(m_hm.group(2))
    else:
        m_h = re.search(r"(\d{1,2})\s*(点|时|hour|hours|h)\b?", source)
        if m_h:
            hour = int(m_h.group(1))
            minute = 0
        else:
            m_colon = re.search(r"\b(\d{1,2}):(\d{1,2})\b", source)
            if m_colon:
                hour = int(m_colon.group(1))
                minute = int(m_colon.group(2))
            else:
                hour = 8
                minute = 0

    if any(x in source for x in ["下午", "晚上", "pm"]) and 1 <= hour <= 11:
        hour += 12
    if any(x in source for x in ["凌晨"]) and hour == 12:
        hour = 0

    hour = max(0, min(hour, 23))
    minute = max(0, min(minute, 59))
    return hour, minute


def _weekday_from_text(text: str) -> str | None:
    mapping = {
        "一": "1", "二": "2", "三": "3", "四": "4", "五": "5", "六": "6", "日": "0", "天": "0",
        "monday": "1", "tuesday": "2", "wednesday": "3", "thursday": "4", "friday": "5", "saturday": "6", "sunday": "0",
        "mon": "1", "tue": "2", "wed": "3", "thu": "4", "fri": "5", "sat": "6", "sun": "0",
    }

    source = text.strip().lower()
    for key, value in mapping.items():
        if f"周{key}" in source or f"星期{key}" in source or key in source:
            return value
    return None


def _cron_from_natural_text(text: str) -> tuple[str, str]:
    source = str(text or "").strip()
    if not source:
        raise ValueError("请输入自然语言时间")

    s = source.lower()
    hour, minute = _parse_time_from_text(s)

    if any(x in s for x in ["每小时", "每个小时", "hourly", "every hour"]):
        return "0 * * * *", "已识别为每小时执行"

    if any(x in s for x in ["每隔", "间隔"]) and "分钟" in s:
        m = re.search(r"每隔\s*(\d{1,2})\s*分钟", s)
        if m:
            step = int(m.group(1))
            if 1 <= step <= 59:
                return f"*/{step} * * * *", f"已识别为每隔 {step} 分钟"

    weekday = _weekday_from_text(s)
    if any(x in s for x in ["每周", "weekly", "every week"]) and weekday is not None:
        return f"{minute} {hour} * * {weekday}", "已识别为每周计划"

    m_day = re.search(r"每月\s*(\d{1,2})\s*(号|日)", s)
    if not m_day:
        m_day = re.search(r"every month\s*(on\s*)?(\d{1,2})", s)
        if m_day:
            day = int(m_day.group(2))
        else:
            day = None
    else:
        day = int(m_day.group(1))
    if day is not None:
        day = max(1, min(day, 31))
        return f"{minute} {hour} {day} * *", "已识别为每月计划"

    if any(x in s for x in ["工作日", "weekday", "weekdays"]):
        return f"{minute} {hour} * * 1-5", "已识别为工作日计划"

    if any(x in s for x in ["每天", "每日", "daily", "every day", "每晚", "每天早上", "每天晚上"]):
        return f"{minute} {hour} * * *", "已识别为每日计划"

    raise ValueError("暂未识别该自然语言，请示例：每天早上8点 / 每周一9:30 / 每月1号8点")


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
        db = next(get_db())
        try:
            if _is_user_blocked(db, username):
                if path.startswith("/api/"):
                    return JSONResponse(status_code=403, content={"detail": "当前账号已被管理员禁用"})
                response = RedirectResponse(url="/login", status_code=307)
                response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
                return response
        finally:
            db.close()

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


@app.get("/api/auth/register-options", response_model=RegisterOptionsResponse)
def api_register_options() -> RegisterOptionsResponse:
    return RegisterOptionsResponse(
        allow_register=auth_allow_register(),
        require_admin_approval=auth_register_require_admin_approval(),
    )


@app.post("/api/auth/register")
def api_auth_register(payload: RegisterRequest, db: Session = Depends(get_db)) -> dict:
    if not auth_allow_register():
        raise HTTPException(status_code=403, detail="当前未开放注册")

    username = payload.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="用户名不能为空")
    if payload.password != payload.confirm_password:
        raise HTTPException(status_code=400, detail="两次密码输入不一致")

    existing_user = get_user_by_username(db, username)
    if existing_user:
        raise HTTPException(status_code=400, detail="用户名已存在")

    pending = db.scalar(select(PendingRegistration).where(PendingRegistration.username == username))
    require_approval = auth_register_require_admin_approval()

    if require_approval:
        if pending and pending.status == "pending":
            raise HTTPException(status_code=400, detail="该用户名已有待审核申请")

        if not pending:
            pending = PendingRegistration(username=username)
            db.add(pending)

        pending.password_hash = hash_password(payload.password)
        pending.status = "pending"
        pending.created_at = datetime.now(timezone.utc)
        pending.decided_at = None
        pending.decided_by = None
        db.commit()
        return {
            "ok": True,
            "pending": True,
            "message": "注册申请已提交，等待管理员审核",
        }

    user = AdminUser(username=username, password_hash=hash_password(payload.password))
    db.add(user)
    if pending:
        db.delete(pending)
    db.commit()
    return {
        "ok": True,
        "pending": False,
        "message": "注册成功，请直接登录",
    }


@app.post("/api/auth/login", response_model=AuthMeResponse)
def api_auth_login(payload: LoginRequest, db: Session = Depends(get_db)) -> Response:
    user = authenticate_user(db, payload.username, payload.password)
    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if _is_user_blocked(db, user.username):
        raise HTTPException(status_code=403, detail="当前账号已被管理员禁用")

    token = create_session_token(user.username)
    response = JSONResponse(
        content=AuthMeResponse(
            authenticated=True,
            username=user.username,
            is_admin=is_admin_username(user.username),
        ).model_dump()
    )
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
    username = _current_username(request)
    if not username:
        raise HTTPException(status_code=401, detail="unauthorized")
    return AuthMeResponse(authenticated=True, username=username, is_admin=is_admin_username(username))


@app.post("/api/auth/change-password")
def api_auth_change_password(payload: ChangePasswordRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    username = _current_username(request)
    if not username:
        raise HTTPException(status_code=401, detail="unauthorized")

    user = get_user_by_username(db, username)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if not authenticate_user(db, username, payload.current_password):
        raise HTTPException(status_code=400, detail="当前密码错误")
    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=400, detail="新密码不能与当前密码相同")

    ok = update_user_password(db, username, payload.new_password)
    if not ok:
        raise HTTPException(status_code=500, detail="修改密码失败")
    return {"message": "密码修改成功"}


@app.get("/api/auth/users", response_model=list[UserRead])
def api_auth_users(request: Request, db: Session = Depends(get_db)) -> list[UserRead]:
    _ensure_admin(request)
    blocked = set(_blocked_usernames_from_settings(get_settings(db)))
    rows = db.scalars(select(AdminUser).order_by(desc(AdminUser.created_at))).all()
    return [
        UserRead(
            id=row.id,
            username=row.username,
            created_at=row.created_at,
            is_admin=is_admin_username(row.username),
            disabled=(row.username in blocked and not is_admin_username(row.username)),
        )
        for row in rows
    ]


@app.post("/api/auth/users/reset-password")
def api_auth_reset_user_password(
    payload: UserResetPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    admin_user = _ensure_admin(request)
    target = payload.username.strip()
    if not target:
        raise HTTPException(status_code=400, detail="目标用户名不能为空")

    user = get_user_by_username(db, target)
    if not user:
        raise HTTPException(status_code=404, detail="目标用户不存在")

    if not update_user_password(db, target, payload.new_password):
        raise HTTPException(status_code=500, detail="重置密码失败")

    return {"message": f"用户 {target} 密码已由管理员 {admin_user} 重置"}


@app.post("/api/auth/users/set-disabled")
def api_auth_set_user_disabled(
    payload: UserSetDisabledRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    admin_user = _ensure_admin(request)
    target = payload.username.strip()
    if not target:
        raise HTTPException(status_code=400, detail="目标用户名不能为空")
    if is_admin_username(target):
        raise HTTPException(status_code=400, detail="管理员账号不允许禁用")

    user = get_user_by_username(db, target)
    if not user:
        raise HTTPException(status_code=404, detail="目标用户不存在")

    _set_user_blocked(db, target, bool(payload.disabled))
    action = "禁用" if payload.disabled else "启用"
    return {"message": f"管理员 {admin_user} 已{action}用户 {target}"}


@app.delete("/api/auth/users/{username}")
def api_auth_delete_user(username: str, request: Request, db: Session = Depends(get_db)) -> dict:
    admin_user = _ensure_admin(request)
    target = username.strip()
    if not target:
        raise HTTPException(status_code=400, detail="目标用户名不能为空")
    if is_admin_username(target):
        raise HTTPException(status_code=400, detail="管理员账号不允许删除")
    if target == admin_user:
        raise HTTPException(status_code=400, detail="不能删除当前登录账号")

    user = get_user_by_username(db, target)
    if not user:
        raise HTTPException(status_code=404, detail="目标用户不存在")

    db.delete(user)
    db.commit()
    _set_user_blocked(db, target, False)
    return {"message": f"已删除用户 {target}"}


@app.get("/api/auth/registrations/pending", response_model=list[PendingRegistrationRead])
def api_auth_pending_registrations(request: Request, db: Session = Depends(get_db)) -> list[PendingRegistrationRead]:
    _ensure_admin(request)
    rows = db.scalars(
        select(PendingRegistration)
        .where(PendingRegistration.status == "pending")
        .order_by(desc(PendingRegistration.created_at))
    ).all()
    return [PendingRegistrationRead.model_validate(row) for row in rows]


@app.post("/api/auth/registrations/{registration_id}/approve")
def api_auth_approve_registration(registration_id: int, request: Request, db: Session = Depends(get_db)) -> dict:
    admin_user = _ensure_admin(request)
    row = db.get(PendingRegistration, registration_id)
    if not row or row.status != "pending":
        raise HTTPException(status_code=404, detail="待审核记录不存在")

    if get_user_by_username(db, row.username):
        row.status = "rejected"
        row.decided_at = datetime.now(timezone.utc)
        row.decided_by = admin_user
        db.commit()
        raise HTTPException(status_code=400, detail="目标用户名已存在，已自动拒绝该申请")

    user = AdminUser(username=row.username, password_hash=row.password_hash)
    db.add(user)
    row.status = "approved"
    row.decided_at = datetime.now(timezone.utc)
    row.decided_by = admin_user
    db.commit()
    return {"message": f"已通过用户注册：{row.username}"}


@app.post("/api/auth/registrations/{registration_id}/reject")
def api_auth_reject_registration(registration_id: int, request: Request, db: Session = Depends(get_db)) -> dict:
    admin_user = _ensure_admin(request)
    row = db.get(PendingRegistration, registration_id)
    if not row or row.status != "pending":
        raise HTTPException(status_code=404, detail="待审核记录不存在")

    row.status = "rejected"
    row.decided_at = datetime.now(timezone.utc)
    row.decided_by = admin_user
    db.commit()
    return {"message": f"已拒绝注册申请：{row.username}"}


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


@app.post("/api/cron/from-natural", response_model=CronNaturalResponse)
def api_cron_from_natural(payload: CronNaturalRequest) -> CronNaturalResponse:
    text = str(payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="请输入自然语言时间")

    try:
        cron, message = _cron_from_natural_text(text)
        return CronNaturalResponse(ok=True, cron=cron, message=message)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"转换失败：{exc}")


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
    url = _validate_rss_url(payload.url)

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


@app.post("/api/sources/import-rss", response_model=ImportRssBatchResponse)
def api_import_rss_batch(payload: ImportRssBatchRequest, db: Session = Depends(get_db)) -> ImportRssBatchResponse:
    rows = payload.items or []
    if not rows:
        raise HTTPException(status_code=400, detail="items 不能为空")

    existing_rss = db.scalars(select(Source).where(Source.source_type == "rss")).all()
    existing_by_url: dict[str, Source] = {}
    for source in existing_rss:
        try:
            config = json.loads(source.config_json or "{}")
        except Exception:
            config = {}
        url = str(config.get("url") or "").strip()
        if url and url not in existing_by_url:
            existing_by_url[url] = source

    created = 0
    updated = 0
    skipped = 0
    errors: list[str] = []
    processed_urls: set[str] = set()

    for idx, item in enumerate(rows, start=1):
        raw_url = str(item.url or "").strip()
        if not raw_url:
            skipped += 1
            errors.append(f"第{idx}条：url 不能为空")
            continue

        if raw_url in processed_urls:
            skipped += 1
            errors.append(f"第{idx}条：url 重复，已跳过 {raw_url}")
            continue
        processed_urls.add(raw_url)

        try:
            url = _validate_rss_url(raw_url)
        except HTTPException as exc:
            skipped += 1
            errors.append(f"第{idx}条：{exc.detail}")
            continue

        item.url = url
        name = (item.name or "").strip() or _default_source_name_from_url(url)
        config = _build_rss_source_config(item)

        existing = existing_by_url.get(url)
        if existing:
            if not payload.overwrite_existing:
                skipped += 1
                continue

            try:
                existing_config = json.loads(existing.config_json or "{}")
            except Exception:
                existing_config = {}
            if existing_config.get("rsshub_route") and "rsshub_route" not in config:
                config["rsshub_route"] = existing_config.get("rsshub_route")

            existing.name = name
            existing.enabled = bool(item.enabled)
            existing.config_json = json.dumps(config, ensure_ascii=False)
            updated += 1
            continue

        source = Source(
            name=name,
            source_type="rss",
            enabled=bool(item.enabled),
            config_json=json.dumps(config, ensure_ascii=False),
        )
        db.add(source)
        created += 1

    db.commit()
    return ImportRssBatchResponse(
        received=len(rows),
        created=created,
        updated=updated,
        skipped=skipped,
        errors=errors,
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
