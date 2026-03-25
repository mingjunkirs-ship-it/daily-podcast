const qs = (id) => document.getElementById(id);

let cachedPresets = [];
let cachedRssHubTemplates = [];
let cachedPromptVersions = [];
let cachedEdgeVoiceLanguages = [];
let episodesPollTimer = null;

const fieldsGeneral = [
  "language",
  "timezone",
  "schedule_cron",
  "topic_keywords",
  "max_items_per_source",
  "max_total_items",
  "podcast_name",
  "podcast_host_style",
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
  "tts_edge_proxy",
  "tts_edge_connect_timeout",
  "tts_edge_receive_timeout",
  "tts_format",
  "telegram_enabled",
  "telegram_bot_token",
  "telegram_chat_id",
  "telegram_send_audio",
  "newsapi_global_key",
  "rsshub_base_url",
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
      { name: "zh-CN-XiaoxiaoNeural", label: "zh-CN-XiaoxiaoNeural" },
      { name: "zh-CN-YunxiNeural", label: "zh-CN-YunxiNeural" },
      { name: "zh-CN-YunjianNeural", label: "zh-CN-YunjianNeural" },
      { name: "zh-CN-XiaoyiNeural", label: "zh-CN-XiaoyiNeural" },
    ],
  },
  {
    code: "en-US",
    name: "English (US)",
    voices: [
      { name: "en-US-AriaNeural", label: "en-US-AriaNeural" },
      { name: "en-US-JennyNeural", label: "en-US-JennyNeural" },
      { name: "en-US-GuyNeural", label: "en-US-GuyNeural" },
    ],
  },
  {
    code: "ja-JP",
    name: "日本語",
    voices: [
      { name: "ja-JP-NanamiNeural", label: "ja-JP-NanamiNeural" },
      { name: "ja-JP-KeitaNeural", label: "ja-JP-KeitaNeural" },
    ],
  },
];

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
      await loadAll();
    } catch {
      // ignore polling error, user can refresh manually
    }
  }, 2500);
}

function sourceItemHTML(source) {
  const config = source.config || {};
  const isRss = source.source_type === "rss";
  const lastError = source.last_error || "";
  return `
    <div class="item" data-id="${source.id}" data-source-type="${source.source_type}">
      <div class="row">
        <div class="col"><strong>${source.name}</strong> <span class="small">#${source.id}</span></div>
        <div class="col small">
          <span class="pill">${source.source_type}</span>
          <span class="pill ${source.enabled ? "ok" : "fail"}">${source.enabled ? "enabled" : "disabled"}</span>
        </div>
      </div>
      <div class="small">last_sync=${source.last_sync_at || "-"}</div>
      <div class="small ${lastError ? "fail" : ""}">last_error=${lastError || "-"}</div>
      ${
        isRss
          ? `<div class="row"><div class="col"><label>RSS URL</label><input class="rss-url-input" value="${config.url || ""}" /></div></div>`
          : `<div class="row"><div class="col"><label>配置 JSON</label><textarea class="source-config">${JSON.stringify(config, null, 2)}</textarea></div></div>`
      }
      <div class="row">
        <button class="save-source">保存来源配置</button>
        <button class="test-source">测试来源</button>
        <button class="toggle-source">切换启用</button>
        <button class="warn delete-source">删除</button>
        <a target="_blank" href="/rss/sources/${source.id}.xml">查看转换后 RSS</a>
      </div>
      <div class="small source-test-result" style="min-height:18px;"></div>
    </div>
  `;
}

function episodeItemHTML(episode) {
  const payload = parseEpisodePayload(episode.payload_json);
  const progress = payload.progress || {};
  const progressPercent = Math.max(
    0,
    Math.min(100, Number(progress.percent ?? (episode.status === "completed" ? 100 : 0))),
  );
  const progressStage = progress.stage || "-";
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
  return `
    <div class="item">
      <div><strong>${episode.title || "(未命名)"}</strong> <span class="small">#${episode.id} ${episode.status}</span></div>
      <div class="small">created=${episode.created_at} | completed=${episode.completed_at || "-"}</div>
      <div class="small">progress=${progressPercent}% | stage=${progressStage}</div>
      <div class="progress-track"><div class="progress-bar" style="width:${progressPercent}%;"></div></div>
      ${progressMessage ? `<div class="small">${progressMessage}</div>` : ""}
      ${showOverview ? `<div>${overviewText}</div>` : ""}
      <div class="small">item_count=${episode.item_count}</div>
      ${
        failedSources.length
          ? `<div class="small fail">来源失败：${failedSources
              .map((row) => `${row.name || "未知来源"}(${row.error || "unknown"})`)
              .join(" | ")}</div>`
          : ""
      }
      ${audioLink ? `<audio controls src="${audioLink}" style="margin-top:8px; width: 100%;"></audio>` : ""}
      <div class="row" style="margin-top:8px;">
        ${audioLink ? `<a target="_blank" href="${audioLink}">下载音频</a>` : `<span class="small">未生成音频</span>`}
        ${notesLink ? `<a target="_blank" href="${notesLink}">查看材料笔记</a>` : ""}
      </div>
      ${showError ? `<div class="small fail">error=${errorMessage}</div>` : ""}
    </div>
  `;
}

function presetItemHTML(preset) {
  return `
    <label style="display:inline-flex;align-items:center;gap:6px;margin-right:14px;margin-bottom:6px;">
      <input type="checkbox" class="preset-checkbox" value="${preset.preset_id}" checked />
      <span>${preset.name} (${preset.source_type}) - ${preset.description}</span>
    </label>
  `;
}

function renderRssHubTemplateOptions(templates) {
  const select = qs("rsshubTemplateSelect");
  if (!select) return;
  const options = [
    `<option value="">选择 RSSHub 模板（可选）</option>`,
    ...templates.map(
      (item) => `<option value="${item.route}">${item.title} - ${item.route}</option>`,
    ),
  ];
  select.innerHTML = options.join("");
}

async function loadAll() {
  const me = await request("/api/auth/me");
  qs("currentUser").textContent = `当前用户：${me.username}`;

  const settings = await request("/api/settings");
  fillSettings(settings.values || {});
  await loadEdgeVoices();

  cachedPresets = await request("/api/source-presets");
  qs("presetsList").innerHTML = cachedPresets.map(presetItemHTML).join("");

  await loadPromptVersions();

  cachedRssHubTemplates = await request("/api/rsshub/templates");
  renderRssHubTemplateOptions(cachedRssHubTemplates);

  const sources = await request("/api/sources");
  qs("sourcesList").innerHTML = sources.map(sourceItemHTML).join("");

  const episodes = await request("/api/episodes");
  qs("episodesList").innerHTML = episodes.map(episodeItemHTML).join("");
  scheduleEpisodePolling(episodes);
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

async function addRssSource() {
  const name = qs("new_rss_name").value.trim();
  const url = qs("new_rss_url").value.trim();
  const enabled = qs("new_rss_enabled").value === "true";

  if (!url) {
    alert("请填写 RSS URL");
    return;
  }

  await request("/api/sources/rss", {
    method: "POST",
    body: JSON.stringify({ name: name || null, url, enabled }),
  });

  qs("new_rss_name").value = "";
  qs("new_rss_url").value = "";
  await loadAll();
}

async function addRssHubSource() {
  await saveSettings(["rsshub_base_url"]);

  const name = qs("new_rsshub_name").value.trim();
  const route = qs("new_rsshub_route").value.trim() || (qs("rsshubTemplateSelect")?.value || "").trim();
  const enabled = qs("new_rsshub_enabled").value === "true";

  if (!route) {
    alert("请填写 RSSHub 路由");
    return;
  }

  await request("/api/sources/rsshub", {
    method: "POST",
    body: JSON.stringify({ name: name || null, route, enabled }),
  });

  qs("new_rsshub_name").value = "";
  qs("new_rsshub_route").value = "";
  await loadAll();
}

function applySelectedRssHubTemplate() {
  const selectedRoute = (qs("rsshubTemplateSelect")?.value || "").trim();
  if (!selectedRoute) return;
  qs("new_rsshub_route").value = selectedRoute;

  const selected = cachedRssHubTemplates.find((item) => item.route === selectedRoute);
  if (selected && !qs("new_rsshub_name").value.trim()) {
    qs("new_rsshub_name").value = selected.title;
  }
}

async function handleSourceListClick(event) {
  const item = event.target.closest(".item");
  if (!item) return;
  const id = Number(item.dataset.id);
  if (!id) return;

  if (event.target.classList.contains("delete-source")) {
    if (!confirm(`确认删除来源 #${id}？`)) return;
    await request(`/api/sources/${id}`, { method: "DELETE" });
    await loadAll();
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
    await loadAll();
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
    await loadAll();
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
        alert("RSS URL 不能为空");
        return;
      }
      config = { ...config, url: rssUrl };
    } else {
      const textarea = item.querySelector(".source-config");
      try {
        config = JSON.parse(textarea.value || "{}");
      } catch (e) {
        alert(`配置 JSON 无效: ${e.message}`);
        return;
      }
    }

    await request(`/api/sources/${id}`, {
      method: "PUT",
      body: JSON.stringify({ config }),
    });
    await loadAll();
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

async function init() {
  qs("refreshBtn").onclick = async () => {
    try {
      await loadAll();
    } catch (e) {
      alert(e.message);
    }
  };

  qs("runNowBtn").onclick = async () => {
    try {
      const res = await request("/api/run-now", { method: "POST" });
      alert(res.message || "任务已触发");
      await loadAll();
    } catch (e) {
      alert(e.message);
    }
  };

  qs("logoutBtn").onclick = async () => {
    try {
      await request("/api/auth/logout", { method: "POST" });
    } finally {
      window.location.href = "/login";
    }
  };

  qs("rebuildFeedsBtn").onclick = async () => {
    try {
      const res = await request("/api/rebuild-feeds", { method: "POST" });
      alert(`重建完成，来源数: ${res.sources}`);
      await loadAll();
    } catch (e) {
      alert(e.message);
    }
  };

  qs("saveGeneralBtn").onclick = async () => {
    try {
      await saveSettings(fieldsGeneral);
      alert("全局设置已保存");
    } catch (e) {
      alert(e.message);
    }
  };

  qs("saveApiBtn").onclick = async () => {
    try {
      await saveSettings(fieldsAPI);
      alert("API 设置已保存");
    } catch (e) {
      alert(e.message);
    }
  };

  qs("testLlmBtn").onclick = async () => {
    try {
      await testLlm();
    } catch (e) {
      setTestStatus("llmTestStatus", false, e.message);
    }
  };

  qs("testTtsBtn").onclick = async () => {
    try {
      await testTts();
    } catch (e) {
      setTestStatus("ttsTestStatus", false, e.message);
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
      try {
        await loadEdgeVoices(true);
        setTestStatus("ttsTestStatus", true, "Edge 音色已刷新");
      } catch (e) {
        setTestStatus("ttsTestStatus", false, e.message);
      }
    };
  }

  qs("savePromptVersionBtn").onclick = async () => {
    try {
      await savePromptVersion();
    } catch (e) {
      setPromptVersionStatus(false, e.message);
    }
  };

  qs("applyPromptVersionBtn").onclick = async () => {
    try {
      await applyPromptVersion();
    } catch (e) {
      setPromptVersionStatus(false, e.message);
    }
  };

  qs("deletePromptVersionBtn").onclick = async () => {
    try {
      await deletePromptVersion();
    } catch (e) {
      setPromptVersionStatus(false, e.message);
    }
  };

  qs("changePasswordBtn").onclick = async () => {
    try {
      const current_password = qs("current_password").value;
      const new_password = qs("new_password").value;
      if (!current_password || !new_password) {
        alert("请填写当前密码和新密码");
        return;
      }
      await request("/api/auth/change-password", {
        method: "POST",
        body: JSON.stringify({ current_password, new_password }),
      });
      qs("current_password").value = "";
      qs("new_password").value = "";
      alert("密码修改成功");
    } catch (e) {
      alert(e.message);
    }
  };

  qs("addRssBtn").onclick = async () => {
    try {
      await addRssSource();
      alert("RSS 来源已新增");
    } catch (e) {
      alert(e.message);
    }
  };

  qs("addRssHubBtn").onclick = async () => {
    try {
      await addRssHubSource();
      alert("RSSHub 来源已新增");
    } catch (e) {
      alert(e.message);
    }
  };

  qs("applyRssHubTemplateBtn").onclick = () => {
    applySelectedRssHubTemplate();
  };

  qs("rsshubTemplateSelect").onchange = () => {
    applySelectedRssHubTemplate();
  };

  qs("importPresetsBtn").onclick = async () => {
    try {
      const checked = Array.from(document.querySelectorAll(".preset-checkbox:checked")).map((node) => node.value);
      const overwrite_existing = qs("overwritePresets").checked;
      const payload = {
        preset_ids: checked.length ? checked : cachedPresets.map((item) => item.preset_id),
        overwrite_existing,
      };
      const res = await request("/api/sources/import-defaults", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      alert(`导入完成: selected=${res.selected}, created=${res.created}, updated=${res.updated}, skipped=${res.skipped}`);
      await loadAll();
    } catch (e) {
      alert(e.message);
    }
  };

  qs("sourcesList").addEventListener("click", async (event) => {
    try {
      await handleSourceListClick(event);
    } catch (e) {
      alert(e.message);
    }
  });

  try {
    await loadAll();
    setTestStatus("llmTestStatus", true, "未测试");
    setTestStatus("ttsTestStatus", true, "未测试");
  } catch (e) {
    alert(e.message);
  }
}

init();
