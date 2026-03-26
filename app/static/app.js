const qs = (id) => document.getElementById(id);

let cachedPromptVersions = [];
let cachedEdgeVoiceLanguages = [];
let episodesPollTimer = null;
let edgePreviewObjectUrl = null;

const fieldsGeneral = [
  "language",
  "timezone",
  "schedule_cron",
  "podcast_name",
];

const fieldsAPI = [
  "llm_api_base",
  "llm_api_key",
  "llm_model",
  "llm_temperature",
  "llm_summary_system_prompt",
  "llm_summary_prompt_template",
  "llm_episode_system_prompt",
  "llm_episode_prompt_template",
  "tts_enabled",
  "tts_provider",
  "tts_api_base",
  "tts_api_key",
  "tts_model",
  "tts_voice",
  "tts_audio_speed",
  "tts_edge_connect_timeout",
  "tts_edge_receive_timeout",
  "tts_format",
  "telegram_enabled",
  "telegram_bot_token",
  "telegram_chat_id",
  "telegram_send_audio",
  "newsapi_global_key",
];

const promptFields = [
  "llm_summary_system_prompt",
  "llm_summary_prompt_template",
  "llm_episode_system_prompt",
  "llm_episode_prompt_template",
];

const EDGE_VOICE_FALLBACK_LANGUAGES = [
  {
    code: "zh-CN",
    name: "中文（简体）",
    voices: [
      { name: "zh-CN-XiaoxiaoNeural", label: "Xiaoxiao (女声) · 温暖自然" },
      { name: "zh-CN-YunxiNeural", label: "Yunxi (男声) · 沉稳" },
      { name: "zh-CN-YunjianNeural", label: "Yunjian (男声) · 阳刚" },
      { name: "zh-CN-XiaoyiNeural", label: "Xiaoyi (女声) · 活泼" },
    ],
  },
  {
    code: "en-US",
    name: "English (US)",
    voices: [
      { name: "en-US-AriaNeural", label: "Aria (Female) · Warm" },
      { name: "en-US-JennyNeural", label: "Jenny (Female) · Friendly" },
      { name: "en-US-GuyNeural", label: "Guy (Male) · Casual" },
    ],
  },
  {
    code: "ja-JP",
    name: "日本語",
    voices: [
      { name: "ja-JP-NanamiNeural", label: "Nanami (女性) · 明るい" },
      { name: "ja-JP-KeitaNeural", label: "Keita (男性) · 落ち着き" },
    ],
  },
];

// ==================== Toast System ====================

function showToast(type, message, duration = 3000) {
  const container = qs("toastContainer");
  if (!container) return;

  const icons = { success: "\u2705", error: "\u274C", info: "\u2139\uFE0F" };
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.innerHTML = `
    <span class="toast-icon">${icons[type] || icons.info}</span>
    <span class="toast-msg">${escapeHtml(message)}</span>
    <button class="toast-close" onclick="this.parentElement.classList.add('removing');setTimeout(()=>this.parentElement.remove(),250)">\u00D7</button>
  `;
  container.appendChild(toast);

  if (duration > 0) {
    setTimeout(() => {
      if (toast.parentElement) {
        toast.classList.add("removing");
        setTimeout(() => toast.remove(), 250);
      }
    }, duration);
  }
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// ==================== Confirm Modal ====================

function showConfirm(title, message) {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "modal-overlay";
    overlay.innerHTML = `
      <div class="modal-box">
        <div class="modal-title">${escapeHtml(title)}</div>
        <div class="modal-body">${escapeHtml(message)}</div>
        <div class="modal-actions">
          <button class="modal-cancel">取消</button>
          <button class="primary modal-confirm">确认</button>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);

    const cleanup = (result) => {
      overlay.remove();
      resolve(result);
    };

    overlay.querySelector(".modal-cancel").onclick = () => cleanup(false);
    overlay.querySelector(".modal-confirm").onclick = () => cleanup(true);
    overlay.onclick = (e) => { if (e.target === overlay) cleanup(false); };
  });
}

// ==================== Button Loading ====================

function setButtonLoading(btn, loading) {
  if (!btn) return;
  if (loading) {
    btn.disabled = true;
    btn._origHTML = btn.innerHTML;
    const text = btn.textContent.trim();
    btn.innerHTML = `<span class="btn-spinner"></span> ${escapeHtml(text)}`;
  } else {
    btn.disabled = false;
    if (btn._origHTML !== undefined) {
      btn.innerHTML = btn._origHTML;
      delete btn._origHTML;
    }
  }
}

// ==================== Tab System ====================

function initTabs() {
  const tabBtns = document.querySelectorAll(".tab-btn");
  const tabPanels = document.querySelectorAll(".tab-panel");

  tabBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      const tabId = btn.dataset.tab;
      tabBtns.forEach((b) => b.classList.remove("active"));
      tabPanels.forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      const panel = qs(`tab-${tabId}`);
      if (panel) panel.classList.add("active");
    });
  });
}

// ==================== Business Logic (unchanged) ====================

function parseValueByField(field, raw) {
  if (["llm_api_base", "tts_api_base"].includes(field)) return normalizeBaseUrl(raw);
  if (["max_items_per_source", "max_total_items", "tts_audio_speed", "tts_edge_connect_timeout", "tts_edge_receive_timeout"].includes(field)) return Number(raw);
  if (["llm_temperature"].includes(field)) return Number(raw);
  if (["tts_enabled", "telegram_enabled", "telegram_send_audio"].includes(field)) return raw === "true";
  return raw;
}

function normalizeBaseUrl(raw) {
  const value = String(raw || "").trim().replace(/\/+$/, "");
  if (!value) return value;
  try {
    const url = new URL(value);
    let path = url.pathname.replace(/\/+$/, "");
    if (path.endsWith("/chat/completions")) path = path.slice(0, -"/chat/completions".length);
    if (path.endsWith("/audio/speech")) path = path.slice(0, -"/audio/speech".length);
    if (/xiaomimimo\.com$/i.test(url.hostname) && (!path || path === "/")) path = "/v1";
    return `${url.protocol}//${url.host}${path}`;
  } catch {
    return value;
  }
}

function initSecretToggles() {
  document.querySelectorAll(".secret-toggle").forEach((button) => {
    button.onclick = () => {
      const targetId = button.dataset.target;
      if (!targetId) return;
      const input = qs(targetId);
      if (!input) return;
      const reveal = input.type === "password";
      input.type = reveal ? "text" : "password";
      button.textContent = reveal ? "隐藏" : "显示";
    };
  });
}

function defaultEdgeVoice() {
  return "zh-CN-XiaoxiaoNeural";
}

function defaultCustomVoice() {
  return "default_zh";
}

function isEdgeVoiceName(voice) {
  if (!voice) return false;
  const value = String(voice).trim();
  if (!value) return false;
  for (const row of cachedEdgeVoiceLanguages) {
    if ((row.voices || []).some((item) => item.name === value)) {
      return true;
    }
  }
  return /Neural$/i.test(value);
}

function firstEdgeVoice() {
  for (const row of cachedEdgeVoiceLanguages) {
    const first = row?.voices?.[0]?.name;
    if (first) return first;
  }
  return "";
}

function resolvedEdgeDefaultVoice() {
  const fallback = defaultEdgeVoice();
  if (!cachedEdgeVoiceLanguages.length) return fallback;
  for (const row of cachedEdgeVoiceLanguages) {
    if ((row.voices || []).some((item) => item.name === fallback)) {
      return fallback;
    }
  }
  return firstEdgeVoice() || fallback;
}

function detectEdgeLanguageByVoice(voice) {
  if (!voice) return "";
  for (const row of cachedEdgeVoiceLanguages) {
    if ((row.voices || []).some((item) => item.name === voice)) {
      return row.code;
    }
  }
  const match = String(voice).match(/^[a-z]{2,3}-[A-Z]{2}/);
  return match ? match[0] : "";
}

function populateEdgeVoiceLanguageOptions(preferredCode = "") {
  const select = qs("edge_voice_language");
  if (!select) return;

  if (!cachedEdgeVoiceLanguages.length) {
    select.innerHTML = `<option value="">暂无可用语言</option>`;
    return;
  }

  const options = cachedEdgeVoiceLanguages.map(
    (row) => `<option value="${row.code}">${row.name} (${row.code})</option>`,
  );
  select.innerHTML = options.join("");

  const languageCodes = new Set(cachedEdgeVoiceLanguages.map((row) => row.code));
  const target = languageCodes.has(preferredCode) ? preferredCode : cachedEdgeVoiceLanguages[0].code;
  select.value = target;
}

function populateEdgeVoiceOptions(languageCode, preferredVoice = "") {
  const select = qs("edge_voice_select");
  if (!select) return;

  const targetLanguage =
    cachedEdgeVoiceLanguages.find((row) => row.code === languageCode) || cachedEdgeVoiceLanguages[0] || null;
  const voices = targetLanguage?.voices || [];
  if (!voices.length) {
    select.innerHTML = `<option value="">暂无可用音色</option>`;
    return;
  }

  select.innerHTML = voices.map((item) => `<option value="${item.name}">${item.label}</option>`).join("");
  const voiceNames = new Set(voices.map((item) => item.name));
  select.value = voiceNames.has(preferredVoice) ? preferredVoice : voices[0].name;
}

function syncVoiceControlsFromSettings() {
  const provider = (qs("tts_provider")?.value || "edge_tts").trim().toLowerCase();
  const hiddenVoice = qs("tts_voice");
  if (!hiddenVoice) return;

  let voice = (hiddenVoice.value || "").trim();
  if (provider === "edge_tts" && !isEdgeVoiceName(voice)) {
    voice = resolvedEdgeDefaultVoice();
    hiddenVoice.value = voice;
  }
  if (provider === "custom_api" && !voice) {
    voice = defaultCustomVoice();
    hiddenVoice.value = voice;
  }

  const customVoice = qs("tts_custom_voice");
  if (customVoice) {
    if (provider === "custom_api") {
      customVoice.value = voice || defaultCustomVoice();
    } else if (!(customVoice.value || "").trim()) {
      customVoice.value = defaultCustomVoice();
    }
  }

  const speed = qs("tts_audio_speed");
  const edgeSpeed = qs("tts_audio_speed_edge");
  if (speed && edgeSpeed) {
    if (provider === "custom_api") {
      edgeSpeed.value = speed.value || "1";
    } else {
      speed.value = edgeSpeed.value || speed.value || "1";
      edgeSpeed.value = speed.value;
    }
  }

  const languageCode = detectEdgeLanguageByVoice(voice);
  populateEdgeVoiceLanguageOptions(languageCode);
  const selectedLanguageCode = qs("edge_voice_language")?.value || languageCode;
  populateEdgeVoiceOptions(selectedLanguageCode, voice);
}

function ensureTtsVoiceByProvider(provider) {
  const hiddenVoice = qs("tts_voice");
  if (!hiddenVoice) return;

  let voice = (hiddenVoice.value || "").trim();
  if (provider === "custom_api") {
    const customVoice = (qs("tts_custom_voice")?.value || "").trim();
    if (!voice || isEdgeVoiceName(voice)) {
      voice = customVoice || defaultCustomVoice();
    }
  } else {
    const selectedEdgeVoice = (qs("edge_voice_select")?.value || "").trim();
    if (!voice || !isEdgeVoiceName(voice) || voice === "default_zh" || voice === "alloy" || /^mimo_/i.test(voice)) {
      voice = selectedEdgeVoice || resolvedEdgeDefaultVoice();
    }
  }
  hiddenVoice.value = voice;
}

function syncTtsVoiceBeforeSave() {
  const provider = (qs("tts_provider")?.value || "edge_tts").trim().toLowerCase();
  const hiddenVoice = qs("tts_voice");
  if (!hiddenVoice) return;

  if (provider === "custom_api") {
    const customVoice = (qs("tts_custom_voice")?.value || "").trim();
    const existing = (hiddenVoice.value || "").trim();
    hiddenVoice.value = customVoice || (isEdgeVoiceName(existing) ? defaultCustomVoice() : existing || defaultCustomVoice());
  } else {
    const edgeVoice = (qs("edge_voice_select")?.value || "").trim();
    const existing = (hiddenVoice.value || "").trim();
    hiddenVoice.value = edgeVoice || (isEdgeVoiceName(existing) ? existing : resolvedEdgeDefaultVoice());
  }

  const speed = qs("tts_audio_speed");
  const edgeSpeed = qs("tts_audio_speed_edge");
  if (speed && edgeSpeed) {
    if (provider === "custom_api") {
      edgeSpeed.value = speed.value;
    } else if (edgeSpeed.value) {
      speed.value = edgeSpeed.value;
    }
  }
}

async function loadEdgeVoices(force = false) {
  if (cachedEdgeVoiceLanguages.length && !force) {
    syncVoiceControlsFromSettings();
    return;
  }

  try {
    const res = await request("/api/tts/edge-voices");
    if (Array.isArray(res?.languages) && res.languages.length) {
      cachedEdgeVoiceLanguages = res.languages;
    } else {
      cachedEdgeVoiceLanguages = EDGE_VOICE_FALLBACK_LANGUAGES;
    }
  } catch {
    cachedEdgeVoiceLanguages = EDGE_VOICE_FALLBACK_LANGUAGES;
  }

  syncVoiceControlsFromSettings();
}

function updateTtsProviderUI() {
  const provider = (qs("tts_provider")?.value || "edge_tts").trim().toLowerCase();
  const isCustom = provider === "custom_api";

  document.querySelectorAll(".tts-custom-only").forEach((node) => {
    node.style.display = isCustom ? "" : "none";
  });
  document.querySelectorAll(".tts-edge-only").forEach((node) => {
    node.style.display = isCustom ? "none" : "";
  });

  ensureTtsVoiceByProvider(provider);
  syncVoiceControlsFromSettings();
  if (!isCustom && !cachedEdgeVoiceLanguages.length) {
    loadEdgeVoices();
  }
}

function setTestStatus(id, ok, message) {
  const el = qs(id);
  if (!el) return;
  el.className = ok ? "pill ok" : "pill fail";
  el.textContent = message;
}

function setCronTestStatus(ok, message) {
  const el = qs("cronTestStatus");
  if (!el) return;
  el.className = ok ? "small ok" : "small fail";
  el.textContent = message;
}

function setEdgeTtsUpdateStatus(ok, message) {
  const el = qs("edgeTtsUpdateStatus");
  if (!el) return;
  el.className = ok ? "small ok" : "small fail";
  el.textContent = message;
}

function setEdgeVoicePreviewStatus(ok, message) {
  const el = qs("edgeVoicePreviewStatus");
  if (!el) return;
  el.className = ok ? "small ok" : "small fail";
  el.textContent = message;
}

function setPromptVersionStatus(ok, message) {
  const el = qs("promptVersionStatus");
  if (!el) return;
  el.className = ok ? "small ok" : "small fail";
  el.textContent = message;
}

function renderPromptVersionOptions(versions) {
  const select = qs("promptVersionSelect");
  if (!select) return;
  const options = [
    `<option value="">选择已保存提示词版本</option>`,
    ...versions.map((row) => `<option value="${row.id}">${row.name} (${String(row.created_at).slice(0, 16)})</option>`),
  ];
  select.innerHTML = options.join("");
}

async function loadPromptVersions() {
  cachedPromptVersions = await request("/api/prompt-versions");
  renderPromptVersionOptions(cachedPromptVersions);
}

async function savePromptVersion() {
  const name = (qs("promptVersionName")?.value || "").trim();
  if (!name) {
    setPromptVersionStatus(false, "请先填写提示词版本名称");
    return;
  }
  await saveSettings(promptFields);
  const res = await request("/api/prompt-versions", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
  qs("promptVersionName").value = "";
  await loadPromptVersions();
  const select = qs("promptVersionSelect");
  if (select && res?.id) select.value = res.id;
  setPromptVersionStatus(true, `已保存版本：${res.name}`);
}

async function applyPromptVersion() {
  const versionId = (qs("promptVersionSelect")?.value || "").trim();
  if (!versionId) {
    setPromptVersionStatus(false, "请先选择一个提示词版本");
    return;
  }
  const res = await request(`/api/prompt-versions/${versionId}/apply`, { method: "POST" });
  fillSettings(res.values || {});
  setPromptVersionStatus(true, "提示词版本已加载");
}

async function deletePromptVersion() {
  const versionId = (qs("promptVersionSelect")?.value || "").trim();
  if (!versionId) {
    setPromptVersionStatus(false, "请先选择要删除的版本");
    return;
  }
  await request(`/api/prompt-versions/${versionId}`, { method: "DELETE" });
  await loadPromptVersions();
  setPromptVersionStatus(true, "提示词版本已删除");
}

async function request(url, opts = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  if (!response.ok) {
    if (response.status === 401) {
      window.location.href = "/login";
      throw new Error("未登录或会话已过期");
    }
    const text = await response.text();
    throw new Error(`${response.status} ${text}`);
  }
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) return response.json();
  return response.text();
}

function fillSettings(values) {
  [...fieldsGeneral, ...fieldsAPI].forEach((field) => {
    const el = qs(field);
    if (!el) return;
    const value = values[field];
    if (value === undefined || value === null) return;
    el.value = String(value);
  });
  updateTtsProviderUI();
}

function parseEpisodePayload(raw) {
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function scheduleEpisodePolling(episodes) {
  if (episodesPollTimer) {
    clearTimeout(episodesPollTimer);
    episodesPollTimer = null;
  }
  const hasActive = episodes.some((row) => ["pending", "running"].includes(String(row.status || "").toLowerCase()));
  if (!hasActive) return;
  episodesPollTimer = setTimeout(async () => {
    try {
      await loadEpisodes();
    } catch {
      // ignore polling error
    }
  }, 2500);
}

// ==================== Rendering ====================

function sourceItemHTML(source) {
  const config = source.config || {};
  const isRss = source.source_type === "rss";
  const lastError = source.last_error || "";
  const keywords = config.keywords || "";
  const maxItems = config.max_items || 20;
  return `
    <div class="item" data-id="${source.id}" data-source-type="${source.source_type}">
      <div class="row">
        <div class="col"><strong>${escapeHtml(source.name)}</strong> <span class="small">#${source.id}</span></div>
        <div class="col small" style="text-align:right;">
          <span class="pill">${source.source_type}</span>
          <span class="pill ${source.enabled ? "ok" : "fail"}">${source.enabled ? "enabled" : "disabled"}</span>
        </div>
      </div>
      <div class="small">last_sync=${source.last_sync_at || "-"}</div>
      <div class="small ${lastError ? "fail" : ""}">last_error=${lastError || "-"}</div>
      ${
        isRss
          ? `<div class="row"><div class="col"><label>RSS URL</label><input class="rss-url-input" value="${escapeHtml(config.url || "")}" /></div></div>
             <div class="row">
               <div class="col"><label>关键词筛选（逗号分隔，留空不筛选）</label><input class="source-keywords-input" value="${escapeHtml(keywords)}" placeholder="留空则不筛选" /></div>
               <div class="col" style="max-width:160px;"><label>最大条目数</label><input class="source-max-items-input" type="number" value="${maxItems}" min="1" max="200" /></div>
             </div>`
          : `<div class="row"><div class="col"><label>配置 JSON</label><textarea class="source-config">${escapeHtml(JSON.stringify(config, null, 2))}</textarea></div></div>`
      }
      <div class="row">
        <button class="save-source sm">\u{1F4BE} 保存配置</button>
        <button class="test-source sm">\u{1F9EA} 测试</button>
        <button class="toggle-source sm">\u{1F504} 切换启用</button>
        <button class="warn delete-source sm">\u{1F5D1} 删除</button>
        <a target="_blank" href="/rss/sources/${source.id}.xml">查看 RSS</a>
      </div>
      <div class="small source-test-result" style="min-height:18px;"></div>
    </div>
  `;
}

function episodeItemHTML(episode) {
  const payload = parseEpisodePayload(episode.payload_json);
  const progress = payload.progress || {};
  const progressMessage = progress.message || "";
  const sourceResults = Array.isArray(payload.source_results) ? payload.source_results : [];
  const failedSources = sourceResults.filter((row) => !row.ok).slice(0, 3);

  const errorMessage =
    (episode.error_message || "").trim() ||
    (String(episode.status || "").toLowerCase() === "failed" ? progressMessage || "任务失败" : "");
  const overviewText = (episode.overview || "").trim();
  const showOverview = overviewText && overviewText !== progressMessage;
  const showError = errorMessage && errorMessage !== progressMessage && errorMessage !== overviewText;

  const audioLink = episode.audio_file ? `/media/audio/${episode.audio_file}` : "";
  const notesLink = episode.notes_file ? `/media/notes/${episode.notes_file}` : "";
  const statusClass = episode.status === "completed" ? "ok" : episode.status === "failed" ? "fail" : "";

  return `
    <div class="item">
      <div class="row">
        <div class="col"><strong>${escapeHtml(episode.title || "(未命名)")}</strong></div>
        <div class="col small" style="text-align:right;">
          <span class="small">#${episode.id}</span>
          <span class="pill ${statusClass}">${episode.status}</span>
        </div>
      </div>
      <div class="small">created=${episode.created_at} | completed=${episode.completed_at || "-"}</div>
      ${progressMessage ? `<div class="small">状态：${escapeHtml(progressMessage)}</div>` : ""}
      ${showOverview ? `<div style="margin-top:6px;">${escapeHtml(overviewText)}</div>` : ""}
      <div class="small">item_count=${episode.item_count}</div>
      ${
        failedSources.length
          ? `<div class="small fail">来源失败：${failedSources
              .map((row) => `${escapeHtml(row.name || "未知来源")}(${escapeHtml(row.error || "unknown")})`)
              .join(" | ")}</div>`
          : ""
      }
      ${audioLink ? `<audio controls src="${audioLink}"></audio>` : ""}
      <div class="row" style="margin-top:8px;">
        ${audioLink ? `<a target="_blank" href="${audioLink}">\u{1F4E5} 下载音频</a>` : `<span class="small">未生成音频</span>`}
        ${notesLink ? `<a target="_blank" href="${notesLink}">\u{1F4DD} 查看材料笔记</a>` : ""}
        <button class="warn sm delete-episode" data-episode-id="${episode.id}">\u{1F5D1} 删除</button>
      </div>
      ${showError ? `<div class="small fail" style="margin-top:4px;">error=${escapeHtml(errorMessage)}</div>` : ""}
    </div>
  `;
}

function renderEmptyState(containerId, icon, title, desc) {
  const el = qs(containerId);
  if (!el) return;
  el.innerHTML = `
    <div class="empty-state">
      <div class="empty-icon">${icon}</div>
      <div class="empty-title">${escapeHtml(title)}</div>
      <div class="empty-desc">${escapeHtml(desc)}</div>
    </div>
  `;
}

// ==================== Partial Data Loading ====================

function renderSources(sources) {
  const countEl = qs("sourcesCount");
  if (countEl) countEl.textContent = `${sources.length} 个来源`;

  if (sources.length) {
    qs("sourcesList").innerHTML = sources.map(sourceItemHTML).join("");
  } else {
    renderEmptyState("sourcesList", "\u{1F4E1}", "暂无来源", "通过上方表单添加 RSS、arXiv 或 RSSHub 来源");
  }
}

function renderEpisodes(episodes) {
  const countEl = qs("episodesCount");
  if (countEl) countEl.textContent = `${episodes.length} 条记录`;

  if (episodes.length) {
    qs("episodesList").innerHTML = episodes.map(episodeItemHTML).join("");
  } else {
    renderEmptyState("episodesList", "\u{1F3A7}", "暂无播客", "配置来源后点击「执行」生成第一期播客");
  }
  scheduleEpisodePolling(episodes);
}

async function loadSources() {
  const sources = await request("/api/sources");
  renderSources(sources);
  return sources;
}

async function loadEpisodes() {
  const episodes = await request("/api/episodes");
  renderEpisodes(episodes);
  return episodes;
}

// ==================== Main Data Loading (parallelized) ====================

async function loadAll() {
  // Phase 1: Fire all independent requests in parallel
  const [me, settingsRes, versions, sources, episodes] = await Promise.all([
    request("/api/auth/me"),
    request("/api/settings"),
    request("/api/prompt-versions").catch(() => []),
    request("/api/sources").catch(() => []),
    request("/api/episodes").catch(() => []),
  ]);

  // Phase 2: Apply results (DOM updates are synchronous and fast)
  qs("currentUser").textContent = `\u{1F464} ${me.username}`;
  fillSettings(settingsRes.values || {});

  cachedPromptVersions = versions;
  renderPromptVersionOptions(cachedPromptVersions);

  renderSources(sources);
  renderEpisodes(episodes);

  // Phase 3: Edge voices (depends on settings being filled first, non-blocking)
  loadEdgeVoices().catch(() => {});
}

async function saveSettings(fieldList) {
  syncTtsVoiceBeforeSave();
  const values = {};
  for (const field of fieldList) {
    const el = qs(field);
    if (!el) continue;
    values[field] = parseValueByField(field, el.value);
  }
  await request("/api/settings", {
    method: "PUT",
    body: JSON.stringify({ values }),
  });
}

// ==================== Source Addition ====================

async function addSource() {
  const name = (qs("new_source_name")?.value || "").trim();
  const enabled = qs("new_source_enabled")?.value === "true";

  const url = (qs("new_rss_url")?.value || "").trim();
  if (!url) {
    showToast("error", "请填写 RSS URL");
    return;
  }

  const keywords = (qs("new_rss_keywords")?.value || "").trim();
  const maxItems = parseInt(qs("new_rss_max_items")?.value) || 20;

  const config = { url };
  if (keywords) config.keywords = keywords;
  if (maxItems !== 20) config.max_items = maxItems;

  await request("/api/sources", {
    method: "POST",
    body: JSON.stringify({
      name: name || url.replace(/^https?:\/\//, "").split("/")[0],
      source_type: "rss",
      enabled,
      config,
    }),
  });

  qs("new_source_name").value = "";
  qs("new_rss_url").value = "";
  qs("new_rss_keywords").value = "";
  qs("new_rss_max_items").value = "20";
  await loadSources();
}

async function handleSourceListClick(event) {
  const item = event.target.closest(".item");
  if (!item) return;
  const id = Number(item.dataset.id);
  if (!id) return;

  if (event.target.classList.contains("delete-source")) {
    const confirmed = await showConfirm("删除来源", `确认删除来源 #${id}？此操作不可撤销。`);
    if (!confirmed) return;
    await request(`/api/sources/${id}`, { method: "DELETE" });
    await loadSources();
    return;
  }

  if (event.target.classList.contains("toggle-source")) {
    const sources = await request("/api/sources");
    const source = sources.find((s) => s.id === id);
    if (!source) return;
    await request(`/api/sources/${id}`, {
      method: "PUT",
      body: JSON.stringify({ enabled: !source.enabled }),
    });
    await loadSources();
    return;
  }

  if (event.target.classList.contains("test-source")) {
    const resultEl = item.querySelector(".source-test-result");
    if (resultEl) {
      resultEl.className = "small source-test-result";
      resultEl.textContent = "测试中...";
    }
    const result = await request(`/api/sources/${id}/test`, { method: "POST" });
    if (resultEl) {
      resultEl.className = `small source-test-result ${result.ok ? "ok" : "fail"}`;
      resultEl.textContent = result.message || "完成";
    }
    return;
  }

  if (event.target.classList.contains("save-source")) {
    const sourceType = item.dataset.sourceType;
    const sources = await request("/api/sources");
    const source = sources.find((s) => s.id === id);
    if (!source) return;

    let config = source.config || {};

    if (sourceType === "rss") {
      const input = item.querySelector(".rss-url-input");
      const rssUrl = (input?.value || "").trim();
      if (!rssUrl) {
        showToast("error", "RSS URL 不能为空");
        return;
      }
      const keywords = (item.querySelector(".source-keywords-input")?.value || "").trim();
      const maxItems = parseInt(item.querySelector(".source-max-items-input")?.value) || 20;
      config = { ...config, url: rssUrl };
      config.keywords = keywords || "";
      config.max_items = maxItems;
    } else {
      const textarea = item.querySelector(".source-config");
      try {
        config = JSON.parse(textarea.value || "{}");
      } catch (e) {
        showToast("error", `配置 JSON 无效: ${e.message}`);
        return;
      }
    }

    await request(`/api/sources/${id}`, {
      method: "PUT",
      body: JSON.stringify({ config }),
    });
    showToast("success", "来源配置已保存");
    await loadSources();
  }
}

async function testLlm() {
  setTestStatus("llmTestStatus", true, "测试中...");
  await saveSettings(fieldsAPI);
  const res = await request("/api/test/llm", { method: "POST" });
  setTestStatus("llmTestStatus", Boolean(res.ok), res.message || "无返回");
}

async function testTts() {
  setTestStatus("ttsTestStatus", true, "测试中...");
  await saveSettings(fieldsAPI);
  const res = await request("/api/test/tts", { method: "POST" });
  setTestStatus("ttsTestStatus", Boolean(res.ok), res.message || "无返回");
}

async function testCron() {
  const scheduleCron = (qs("schedule_cron")?.value || "").trim();
  const timezone = (qs("timezone")?.value || "").trim();
  if (!scheduleCron) {
    setCronTestStatus(false, "请先填写 Cron 表达式");
    return;
  }

  setCronTestStatus(true, "测试中...");
  const res = await request("/api/test/cron", {
    method: "POST",
    body: JSON.stringify({ schedule_cron: scheduleCron, timezone }),
  });
  const nextRuns = Array.isArray(res.next_runs) ? res.next_runs : [];
  const suffix = nextRuns.length ? ` | 下次触发: ${nextRuns[0]}` : "";
  setCronTestStatus(Boolean(res.ok), (res.message || "无返回") + suffix);
}

async function checkEdgeTtsUpdate(notifyOnLatest = false) {
  setEdgeTtsUpdateStatus(true, "检查中...");
  const res = await request("/api/tts/edge-version");

  const installed = res.installed_version || "unknown";
  const latest = res.latest_version || "unknown";
  const updateAvailable = Boolean(res.update_available);

  if (updateAvailable) {
    setEdgeTtsUpdateStatus(false, `发现新版本：${installed} → ${latest}，建议重建 Docker 镜像更新`);
    showToast("info", `edge-tts 有新版本 ${latest}，建议更新 Docker 应用`);
    return;
  }

  const msg = `edge-tts 当前版本：${installed}${latest !== "unknown" ? `（latest: ${latest}）` : ""}`;
  setEdgeTtsUpdateStatus(true, msg);
  if (notifyOnLatest) {
    showToast("success", msg);
  }
}

async function previewEdgeVoice() {
  const voice = (qs("edge_voice_select")?.value || "").trim();
  if (!voice) {
    setEdgeVoicePreviewStatus(false, "请先选择 Edge Voice");
    return;
  }

  const speedRaw = (qs("tts_audio_speed_edge")?.value || "").trim();
  const speed = speedRaw ? Number(speedRaw) : null;

  setEdgeVoicePreviewStatus(true, `正在生成试听音频：我是${voice}`);
  const resp = await fetch("/api/test/edge-voice", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      voice,
      audio_speed: Number.isFinite(speed) ? speed : null,
    }),
  });

  if (!resp.ok) {
    const raw = await resp.text();
    throw new Error(`${resp.status} ${raw}`);
  }

  const blob = await resp.blob();
  if (!blob || !blob.size) {
    throw new Error("未获取到试听音频数据");
  }

  if (edgePreviewObjectUrl) {
    URL.revokeObjectURL(edgePreviewObjectUrl);
    edgePreviewObjectUrl = null;
  }

  edgePreviewObjectUrl = URL.createObjectURL(blob);
  const previewAudio = new Audio(edgePreviewObjectUrl);
  try {
    await previewAudio.play();
    setEdgeVoicePreviewStatus(true, `试听成功：${voice}`);
  } catch {
    setEdgeVoicePreviewStatus(true, `试听音频已生成，请允许浏览器播放后重试：${voice}`);
  }
}

// ==================== Init ====================

async function init() {
  initTabs();

  qs("refreshBtn").onclick = async () => {
    const btn = qs("refreshBtn");
    setButtonLoading(btn, true);
    try {
      await loadAll();
      showToast("success", "面板已刷新");
    } catch (e) {
      showToast("error", e.message);
    } finally {
      setButtonLoading(btn, false);
    }
  };

  qs("runNowBtn").onclick = async () => {
    const btn = qs("runNowBtn");
    setButtonLoading(btn, true);
    try {
      const res = await request("/api/run-now", { method: "POST" });
      showToast("success", res.message || "任务已触发");
      // Switch to episodes tab immediately, don't block
      document.querySelector('.tab-btn[data-tab="episodes"]')?.click();
      // Non-blocking refresh of episodes only
      loadEpisodes().catch(() => {});
    } catch (e) {
      showToast("error", e.message);
    } finally {
      setButtonLoading(btn, false);
    }
  };

  qs("logoutBtn").onclick = async () => {
    try {
      await request("/api/auth/logout", { method: "POST" });
    } finally {
      window.location.href = "/login";
    }
  };

  qs("saveGeneralBtn").onclick = async () => {
    const btn = qs("saveGeneralBtn");
    setButtonLoading(btn, true);
    try {
      await saveSettings(fieldsGeneral);
      showToast("success", "全局设置已保存");
    } catch (e) {
      showToast("error", e.message);
    } finally {
      setButtonLoading(btn, false);
    }
  };

  qs("testCronBtn").onclick = async () => {
    const btn = qs("testCronBtn");
    setButtonLoading(btn, true);
    try {
      await testCron();
    } catch (e) {
      setCronTestStatus(false, e.message);
    } finally {
      setButtonLoading(btn, false);
    }
  };

  qs("checkEdgeTtsUpdateBtn").onclick = async () => {
    const btn = qs("checkEdgeTtsUpdateBtn");
    setButtonLoading(btn, true);
    try {
      await checkEdgeTtsUpdate(true);
    } catch (e) {
      setEdgeTtsUpdateStatus(false, e.message);
    } finally {
      setButtonLoading(btn, false);
    }
  };

  qs("previewEdgeVoiceBtn").onclick = async () => {
    const btn = qs("previewEdgeVoiceBtn");
    setButtonLoading(btn, true);
    try {
      await previewEdgeVoice();
    } catch (e) {
      setEdgeVoicePreviewStatus(false, e.message);
    } finally {
      setButtonLoading(btn, false);
    }
  };

  qs("saveApiBtn").onclick = async () => {
    const btn = qs("saveApiBtn");
    setButtonLoading(btn, true);
    try {
      await saveSettings(fieldsAPI);
      showToast("success", "API 设置已保存");
    } catch (e) {
      showToast("error", e.message);
    } finally {
      setButtonLoading(btn, false);
    }
  };

  qs("testLlmBtn").onclick = async () => {
    const btn = qs("testLlmBtn");
    setButtonLoading(btn, true);
    try {
      await testLlm();
    } catch (e) {
      setTestStatus("llmTestStatus", false, e.message);
    } finally {
      setButtonLoading(btn, false);
    }
  };

  qs("testTtsBtn").onclick = async () => {
    const btn = qs("testTtsBtn");
    setButtonLoading(btn, true);
    try {
      await testTts();
    } catch (e) {
      setTestStatus("ttsTestStatus", false, e.message);
    } finally {
      setButtonLoading(btn, false);
    }
  };

  initSecretToggles();

  qs("tts_provider").onchange = () => {
    updateTtsProviderUI();
    syncTtsVoiceBeforeSave();
  };

  const edgeLangSelect = qs("edge_voice_language");
  if (edgeLangSelect) {
    edgeLangSelect.onchange = () => {
      populateEdgeVoiceOptions(edgeLangSelect.value, qs("tts_voice")?.value || "");
      syncTtsVoiceBeforeSave();
    };
  }

  const edgeVoiceSelect = qs("edge_voice_select");
  if (edgeVoiceSelect) {
    edgeVoiceSelect.onchange = () => syncTtsVoiceBeforeSave();
  }

  const customVoiceInput = qs("tts_custom_voice");
  if (customVoiceInput) {
    customVoiceInput.oninput = () => syncTtsVoiceBeforeSave();
  }

  const edgeSpeedInput = qs("tts_audio_speed_edge");
  if (edgeSpeedInput) {
    edgeSpeedInput.oninput = () => syncTtsVoiceBeforeSave();
  }

  const customSpeedInput = qs("tts_audio_speed");
  if (customSpeedInput) {
    customSpeedInput.oninput = () => syncTtsVoiceBeforeSave();
  }

  const refreshEdgeVoicesBtn = qs("refreshEdgeVoicesBtn");
  if (refreshEdgeVoicesBtn) {
    refreshEdgeVoicesBtn.onclick = async () => {
      setButtonLoading(refreshEdgeVoicesBtn, true);
      try {
        await loadEdgeVoices(true);
        setTestStatus("ttsTestStatus", true, "Edge 音色已刷新");
      } catch (e) {
        setTestStatus("ttsTestStatus", false, e.message);
      } finally {
        setButtonLoading(refreshEdgeVoicesBtn, false);
      }
    };
  }

  qs("savePromptVersionBtn").onclick = async () => {
    const btn = qs("savePromptVersionBtn");
    setButtonLoading(btn, true);
    try {
      await savePromptVersion();
    } catch (e) {
      setPromptVersionStatus(false, e.message);
    } finally {
      setButtonLoading(btn, false);
    }
  };

  qs("applyPromptVersionBtn").onclick = async () => {
    const btn = qs("applyPromptVersionBtn");
    setButtonLoading(btn, true);
    try {
      await applyPromptVersion();
    } catch (e) {
      setPromptVersionStatus(false, e.message);
    } finally {
      setButtonLoading(btn, false);
    }
  };

  qs("deletePromptVersionBtn").onclick = async () => {
    const btn = qs("deletePromptVersionBtn");
    setButtonLoading(btn, true);
    try {
      await deletePromptVersion();
    } catch (e) {
      setPromptVersionStatus(false, e.message);
    } finally {
      setButtonLoading(btn, false);
    }
  };

  qs("changePasswordBtn").onclick = async () => {
    const btn = qs("changePasswordBtn");
    setButtonLoading(btn, true);
    try {
      const current_password = qs("current_password").value;
      const new_password = qs("new_password").value;
      if (!current_password || !new_password) {
        showToast("error", "请填写当前密码和新密码");
        return;
      }
      await request("/api/auth/change-password", {
        method: "POST",
        body: JSON.stringify({ current_password, new_password }),
      });
      qs("current_password").value = "";
      qs("new_password").value = "";
      showToast("success", "密码修改成功");
    } catch (e) {
      showToast("error", e.message);
    } finally {
      setButtonLoading(btn, false);
    }
  };

  // Unified add source button
  qs("addSourceBtn").onclick = async () => {
    const btn = qs("addSourceBtn");
    setButtonLoading(btn, true);
    try {
      await addSource();
      showToast("success", "来源已新增");
    } catch (e) {
      showToast("error", e.message);
    } finally {
      setButtonLoading(btn, false);
    }
  };

  qs("sourcesList").addEventListener("click", async (event) => {
    try {
      await handleSourceListClick(event);
    } catch (e) {
      showToast("error", e.message);
    }
  });

  qs("episodesList").addEventListener("click", async (event) => {
    const btn = event.target.closest(".delete-episode");
    if (!btn) return;

    const episodeId = Number(btn.dataset.episodeId || "0");
    if (!episodeId) return;

    if (!confirm(`确认删除播客 #${episodeId}？`)) return;

    setButtonLoading(btn, true);
    try {
      await request(`/api/episodes/${episodeId}`, { method: "DELETE" });
      showToast("success", `已删除播客 #${episodeId}`);
      await loadEpisodes();
    } catch (e) {
      showToast("error", e.message);
    } finally {
      setButtonLoading(btn, false);
    }
  });

  qs("clearEpisodesBtn").onclick = async () => {
    const btn = qs("clearEpisodesBtn");
    if (!confirm("确认清空全部播客历史？该操作会删除数据库记录与对应音频/笔记文件。")) return;

    setButtonLoading(btn, true);
    try {
      const res = await request("/api/episodes", { method: "DELETE" });
      showToast("success", `清空完成，已删除 ${res.deleted ?? 0} 条记录`);
      await loadEpisodes();
    } catch (e) {
      showToast("error", e.message);
    } finally {
      setButtonLoading(btn, false);
    }
  };

  try {
    await loadAll();
    setTestStatus("llmTestStatus", true, "未测试");
    setTestStatus("ttsTestStatus", true, "未测试");
    setEdgeVoicePreviewStatus(true, "可试听当前选择音色");
    checkEdgeTtsUpdate(false).catch((e) => setEdgeTtsUpdateStatus(false, e.message));
  } catch (e) {
    showToast("error", e.message);
  }
}

init();
