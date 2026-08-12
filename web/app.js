import { apiRequest } from "/generated/client.js?v=20260812-3";
import {
  AICard,
  AssetCard,
  HoldingHealthCard,
  MarketCard,
  RiskCard,
  StockCard,
  TaskCard,
} from "/components/dashboard/dashboard-components.js?v=20260812-1";

const state = {
  daily: null,
  status: null,
  workbench: null,
  equityPoints: [],
  paperAuditView: "open_orders",
  reviewActivityKind: "orders",
  quotePollingActive: false,
  quoteRequestSequence: 0,
  currentQuote: null,
  paperOrderPreview: null,
  brokerMatchTimer: null,
  brokerMatchInFlight: false,
  activeWorkspace: "dashboard",
  currentRanking: null,
  selectedCandidateSymbol: null,
  rankingQuery: "",
  rankingFilter: "all",
  rankingView: "cards",
  liveSyncEnabled: true,
  liveSyncIntervalSeconds: 15,
  liveSyncCountdownTimer: null,
  candidateAutoEnabled: true,
  candidateAutoIntervalSeconds: 300,
  candidateAutoNextAt: 0,
  candidateRefreshInFlight: false,
  liveModules: {},
  syncJobs: {},
  networkOnline: navigator.onLine !== false,
  investmentOS: {
    summary: null,
    reports: [],
    notifications: [],
    reportFilter: "all",
    notificationFilter: "all",
    selectedReport: null,
    inFlight: false,
  },
  copilot: { status: null, reports: [], memories: [], selectedReport: null, inFlight: false },
  strategyLab: { dashboard: null, selectedReview: null, selectedPromotion: null, inFlight: false },
  aiResearch: { dashboard: null, selectedReport: null, selectedAgent: "strategy_analyst", inFlight: false },
  personalOS: { dashboard: null, inFlight: false },
  investmentDashboard: { snapshot: null, inFlight: false },
  dataIntelligence: { dashboard: null, inFlight: false, actionInFlight: false },
  strategyEvolution: {
    dashboard: null, inFlight: false, actionInFlight: false, selectedBranchKey: null,
  },
  engineering: { dashboard: null, inFlight: false, actionInFlight: false },
  observability: { dashboard: null, inFlight: false, actionInFlight: false },
  personalAssistant: { intent: "daily_attention", inFlight: false, answer: null },
  investmentReview: { dashboard: null, draft: null, journalId: null, inFlight: false },
};

const byId = (id) => document.getElementById(id);

function installPersonalWorkspaceTab() {
  const nav = document.querySelector(".workspace-nav");
  if (!nav || nav.querySelector('[data-workspace="personalos"]')) return;
  const tab = document.createElement("button");
  tab.className = "workspace-tab";
  tab.type = "button";
  tab.setAttribute("role", "tab");
  tab.dataset.workspace = "personalos";
  tab.setAttribute("aria-controls", "workspace-personalos");
  tab.setAttribute("aria-selected", "false");
  const index = document.createElement("span");
  index.textContent = "11";
  tab.append(index, document.createTextNode("总控"));
  nav.append(tab);
}

function installOperatingWorkspaceTab() {
  const nav = document.querySelector(".workspace-nav");
  if (!nav || nav.querySelector('[data-workspace="overview"]')) return;
  const tab = document.createElement("button");
  tab.className = "workspace-tab";
  tab.type = "button";
  tab.setAttribute("role", "tab");
  tab.dataset.workspace = "overview";
  tab.setAttribute("aria-controls", "workspace-overview");
  tab.setAttribute("aria-selected", "false");
  const index = document.createElement("span");
  index.textContent = "12";
  tab.append(index, document.createTextNode("运营"));
  nav.append(tab);
}
const money = (value) => new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", maximumFractionDigits: 2 }).format(Number(value || 0));
const fractionPct = (value) => `${(Number(value || 0) * 100).toFixed(2)}%`;
const time = (value) => value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—";
function valuationBadgeLabel(value) {
  if (!value) return "估值时间未知";
  const observed = new Date(value);
  if (Number.isNaN(observed.getTime())) return "估值时间未知";
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).formatToParts(observed).map((part) => [part.type, part.value]),
  );
  return `估值 ${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
}
const text = (id, value) => { byId(id).textContent = value ?? "—"; };
const WORKSPACE_TITLES = {
  dashboard: "投资驾驶舱",
  personalos: "我的投资操作系统",
  overview: "今日总览",
  market: "股票研究",
  etf: "我的持仓",
  review: "投资复盘",
  settings: "设置",
  assistant: "AI投资助手",
  copilotadvanced: "专业AI报告与模型设置",
  strategy: "策略实验室",
  quantai: "AI量化研究",
  dataintel: "数据监控中心",
  evolution: "策略进化中心",
  engineering: "工程健康中心",
  observability: "可观测中心",
};

const FACTOR_LABELS = [
  ["value", "价值", 20], ["quality", "质量", 20], ["growth", "成长", 15],
  ["momentum", "动量", 20], ["trend", "趋势", 10], ["low_risk", "低风险", 10], ["liquidity", "流动性", 5],
];

const LIVE_MODULE_LABELS = {
  system: "系统与模型",
  research: "候选与计划",
  account: "模拟账户",
  review: "持仓复盘",
  operations: "投资运营",
  data: "数据健康",
  evolution: "策略健康",
  engineering: "工程健康",
  observability: "可观测",
  quote: "所选股票行情",
  candidates: "全市场候选重算",
  dashboard: "投资驾驶舱",
  personal: "个人投资闭环",
  journal: "日志与复盘",
};

const SYNC_JOB_CONFIG = {
  system: { operation: "get_status", moduleKeys: ["system"], timeoutMs: 15000 },
  daily: { operation: "get_daily_research", moduleKeys: ["research", "account"], timeoutMs: 20000 },
  review: { operation: "get_decision_workbench", moduleKeys: ["review"], timeoutMs: 20000 },
  operating: { operation: "get_investment_os", moduleKeys: ["operations"], timeoutMs: 20000 },
  data: { operation: "get_data_intelligence_dashboard", moduleKeys: ["data"], timeoutMs: 20000 },
  evolution: { operation: "get_strategy_evolution_center", moduleKeys: ["evolution"], timeoutMs: 20000 },
  engineering: { operation: "get_engineering_dashboard", moduleKeys: ["engineering"], timeoutMs: 20000 },
  observability: { operation: "get_observability_dashboard", moduleKeys: ["observability"], timeoutMs: 20000 },
  dashboard: { operation: "get_investment_dashboard_overview", moduleKeys: ["dashboard"], timeoutMs: 20000 },
  personal: { operation: "get_personal_investment_os", moduleKeys: ["personal"], timeoutMs: 30000 },
  reviewloop: { operation: "get_investment_review_loop", moduleKeys: ["journal"], timeoutMs: 20000 },
  quote: { operation: "get_live_market_quote", moduleKeys: ["quote"], timeoutMs: 15000 },
};

const CORE_SYNC_JOBS = [
  "system", "daily", "review", "operating", "dashboard", "personal",
  "reviewloop", "data", "evolution", "engineering", "observability",
];

state.syncJobs = Object.fromEntries(Object.keys(SYNC_JOB_CONFIG).map((key) => [key, {
  inFlight: false,
  promise: null,
  failures: 0,
  lastAttemptAt: 0,
  lastSuccessAt: 0,
  nextAt: 0,
}]));

// 轻量提示用于确认页面动作结果，避免用户只能从大段表格里猜测是否成功。
function showToast(title, message, tone = "info", duration = 4200) {
  const toast = document.createElement("div");
  toast.className = `toast ${tone}`;
  const copy = document.createElement("div");
  const heading = document.createElement("strong");
  const detail = document.createElement("small");
  const close = document.createElement("button");
  heading.textContent = title;
  detail.textContent = message;
  close.type = "button";
  close.setAttribute("aria-label", "关闭提示");
  close.textContent = "×";
  copy.append(heading, detail);
  toast.append(copy, close);
  close.addEventListener("click", () => toast.remove());
  byId("toastStack").append(toast);
  window.setTimeout(() => toast.remove(), duration);
}

// 长耗时研究动作使用明确的全屏反馈；仅遮挡当前页面，不改变后端任务状态。
function setActionOverlay(visible, title = "正在处理", message = "请稍候，完成后会自动刷新页面。") {
  text("actionOverlayTitle", title);
  text("actionOverlayMessage", message);
  byId("actionOverlay").classList.toggle("hidden", !visible);
}

// 工作区只管理前端视图；研究、模拟账户和安全状态仍由后端统一持有。
function activateWorkspace(name, { updateHash = true, scroll = true } = {}) {
  const workspace = Object.hasOwn(WORKSPACE_TITLES, name) ? name : "dashboard";
  state.activeWorkspace = workspace;
  document.querySelectorAll(".workspace-view").forEach((view) => {
    view.hidden = view.dataset.workspaceView !== workspace;
  });
  document.querySelectorAll(".workspace-tab").forEach((tab) => {
    const active = tab.dataset.workspace === workspace;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  document.title = `盘古·天机 · ${WORKSPACE_TITLES[workspace]}`;
  if (updateHash) history.replaceState(null, "", `#${workspace}`);
  if (scroll) window.scrollTo({ top: 0, behavior: "smooth" });

  // 隐藏画布没有可靠尺寸，进入持仓复盘后再按可见宽度重绘。
  if (workspace === "etf") {
    requestAnimationFrame(() => {
      drawEquity(state.equityPoints);
      drawRadar(state.workbench?.discipline?.dimensions || []);
    });
  }
  if (workspace === "market" && document.visibilityState === "visible") {
    startQuotePolling();
    startBrokerMatching();
  } else {
    stopQuotePolling();
    stopBrokerMatching();
  }
  if (workspace === "overview") void loadInvestmentOperatingCenter({ quiet: true });
  if (workspace === "dashboard") void loadInvestmentDashboard({ quiet: true });
  if (workspace === "personalos") void loadPersonalInvestmentOS({ quiet: true });
  if (workspace === "review") void loadInvestmentReviewLoop({ quiet: true });
  if (workspace === "settings") void loadSettingsSummary({ quiet: true });
  if (workspace === "copilotadvanced") void loadCopilotWorkspace({ quiet: true });
  if (workspace === "strategy") void loadStrategyLab({ quiet: true });
  if (workspace === "quantai") void loadAIResearchCenter({ quiet: true });
  if (workspace === "dataintel") void loadDataIntelligence({ quiet: true });
  if (workspace === "evolution") void loadStrategyEvolution({ quiet: true });
  if (workspace === "engineering") void loadEngineeringCenter({ quiet: true });
  if (workspace === "observability") void loadObservabilityCenter({ quiet: true });
}

function badge(label, tone = "muted") {
  const node = document.createElement("span");
  node.className = `badge badge-${tone}`;
  node.textContent = label;
  return node;
}

function showError(message) {
  text("serviceAlert", message);
  byId("serviceAlert").classList.remove("hidden");
}

function clearError() {
  byId("serviceAlert").classList.add("hidden");
}

function clockLabel(value = Date.now()) {
  return new Date(value).toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function liveModuleStaleAfterMs(key) {
  if (key === "quote") return Math.max(15000, Number(state.daily?.quote_refresh_seconds || 5) * 3000);
  if (key === "candidates") return Math.max(600000, state.candidateAutoIntervalSeconds * 2000);
  return Math.max(30000, state.liveSyncIntervalSeconds * 3000);
}

// 模块卡片保存最后成功时间并持续计算新鲜度，避免旧数据长期显示为绿色正常。
function markLiveModule(key, tone, detail, observedAt = Date.now()) {
  state.liveModules[key] = { tone, detail, observedAt, staleAfterMs: liveModuleStaleAfterMs(key) };
  renderLiveModule(key);
}

function renderLiveModule(key, now = Date.now()) {
  const snapshot = state.liveModules[key];
  const node = document.querySelector(`[data-live-module="${key}"]`);
  if (!node || !snapshot) return;
  const ageSeconds = Math.max(0, Math.floor((now - snapshot.observedAt) / 1000));
  const stale = snapshot.tone === "ok" && now - snapshot.observedAt > snapshot.staleAfterMs;
  node.classList.remove("ok", "busy", "error", "paused", "stale");
  node.classList.add(stale ? "stale" : snapshot.tone);
  const label = LIVE_MODULE_LABELS[key] || key;
  node.querySelector("strong").textContent = label;
  node.querySelector("small").textContent = stale
    ? `${snapshot.detail} · 已${ageSeconds}秒未更新`
    : snapshot.detail;
  const refreshButton = node.querySelector("[data-live-refresh]");
  if (refreshButton) refreshButton.disabled = snapshot.tone === "busy";
}

function refreshLiveModuleAges() {
  const now = Date.now();
  Object.keys(state.liveModules).forEach((key) => renderLiveModule(key, now));
}

function isMarketMonitoringWindow(now = new Date()) {
  const weekday = now.getDay();
  if (weekday === 0 || weekday === 6) return false;
  const minute = now.getHours() * 60 + now.getMinutes();
  return (minute >= 575 && minute <= 690) || (minute >= 780 && minute <= 895);
}

function secondsUntil(timestamp) {
  return Math.max(0, Math.ceil((timestamp - Date.now()) / 1000));
}

function syncJobIntervalMs(jobKey) {
  if (jobKey === "quote") return Math.max(3000, Math.min(60000, Number(state.daily?.quote_refresh_seconds || 5) * 1000));
  return state.liveSyncIntervalSeconds * 1000;
}

function scheduleJob(jobKey, succeeded) {
  const job = state.syncJobs[jobKey];
  const base = syncJobIntervalMs(jobKey);
  const retry = succeeded ? base : Math.min(120000, base * (2 ** Math.min(job.failures, 4)));
  job.nextAt = Date.now() + retry;
}

function markJobModules(jobKey, tone, detail) {
  SYNC_JOB_CONFIG[jobKey].moduleKeys.forEach((key) => markLiveModule(key, tone, detail));
}

function applySyncPayload(jobKey, payload) {
  if (jobKey === "system") {
    renderStatus(payload);
    markLiveModule("system", "ok", `安全与模型 · ${clockLabel()}`);
    return;
  }
  if (jobKey === "daily") {
    renderDaily(payload);
    const rankingDate = payload?.latest_preview?.research_date || payload?.latest_research?.research_date || "暂无榜单";
    markLiveModule("research", "ok", `${rankingDate} · ${clockLabel()}`);
    markLiveModule("account", "ok", `${payload?.account?.positions?.length || 0}只持仓 · ${clockLabel()}`);
    return;
  }
  if (jobKey === "review") {
    renderWorkbench(payload);
    markLiveModule("review", "ok", `${payload?.positions?.length || 0}只持仓 · ${clockLabel()}`);
    return;
  }
  if (jobKey === "dashboard") {
    renderInvestmentDashboard(payload);
    markLiveModule("dashboard", "ok", `${payload?.holding_health?.length || 0}只持仓 · ${clockLabel()}`);
    return;
  }
  if (jobKey === "personal") {
    renderPersonalInvestmentOS(payload);
    markLiveModule("personal", "ok", `${payload?.portfolio?.positions?.length || 0}只持仓 · ${clockLabel()}`);
    return;
  }
  if (jobKey === "reviewloop") {
    renderInvestmentReviewLoop(payload);
    markLiveModule("journal", "ok", `${payload?.counts?.pending_reviews || 0}条待复盘 · ${clockLabel()}`);
    return;
  }
  if (jobKey === "operating") {
    renderInvestmentOperatingSummary(payload);
    const unread = Number(payload?.counts?.unread_notification_count ?? payload?.unread_notification_count ?? 0);
    markLiveModule("operations", "ok", `${unread}条未读 · ${clockLabel()}`);
    return;
  }
  if (jobKey === "data") {
    renderDataIntelligence(payload);
    const health = payload?.health || {};
    markLiveModule("data", health.status === "NORMAL" ? "ok" : health.status === "NOT_EVALUATED" ? "paused" : "error", `${health.status || "等待评估"} · ${health.score ?? "—"}分 · ${clockLabel()}`);
    return;
  }
  if (jobKey === "evolution") {
    renderStrategyEvolution(payload);
    const latest = payload?.health_history?.[0] || {};
    const tone = ["HEALTHY", "WATCH"].includes(latest.status) ? "ok" : latest.status ? "error" : "paused";
    markLiveModule("evolution", tone, `${latest.status || "等待观察"} · ${latest.score ?? "—"}分 · ${clockLabel()}`);
    return;
  }
  if (jobKey === "engineering") {
    renderEngineeringCenter(payload);
    const health = payload?.health || {};
    const tone = health.status === "HEALTHY" ? "ok" : health.status === "NOT_EVALUATED" ? "paused" : "error";
    markLiveModule("engineering", tone, `${health.status || "等待检查"} · ${health.score ?? "—"}分 · ${clockLabel()}`);
    return;
  }
  if (jobKey === "observability") {
    renderObservabilityCenter(payload);
    const tone = payload.status === "OBSERVING" ? "ok" : payload.status === "ALERTING" ? "error" : "paused";
    markLiveModule("observability", tone, `${payload.status || "等待观测"} · ${payload.counts?.active_alert_count || 0}条活动告警 · ${clockLabel()}`);
  }
}

// 每个读取模块拥有独立锁、超时和失败退避；慢模块不会阻塞其他模块更新。
async function syncModuleJob(jobKey, { source = "auto", quiet = true } = {}) {
  const job = state.syncJobs[jobKey];
  const config = SYNC_JOB_CONFIG[jobKey];
  if (job.inFlight) return job.promise;
  if (jobKey === "quote") return syncQuoteJob({ source, quiet });

  job.inFlight = true;
  job.lastAttemptAt = Date.now();
  markJobModules(jobKey, "busy", "正在独立同步");
  job.promise = (async () => {
    try {
      const payload = await apiRequest(config.operation, { timeoutMs: config.timeoutMs });
      applySyncPayload(jobKey, payload);
      job.failures = 0;
      job.lastSuccessAt = Date.now();
      scheduleJob(jobKey, true);
      text("lastSync", `${source === "auto" ? "实时" : "同步"} ${clockLabel(job.lastSuccessAt)}`);
      return { ok: true, jobKey };
    } catch (error) {
      job.failures += 1;
      scheduleJob(jobKey, false);
      const retrySeconds = secondsUntil(job.nextAt);
      markJobModules(jobKey, "error", `同步失败 · ${retrySeconds}秒后重试`);
      return { ok: false, jobKey, error };
    } finally {
      job.inFlight = false;
      job.promise = null;
      renderLiveSyncCenter();
    }
  })();
  return job.promise;
}

// 倒计时明确区分轻量状态轮询和全市场重算，避免把二者混成一种“实时行情”。
function renderLiveSyncCenter() {
  const active = state.liveSyncEnabled && document.visibilityState === "visible";
  const pulse = byId("liveSyncPulse");
  const quick = byId("liveSyncQuickButton");
  pulse.classList.toggle("paused", !active);
  quick.classList.toggle("paused", !active);
  quick.setAttribute("aria-pressed", String(state.liveSyncEnabled));
  text("liveSyncQuickLabel", state.liveSyncEnabled ? "实时同步" : "同步已暂停");
  byId("liveSyncEnabled").checked = state.liveSyncEnabled;
  byId("candidateAutoEnabled").checked = state.candidateAutoEnabled;

  if (!state.liveSyncEnabled) {
    text("liveSyncSummary", "自动更新已暂停，可随时恢复");
    text("liveSyncCountdown", "手动刷新仍可用");
    return;
  }
  if (document.visibilityState !== "visible") {
    text("liveSyncSummary", "页面在后台，已暂停网络轮询");
    text("liveSyncCountdown", "返回页面后立即同步");
    return;
  }

  const coreJobs = CORE_SYNC_JOBS.map((key) => state.syncJobs[key]);
  const coreDue = Math.min(...coreJobs.map((job) => secondsUntil(job.nextAt)));
  const runningCount = coreJobs.filter((job) => job.inFlight).length + Number(state.syncJobs.quote.inFlight) + Number(state.candidateRefreshInFlight);
  const retryCount = coreJobs.filter((job) => job.failures > 0).length;
  const candidateText = !state.candidateAutoEnabled
    ? "候选重算关闭"
    : !state.networkOnline
      ? "候选等待联网"
    : isMarketMonitoringWindow()
      ? `候选 ${Math.ceil(secondsUntil(state.candidateAutoNextAt) / 60)} 分钟`
      : "候选等待交易时段";
  text("liveSyncSummary", `${CORE_SYNC_JOBS.length}条独立通道 · 每${state.liveSyncIntervalSeconds}秒 · ${candidateText}`);
  text("liveSyncCountdown", runningCount ? `${runningCount}个模块更新中` : retryCount ? `${retryCount}个模块等待重试` : `${coreDue}秒后同步`);
}

function stopLiveSync() {
  if (state.liveSyncCountdownTimer) window.clearInterval(state.liveSyncCountdownTimer);
  state.liveSyncCountdownTimer = null;
  renderLiveSyncCenter();
}

function tickCandidateAutoRefresh() {
  if (!state.liveSyncEnabled || !state.candidateAutoEnabled || document.visibilityState !== "visible") return;
  if (!state.networkOnline) {
    if (!state.candidateRefreshInFlight) markLiveModule("candidates", "paused", "等待网络恢复 · 只重算不成交");
    return;
  }
  if (!isMarketMonitoringWindow()) {
    if (!state.candidateRefreshInFlight) markLiveModule("candidates", "paused", "等待交易时段 · 只重算不成交");
    return;
  }
  if (!state.candidateAutoNextAt) state.candidateAutoNextAt = Date.now() + state.candidateAutoIntervalSeconds * 1000;
  if (Date.now() >= state.candidateAutoNextAt && !state.candidateRefreshInFlight) void refreshCandidatesAutomatically();
}

function tickLiveScheduler() {
  refreshLiveModuleAges();
  if (!state.liveSyncEnabled || document.visibilityState !== "visible") {
    renderLiveSyncCenter();
    return;
  }
  CORE_SYNC_JOBS.forEach((jobKey) => {
    if (Date.now() >= state.syncJobs[jobKey].nextAt) void syncModuleJob(jobKey);
  });
  if (state.quotePollingActive && state.networkOnline && Date.now() >= state.syncJobs.quote.nextAt) {
    void syncModuleJob("quote");
  }
  tickCandidateAutoRefresh();
  renderLiveSyncCenter();
}

function startLiveSync({ immediate = false } = {}) {
  stopLiveSync();
  if (!state.liveSyncEnabled || document.visibilityState !== "visible") return;
  if (immediate) CORE_SYNC_JOBS.forEach((key) => { state.syncJobs[key].nextAt = 0; });
  if (!state.candidateAutoNextAt) state.candidateAutoNextAt = Date.now() + state.candidateAutoIntervalSeconds * 1000;
  state.liveSyncCountdownTimer = window.setInterval(tickLiveScheduler, 1000);
  tickLiveScheduler();
}

function renderModelStatus(model) {
  const configured = Boolean(model?.api_key_configured);
  const connected = model?.state === "connected";
  const labels = { not_configured: "未配置", checking: "检测中", connected: "已连接", error: "连接失败" };
  text("modelValue", labels[model?.state] || "未知");
  text("modelDetail", model?.message || "只读解释");
  text("modelState", configured ? `${model.model || "DeepSeek"} · ${model.message}` : "尚未配置 DeepSeek；请在下方输入 API Key。");
  byId("modelTestButton").disabled = !configured;
  byId("modelClearButton").disabled = !configured;
  document.querySelectorAll("[data-copilot-report]").forEach((button) => {
    button.disabled = !connected || !state.copilot?.status?.evidence_ready;
  });
  if (model?.model) byId("deepseekModel").value = model.model;
}

function renderStatus(payload) {
  state.status = payload;
  const { safety } = payload;
  const banner = byId("safetyBanner");
  banner.classList.toggle("danger", !safety.safe || safety.kill_switch);
  byId("safetyBadges").replaceChildren(
    badge("PAPER 自动化", "success"),
    badge("LIVE 关闭", "muted"),
    badge("can_submit_orders=false", "muted"),
    badge(safety.kill_switch ? "安全停止" : "研究可运行", safety.kill_switch ? "danger" : "info"),
  );
  if (!safety.safe) {
    text("safetyTitle", "配置不安全，系统已拒绝运行");
    text("safetyMessage", safety.message || "请恢复 paper 模式并关闭实盘开关。");
  } else if (safety.kill_switch) {
    text("safetyTitle", "共享安全停止已触发");
    text("safetyMessage", "新的全市场模拟开仓已停止；持仓复盘保持只读，解除停止不会开启实盘。");
  } else {
    text("safetyTitle", "模拟交易安全边界已确认");
    text("safetyMessage", "全市场模拟与持仓复盘共用本地 MockBroker，不连接证券账户。");
  }
  renderModelStatus(payload.model);
  byId("killButton").disabled = !safety.safe;
  byId("killButton").textContent = safety.kill_switch ? "解除安全停止" : "触发安全停止";
}

// 首页把底层研究状态翻译成一个可执行的下一步，并与安全停止保持一致。
function renderGuidedAction(payload) {
  const plan = payload.latest_research;
  const preview = payload.preview_state === "current" ? payload.latest_preview : null;
  const account = payload.account;
  const button = byId("nextActionButton");
  const phase = byId("nextActionPhase");
  let action = "open-market";
  let title = "先刷新一份盘中候选榜";
  let message = "从全市场最新快照开始，生成只读候选排名；它不会自动买入，也不会覆盖收盘正式计划。";
  let label = "开始选股";
  let phaseLabel = "研究准备";
  let phaseTone = "warning";

  if (account.kill_switch) {
    action = "open-review";
    title = "安全停止已触发，先检查模拟账户";
    message = "新开仓已被阻止。你仍可查看持仓、订单与成交，确认风险后再决定是否解除停止。";
    label = "检查模拟账户";
    phaseLabel = "安全停止";
    phaseTone = "danger";
  } else if (payload.automation_execution_ready) {
    action = "focus-execute";
    title = "正式计划已进入模拟执行窗口";
    message = `当前有 ${plan?.targets?.length || 0} 只目标股票。进入工作台复核计划后，可手动执行本机模拟调仓。`;
    label = "复核并执行模拟计划";
    phaseLabel = "需要处理";
    phaseTone = "success";
  } else if (plan && ["awaiting_next_session", "awaiting_execution_window"].includes(payload.plan_state)) {
    action = "open-market";
    title = payload.plan_state === "awaiting_execution_window" ? "正式计划已就绪，等待 09:35" : "正式计划已就绪，等待下一交易日";
    message = `${plan.targets?.length || 0} 只股票已进入计划。当前只需复核候选证据，系统不会提前补发订单。`;
    label = "查看正式计划";
    phaseLabel = "等待执行";
    phaseTone = "info";
  } else if (plan && payload.plan_state === "expired") {
    action = "focus-preview";
    title = "上一份计划已过期，请重新研究";
    message = "过期计划不会补发。盘中可以先刷新预览，收盘后再生成下一份正式计划。";
    label = "刷新候选榜";
    phaseLabel = "计划过期";
    phaseTone = "warning";
  } else if (preview) {
    action = "open-market";
    title = "盘中候选榜已更新，可以开始筛选";
    message = `已从 ${preview.eligible_count || 0} 只合格股票中生成候选榜。点选股票即可查看评分、风险和模拟行情。`;
    label = "查看候选榜";
    phaseLabel = "盘中预览";
    phaseTone = "info";
  }

  text("nextActionTitle", title);
  text("nextActionMessage", message);
  text("nextActionPhase", phaseLabel);
  text("nextActionTime", `最近同步 ${new Date().toLocaleTimeString("zh-CN", { hour12: false })}`);
  phase.className = `badge badge-${phaseTone}`;
  button.textContent = label;
  button.dataset.action = action;

  const steps = [...document.querySelectorAll("[data-progress-step]")];
  steps.forEach((step) => step.classList.remove("active", "complete"));
  const hasRanking = Boolean(preview || plan);
  const hasExecutionEvidence = (payload.activity?.trades || []).length > 0;
  const completion = { research: hasRanking, plan: Boolean(plan), execute: hasExecutionEvidence, review: false };
  steps.forEach((step) => { if (completion[step.dataset.progressStep]) step.classList.add("complete"); });
  const activeStep = account.kill_switch || hasExecutionEvidence ? "review" : payload.automation_execution_ready ? "execute" : plan ? "plan" : "research";
  document.querySelector(`[data-progress-step="${activeStep}"]`)?.classList.add("active");
  text("controlGuidance", account.kill_switch
    ? "安全停止中：禁止新开仓，仍可查看行情和处理允许卖出的持仓。"
    : payload.automation_execution_ready
      ? "当前正式计划可执行：建议先核对目标，再点击执行有效计划。"
      : preview
        ? "当前展示盘中预览：可筛选、看行情和手动模拟，不能替代收盘计划。"
        : "尚无当前候选榜：先盘中刷新，收盘后再生成正式计划。");
}

function renderDaily(payload) {
  state.daily = payload;
  const plan = payload.latest_research;
  const preview = payload.preview_state === "current" ? payload.latest_preview : null;
  const ranking = preview || plan;
  const account = payload.account;
  const valuation = payload.asset_valuation;
  text("researchDate", ranking?.research_date || "暂无");
  text("marketCoverage", ranking ? `全市场 ${ranking.universe_count} · 过滤后 ${ranking.eligible_count}` : "等待全市场采集");
  const nextSessionPlan = payload.next_session_plan || {};
  const nextSessionSymbols = nextSessionPlan.symbols || plan?.targets || [];
  text("targetCount", nextSessionSymbols.length);
  text("nextPlan", nextSessionSymbols.length
    ? nextSessionSymbols.join(" · ")
    : "生成后仅下一交易日有效");
  text("paperEquity", money(valuation.equity));
  text("paperCash", `现金 ${money(valuation.cash)} · 市值 ${money(valuation.market_value)}`);
  text("deskEquity", money(valuation.equity));
  text("deskAvailableCash", money(valuation.available_cash));
  text("deskFrozenCash", money(valuation.frozen_cash));
  text("deskMarketValue", money(valuation.market_value));
  text("paperDrawdown", fractionPct(valuation.drawdown));
  text("automationState", payload.paper_automation_enabled ? "自动模拟已配置" : "自动模拟已关闭");
  text("schedule", `${payload.schedule.research} · ${payload.schedule.execute} · ${payload.schedule.monitor}`);
  text("lastAction", payload.last_error ? `失败：${payload.last_error}` : (payload.last_action || "暂无操作"));
  const targets = plan?.targets || [];
  text("marketPlanCount", nextSessionSymbols.length);
  text("marketPlanSymbols", nextSessionSymbols.length
    ? `${nextSessionSymbols.join(" · ")} · ${nextSessionPlan.message || "全部持仓已纳入次日检查"}`
    : "尚未生成次日目标。完成收盘研究后自动形成计划。");
  const planLabels = {execution_window_open: "执行窗口开放", awaiting_next_session: "等待下一交易日", awaiting_execution_window: "等待09:35", expired: "计划已过期", calendar_unavailable: "日历不可用", none: "等待计划"};
  text("marketPlanState", planLabels[payload.plan_state] || "计划不可执行");
  byId("marketPlanState").className = `badge ${payload.automation_execution_ready ? "badge-success" : "badge-muted"}`;
  text("overviewMarketSummary", plan
    ? `七维候选榜已生成，${targets.length} 只进入下一交易日模拟计划；${payload.production_backtest?.message || "回测状态未知"}。`
    : "尚未生成全市场研究，交易日收盘后采集并排名。");
  text("overviewMarketState", plan ? (planLabels[payload.plan_state] || "研究仅展示") : "等待收盘研究");
  byId("overviewMarketState").className = `badge ${payload.automation_execution_ready ? "badge-success" : "badge-muted"}`;
  byId("killButton").textContent = account.kill_switch ? "解除安全停止" : "触发安全停止";
  byId("collectButton").disabled = account.kill_switch;
  byId("previewButton").disabled = false;
  byId("executeButton").disabled = account.kill_switch || !payload.automation_execution_ready;
  const actualPositions = account.positions || [];
  const paperOrders = payload.activity?.orders || [];
  const paperTrades = payload.activity?.trades || [];
  text("runtimeValue", `${actualPositions.length}只`);
  text("runtimeDetail", `${paperOrders.length}笔订单 · ${paperTrades.length}笔成交`);
  text("backtestValue", actualPositions.length ? `${actualPositions.length}只持仓` : "当前空仓");
  text("backtestDetail", payload.activity?.nav?.[0]?.trade_date ? `最近净值 ${payload.activity.nav[0].trade_date}` : "不读取固定白名单");
  text("overviewEtfSummary", actualPositions.length
    ? `复盘模拟盘实际持有的 ${actualPositions.length} 只股票及全部成交证据。`
    : "模拟盘当前空仓；复盘页不会显示任何固定白名单股票。");
  renderGuidedAction(payload);
  renderRanking(ranking);
  renderManualCandidates(ranking);
  // The daily payload renders first; Workbench then replaces this table with
  // the canonical, quote-valued ledger snapshot in the same refresh cycle.
  renderPositions(account);
  renderPaperAudit();
  if (state.currentQuote) renderLiveQuote(state.currentQuote);
  if (state.status?.model) renderModelStatus(state.status.model);
}

// 候选列表只用于方便输入；用户仍可输入任一同花顺代码表中的沪深主板股票。
function renderManualCandidates(plan) {
  const candidates = plan?.candidates || [];
  const dataList = byId("manualCandidates");
  dataList.replaceChildren();
  for (const item of candidates) {
    const option = document.createElement("option");
    option.value = item.symbol;
    option.label = `${item.name} · 排名 ${item.rank}`;
    dataList.append(option);
  }
  const input = byId("manualSymbol");
  if (!/^\d{6}\.(SH|SZ)$/.test(input.value.trim().toUpperCase()) && candidates.length) {
    input.value = candidates[0].symbol;
    if (state.activeWorkspace === "market") void fetchLiveQuote({ quiet: true });
  }
}

function renderRanking(plan) {
  state.currentRanking = plan;
  const body = byId("rankingBody");
  const cards = byId("rankingCards");
  body.replaceChildren();
  cards.replaceChildren();
  const allCandidates = plan?.candidates || [];
  const targets = new Set(plan?.mode === "formal_close_plan" ? (plan.targets || []) : []);
  const query = state.rankingQuery.trim().toLowerCase();
  const candidates = allCandidates.filter((item) => {
    const riskFlags = item.risk_flags || [];
    const haystack = [item.symbol, item.name, item.reason, item.industry, ...riskFlags].join(" ").toLowerCase();
    const queryMatch = !query || haystack.includes(query);
    const filterMatch = state.rankingFilter === "all"
      || (state.rankingFilter === "target" && targets.has(item.symbol))
      || (state.rankingFilter === "clean" && riskFlags.length === 0)
      || (state.rankingFilter === "risk" && riskFlags.length > 0);
    return queryMatch && filterMatch;
  });

  if (!state.selectedCandidateSymbol || !candidates.some((item) => item.symbol === state.selectedCandidateSymbol)) {
    state.selectedCandidateSymbol = candidates[0]?.symbol || null;
  }

  for (const item of candidates) {
    const components = item.components || {};
    const selected = item.symbol === state.selectedCandidateSymbol;
    const riskFlags = item.risk_flags || [];
    const risk = riskFlags.join("；") || "无额外风险标签";

    const card = document.createElement("article");
    card.className = `candidate-card${selected ? " selected" : ""}${targets.has(item.symbol) ? " target" : ""}`;
    card.dataset.candidateSymbol = item.symbol;
    card.tabIndex = 0;
    card.setAttribute("role", "button");
    card.setAttribute("aria-label", `选择第${item.rank}名 ${item.name}`);
    const header = document.createElement("header");
    const rank = document.createElement("span");
    const identity = document.createElement("div");
    const identityName = document.createElement("strong");
    const identityCode = document.createElement("small");
    const score = document.createElement("div");
    const scoreValue = document.createElement("strong");
    const scoreLabel = document.createElement("small");
    rank.className = "candidate-rank";
    rank.textContent = String(item.rank);
    identity.className = "candidate-identity";
    identityName.textContent = item.name;
    identityCode.textContent = `${item.symbol}${item.industry ? ` · ${item.industry}` : ""}`;
    identity.append(identityName, identityCode);
    score.className = "candidate-score";
    scoreValue.textContent = Number(item.score).toFixed(1);
    scoreLabel.textContent = "综合分";
    score.append(scoreValue, scoreLabel);
    header.append(rank, identity, score);

    const rail = document.createElement("div");
    rail.className = "score-rail";
    for (const [key, label, maximum] of FACTOR_LABELS) {
      const bar = document.createElement("i");
      const ratio = Math.max(0.08, Math.min(1, Number(components[key] || 0) / maximum));
      bar.style.height = `${Math.round(ratio * 100)}%`;
      bar.title = `${label} ${Number(components[key] || 0).toFixed(1)} / ${maximum}`;
      rail.append(bar);
    }
    const reason = document.createElement("p");
    reason.className = "candidate-reason";
    reason.textContent = item.reason || "暂无推荐理由";
    const footer = document.createElement("footer");
    footer.className = "candidate-footer";
    const riskPill = document.createElement("span");
    riskPill.className = `risk-pill${riskFlags.length ? "" : " clean"}`;
    riskPill.textContent = riskFlags.length ? `${riskFlags.length}项风险提示 · ${riskFlags[0]}` : "✓ 无额外风险标签";
    const open = document.createElement("span");
    open.className = "candidate-open";
    open.textContent = selected ? "已选中" : "查看详情 →";
    footer.append(riskPill, open);
    card.append(header, rail, reason, footer);
    cards.append(card);

    const row = document.createElement("tr");
    row.dataset.candidateSymbol = item.symbol;
    if (targets.has(item.symbol)) row.classList.add("target-row");
    if (selected) row.classList.add("selected-row");
    const values = [
      item.rank, `${item.name} ${item.symbol}`, Number(item.score).toFixed(2),
      components.value, components.quality, components.growth, components.momentum,
      components.trend, components.low_risk, components.liquidity, `${item.reason}｜${risk}`,
    ];
    values.forEach((value) => { const cell = document.createElement("td"); cell.textContent = value ?? "—"; row.append(cell); });
    const actionCell = document.createElement("td");
    const quoteButton = document.createElement("button");
    quoteButton.type = "button";
    quoteButton.className = "button button-ghost quote-action-button";
    quoteButton.dataset.tradeSymbol = item.symbol;
    quoteButton.textContent = "带入模拟盘";
    actionCell.append(quoteButton);
    row.append(actionCell);
    body.append(row);
  }
  text("rankingCount", `${candidates.length} / ${allCandidates.length} 只`);
  byId("rankingEmpty").classList.toggle("hidden", candidates.length > 0);
  renderCandidateDetail(candidates.find((item) => item.symbol === state.selectedCandidateSymbol));
  const isPreview = plan?.mode === "intraday_preview";
  const ready = !isPreview && Boolean(state.daily?.automation_execution_ready);
  const observed = plan?.observed_at ? new Date(plan.observed_at).toLocaleTimeString("zh-CN", { hour12: false }) : "";
  text("researchState", !plan
    ? "暂无研究"
    : isPreview
      ? `盘中预览 ${observed} · 不用于交易`
      : ready
        ? "正式计划 · 执行窗口开放"
        : (state.daily?.plan_state === "expired" ? "正式榜单 · 计划已过期" : "正式收盘榜单"));
  byId("researchState").className = `badge ${ready ? "badge-success" : (isPreview ? "badge-warning" : "badge-muted")}`;
}

// 详情区只解释已生成的确定性候选证据，不重新评分也不触发订单。
function renderCandidateDetail(item) {
  const container = byId("candidateDetail");
  container.replaceChildren();
  if (!item) {
    const empty = document.createElement("div");
    empty.className = "candidate-detail-empty";
    empty.innerHTML = "<span>01</span><strong>没有匹配的候选股票</strong><p>清除搜索或切换筛选条件后再选择。</p>";
    container.append(empty);
    return;
  }
  const components = item.components || {};
  const header = document.createElement("div");
  header.className = "detail-header";
  const rank = document.createElement("span");
  rank.className = "candidate-rank";
  rank.textContent = String(item.rank);
  const title = document.createElement("div");
  title.className = "detail-title";
  const heading = document.createElement("h3");
  const subtitle = document.createElement("p");
  heading.textContent = item.name;
  subtitle.textContent = `${item.symbol}${item.industry ? ` · ${item.industry}` : ""}`;
  title.append(heading, subtitle);
  const total = document.createElement("div");
  total.className = "detail-total";
  const totalValue = document.createElement("strong");
  const totalLabel = document.createElement("small");
  totalValue.textContent = Number(item.score).toFixed(1);
  totalLabel.textContent = "综合得分 / 100";
  total.append(totalValue, totalLabel);
  header.append(rank, title, total);

  const factors = document.createElement("div");
  factors.className = "factor-list";
  for (const [key, label, maximum] of FACTOR_LABELS) {
    const row = document.createElement("div");
    row.className = "factor-row";
    const name = document.createElement("span");
    const track = document.createElement("div");
    const fill = document.createElement("div");
    const value = document.createElement("strong");
    name.textContent = label;
    track.className = "factor-track";
    fill.className = "factor-fill";
    fill.style.width = `${Math.max(0, Math.min(100, Number(components[key] || 0) / maximum * 100))}%`;
    track.append(fill);
    value.textContent = Number(components[key] || 0).toFixed(1);
    row.append(name, track, value);
    factors.append(row);
  }

  const evidence = document.createElement("div");
  evidence.className = "detail-evidence";
  const blocks = [
    ["推荐依据", item.reason || "暂无"],
    ["风险标签", (item.risk_flags || []).join("；") || "无额外风险标签"],
    ["失效条件", item.invalidation || "报价或研究数据失效"],
  ];
  blocks.forEach(([label, copy], index) => {
    const block = document.createElement("div");
    const strong = document.createElement("strong");
    const paragraph = document.createElement("p");
    strong.textContent = label;
    paragraph.textContent = copy;
    if (index > 0) paragraph.className = "risk-copy";
    block.append(strong, paragraph);
    evidence.append(block);
  });
  const actions = document.createElement("div");
  actions.className = "button-row candidate-detail-actions";
  const trade = document.createElement("button");
  trade.type = "button";
  trade.className = "button button-primary";
  trade.dataset.tradeSymbol = item.symbol;
  trade.textContent = "带入模拟交易票据";
  const observe = document.createElement("button");
  observe.type = "button";
  observe.className = "button button-ghost";
  observe.dataset.journalAction = "OBSERVE";
  observe.dataset.journalSymbol = item.symbol;
  observe.dataset.journalTitle = `观察 ${item.name || item.symbol}`;
  observe.dataset.journalReason = `当前排名 ${item.rank ?? "—"}，评分 ${item.score ?? "—"}。我的观察是：`;
  observe.textContent = "记录观察";
  const decision = document.createElement("button");
  decision.type = "button";
  decision.className = "button button-ghost";
  decision.dataset.journalAction = "DECISION";
  decision.dataset.journalSymbol = item.symbol;
  decision.dataset.journalTitle = `投资理由 ${item.name || item.symbol}`;
  decision.dataset.journalReason = "我做出这项投资判断的理由是：";
  decision.textContent = "记录投资理由";
  actions.append(trade, observe, decision);
  container.append(header, factors, evidence, actions);
}

// 候选选择只改变页面焦点；是否获取行情或提交模拟订单由用户后续动作决定。
function selectCandidate(symbol, { openTicket = false } = {}) {
  const candidate = state.currentRanking?.candidates?.find((item) => item.symbol === symbol);
  if (!candidate) return;
  state.selectedCandidateSymbol = symbol;
  renderRanking(state.currentRanking);
  if (openTicket) {
    byId("manualSymbol").value = symbol;
    clearPaperOrderPreview();
    byId("liveTradeTitle").scrollIntoView({ behavior: "smooth", block: "start" });
    void fetchLiveQuote();
    startQuotePolling();
  }
}

const normalizeSymbol = (value) => String(value || "").trim().toUpperCase();
const validSymbol = (value) => /^\d{6}\.(SH|SZ)$/.test(normalizeSymbol(value));

function turnoverText(value) {
  const amount = Number(value || 0);
  if (amount >= 100_000_000) return `${(amount / 100_000_000).toFixed(2)} 亿元`;
  if (amount >= 10_000) return `${(amount / 10_000).toFixed(2)} 万元`;
  return `${amount.toFixed(2)} 元`;
}

// 行情响应明确标记为轮询快照；买入按钮只反映后端返回的本地模拟可用性。
function renderLiveQuote(quote) {
  state.currentQuote = quote;
  text("quoteName", quote.name);
  text("quoteSymbol", quote.symbol);
  text("quotePrice", Number(quote.last_price).toFixed(3));
  const ratio = Number(quote.price_change_ratio_pct || 0);
  text("quoteChange", `${ratio >= 0 ? "+" : ""}${ratio.toFixed(2)}% · ${Number(quote.price_change || 0).toFixed(3)}`);
  byId("quoteChange").className = `quote-change ${ratio > 0 ? "positive" : (ratio < 0 ? "negative" : "")}`;
  text("quoteOpenHighLow", `${Number(quote.open_price).toFixed(3)} / ${Number(quote.high_price).toFixed(3)} / ${Number(quote.low_price).toFixed(3)}`);
  text("quoteTurnover", turnoverText(quote.turnover));
  text("quoteTimestamp", time(quote.observed_at));
  text("quoteAge", `${Number(quote.age_seconds).toFixed(1)} 秒 · ${quote.market_open ? "交易时段" : "休市"}`);
  const canBuy = Boolean(quote.can_submit_paper_buy ?? quote.can_submit_paper_order) && !state.daily?.account?.kill_switch;
  const canSell = Boolean(quote.can_submit_paper_sell);
  const reasons = [
    `买入：${quote.blocked_reason || "可提交"}`,
    `卖出：${quote.sell_blocked_reason || `可卖 ${quote.available_quantity || 0} 股`}`,
  ];
  text("quoteBlockedReason", `${reasons.join("；")} 。提交时服务端会重新报价。`);
  text("quoteState", canBuy && canSell ? "模拟买卖可用" : (canBuy ? "模拟买入可用" : (canSell ? "模拟卖出可用" : (quote.stale ? "行情已过期" : "仅可查看"))));
  const ready = canBuy || canSell;
  byId("quoteState").className = `badge ${ready ? "badge-success" : (quote.stale ? "badge-danger" : "badge-muted")}`;
  byId("paperBuyButton").disabled = !canBuy;
  byId("paperSellButton").disabled = !canSell;
  text("deskBuyingPower", `可买约 ${Number(quote.max_buy_quantity || 0)} 股`);
  text("deskSellable", `可卖 ${Number(quote.available_quantity || 0)} 股`);
}

function renderQuoteFailure(message) {
  state.currentQuote = null;
  text("quoteState", "行情不可用");
  byId("quoteState").className = "badge badge-danger";
  text("quoteName", "无法读取");
  text("quoteSymbol", normalizeSymbol(byId("manualSymbol").value) || "—");
  text("quotePrice", "—");
  text("quoteChange", "—");
  byId("quoteChange").className = "quote-change";
  text("quoteOpenHighLow", "—");
  text("quoteTurnover", "—");
  text("quoteTimestamp", "—");
  text("quoteAge", "—");
  text("quoteBlockedReason", message);
  byId("paperBuyButton").disabled = true;
  byId("paperSellButton").disabled = true;
  text("deskBuyingPower", "可买 — 股");
  text("deskSellable", "可卖 — 股");
}

async function syncQuoteJob({ source = "auto", quiet = true } = {}) {
  const job = state.syncJobs.quote;
  if (job.inFlight) return job.promise;
  const symbol = normalizeSymbol(byId("manualSymbol").value);
  byId("manualSymbol").value = symbol;
  if (!validSymbol(symbol)) {
    renderQuoteFailure("请输入 6 位股票代码和市场后缀，例如 600519.SH。");
    markLiveModule("quote", "paused", "等待选择有效股票");
    if (!quiet) showError("股票代码格式无效，应为 600519.SH 或 000001.SZ。 ");
    job.nextAt = Date.now() + syncJobIntervalMs("quote");
    return { ok: false, jobKey: "quote", skipped: true };
  }
  if (!state.networkOnline) {
    markLiveModule("quote", "paused", "等待网络恢复");
    job.nextAt = Date.now() + syncJobIntervalMs("quote");
    return { ok: false, jobKey: "quote", skipped: true };
  }

  job.inFlight = true;
  job.lastAttemptAt = Date.now();
  markLiveModule("quote", "busy", `${symbol} · 正在读取快照`);
  const sequence = ++state.quoteRequestSequence;
  byId("quoteRefreshButton").disabled = true;
  if (!quiet) clearError();
  job.promise = (async () => {
    try {
      const quote = await apiRequest("get_live_market_quote", { query: { symbol }, timeoutMs: SYNC_JOB_CONFIG.quote.timeoutMs });
      if (sequence !== state.quoteRequestSequence) return { ok: false, jobKey: "quote", superseded: true };
      renderLiveQuote(quote);
      job.failures = 0;
      job.lastSuccessAt = Date.now();
      scheduleJob("quote", true);
      markLiveModule("quote", "ok", `${symbol} · ${clockLabel(job.lastSuccessAt)} 快照`);
      text("lastSync", `${source === "auto" ? "实时" : "同步"} ${clockLabel(job.lastSuccessAt)}`);
      return { ok: true, jobKey: "quote" };
    } catch (error) {
      if (sequence !== state.quoteRequestSequence) return { ok: false, jobKey: "quote", superseded: true };
      job.failures += 1;
      scheduleJob("quote", false);
      renderQuoteFailure(error.message);
      markLiveModule("quote", "error", `读取失败 · ${secondsUntil(job.nextAt)}秒后重试`);
      if (!quiet) showError(`同花顺行情读取失败：${error.message}`);
      return { ok: false, jobKey: "quote", error };
    } finally {
      job.inFlight = false;
      job.promise = null;
      if (sequence === state.quoteRequestSequence) byId("quoteRefreshButton").disabled = false;
      renderLiveSyncCenter();
    }
  })();
  return job.promise;
}

async function fetchLiveQuote({ quiet = false } = {}) {
  state.syncJobs.quote.nextAt = 0;
  return syncModuleJob("quote", { source: quiet ? "auto" : "manual", quiet });
}

function stopQuotePolling() {
  state.quotePollingActive = false;
  state.quoteRequestSequence += 1;
  if (state.liveModules.quote?.tone !== "error") markLiveModule("quote", "paused", "离开选股页后暂停行情轮询");
}

function startQuotePolling() {
  state.quotePollingActive = Boolean(state.liveSyncEnabled && state.activeWorkspace === "market" && document.visibilityState === "visible");
  if (!state.quotePollingActive) return;
  state.syncJobs.quote.nextAt = 0;
  tickLiveScheduler();
}

function newIdempotencyKey() {
  if (window.crypto?.randomUUID) return window.crypto.randomUUID();
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (character) => {
    const random = Math.floor(Math.random() * 16);
    const value = character === "x" ? random : ((random & 3) | 8);
    return value.toString(16);
  });
}

// 清除旧预检回执，避免用户更换股票或数量后误认为仍有效。
function clearPaperOrderPreview() {
  state.paperOrderPreview = null;
  byId("paperOrderPreview").classList.add("hidden");
}

// 预检和最终回执共用一个可视化票据，但永远保留“模拟盘”边界。
function renderPaperOrderPreview(preview, final = false) {
  state.paperOrderPreview = preview;
  const fees = preview.fee_breakdown || {};
  const after = preview.after || {};
  const allowed = Boolean(preview.allowed);
  byId("paperOrderPreview").classList.remove("hidden");
  const typeLabel = preview.order_type === "LIMIT" ? "限价委托" : "即时模拟";
  text("previewTitle", final ? "模拟委托回执" : `${preview.side === "BUY" ? "买入" : "卖出"}${typeLabel}预检`);
  text("previewFill", `${Number(preview.estimated_fill_price || 0).toFixed(3)} · ${preview.quantity || 0}股`);
  text("previewFees", `${money(fees.total)}（佣金 ${money(fees.commission)} · 印花税 ${money(fees.stamp_tax)}）`);
  text("previewSlippage", money(preview.estimated_slippage_cost));
  text("previewImpact", `现金 ${money(after.cash)} · 总仓位 ${fractionPct(after.exposure_ratio)}`);
  text("previewRisk", final ? "已写入账本" : (allowed ? "风控通过" : "风控拒绝"));
  byId("previewRisk").className = `badge badge-${final ? "info" : (allowed ? "success" : "danger")}`;
  text("previewReason", final
    ? `订单状态 ${preview.order_status || "—"}；限价待成交时资金或股份保持冻结，可在当前委托中撤销。`
    : (preview.blocked_reason || `${preview.marketable_now ? "当前快照可撮合" : "当前价格尚未达到限价"}；提交时仍会重新报价并执行风控。`));
}

// 手动操作只写入本地 PAPER 账本；先预检、后确认，提交时服务端再次风控。
async function submitManualPaperOrder(side, event) {
  event?.preventDefault();
  clearError();
  const symbol = normalizeSymbol(byId("manualSymbol").value);
  const quantity = Number(byId("manualQuantity").value);
  const orderType = byId("manualOrderType").value;
  const rawLimitPrice = Number(byId("manualLimitPrice").value);
  const limitPrice = orderType === "LIMIT" ? rawLimitPrice : null;
  const isBuy = side === "BUY";
  const quantityValid = Number.isInteger(quantity) && quantity > 0 && (!isBuy || (quantity >= 100 && quantity % 100 === 0));
  if (!validSymbol(symbol) || !quantityValid || (orderType === "LIMIT" && (!Number.isFinite(limitPrice) || limitPrice <= 0))) {
    showError(orderType === "LIMIT" && (!Number.isFinite(limitPrice) || limitPrice <= 0)
      ? "限价委托必须填写大于 0 的委托价格。"
      : (isBuy ? "买入数量必须是 100 股的整数倍。" : "卖出数量必须是大于 0 的整数。"));
    return;
  }
  const sideReady = isBuy
    ? Boolean(state.currentQuote?.can_submit_paper_buy ?? state.currentQuote?.can_submit_paper_order)
    : Boolean(state.currentQuote?.can_submit_paper_sell);
  if (state.currentQuote?.symbol !== symbol || !sideReady) {
    showError(`当前快照不允许模拟${isBuy ? "买入" : "卖出"}；请检查交易时段、T+1、可卖数量和涨跌停状态。`);
    return;
  }
  const sideText = isBuy ? "买入" : "卖出";
  const button = byId(isBuy ? "paperBuyButton" : "paperSellButton");
  button.disabled = true;
  button.textContent = "服务端预检…";
  try {
    const preview = await apiRequest("preview_broker_paper_order", {
      body: { symbol, quantity, side, order_type: orderType, limit_price: limitPrice },
      timeoutMs: 30000,
    });
    renderPaperOrderPreview(preview);
    if (!preview.allowed) {
      const blocked = preview.blocked_reason || "服务端风控拒绝";
      text("paperOrderResult", `模拟${sideText}未提交：${blocked}`);
      byId("paperOrderResult").className = "order-result danger";
      showToast("模拟订单未通过预检", blocked, "error", 6000);
      return;
    }
    const fee = Number(preview.fee_breakdown?.total || 0);
    const confirmation = [
      `确认在本地模拟盘${sideText} ${symbol} ${quantity} 股？`,
      `委托类型：${orderType === "LIMIT" ? `限价 ${Number(limitPrice).toFixed(3)}` : "即时模拟"}`,
      `预计参考价：${Number(preview.estimated_fill_price).toFixed(3)}`,
      `预计全部费用：${money(fee)}`,
      `操作后现金：${money(preview.after?.cash)}`,
      `操作后总仓位：${fractionPct(preview.after?.exposure_ratio)}`,
      "",
      "不会连接或提交真实证券订单。",
    ].join("\n");
    if (!window.confirm(confirmation)) return;
    button.textContent = "服务端重新报价…";
    const result = await apiRequest("submit_broker_paper_order", {
      body: { symbol, quantity, side, order_type: orderType, limit_price: limitPrice, idempotency_key: newIdempotencyKey() },
      timeoutMs: 30000,
    });
    const filled = result.order?.status === "FILLED";
    const pending = ["PENDING", "SUBMITTED"].includes(result.order?.status);
    const message = filled
      ? `模拟${sideText}已成交：${symbol} ${result.order.quantity} 股，费用 ${money(result.trade?.fee || result.receipt?.fee_breakdown?.total)}`
      : pending
        ? `限价委托已受理：${symbol} ${result.order.quantity} 股，等待快照达到 ${Number(result.order.limit_price).toFixed(3)}`
        : `模拟订单未受理：${result.order?.reason || result.order?.status || "风控拒绝"}`;
    if (result.receipt) renderPaperOrderPreview(result.receipt, filled);
    text("paperOrderResult", message);
    byId("paperOrderResult").className = `order-result ${filled || pending ? "success" : "danger"}`;
    showToast(filled ? `模拟${sideText}已成交` : (pending ? "限价委托已受理" : "模拟订单未受理"), filled ? `${symbol} · ${result.order.quantity} 股` : message, filled || pending ? "success" : "error", 6000);
    await refreshAll();
    await fetchLiveQuote({ quiet: true });
  } catch (error) {
    text("paperOrderResult", `模拟${sideText}失败：${error.message}`);
    byId("paperOrderResult").className = "order-result danger";
    showError(`模拟${sideText}失败：${error.message}`);
    showToast(`模拟${sideText}失败`, error.message, "error", 6500);
  } finally {
    button.textContent = `委托${sideText}`;
    if (state.currentQuote) renderLiveQuote(state.currentQuote);
  }
}

function renderPositions(account) {
  const body = byId("positionBody");
  body.replaceChildren();
  const positions = account.positions || [];
  for (const item of positions) {
    const row = document.createElement("tr");
    const sellable = Number(item.sellable_quantity ?? item.available_quantity ?? 0);
    [
      `${item.name} ${item.symbol}`, item.quantity, `${sellable}${item.frozen_quantity ? `（冻${item.frozen_quantity}）` : ""}`,
      Number(item.average_cost).toFixed(3), money(item.market_value),
      item.pending_exit ? "待次日退出" : (sellable ? "可卖" : (item.frozen_quantity ? "委托冻结" : "T+1 锁定")),
    ].forEach((value) => { const cell = document.createElement("td"); cell.textContent = value; row.append(cell); });
    const action = document.createElement("td");
    const sellButton = document.createElement("button");
    sellButton.type = "button";
    sellButton.className = "button button-danger position-sell-button";
    sellButton.dataset.sellSymbol = item.symbol;
    sellButton.dataset.sellQuantity = sellable;
    sellButton.textContent = sellable ? "卖出" : (item.frozen_quantity ? "已冻结" : "T+1");
    sellButton.disabled = !sellable;
    action.append(sellButton);
    row.append(action);
    body.append(row);
  }
  text("positionCount", `${positions.length}只 · 全部实际持仓`);
  byId("positionEmpty").classList.toggle("hidden", positions.length > 0);
}

const paperAuditColumns = {
  open_orders: [["created_at", "委托时间"], ["symbol", "股票"], ["side", "方向"], ["order_type", "类型"], ["requested_price", "委托价"], ["quantity", "委托数量"], ["frozen_resource", "冻结"], ["status", "状态"], ["action", "操作"]],
  orders: [["created_at", "委托时间"], ["symbol", "股票"], ["side", "方向"], ["order_type", "类型"], ["requested_price", "委托价"], ["quantity", "数量"], ["filled_quantity", "已成"], ["status", "状态"], ["reason", "说明"], ["client_order_id", "订单号"]],
  trades: [["created_at", "时间"], ["symbol", "股票"], ["side", "方向"], ["quantity", "数量"], ["fill_price", "成交价"], ["fee", "费用"], ["realized_pnl", "已实现盈亏"], ["client_order_id", "订单号"]],
  events: [["observed_at", "时间"], ["symbol", "股票"], ["event_type", "事件"], ["price", "价格"], ["message", "说明"]],
  nav: [["trade_date", "日期"], ["cash", "现金"], ["market_value", "市值"], ["equity", "权益"], ["drawdown", "回撤"]],
};

function renderPaperAudit() {
  const columns = paperAuditColumns[state.paperAuditView];
  const allOrders = state.daily?.activity?.orders || [];
  const items = state.paperAuditView === "open_orders"
    ? allOrders.filter((item) => ["PENDING", "SUBMITTED"].includes(item.status))
    : (state.daily?.activity?.[state.paperAuditView] || []);
  byId("paperAuditHead").replaceChildren();
  byId("paperAuditBody").replaceChildren();
  for (const [, label] of columns) { const th = document.createElement("th"); th.textContent = label; byId("paperAuditHead").append(th); }
  for (const item of items) {
    const row = document.createElement("tr");
    for (const [key] of columns) {
      const cell = document.createElement("td");
      if (key === "action") {
        const cancel = document.createElement("button");
        cancel.type = "button";
        cancel.className = "button button-danger broker-cancel-button";
        cancel.dataset.cancelOrder = item.client_order_id;
        cancel.textContent = "撤单";
        cell.append(cancel);
        row.append(cell);
        continue;
      }
      let value = item[key] ?? "—";
      if (key === "order_type") value = value === "LIMIT" ? "限价" : "即时";
      if (key === "side") value = value === "BUY" ? "买入" : "卖出";
      if (key === "frozen_resource") value = item.side === "BUY" ? money(item.frozen_cash) : `${item.frozen_quantity || 0} 股`;
      if (["created_at"].includes(key)) value = time(value);
      if (["requested_price"].includes(key)) value = Number(value || 0).toFixed(3);
      if (["cash", "market_value", "equity", "fee", "realized_pnl"].includes(key)) value = money(value);
      if (key === "drawdown") value = fractionPct(value);
      cell.textContent = String(value);
      row.append(cell);
    }
    byId("paperAuditBody").append(row);
  }
  byId("paperAuditEmpty").classList.toggle("hidden", items.length > 0);
}

function activeBrokerOrders() {
  return (state.daily?.activity?.orders || []).filter((item) => ["PENDING", "SUBMITTED"].includes(item.status));
}

// 撮合只作用于本地模拟委托；行情未开盘或没有待成交委托时不会发起写请求。
async function matchBrokerOrders({ quiet = false } = {}) {
  if (state.brokerMatchInFlight) return;
  const active = activeBrokerOrders();
  if (!active.length) {
    text("brokerMatchState", "无待成交委托");
    byId("brokerMatchState").className = "badge badge-muted";
    return;
  }
  if (!state.currentQuote?.market_open && quiet) return;
  state.brokerMatchInFlight = true;
  byId("matchOrdersButton").disabled = true;
  text("brokerMatchState", "正在快照撮合…");
  byId("brokerMatchState").className = "badge badge-info";
  try {
    const result = await apiRequest("match_broker_paper_orders", { body: {}, timeoutMs: 30000 });
    const count = result.matched?.length || 0;
    text("brokerMatchState", count ? `本轮成交 ${count} 笔` : `等待价格 · ${result.waiting?.length || 0} 笔`);
    byId("brokerMatchState").className = `badge ${count ? "badge-success" : "badge-muted"}`;
    if (count) await refreshAccountAuthorities("match");
    else await syncModuleJob("daily", { source: "match", quiet: true });
    if (count) showToast("模拟限价单已成交", `本轮按同花顺快照撮合 ${count} 笔。`, "success", 4200);
  } catch (error) {
    text("brokerMatchState", "撮合暂停");
    byId("brokerMatchState").className = "badge badge-muted";
    if (!quiet) showError(`模拟撮合未执行：${error.message}`);
  } finally {
    state.brokerMatchInFlight = false;
    byId("matchOrdersButton").disabled = false;
  }
}

function startBrokerMatching() {
  stopBrokerMatching();
  if (state.activeWorkspace !== "market" || document.visibilityState !== "visible") return;
  state.brokerMatchTimer = window.setInterval(() => void matchBrokerOrders({ quiet: true }), 10000);
}

function stopBrokerMatching() {
  if (state.brokerMatchTimer) window.clearInterval(state.brokerMatchTimer);
  state.brokerMatchTimer = null;
}

async function cancelBrokerOrder(clientOrderId) {
  if (!window.confirm("确认撤销这笔本地模拟委托？冻结资金或股份会立即释放。")) return;
  try {
    const result = await apiRequest("cancel_broker_paper_order", {
      pathParams: { client_order_id: clientOrderId },
      body: {},
      timeoutMs: 30000,
    });
    showToast("模拟撤单成功", `${result.order.symbol} · ${result.order.quantity} 股`, "success", 3500);
    await refreshAccountAuthorities("cancel");
  } catch (error) {
    showError(`模拟撤单失败：${error.message}`);
  }
}

function renderWorkbench(payload) {
  state.workbench = payload;
  renderPositions({
    ...(payload.account || {}),
    positions: payload.positions || [],
  });
  renderSimpleHoldings(payload);
  const currentValuation = payload.valuation_observed_at || payload.valuation?.observed_at;
  text("sourceBadge", valuationBadgeLabel(currentValuation));
  byId("sourceBadge").className = `badge badge-${
    currentValuation ? (payload.valuation?.stale ? "warning" : "info") : "muted"
  }`;
  byId("sourceBadge").title = payload.nav_snapshot_message || "当前估值时间与最近持久化净值日分开显示";
  text("workbenchProvenance", payload.provenance);
  const grid = byId("watchlistGrid");
  grid.replaceChildren();
  payload.positions.forEach((item) => {
    const card = document.createElement("article");
    card.className = `watch-card selected ${item.unrealized_pnl >= 0 ? "profit" : "loss"}`;
    const sellable = Number(item.sellable_quantity ?? item.available_quantity ?? 0);
    const status = item.pending_exit
      ? ["待次日退出", "danger"]
      : item.frozen_quantity > 0
        ? [`委托冻结${item.frozen_quantity}股`, "info"]
        : sellable > 0
          ? ["可卖", "success"]
          : ["T+1锁定", "warning"];
    const valuationLabels = {
      ths_polling_snapshot: "同花顺轮询快照",
      latest_paper_nav: "最近模拟净值",
      average_cost_fallback: "含费成本回退",
    };
    card.innerHTML = `<header><h3></h3><span class="badge badge-${status[1]}">${status[0]}</span></header><div class="signal-score position-pnl"></div><p></p><p></p><p></p><p></p>`;
    card.querySelector("h3").textContent = `${item.name} ${item.symbol}`;
    card.querySelector(".position-pnl").textContent = `${item.unrealized_pnl >= 0 ? "+" : ""}${money(item.unrealized_pnl)}`;
    card.querySelectorAll("p")[0].textContent = `持有 ${item.quantity} 股 · 可卖 ${sellable} 股 · 冻结 ${item.frozen_quantity || 0} 股 · 权重 ${fractionPct(item.weight)}`;
    card.querySelectorAll("p")[1].textContent = `成本 ${Number(item.average_cost).toFixed(3)} · 最近估值 ${Number(item.last_price).toFixed(3)} · 浮动 ${fractionPct(item.unrealized_pnl_pct)}`;
    card.querySelectorAll("p")[2].textContent = `持有 ${item.holding_days}天 · MFE ${fractionPct(item.mfe_pct)} · MAE ${fractionPct(item.mae_pct)}`;
    card.querySelectorAll("p")[3].textContent = `止损参考 ${Number(item.stop_price).toFixed(3)} · 距止损 ${fractionPct(item.distance_to_stop_pct)} · ${valuationLabels[item.valuation_source] || item.valuation_source}`;
    grid.append(card);
  });
  if (!payload.positions.length) {
    const empty = document.createElement("article");
    empty.className = "watch-card unavailable";
    empty.innerHTML = "<header><h3>模拟账户当前空仓</h3><span class=\"badge badge-muted\">0只</span></header><p>本页不会用固定白名单填充空仓。</p><p>发生模拟买入成交后，股票会自动出现在这里。</p>";
    grid.append(empty);
  }

  const discipline = payload.discipline;
  text("disciplineScore", discipline.score ?? "—");
  text("disciplineGrade", discipline.grade);
  text("disciplineMeaning", discipline.meaning);
  const list = byId("disciplineList");
  list.replaceChildren();
  discipline.dimensions.forEach((item) => {
    const node = document.createElement("article");
    node.className = "dimension";
    node.innerHTML = "<header><span></span><strong></strong></header><p></p>";
    node.querySelector("span").textContent = item.label;
    node.querySelector("strong").textContent = item.score === null ? "不可用" : `${item.score}分`;
    node.querySelector("p").textContent = item.evidence;
    list.append(node);
  });
  drawRadar(discipline.dimensions);

  const summary = payload.activity_summary;
  text("summarySignals", summary.position_count); text("summaryOrders", summary.order_count); text("summaryTrades", summary.trade_count);
  text("summaryRejections", summary.rejection_count); text("summaryUnknown", summary.monitor_event_count); text("summaryFees", money(summary.total_fees));
  text("summaryDate", summary.latest_activity_date ? `最近活动日 ${summary.latest_activity_date}` : "尚无活动记录。");
  const availability = byId("availabilityList");
  availability.replaceChildren();
  payload.data_availability.forEach((item) => {
    const node = document.createElement("div");
    node.className = `availability-item ${item.state}`;
    node.innerHTML = "<i></i><div><strong></strong><small></small></div>";
    node.querySelector("strong").textContent = `${item.label} · ${item.state === "available" ? "可用" : item.state === "stale" ? "已过期" : "未接入"}`;
    node.querySelector("small").textContent = item.message;
    availability.append(node);
  });
  renderPortfolioMetrics(payload);
  renderReviewInsights(payload);
  drawEquity(payload.equity_curve || []);
  renderReviewActivity(state.reviewActivityKind, payload.activity?.[state.reviewActivityKind] || []);
  if (state.status?.model) renderModelStatus(state.status.model);
}

function renderSimpleHoldings(payload = {}) {
  const list = byId("simpleHoldingList");
  if (!list) return;
  const positions = payload.positions || [];
  const researched = Object.fromEntries(
    (state.investmentDashboard.snapshot?.holding_health || []).map((item) => [item.symbol, item]),
  );
  list.replaceChildren();
  positions.forEach((position) => {
    const evidence = researched[position.symbol] || {};
    const card = document.createElement("article");
    card.className = "simple-holding-card";
    const title = document.createElement("div");
    const detail = document.createElement("dl");
    const exit = evidence.exit_signal || evidence.exit || {};
    title.innerHTML = "<strong></strong><span></span>";
    title.querySelector("strong").textContent = `${position.name || position.symbol} · ${position.symbol}`;
    title.querySelector("span").textContent = position.unrealized_pnl == null ? "收益不可用" : `${money(position.unrealized_pnl)} · ${fractionPct(position.unrealized_pnl_pct)}`;
    const fields = [
      ["持仓 / 可卖", `${position.quantity ?? "—"} / ${position.sellable_quantity ?? position.available_quantity ?? "—"} 股`],
      ["成本 / 估值", `${position.average_cost ?? "—"} / ${position.last_price ?? "—"}`],
      ["研究评分", evidence.score ?? "暂无正式评分"],
      ["当前风险", evidence.risk_level || evidence.risk?.level || "暂无风险证据"],
      ["风险变化", "暂无历史风险序列"],
      ["退出证据", exit.reason || exit.signal || (position.pending_exit ? "已登记退出" : "暂无退出信号")],
    ];
    fields.forEach(([label, value]) => {
      const row = document.createElement("div");
      row.innerHTML = "<dt></dt><dd></dd>";
      row.querySelector("dt").textContent = label;
      row.querySelector("dd").textContent = value;
      detail.append(row);
    });
    const actions = document.createElement("div");
    actions.className = "button-row holding-journal-actions";
    [["DECISION", "记录继续持有理由", "我决定继续持有，理由是："], ["DECISION", "记录减仓 / 退出理由", "我考虑减仓或退出，理由是："]].forEach(([entryType, label, reason]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "button button-ghost button-compact";
      button.dataset.journalAction = entryType;
      button.dataset.journalSymbol = position.symbol;
      button.dataset.journalTitle = `${label.replace("记录", "")} ${position.name || position.symbol}`;
      button.dataset.journalReason = reason;
      button.textContent = label;
      actions.append(button);
    });
    card.append(title, detail, actions);
    list.append(card);
  });
  byId("simpleHoldingEmpty").classList.toggle("hidden", positions.length > 0);
  text("simpleHoldingState", positions.length
    ? `${positions.length}只 · T+1 ${payload.position_as_of_trade_date || "—"} · ${time(payload.generated_at)}`
    : `当前空仓 · ${payload.position_as_of_trade_date || "—"}`);
}

// 复盘指标只使用本地模拟账户账本，不读取历史ETF回测或固定研究池。
function renderPortfolioMetrics(payload) {
  const account = payload.account;
  const valuationSummary = payload.asset_valuation;
  const hasEvidence = payload.positions.length > 0 || payload.activity_summary.order_count > 0 || payload.nav.length > 0;
  byId("emptyResult").classList.toggle("hidden", hasEvidence);
  byId("resultContent").classList.toggle("hidden", !hasEvidence);
  text("resultTimestamp", payload.source_nav_date ? `最近模拟净值日 ${payload.source_nav_date}` : "尚无模拟净值快照");
  const valuation = payload.valuation || {};
  text("dataSourceBadge", valuation.stale ? "回退估值" : "持仓快照估值");
  byId("dataSourceBadge").className = `badge badge-${valuation.stale ? "warning" : "info"}`;
  text("chartDisclaimer", valuation.message || "持仓估值来源未说明。");
  text("metricFinal", money(valuationSummary.equity));
  text("metricInitial", `初始 ${money(valuationSummary.initial_cash)} · 现金 ${money(valuationSummary.cash)}`);
  text("metricNetPnl", money(valuationSummary.pnl));
  text("metricReturn", fractionPct(valuationSummary.total_return));
  text("metricRealized", money(valuationSummary.realized_pnl));
  text("metricUnrealized", money(valuationSummary.unrealized_pnl));
  text("metricDrawdown", fractionPct(valuationSummary.max_drawdown));
  text("metricSharpe", `${valuationSummary.position_count}只`);
  text("metricVolatility", `${valuationSummary.available_position_count}只可卖 · 全部${valuationSummary.position_count}只持仓`);
  text("metricFees", money(valuationSummary.total_fees));
  text("metricFeeDrag", `费用侵蚀 ${fractionPct(valuationSummary.fee_drag_pct)} · 拒单 ${payload.activity_summary.rejection_count}`);
  text("metricTurnover", money(valuationSummary.turnover));
  text("metricTrades", `${payload.activity_summary.trade_count}笔成交 · 换手 ${fractionPct(payload.performance.turnover_ratio)}`);
}

// 交易绩效、风险提示和个股归因都由同一份后端复盘快照驱动。
function renderReviewInsights(payload) {
  const performance = payload.performance || {};
  const stats = [
    ["卖出成交", `${performance.sell_trade_count || 0}笔`],
    ["胜率", performance.win_rate == null ? "不可用" : fractionPct(performance.win_rate)],
    ["盈利因子", performance.profit_factor == null ? "不可用" : Number(performance.profit_factor).toFixed(2)],
    ["平均盈利", performance.average_win == null ? "不可用" : money(performance.average_win)],
    ["平均亏损", performance.average_loss == null ? "不可用" : money(performance.average_loss)],
    ["最佳净值日", performance.best_day_return == null ? "不可用" : fractionPct(performance.best_day_return)],
    ["最差净值日", performance.worst_day_return == null ? "不可用" : fractionPct(performance.worst_day_return)],
    ["对账差额", money(performance.pnl_reconciliation_gap)],
  ];
  const statGrid = byId("performanceStats");
  statGrid.replaceChildren();
  stats.forEach(([label, value]) => {
    const item = document.createElement("div");
    const name = document.createElement("span");
    const result = document.createElement("strong");
    name.textContent = label;
    result.textContent = value;
    item.append(name, result);
    statGrid.append(item);
  });
  text("performanceNote", performance.sell_trade_count
    ? `盈利 ${performance.winning_trade_count}笔 · 亏损 ${performance.losing_trade_count}笔 · 净值证据 ${performance.nav_days}日`
    : "没有卖出成交前，胜率和盈利因子不可用。");

  const flags = byId("reviewRiskFlags");
  flags.replaceChildren();
  (payload.risk_flags || []).forEach((flag) => {
    const node = document.createElement("div");
    node.className = `review-risk-flag ${flag.level}`;
    const marker = document.createElement("i");
    const copy = document.createElement("span");
    copy.textContent = flag.message;
    node.append(marker, copy);
    flags.append(node);
  });
  const valuation = payload.valuation || {};
  text("valuationState", valuation.stale ? "估值已回退" : "估值可用");
  byId("valuationState").className = `badge badge-${valuation.stale ? "warning" : "success"}`;
  text("valuationMeta", `${valuation.message || "估值来源未说明"}${valuation.observed_at ? ` · ${time(valuation.observed_at)}` : ""}`);

  const body = byId("attributionBody");
  body.replaceChildren();
  (payload.symbol_attribution || []).forEach((item) => {
    const row = document.createElement("tr");
    [
      `${item.name} ${item.symbol}`,
      money(item.realized_pnl),
      money(item.unrealized_pnl),
      money(item.total_pnl),
      fractionPct(item.weight),
    ].forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    });
    body.append(row);
  });
  byId("attributionEmpty").classList.toggle("hidden", (payload.symbol_attribution || []).length > 0);
}

function drawRadar(dimensions) {
  const canvas = byId("disciplineChart");
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, rect.width * ratio);
  canvas.height = Math.max(1, rect.height * ratio);
  const context = canvas.getContext("2d");
  context.scale(ratio, ratio);
  context.clearRect(0, 0, rect.width, rect.height);
  if (dimensions.length < 3) return;
  const centerX = rect.width / 2;
  const centerY = rect.height / 2;
  const radius = Math.max(34, Math.min(rect.width, rect.height) * 0.32);
  const count = dimensions.length;
  const point = (index, scale) => {
    const angle = -Math.PI / 2 + index * Math.PI * 2 / count;
    return [centerX + Math.cos(angle) * radius * scale, centerY + Math.sin(angle) * radius * scale];
  };
  context.strokeStyle = "#303641";
  context.lineWidth = 1;
  [0.25, 0.5, 0.75, 1].forEach((scale) => {
    context.beginPath();
    for (let index = 0; index < count; index += 1) {
      const [x, y] = point(index, scale);
      if (index) context.lineTo(x, y); else context.moveTo(x, y);
    }
    context.closePath();
    context.stroke();
  });
  context.beginPath();
  dimensions.forEach((item, index) => {
    const [x, y] = point(index, (item.score ?? 0) / 100);
    if (index) context.lineTo(x, y); else context.moveTo(x, y);
  });
  context.closePath();
  context.fillStyle = "rgba(215,169,39,.20)";
  context.strokeStyle = "#d7a927";
  context.lineWidth = 2;
  context.fill();
  context.stroke();
  context.fillStyle = "#9ba3af";
  context.font = "10px Microsoft YaHei";
  context.textAlign = "center";
  dimensions.forEach((item, index) => { const [x, y] = point(index, 1.2); context.fillText(item.label, x, y + 3); });
}

function drawEquity(points) {
  state.equityPoints = points;
  const canvas = byId("equityChart");
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, rect.width * ratio);
  canvas.height = Math.max(1, rect.height * ratio);
  const context = canvas.getContext("2d");
  context.scale(ratio, ratio);
  context.clearRect(0, 0, rect.width, rect.height);
  if (!points.length) { text("chartSummary", "暂无净值数据。"); return; }
  const values = points.map((item) => Number(item.equity));
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const range = Math.max(maximum - minimum, 1);
  const padding = { top: 18, right: 12, bottom: 24, left: 56 };
  const width = rect.width - padding.left - padding.right;
  const height = rect.height - padding.top - padding.bottom;
  context.strokeStyle = "#252b34";
  context.lineWidth = 1;
  for (let index = 0; index <= 4; index += 1) {
    const y = padding.top + height * index / 4;
    context.beginPath(); context.moveTo(padding.left, y); context.lineTo(rect.width - padding.right, y); context.stroke();
  }
  const gradient = context.createLinearGradient(0, padding.top, 0, padding.top + height);
  gradient.addColorStop(0, "rgba(67,139,255,.32)"); gradient.addColorStop(1, "rgba(67,139,255,0)");
  context.beginPath();
  points.forEach((item, index) => {
    const x = padding.left + width * index / Math.max(points.length - 1, 1);
    const y = padding.top + height - ((Number(item.equity) - minimum) / range) * height;
    if (index) context.lineTo(x, y); else context.moveTo(x, y);
  });
  context.lineTo(padding.left + width, padding.top + height); context.lineTo(padding.left, padding.top + height); context.closePath();
  context.fillStyle = gradient; context.fill();
  context.beginPath();
  points.forEach((item, index) => {
    const x = padding.left + width * index / Math.max(points.length - 1, 1);
    const y = padding.top + height - ((Number(item.equity) - minimum) / range) * height;
    if (index) context.lineTo(x, y); else context.moveTo(x, y);
  });
  context.strokeStyle = "#438bff"; context.lineWidth = 2; context.stroke();
  context.fillStyle = "#8c95a3"; context.font = "11px Inter";
  context.fillText(maximum.toFixed(0), 8, padding.top + 4); context.fillText(minimum.toFixed(0), 8, padding.top + height);
  text("chartSummary", `起始 ${money(values[0])} · 最低 ${money(minimum)} · ${points.at(-1)?.valuation_point ? "当前估值" : "期末"} ${money(values.at(-1))}`);
}

function renderReviewActivity(kind, items) {
  const header = byId("etfActivityHead");
  const body = byId("etfActivityBody");
  header.replaceChildren(); body.replaceChildren();
  const columns = kind === "trades"
    ? [["created_at", "时间"], ["symbol", "股票"], ["side", "方向"], ["quantity", "数量"], ["fill_price", "成交价"], ["fee", "费用"], ["realized_pnl", "已实现盈亏"], ["client_order_id", "订单编号"]]
    : kind === "events"
      ? [["observed_at", "时间"], ["symbol", "股票"], ["event_type", "事件"], ["price", "价格"], ["message", "说明"]]
      : kind === "nav"
        ? [["trade_date", "日期"], ["cash", "现金"], ["market_value", "市值"], ["equity", "权益"], ["drawdown", "回撤"]]
        : [["trade_date", "日期"], ["symbol", "股票"], ["side", "方向"], ["quantity", "数量"], ["status", "状态"], ["reason", "说明"], ["client_order_id", "订单编号"]];
  columns.forEach(([, label]) => { const th = document.createElement("th"); th.textContent = label; header.append(th); });
  items.forEach((item) => {
    const row = document.createElement("tr");
    columns.forEach(([key]) => {
      const cell = document.createElement("td");
      let value = item[key] ?? "—";
      if (["fee", "realized_pnl", "cash", "market_value", "equity"].includes(key)) value = money(value);
      if (key === "drawdown") value = fractionPct(value);
      cell.textContent = String(value); row.append(cell);
    });
    body.append(row);
  });
  byId("etfActivityEmpty").classList.toggle("hidden", items.length > 0);
  if (!items.length) text("etfActivityEmpty", "模拟账户还没有此类记录。");
}

function personalRecordNode(titleValue, metaValue, detailValue = "") {
  const article = document.createElement("article");
  const heading = document.createElement("strong");
  const meta = document.createElement("small");
  heading.textContent = String(titleValue || "未命名记录");
  meta.textContent = String(metaValue || "");
  article.append(heading, meta);
  if (detailValue) {
    const detail = document.createElement("p");
    detail.textContent = String(detailValue);
    article.append(detail);
  }
  return article;
}

function renderPersonalScore(score = {}) {
  text("personalScoreValue", score.total_score == null ? "—" : `${score.total_score}/100`);
  text("personalScoreCoverage", score.coverage_display || (score.coverage == null ? "证据覆盖不可用" : `证据覆盖 ${score.coverage}`));
  const root = byId("personalScoreDimensions");
  root.replaceChildren();
  (score.dimensions || []).forEach((item) => {
    const row = document.createElement("article");
    const header = document.createElement("div");
    const label = document.createElement("strong");
    const value = document.createElement("span");
    const track = document.createElement("div");
    const bar = document.createElement("i");
    const note = document.createElement("small");
    label.textContent = `${item.label} · ${item.weight}分`;
    value.textContent = item.availability === "available" ? `${item.score}/100` : "证据不足";
    bar.style.width = item.availability === "available" ? `${Math.max(0, Math.min(100, Number(item.score)))}%` : "0%";
    note.textContent = item.rule || "";
    header.append(label, value); track.append(bar); row.append(header, track, note); root.append(row);
  });
  if (!root.children.length) root.append(personalRecordNode("暂无评分", "需要先同步后端证据"));
}

function renderPersonalProfile(profile = {}) {
  byId("personalProfileCapital").value = profile.capital ?? 100000;
  byId("personalProfileRisk").value = profile.risk_level || "balanced";
  byId("personalProfilePeriod").value = profile.holding_period || "medium";
  byId("personalProfileStyle").value = profile.investment_style || "balanced";
  byId("personalProfileDrawdown").value = Number(profile.max_drawdown || 0.1) * 100;
  byId("personalProfileBehavior").value = (profile.behavior || []).join("\n");
  text("personalProfileRevision", profile.persisted ? `版本 ${profile.revision}` : "继承默认画像 · 未保存");
}

function renderPersonalCoach(coach = {}) {
  text("personalCoachSummary", coach.summary || "等待事件、日志与评分证据。");
  const patterns = byId("personalCoachPatterns");
  const improvements = byId("personalCoachImprovements");
  patterns.replaceChildren(); improvements.replaceChildren();
  (coach.patterns || []).forEach((item) => patterns.append(personalRecordNode(item.pattern, item.label, item.statement)));
  (coach.improvements || []).forEach((item, index) => improvements.append(personalRecordNode(`改进 ${index + 1}`, "PROCESS", item)));
  if (!patterns.children.length) patterns.append(personalRecordNode("尚无可确认的行为模式", "缺少事实时不推断"));
}

function renderPersonalCollections(payload = {}) {
  const events = byId("personalEventList");
  const journals = byId("personalJournalList");
  const knowledge = byId("personalKnowledgeList");
  const reports = byId("personalReportList");
  events.replaceChildren(); journals.replaceChildren(); knowledge.replaceChildren(); reports.replaceChildren();
  (payload.events || []).slice(0, 12).forEach((item) => events.append(personalRecordNode(
    `${item.event_type} · ${item.symbol || "组合"}`,
    `${item.trade_date} · ${item.source_type}`,
    item.reason,
  )));
  (payload.journals || []).filter((item) => item.status === "active").slice(0, 8).forEach((item) => journals.append(personalRecordNode(
    item.title, `${item.trade_date} · ${item.entry_type}`, item.content,
  )));
  (payload.knowledge || []).filter((item) => item.status === "active").slice(0, 8).forEach((item) => knowledge.append(personalRecordNode(
    item.title, `${item.category} · ${item.subject}`, item.content,
  )));
  (payload.reports || []).slice(0, 10).forEach((item) => {
    const content = item.report_content?.content || item.content || {};
    reports.append(personalRecordNode(
      content.title || item.report_type || "投资报告",
      `${item.source || "personal_os"} · ${item.status || "published"}`,
      content.summary || item.summary || "证据型报告已归档",
    ));
  });
  if (!events.children.length) events.append(personalRecordNode("暂无投资事件", "可同步模拟成交或记录观察"));
  if (!journals.children.length) journals.append(personalRecordNode("暂无投资日志", "记录理由、结果与教训"));
  if (!knowledge.children.length) knowledge.append(personalRecordNode("暂无个人知识", "把经验写成可验证的边界"));
  if (!reports.children.length) reports.append(personalRecordNode("暂无个人报告", "可生成教练、周委员会或月度复盘"));
}

function renderPersonalInvestmentOS(payload) {
  state.personalOS.dashboard = payload;
  const valuation = payload.asset_valuation || {};
  const flags = payload.risk?.flags || [];
  const firstRisk = flags[0] || {};
  const evidence = payload.quant_research?.evidence || {};
  text("personalOsState", `已同步 · ${time(payload.generated_at)}`);
  text("personalEquity", valuation.equity == null ? "—" : money(valuation.equity));
  text("personalPnl", valuation.pnl == null ? "盈亏不可用" : `累计盈亏 ${money(valuation.pnl)}`);
  text("personalDrawdown", valuation.drawdown == null ? "—" : fractionPct(valuation.drawdown));
  text("personalRiskValue", firstRisk.level ? String(firstRisk.level).toUpperCase() : "—");
  text("personalRiskDetail", firstRisk.message || "后端暂无风险标记");
  text("personalEvidenceCount", evidence.evidence_count ?? 0);
  text("personalResearchDetail", evidence.strategy_version || "尚无正式量化验证证据");
  text("personalLoopState", payload.investment_loop?.current_state || "研究与记录");
  text("personalLoopDetail", `${payload.investment_loop?.event_count || 0}个事件 · ${payload.investment_loop?.journal_count || 0}篇日志 · ${payload.investment_loop?.knowledge_count || 0}条知识`);
  renderPersonalScore(payload.personal_score || {});
  renderPersonalProfile(payload.investor_profile || {});
  renderPersonalCoach(payload.coach || {});
  renderPersonalCollections(payload);
  renderSimpleInvestmentReview(payload);
}

function renderSimpleInvestmentReview(payload = {}) {
  if (!byId("simpleReviewResult")) return;
  const valuation = payload.asset_valuation || {};
  const activity = payload.portfolio?.activity_summary || {};
  const coach = payload.coach || {};
  const activeJournals = (payload.journals || []).filter((item) => item.status === "active");
  byId("simpleReviewResult").textContent = valuation.pnl == null
    ? "当日或累计收益证据不可用，未用其他数字代替。"
    : `账户盈亏 ${money(valuation.pnl)}；估值时间 ${time(valuation.valued_at)}。`;
  byId("simpleReviewPlan").textContent = `${activity.trade_count || 0}笔成交、${activity.order_count || 0}笔委托；暂无结构化“计划与实际”映射时不推断偏差。`;
  byId("simpleReviewDiscipline").textContent = (coach.improvements || [])[0]
    || (coach.data_gaps || [])[0]
    || "维持证据先行、风险优先和定期复盘纪律。";
  byId("simpleReviewPending").textContent = activeJournals.length
    ? `${activeJournals.length}篇有效日志；最近：${activeJournals[0].title || activeJournals[0].entry_type}`
    : "暂无待复盘日志，可在个人投资操作系统记录投资理由。";
}

async function loadPersonalInvestmentOS({ quiet = false } = {}) {
  if (state.personalOS.inFlight) return;
  state.personalOS.inFlight = true;
  try {
    renderPersonalInvestmentOS(await apiRequest("get_personal_investment_os", { timeoutMs: 30000 }));
  } catch (error) {
    if (!quiet) showToast("个人操作系统同步失败", error.message, "error", 6500);
  } finally {
    state.personalOS.inFlight = false;
  }
}

async function loadSettingsSummary({ quiet = false } = {}) {
  try {
    const [status, daily] = await Promise.all([
      apiRequest("get_status", { timeoutMs: 15000 }),
      apiRequest("get_daily_research", { timeoutMs: 20000 }),
    ]);
    const model = status.model || {};
    text("settingsModelName", model.model || "未配置");
    text("settingsModelStatus", model.connected || model.enabled ? "已连接" : "未连接 / 不可用");
    text("settingsDataSource", daily.data_source?.name || daily.data_source || "同花顺结构化数据");
    const formal = daily.latest_research?.run_id;
    text("settingsResearchState", formal ? `正式run · ${formal}` : "暂无正式收盘run");
  } catch (error) {
    if (!quiet) showToast("设置状态读取失败", error.message, "error", 5000);
  }
}

function setPersonalAssistantIntent(intent) {
  state.personalAssistant.intent = intent;
  document.querySelectorAll("[data-assistant-intent]").forEach((button) => {
    button.classList.toggle("active", button.dataset.assistantIntent === intent);
  });
  byId("personalAssistantSymbolLabel").classList.toggle(
    "hidden", !["stock_reason", "portfolio_fit"].includes(intent),
  );
}

function renderPersonalAssistantAnswer(payload = {}) {
  state.personalAssistant.answer = payload;
  text("personalAssistantHeadline", {
    daily_attention: "今日关注",
    portfolio_analysis: "组合风险",
    stock_reason: "个股关注理由",
    portfolio_fit: "组合适配说明",
    behavior_review: "投资行为复盘",
  }[payload.intent] || "AI投资助手");
  text("personalAssistantMeta", `${payload.model_used ? "模型解释" : "确定性证据摘要"} · ${time(payload.generated_at)}`);
  text("personalAssistantState", payload.status || "unavailable");
  byId("personalAssistantState").className = `badge badge-${payload.status === "published" ? "success" : payload.status === "degraded" ? "warning" : "muted"}`;
  text("personalAssistantSummary", payload.summary || "暂无结论");
  text("personalAssistantNextAction", `下一步：${payload.suggested_next_action || "继续观察"}`);
  const renderList = (containerId, items, empty) => {
    const node = byId(containerId);
    node.replaceChildren();
    (items || []).forEach((item) => {
      const row = document.createElement("p");
      row.textContent = item;
      node.append(row);
    });
    if (!node.children.length) {
      const row = document.createElement("p");
      row.className = "empty-inline";
      row.textContent = empty;
      node.append(row);
    }
  };
  renderList("personalAssistantKeyPoints", payload.key_points, "暂无可展示的关键依据。");
  renderList("personalAssistantRisks", [...(payload.risks || []), ...(payload.uncertainties || [])], "暂无新增风险；仍不代表确定性预测。");
  const evidence = byId("personalAssistantEvidenceList");
  evidence.replaceChildren();
  (payload.evidence_refs || []).forEach((ref) => {
    const row = document.createElement("article");
    row.className = "assistant-evidence-row";
    row.innerHTML = "<strong></strong><dl></dl>";
    row.querySelector("strong").textContent = ref.evidence_id;
    const fields = [["来源", ref.source], ["数据时间", ref.data_time], ["run_id", ref.run_id], ["report_id", ref.report_id], ["策略版本", ref.strategy_version], ["因子版本", ref.factor_version]];
    fields.forEach(([label, value]) => {
      const item = document.createElement("div");
      item.innerHTML = "<dt></dt><dd></dd>";
      item.querySelector("dt").textContent = label;
      item.querySelector("dd").textContent = value || "不可用";
      row.querySelector("dl").append(item);
    });
    evidence.append(row);
  });
  if (!evidence.children.length) {
    const row = document.createElement("p");
    row.className = "empty-inline";
    row.textContent = "当前没有可回放的正式证据引用。";
    evidence.append(row);
  }
  byId("assistantJournalSavePanel").classList.toggle("hidden", !payload.summary);
}

async function queryPersonalAssistant() {
  if (state.personalAssistant.inFlight) return;
  const intent = state.personalAssistant.intent;
  const symbol = byId("personalAssistantSymbol").value.trim().toUpperCase() || null;
  if (["stock_reason", "portfolio_fit"].includes(intent) && !/^\d{6}\.(SH|SZ)$/.test(symbol || "")) {
    showToast("需要股票代码", "请输入如 600519.SH 的沪深股票代码。", "warning", 4500);
    return;
  }
  state.personalAssistant.inFlight = true;
  byId("personalAssistantSubmit").disabled = true;
  text("personalAssistantState", "查询中");
  try {
    const answer = await apiRequest("query_personal_ai_assistant", {
      body: { intent, symbol, question: byId("personalAssistantQuestion").value.trim() || null },
      timeoutMs: 90000,
    });
    renderPersonalAssistantAnswer(answer);
  } catch (error) {
    showToast("AI助手查询失败", error.message, "error", 6500);
  } finally {
    state.personalAssistant.inFlight = false;
    byId("personalAssistantSubmit").disabled = false;
  }
}

function reviewRecordNode(item, { reviewable = false } = {}) {
  const row = document.createElement("article");
  row.className = "review-loop-card";
  const copy = document.createElement("div");
  const title = document.createElement("strong");
  const meta = document.createElement("small");
  const reason = document.createElement("p");
  title.textContent = item.title || item.reminder_type || item.thesis_status || "投资记录";
  meta.textContent = [item.trade_date || item.reviewed_at, item.symbol, item.entry_type || item.status].filter(Boolean).join(" · ");
  reason.textContent = item.reason || item.message || item.result_summary || item.user_text || "暂无摘要";
  copy.append(title, meta, reason); row.append(copy);
  if (reviewable) {
    const button = document.createElement("button");
    button.type = "button"; button.className = "button button-primary button-compact";
    button.dataset.reviewJournalId = item.journal_id; button.textContent = "开始复盘"; row.append(button);
  }
  if (item.reminder_id && ["OPEN", "READ"].includes(item.status)) {
    const actions = document.createElement("div"); actions.className = "review-reminder-actions";
    [["READ","已读"],["DISMISSED","忽略"],["DONE","完成"]].forEach(([status,label]) => {
      const button = document.createElement("button"); button.type="button"; button.className="button button-ghost button-compact";
      button.dataset.reminderId=item.reminder_id; button.dataset.reminderStatus=status; button.textContent=label; actions.append(button);
    }); row.append(actions);
  }
  return row;
}

function fillReviewList(id, items, empty, options = {}) {
  const root = byId(id); root.replaceChildren();
  (items || []).forEach((item) => root.append(reviewRecordNode(item, options)));
  if (!root.children.length) root.append(personalRecordNode(empty, "当前没有需要处理的记录"));
}

function renderInvestmentReviewLoop(payload = {}) {
  state.investmentReview.dashboard = payload;
  const center = payload.review_center || {};
  const today = center.today || {}; const recent = center.recent || {}; const counts = center.counts || {};
  text("reviewPendingCount", counts.pending_reviews ?? 0);
  text("reviewTodayEntryCount", (today.entries || []).length);
  text("reviewReminderCount", (today.reminders || []).length);
  const important = center.most_important || {};
  text("reviewMostImportant", important.title ? `${important.title}：${important.reason || important.message || "请复核"}` : "今日没有紧急复盘任务。 ");
  fillReviewList("reviewDueList", today.due_reviews, "暂无到期复盘", { reviewable: true });
  fillReviewList("reviewDecisionList", recent.decisions, "最近7天暂无决策日志");
  fillReviewList("reviewCompletedList", recent.reviews, "最近暂无已确认复盘");
  fillReviewList("reviewLessonList", recent.lessons, "最近暂无已确认经验");
  fillReviewList("reviewReminderList", today.reminders, "今日暂无站内提醒");
}

async function loadInvestmentReviewLoop({ quiet = false, sync = false } = {}) {
  if (state.investmentReview.inFlight) return;
  state.investmentReview.inFlight = true;
  try {
    renderInvestmentReviewLoop(await apiRequest("get_investment_review_loop", {
      query: { sync_reminders: sync }, timeoutMs: 30000,
    }));
  } catch (error) {
    if (!quiet) showToast("投资复盘同步失败", error.message, "error", 6500);
  } finally { state.investmentReview.inFlight = false; }
}

function splitLines(id) {
  return byId(id).value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
}

async function openJournalReview(journalId) {
  try {
    const draft = await apiRequest("get_investment_review_draft", { pathParams: { journal_id: journalId }, timeoutMs: 30000 });
    state.investmentReview.draft = draft; state.investmentReview.journalId = journalId;
    const root = byId("journalReviewDraft"); root.replaceChildren();
    const original = document.createElement("article"); original.innerHTML = "<strong>当时理由</strong><p></p>";
    original.querySelector("p").textContent = draft.original?.user_text || draft.original?.reason || "未记录"; root.append(original);
    const facts = document.createElement("article"); facts.innerHTML = "<strong>事实变化</strong><dl></dl>";
    Object.entries(draft.facts_changed || {}).forEach(([key,value]) => { const row=document.createElement("div"); row.innerHTML="<dt></dt><dd></dd>"; row.querySelector("dt").textContent=key; row.querySelector("dd").textContent=value == null ? "证据不足" : (typeof value === "object" ? JSON.stringify(value) : String(value)); facts.querySelector("dl").append(row); }); root.append(facts);
    const gaps = document.createElement("article"); gaps.innerHTML="<strong>不确定项</strong><p></p>"; gaps.querySelector("p").textContent=(draft.uncertainties || []).join("；") || "无已知缺口"; root.append(gaps);
    byId("journalReviewDialog").showModal();
  } catch (error) { showToast("复盘草稿生成失败", error.message, "error", 6500); }
}

/**
 * Render the backend-owned PANGU-V3.2 daily cockpit.
 * Formatting helpers receive authoritative values and never derive asset, PnL,
 * exposure, drawdown, factor score or risk decisions in the browser.
 */
function renderInvestmentDashboard(payload = {}) {
  state.investmentDashboard.snapshot = payload;
  MarketCard(byId("dashboardMarketCard"), payload.market || {});
  AssetCard(byId("dashboardAssetCard"), payload.asset || {}, { money, percent: fractionPct });
  RiskCard(byId("dashboardRiskCard"), payload.risk || {});
  AICard(byId("dashboardAICard"), payload.ai_summary || {});
  StockCard(byId("dashboardStockCard"), payload.watchlist || []);
  HoldingHealthCard(byId("dashboardHoldingHealthCard"), payload.holding_health || [], { money, percent: fractionPct });
  TaskCard(byId("dashboardTaskCard"), payload.tasks || []);
  const source = payload.research?.source_mode === "formal_close_plan" ? "正式收盘研究" : "盘中预览 / 无正式计划";
  const stateNode = byId("investmentDashboardState");
  stateNode.className = `badge badge-${payload.research?.used_for_execution ? "success" : "warning"}`;
  stateNode.textContent = `已同步 · ${source} · ${time(payload.created_at)}`;
  text("investmentDashboardSnapshot", payload.snapshot_id || "—");
  text("investmentDashboardRun", payload.research?.run_id || "run_id不可用");
  text("investmentDashboardGaps", (payload.data_gaps || []).length ? `${payload.data_gaps.length}项 · ${payload.data_gaps[0]}` : "无已知证据缺口");
}

async function loadInvestmentDashboard({ quiet = false } = {}) {
  if (state.investmentDashboard.inFlight) return;
  state.investmentDashboard.inFlight = true;
  const button = byId("investmentDashboardRefreshButton");
  button.disabled = true;
  try {
    renderInvestmentDashboard(await apiRequest("get_investment_dashboard_overview", { timeoutMs: 30000 }));
  } catch (error) {
    text("investmentDashboardState", "驾驶舱同步失败");
    if (!quiet) showToast("投资驾驶舱同步失败", error.message, "error", 6500);
  } finally {
    state.investmentDashboard.inFlight = false;
    button.disabled = false;
  }
}

const DATA_COMPONENT_LABELS = {
  completeness: "数据完整性",
  freshness: "更新时间",
  coverage: "覆盖率",
  anomaly: "异常检测",
  consistency: "一致性",
};

function dataHealthTone(status) {
  if (status === "NORMAL") return "success";
  if (status === "WARNING") return "warning";
  if (["ERROR", "BLOCKED"].includes(status)) return "danger";
  return "muted";
}

function dataHealthLabel(status) {
  return {
    NORMAL: "正常",
    WARNING: "警告",
    ERROR: "错误",
    BLOCKED: "已阻断",
    NOT_EVALUATED: "尚未评估",
  }[status] || status || "尚未评估";
}

function dataComponentCard(name, value = {}) {
  const card = document.createElement("article");
  card.className = `data-component-card tone-${dataHealthTone(value.status)}`;
  const heading = document.createElement("div");
  const copy = document.createElement("div");
  const label = document.createElement("strong");
  const weight = document.createElement("small");
  const score = document.createElement("b");
  const meter = document.createElement("div");
  const bar = document.createElement("i");
  const detail = document.createElement("p");
  label.textContent = DATA_COMPONENT_LABELS[name] || name;
  weight.textContent = `权重 ${value.weight ?? "—"} · 贡献 ${value.contribution ?? "—"}`;
  score.textContent = value.score == null ? "—" : `${value.score}`;
  copy.append(label, weight);
  heading.append(copy, score);
  meter.className = "data-component-meter";
  bar.style.setProperty("--score", `${Math.max(0, Math.min(100, Number(value.score || 0)))}%`);
  meter.append(bar);
  const firstIssue = (value.issues || [])[0];
  const ratios = value.checks?.ratios || {};
  const ratioText = Object.entries(ratios).map(([key, ratio]) => `${key} ${Number(ratio * 100).toFixed(0)}%`).join(" · ");
  detail.textContent = firstIssue?.message || ratioText || (value.notes || [])[0] || "检查通过，未发现异常。";
  card.append(heading, meter, detail);
  return card;
}

function dataIncidentCard(item) {
  const card = document.createElement("article");
  card.className = `data-incident-card level-${String(item.level || "warning").toLowerCase()}`;
  const top = document.createElement("div");
  const label = document.createElement("span");
  const status = document.createElement("small");
  const title = document.createElement("strong");
  const description = document.createElement("p");
  const metadata = document.createElement("code");
  label.className = `badge badge-${dataHealthTone(item.level)}`;
  label.textContent = item.level || "WARNING";
  status.textContent = item.status || "OPEN";
  top.append(label, status);
  title.textContent = item.code || item.category || "数据事件";
  description.textContent = item.description || "数据监控发现异常。";
  metadata.textContent = `${item.run_id || "未知run"} · ${String(item.fingerprint || "").slice(0, 12)}`;
  card.append(top, title, description, metadata);
  if (item.status === "OPEN") {
    const button = document.createElement("button");
    button.className = "button button-ghost button-compact";
    button.type = "button";
    button.dataset.ackDataIncident = item.incident_id;
    button.textContent = "标记已读";
    card.append(button);
  }
  return card;
}

function dataLineageNode(item) {
  const node = document.createElement("article");
  const type = document.createElement("span");
  const copy = document.createElement("div");
  const title = document.createElement("strong");
  const id = document.createElement("small");
  type.textContent = item.node_type || "evidence";
  title.textContent = item.label || item.node_id || "证据节点";
  id.textContent = item.node_id || "—";
  copy.append(title, id);
  node.append(type, copy);
  return node;
}

function renderDataIntelligence(payload = {}) {
  state.dataIntelligence.dashboard = payload;
  const health = payload.health || {};
  const latest = payload.data_center?.latest || {};
  const incidents = payload.incidents || { items: [], counts: {} };
  const lineage = payload.lineage || { nodes: [], edges: [], counts: {} };
  const catalog = payload.catalog || [];
  const tone = dataHealthTone(health.status);
  const stateBadge = byId("dataIntelState");
  stateBadge.className = `badge badge-${tone}`;
  stateBadge.textContent = dataHealthLabel(health.status);
  text("dataIntelScore", health.score == null ? "—" : `${health.score}/100`);
  text("dataIntelScoreDetail", health.health_id ? `证据 ${String(health.evidence_hash || "").slice(0, 12)}` : "点击“评估最新数据”生成健康证据");
  text("dataIntelResearchDate", latest.research_date || health.research_date || "—");
  text("dataIntelRunId", latest.run_id || health.run_id || "等待 Data Center run");
  text("dataIntelVersion", latest.data_version || health.data_version || "—");
  text("dataIntelSource", catalog[0]?.source ? `来源 ${catalog[0].source}` : "来源待验证");
  text("dataIntelIncidentCount", incidents.counts?.open ?? 0);
  text("dataIntelBlockedCount", `${incidents.counts?.blocked ?? 0} 条 BLOCKED`);
  text("dataIntelLineageCount", `${lineage.counts?.nodes ?? 0} / ${lineage.counts?.edges ?? 0}`);
  const gate = byId("dataIntelPublishState");
  gate.className = `badge badge-${health.publish_allowed ? "success" : health.status === "NOT_EVALUATED" ? "muted" : "danger"}`;
  gate.textContent = health.publish_allowed ? "正式发布门禁：允许" : "正式发布门禁：阻断";
  byId("dataIntelEvaluateButton").disabled = !payload.data_center?.available || state.dataIntelligence.actionInFlight;

  const componentGrid = byId("dataIntelComponents");
  componentGrid.replaceChildren();
  Object.keys(DATA_COMPONENT_LABELS).forEach((name) => componentGrid.append(dataComponentCard(name, health.components?.[name] || { weight: { completeness: 25, freshness: 20, coverage: 20, anomaly: 20, consistency: 15 }[name] })));

  const incidentList = byId("dataIntelIncidentList");
  incidentList.replaceChildren();
  (incidents.items || []).slice(0, 20).forEach((item) => incidentList.append(dataIncidentCard(item)));
  byId("dataIntelIncidentEmpty").classList.toggle("hidden", Boolean(incidentList.children.length));
  text("dataIntelIncidentBadge", `${incidents.counts?.total ?? 0}`);

  const lineageList = byId("dataIntelLineage");
  lineageList.replaceChildren();
  (lineage.nodes || []).slice(0, 18).forEach((item) => lineageList.append(dataLineageNode(item)));
  byId("dataIntelLineageEmpty").classList.toggle("hidden", Boolean(lineageList.children.length));

  const catalogBody = byId("dataIntelCatalogBody");
  catalogBody.replaceChildren();
  catalog.forEach((item) => {
    const row = document.createElement("tr");
    [
      String(item.run_id || "").slice(0, 10) || "—",
      item.data_version || "—",
      item.source || "—",
      item.coverage == null ? "—" : `${(Number(item.coverage) * 100).toFixed(1)}%`,
      item.quality_score == null ? "—" : `${item.quality_score}`,
      item.status || "—",
      String(item.manifest_hash || "").slice(0, 16),
    ].forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    });
    catalogBody.append(row);
  });
  byId("dataIntelCatalogEmpty").classList.toggle("hidden", Boolean(catalogBody.children.length));
  text("dataIntelCatalogCount", `${catalog.length} 个版本`);
}

async function loadDataIntelligence({ quiet = false } = {}) {
  if (state.dataIntelligence.inFlight) return null;
  state.dataIntelligence.inFlight = true;
  try {
    const payload = await apiRequest("get_data_intelligence_dashboard", { timeoutMs: 30000 });
    renderDataIntelligence(payload);
    return payload;
  } catch (error) {
    if (!quiet) showToast("数据中心同步失败", error.message, "error", 6500);
    return null;
  } finally {
    state.dataIntelligence.inFlight = false;
  }
}

async function evaluateLatestData() {
  if (state.dataIntelligence.actionInFlight) return;
  state.dataIntelligence.actionInFlight = true;
  const button = byId("dataIntelEvaluateButton");
  button.disabled = true;
  button.textContent = "评估中…";
  try {
    const health = await apiRequest("evaluate_data_intelligence_run", { timeoutMs: 120000 });
    await loadDataIntelligence();
    showToast(
      "数据健康评估完成",
      `${dataHealthLabel(health.status)} · ${health.score}/100 · 正式发布${health.publish_allowed ? "允许" : "已阻断"}`,
      health.status === "NORMAL" ? "success" : "error",
      5200,
    );
  } catch (error) {
    showToast("数据健康评估失败", error.message, "error", 7000);
  } finally {
    state.dataIntelligence.actionInFlight = false;
    button.textContent = "评估最新数据";
    button.disabled = !state.dataIntelligence.dashboard?.data_center?.available;
  }
}

async function acknowledgeDataIncident(incidentId, button) {
  if (!incidentId || button.disabled) return;
  button.disabled = true;
  button.textContent = "确认中…";
  try {
    await apiRequest("acknowledge_data_incident", {
      pathParams: { incident_id: incidentId }, timeoutMs: 30000,
    });
    await loadDataIntelligence();
    showToast("事件已标记为已读", "数据门禁状态没有改变，异常证据仍完整保留。", "success");
  } catch (error) {
    button.disabled = false;
    button.textContent = "标记已读";
    showToast("事件确认失败", error.message, "error", 6500);
  }
}

const EVOLUTION_COMPONENT_LABELS = {
  performance: "绩效", risk: "风险", factor: "因子",
  execution: "执行", environment: "环境",
};

function evolutionTone(status) {
  const value = String(status || "").toUpperCase();
  if (["HEALTHY", "STABLE", "APPROVED"].includes(value)) return "success";
  if (["WATCH", "BASELINE_BUILDING", "PENDING"].includes(value)) return "warning";
  if (["DECAYING", "CRITICAL", "REJECTED"].includes(value)) return "danger";
  return "muted";
}

function evolutionBranchKey(branch) {
  return `${branch.strategy_id || ""}::${branch.version || ""}`;
}

function evolutionSelectedBranch(payload = state.strategyEvolution.dashboard) {
  const branches = payload?.branches || [];
  return branches.find((item) => evolutionBranchKey(item) === state.strategyEvolution.selectedBranchKey) || branches[0] || null;
}

function renderEvolutionComponents(health = {}) {
  const componentGrid = byId("evolutionComponentGrid");
  componentGrid.replaceChildren();
  Object.entries(EVOLUTION_COMPONENT_LABELS).forEach(([key, label]) => {
    const card = document.createElement("article");
    const top = document.createElement("div");
    const name = document.createElement("strong");
    const value = document.createElement("span");
    const meter = document.createElement("i");
    const score = health.components?.[key];
    name.textContent = label;
    value.textContent = score == null ? "缺证据" : `${score}/100`;
    top.append(name, value);
    meter.style.setProperty("--score", `${Math.max(0, Math.min(100, Number(score || 0)))}%`);
    card.append(top, meter);
    componentGrid.append(card);
  });

  const factorGrid = byId("evolutionFactorGrid");
  factorGrid.replaceChildren();
  const labels = Object.fromEntries(FACTOR_LABELS.map(([key, label]) => [key, label]));
  (health.factor_drift?.factors || []).forEach((factor) => {
    const item = document.createElement("article");
    const title = document.createElement("strong");
    const status = badge(factor.status || "UNAVAILABLE", evolutionTone(factor.status));
    const detail = document.createElement("small");
    const current = factor.current || {};
    title.textContent = labels[factor.factor_name] || factor.factor_name || "因子";
    detail.textContent = `IC ${current.ic ?? "—"} · ICIR ${current.icir ?? "—"} · 贡献 ${current.contribution ?? "—"}`;
    item.append(title, status, detail);
    factorGrid.append(item);
  });
  if (!factorGrid.children.length) {
    const empty = document.createElement("p");
    empty.className = "empty-inline";
    empty.textContent = "生成至少一次健康观察后显示七因子漂移。";
    factorGrid.append(empty);
  }
}

function renderStrategyEvolution(payload = {}) {
  state.strategyEvolution.dashboard = payload;
  const branches = payload.branches || [];
  const counts = payload.counts || {};
  const latest = evolutionSelectedBranch(payload)?.latest_health || payload.health_history?.[0] || null;
  const stateBadge = byId("evolutionState");
  stateBadge.className = `badge badge-${evolutionTone(latest?.status)}`;
  stateBadge.textContent = latest?.status || "等待策略证据";
  text("evolutionBranchCount", counts.branches ?? branches.length);
  text("evolutionHealthScore", latest?.score == null ? "—" : `${latest.score}/100`);
  text("evolutionHealthStatus", latest ? `${latest.status} · 覆盖 ${Math.round(Number(latest.coverage || 0) * 100)}%` : "缺证据不重分配权重");
  text("evolutionDecayCount", counts.decaying ?? 0);
  text("evolutionComparisonCount", counts.comparisons ?? 0);
  text("evolutionPendingCount", counts.pending_transitions ?? 0);

  const select = byId("evolutionStrategySelect");
  const selectedStrategy = select.value;
  select.replaceChildren();
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "选择已登记策略";
  select.append(placeholder);
  (payload.available_validation_strategies || []).forEach((strategy) => {
    const option = document.createElement("option");
    option.value = strategy.strategy_id;
    option.dataset.version = strategy.active_version || "";
    option.textContent = `${strategy.name || strategy.strategy_id} · ${strategy.current_state || "DRAFT"}`;
    select.append(option);
  });
  if ([...select.options].some((option) => option.value === selectedStrategy)) select.value = selectedStrategy;

  const branchList = byId("evolutionBranchList");
  branchList.replaceChildren();
  branches.forEach((branch) => {
    const card = document.createElement("button");
    const top = document.createElement("span");
    const title = document.createElement("strong");
    const lifecycle = badge(branch.lifecycle_state || "DRAFT", "info");
    const health = branch.latest_health;
    const score = document.createElement("b");
    const detail = document.createElement("small");
    card.type = "button";
    card.className = "evolution-branch-card";
    card.dataset.evolutionBranch = evolutionBranchKey(branch);
    card.classList.toggle("active", evolutionBranchKey(branch) === evolutionBranchKey(evolutionSelectedBranch(payload) || {}));
    title.textContent = `${branch.strategy_name || branch.strategy_id} · ${branch.version}`;
    top.append(title, lifecycle);
    score.textContent = health?.score == null ? "健康分 —" : `健康分 ${health.score}`;
    detail.textContent = `${branch.branch_name} · Gate ${branch.validation_state_current || "—"} · 下一阶段 ${branch.next_state || "终态"}`;
    card.append(top, score, detail);
    branchList.append(card);
  });
  byId("evolutionBranchEmpty").classList.toggle("hidden", Boolean(branchList.children.length));
  renderEvolutionComponents(latest || {});

  const comparisonList = byId("evolutionComparisonList");
  comparisonList.replaceChildren();
  (payload.comparisons || []).slice(0, 8).forEach((item) => {
    const card = document.createElement("article");
    const title = document.createElement("strong");
    const detail = document.createElement("small");
    title.textContent = `${item.version_a} ↔ ${item.version_b}`;
    detail.textContent = `健康 ${item.health_a ?? "—"} / ${item.health_b ?? "—"} · 不自动选胜者`;
    card.append(title, detail);
    comparisonList.append(card);
  });

  const lifecycleList = byId("evolutionLifecycleList");
  lifecycleList.replaceChildren();
  (payload.lifecycle_history || []).slice(0, 12).forEach((item) => {
    const card = document.createElement("article");
    const copy = document.createElement("div");
    const title = document.createElement("strong");
    const detail = document.createElement("small");
    title.textContent = `${item.from_state} → ${item.to_state}`;
    detail.textContent = `${item.strategy_version} · ${item.requested_by} · ${time(item.requested_at)}`;
    copy.append(title, detail);
    card.append(copy, badge(item.status, evolutionTone(item.status)));
    if (item.status === "PENDING") {
      const actions = document.createElement("div");
      [
        ["approve", "批准", "button-primary"], ["reject", "拒绝", "button-danger"],
      ].forEach(([decision, label, css]) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = `button ${css} button-compact`;
        button.dataset.evolutionDecision = decision;
        button.dataset.requestId = item.request_id;
        button.textContent = label;
        actions.append(button);
      });
      card.append(actions);
    }
    lifecycleList.append(card);
  });

  const observer = byId("evolutionObserver");
  observer.replaceChildren();
  const observerCopy = document.createElement("p");
  observerCopy.textContent = latest
    ? `当前观察：${latest.status}。衰减 ${latest.decay?.status || "—"}，因子漂移 ${latest.factor_drift?.status || "—"}。所有输出仅形成研究问题。`
    : "尚无封存健康观察。Strategy Observer 不会在缺证据时推断策略状态。";
  observer.append(observerCopy);

  const reportList = byId("evolutionReportList");
  reportList.replaceChildren();
  (payload.reports || []).slice(0, 10).forEach((item) => {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.evolutionReport = item.report_id;
    button.textContent = `健康报告 · ${String(item.report_hash || "").slice(0, 12)} · ${time(item.created_at)}`;
    reportList.append(button);
  });
}

async function loadStrategyEvolution({ quiet = false } = {}) {
  if (state.strategyEvolution.inFlight) return null;
  state.strategyEvolution.inFlight = true;
  try {
    const payload = await apiRequest("get_strategy_evolution_center", { timeoutMs: 30000 });
    renderStrategyEvolution(payload);
    return payload;
  } catch (error) {
    if (!quiet) showToast("策略进化中心同步失败", error.message, "error", 6500);
    return null;
  } finally {
    state.strategyEvolution.inFlight = false;
  }
}

function selectedEvolutionIdentity() {
  const branch = evolutionSelectedBranch();
  return {
    strategy_id: branch?.strategy_id || byId("evolutionStrategySelect").value,
    version: branch?.version || byId("evolutionVersionInput").value.trim(),
  };
}

async function runEvolutionAction(operation, options, successMessage) {
  if (state.strategyEvolution.actionInFlight) return null;
  state.strategyEvolution.actionInFlight = true;
  try {
    const result = await apiRequest(operation, { ...options, timeoutMs: 120000 });
    await loadStrategyEvolution();
    showToast("策略进化证据已更新", successMessage, "success");
    return result;
  } catch (error) {
    showToast("策略进化操作未完成", error.message, "error", 7000);
    return null;
  } finally {
    state.strategyEvolution.actionInFlight = false;
  }
}

async function importEvolutionBranch(event) {
  event.preventDefault();
  const body = {
    strategy_id: byId("evolutionStrategySelect").value,
    version: byId("evolutionVersionInput").value.trim(),
    branch_name: byId("evolutionBranchNameInput").value.trim(),
    parent_version: byId("evolutionParentVersionInput").value.trim() || null,
  };
  state.strategyEvolution.selectedBranchKey = `${body.strategy_id}::${body.version}`;
  const result = await runEvolutionAction(
    "import_strategy_evolution_branch", { body }, "不可变策略分支已登记；未复制或修改执行代码。",
  );
  if (!result) state.strategyEvolution.selectedBranchKey = null;
}

async function evaluateEvolutionBranch() {
  const identity = selectedEvolutionIdentity();
  if (!identity.strategy_id || !identity.version) {
    showToast("请选择策略版本", "先导入或选择一个策略分支。", "error");
    return;
  }
  await runEvolutionAction(
    "evaluate_strategy_evolution", { body: { ...identity, review_id: null } },
    "Strategy Health 已从封存审查生成；没有调整参数或因子权重。",
  );
}

async function compareEvolutionVersions(event) {
  event.preventDefault();
  await runEvolutionAction("compare_strategy_versions", {
    body: {
      strategy_id: byId("evolutionCompareStrategy").value.trim(),
      version_a: byId("evolutionVersionA").value.trim(),
      version_b: byId("evolutionVersionB").value.trim(),
    },
  }, "版本比较已封存；系统没有选择自动胜者。");
}

async function requestEvolutionTransition(event) {
  event.preventDefault();
  await runEvolutionAction("request_strategy_evolution_transition", {
    body: {
      strategy_id: byId("evolutionTransitionStrategy").value.trim(),
      version: byId("evolutionTransitionVersion").value.trim(),
      target_state: byId("evolutionTargetState").value,
      requested_by: byId("evolutionRequestedBy").value.trim(),
      reason: byId("evolutionTransitionReason").value.trim(),
    },
  }, "人工治理申请已登记，策略状态尚未改变。");
}

async function decideEvolutionTransition(requestId, decision) {
  const actor = window.prompt("请输入人工审批人身份：", "本人");
  if (!actor) return;
  const reason = window.prompt("请输入审批理由（至少8个字符）：", "已人工核对全部证据与风险边界");
  if (!reason) return;
  await runEvolutionAction(
    decision === "approve" ? "approve_strategy_evolution_transition" : "reject_strategy_evolution_transition",
    { pathParams: { request_id: requestId }, body: { actor, reason } },
    decision === "approve" ? "生命周期已按顺序人工批准；不影响交易系统。" : "申请已人工拒绝；当前生命周期未改变。",
  );
}

async function openEvolutionReport(reportId) {
  try {
    const report = await apiRequest("get_strategy_evolution_report", {
      pathParams: { report_id: reportId }, timeoutMs: 30000,
    });
    const detail = byId("evolutionReportDetail");
    detail.textContent = JSON.stringify(report.sections || report, null, 2);
    detail.classList.remove("hidden");
  } catch (error) {
    showToast("健康报告读取失败", error.message, "error", 6500);
  }
}

const ENGINEERING_COMPONENT_LABELS = {
  data: "数据资产",
  database: "数据库",
  ai: "AI 服务",
  strategy: "策略合同",
};

function engineeringTone(status) {
  if (status === "HEALTHY" || status === "SUCCEEDED") return "success";
  if (status === "DEGRADED" || status === "RUNNING") return "warning";
  if (status === "UNHEALTHY" || status === "FAILED") return "danger";
  return "muted";
}

function renderEngineeringComponents(components = {}) {
  const container = byId("engineeringComponentGrid");
  container.replaceChildren();
  const entries = Object.entries(components);
  byId("engineeringComponentEmpty").classList.toggle("hidden", entries.length > 0);
  entries.forEach(([key, component]) => {
    const card = document.createElement("article");
    card.className = "engineering-component-card";
    const header = document.createElement("header");
    const copy = document.createElement("div");
    const title = document.createElement("span");
    const score = document.createElement("strong");
    const status = badge(component.status || "UNKNOWN", engineeringTone(component.status));
    title.textContent = ENGINEERING_COMPONENT_LABELS[key] || key;
    score.textContent = component.score == null ? "—" : `${component.score} / 100`;
    copy.append(title, score);
    header.append(copy, status);
    const meter = document.createElement("meter");
    meter.min = "0";
    meter.max = "100";
    meter.value = String(component.score ?? 0);
    const message = document.createElement("p");
    message.textContent = component.message || "无说明";
    const gaps = document.createElement("small");
    gaps.textContent = component.data_gaps?.length ? `数据缺口：${component.data_gaps.join("、")}` : "证据完整性未发现额外缺口";
    card.append(header, meter, message, gaps);
    container.append(card);
  });
}

function renderEngineeringBackups(items = []) {
  const container = byId("engineeringBackupList");
  container.replaceChildren();
  byId("engineeringBackupEmpty").classList.toggle("hidden", items.length > 0);
  items.forEach((item) => {
    const card = document.createElement("article");
    const header = document.createElement("header");
    const title = document.createElement("strong");
    title.textContent = item.backup_id || "未命名备份";
    header.append(title, badge(item.status || "UNKNOWN", engineeringTone(item.status)));
    const path = document.createElement("code");
    path.textContent = item.backup_path || "—";
    const meta = document.createElement("small");
    meta.textContent = `${time(item.completed_at || item.started_at)} · ${item.file_count || 0} 个文件 · ${item.byte_count || 0} bytes`;
    const hash = document.createElement("small");
    hash.textContent = item.manifest_hash ? `manifest ${item.manifest_hash.slice(0, 16)}…` : (item.error_message || "等待清单");
    card.append(header, path, meta, hash);
    container.append(card);
  });
}

function renderEngineeringEvents(items = []) {
  const container = byId("engineeringEventList");
  container.replaceChildren();
  byId("engineeringEventEmpty").classList.toggle("hidden", items.length > 0);
  items.forEach((item) => {
    const card = document.createElement("article");
    const header = document.createElement("header");
    const title = document.createElement("strong");
    title.textContent = `${item.module || "unknown"} · ${item.event || "event"}`;
    header.append(title, badge(item.level || "INFO", item.level === "ERROR" || item.level === "CRITICAL" ? "danger" : item.level === "WARNING" ? "warning" : "muted"));
    const message = document.createElement("p");
    message.textContent = item.message || "";
    const meta = document.createElement("small");
    meta.textContent = `${time(item.timestamp)} · trace ${String(item.trace_id || "—").slice(0, 12)}`;
    card.append(header, message, meta);
    container.append(card);
  });
}

function renderEngineeringCenter(payload = {}) {
  state.engineering.dashboard = payload;
  const health = payload.health || {};
  const versions = payload.versions || {};
  const config = payload.configuration || {};
  const strategyVersions = versions.strategy_versions || {};
  const factorVersions = versions.factor_versions || {};
  text("engineeringScore", health.score == null ? "—" : String(health.score));
  text("engineeringCheckedAt", health.checked_at ? `检查于 ${time(health.checked_at)}` : "等待首次检查");
  text("engineeringState", health.status || "NOT_EVALUATED");
  byId("engineeringState").className = `badge badge-${engineeringTone(health.status)}`;
  text("engineeringSystemVersion", versions.system_version || "—");
  text("engineeringVersionHash", versions.manifest_hash ? `清单 ${versions.manifest_hash.slice(0, 16)}…` : "版本清单未加载");
  text("engineeringStrategyVersion", strategyVersions.production || "—");
  text("engineeringFactorVersion", `因子 ${factorVersions.production || "—"}`);
  text("engineeringEnvironment", config.environment || "—");
  text("engineeringConfigHash", config.config_hash ? `配置 ${config.config_hash.slice(0, 16)}…` : "—");
  text("engineeringBackupCount", payload.counts?.successful_backup_count ?? 0);
  renderEngineeringComponents(health.components || {});
  renderEngineeringBackups(payload.backups || []);
  renderEngineeringEvents(payload.events || []);
}

async function loadEngineeringCenter({ quiet = false } = {}) {
  if (state.engineering.inFlight) return;
  state.engineering.inFlight = true;
  try {
    renderEngineeringCenter(await apiRequest("get_engineering_dashboard", { timeoutMs: 20000 }));
  } catch (error) {
    if (!quiet) showToast("工程健康读取失败", error.message, "error", 6500);
  } finally {
    state.engineering.inFlight = false;
  }
}

async function runEngineeringAction(operation, button, workingMessage, successMessage) {
  if (state.engineering.actionInFlight) return;
  state.engineering.actionInFlight = true;
  button.disabled = true;
  showToast("工程任务已启动", workingMessage, "info", 3200);
  try {
    await apiRequest(operation, { body: {}, timeoutMs: operation === "create_engineering_backup" ? 180000 : 45000 });
    await loadEngineeringCenter();
    showToast("工程任务完成", successMessage, "success", 4800);
  } catch (error) {
    showToast("工程任务失败", error.message, "error", 7000);
  } finally {
    state.engineering.actionInFlight = false;
    button.disabled = false;
  }
}

function observabilityTone(status) {
  if (["HEALTHY", "OBSERVING", "SUCCEEDED", "OK", "RESOLVED", "NOT_APPLICABLE"].includes(status)) return "success";
  if (["AT_RISK", "ALERTING", "WARNING", "RUNNING", "ACKNOWLEDGED", "INSUFFICIENT_DATA"].includes(status)) return "warning";
  if (["BREACHED", "CRITICAL", "ERROR", "FAILED", "STALE", "BLOCKED", "OPEN"].includes(status)) return "danger";
  return "muted";
}

function renderObservationList(id, emptyId, items, builder) {
  const container = byId(id);
  container.replaceChildren();
  byId(emptyId).classList.toggle("hidden", items.length > 0);
  items.forEach((item) => container.append(builder(item)));
}

function observationCard(titleText, status, message, meta) {
  const card = document.createElement("article");
  const header = document.createElement("header");
  const title = document.createElement("strong");
  title.textContent = titleText;
  header.append(title, badge(status || "UNKNOWN", observabilityTone(status)));
  const copy = document.createElement("p");
  copy.textContent = message || "";
  const small = document.createElement("small");
  small.textContent = meta || "";
  card.append(header, copy, small);
  return card;
}

function renderObservabilityCenter(payload = {}) {
  state.observability.dashboard = payload;
  text("observabilityState", payload.status || "等待观测");
  byId("observabilityState").className = `badge badge-${observabilityTone(payload.status)}`;
  text("observabilityAlertCount", payload.counts?.active_alert_count ?? 0);
  const slos = payload.slos || [];
  const breached = slos.filter((item) => item.status === "BREACHED").length;
  const available = slos.filter((item) => !["INSUFFICIENT_DATA", "NOT_APPLICABLE"].includes(item.status)).length;
  text("observabilitySloState", breached ? `${breached}项突破` : available ? "无突破" : "证据不足");
  text("observabilitySloCoverage", `${available}/${slos.length} 项有可判定证据`);
  const jobs = payload.jobs || [];
  text("observabilityJobState", jobs[0]?.status || "—");
  text("observabilityJobTime", jobs[0] ? `${jobs[0].job_type} · ${time(jobs[0].completed_at || jobs[0].started_at)}` : "尚无作业");
  const latency = (payload.metrics || []).find((item) => item.metric_name === "api.request.duration");
  text("observabilityApiP95", latency?.p95 == null ? "—" : `${Number(latency.p95).toFixed(1)} ms`);
  text("observabilityTraceCount", payload.counts?.trace_count ?? 0);

  const alerts = (payload.alerts || []).filter((item) => ["OPEN", "ACKNOWLEDGED"].includes(item.status));
  renderObservationList("observabilityAlertList", "observabilityAlertEmpty", alerts, (item) => {
    const card = observationCard(item.message || item.rule_id, item.severity, item.recommended_manual_action, `${item.status} · ${time(item.last_seen_at)}`);
    if (item.status === "OPEN") {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "button button-ghost button-compact";
      button.dataset.observabilityAck = item.alert_id;
      button.textContent = "确认已查看";
      card.append(button);
    }
    return card;
  });
  renderObservationList("observabilitySloList", "observabilitySloEmpty", slos, (item) => observationCard(
    item.name || item.slo_id, item.status,
    item.actual == null ? "证据不足，未判定达标" : `实际 ${Number(item.actual).toFixed(4)} · 目标 ${item.target}`,
    `样本 ${item.sample_count || 0} · 窗口 ${item.window || "—"}秒`,
  ));
  renderObservationList("observabilityJobList", "observabilityJobEmpty", jobs.slice(0, 20), (item) => observationCard(
    item.job_type || item.job_id, item.status, item.error_message || `关联 ${item.linked_run_id || item.trace_id || "无"}`,
    `${time(item.started_at || item.scheduled_for)} · ${String(item.job_id).slice(0, 20)}`,
  ));
  renderObservationList("observabilityMetricList", "observabilityMetricEmpty", payload.metrics || [], (item) => observationCard(
    item.metric_name, item.count ? "OK" : "INSUFFICIENT_DATA",
    item.count ? `p50 ${Number(item.p50 ?? 0).toFixed(2)} · p95 ${Number(item.p95 ?? 0).toFixed(2)} · p99 ${Number(item.p99 ?? 0).toFixed(2)}` : "暂无样本",
    `${item.count || 0} 个样本 · ${item.unit || "—"}`,
  ));
  renderObservationList("observabilityTraceList", "observabilityTraceEmpty", payload.traces || [], (item) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "observability-trace-card";
    button.dataset.observabilityTrace = item.trace_id;
    const title = document.createElement("strong");
    title.textContent = item.root_operation;
    const meta = document.createElement("small");
    meta.textContent = `${item.status} · ${Number(item.duration_ms || 0).toFixed(1)} ms · ${item.trace_id}`;
    button.append(title, meta);
    return button;
  });
  renderObservationList("observabilityIncidentList", "observabilityIncidentEmpty", payload.incidents || [], (item) => observationCard(
    item.title, item.status, item.impact || "无影响说明", `${item.severity} · v${item.version} · ${time(item.updated_at)}`,
  ));
  renderObservationList("observabilityRetentionList", "observabilityRetentionEmpty", payload.retention || [], (item) => observationCard(
    item.retention_id, item.status, `计划 ${item.plan_hash?.slice(0, 16) || "—"}…`, time(item.executed_at || item.planned_at),
  ));
}

async function loadObservabilityCenter({ quiet = false } = {}) {
  if (state.observability.inFlight) return;
  state.observability.inFlight = true;
  try {
    const hours = Number(byId("observabilityTimeRange").value || 24);
    renderObservabilityCenter(await apiRequest("get_observability_dashboard", { query: { hours }, timeoutMs: 30000 }));
  } catch (error) {
    if (!quiet) showToast("可观测中心读取失败", error.message, "error", 6500);
  } finally {
    state.observability.inFlight = false;
  }
}

async function runObservabilityOperation(operation, options = {}, message = "可观测证据已更新") {
  if (state.observability.actionInFlight) return;
  state.observability.actionInFlight = true;
  try {
    const result = await apiRequest(operation, { ...options, timeoutMs: 45000 });
    await loadObservabilityCenter();
    showToast("可观测任务完成", message, "success");
    return result;
  } catch (error) {
    showToast("可观测任务失败", error.message, "error", 6500);
    return null;
  } finally {
    state.observability.actionInFlight = false;
  }
}

async function runPersonalAction(operation, options, successMessage) {
  try {
    await apiRequest(operation, { ...options, timeoutMs: 45000 });
    await loadPersonalInvestmentOS();
    showToast("个人操作系统已更新", successMessage, "success");
  } catch (error) {
    showToast("操作未完成", error.message, "error", 6500);
  }
}

function personalToday() {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai" }).format(new Date());
}

function openReviewQuickForm({ entryType = "OBSERVE", symbol = "", title = "", reason = "" } = {}) {
  activateWorkspace("review");
  byId("reviewQuickType").value = entryType;
  byId("reviewQuickSymbol").value = symbol;
  byId("reviewQuickTitle").value = title;
  byId("reviewQuickReason").value = reason;
  byId("reviewQuickReason").focus();
}

function journalCreateBody({ entryType, symbol, title, reason, cycle = "NONE", customDate = null, source = "USER", aiSummary = null, evidenceRefs = [] }) {
  const evidenceIds = (evidenceRefs || []).map((item) => item?.evidence_id).filter(Boolean);
  const relatedRun = (evidenceRefs || []).find((item) => item?.run_id)?.run_id || null;
  return {
    idempotency_key: crypto.randomUUID(),
    entry_type: entryType,
    trade_date: personalToday(),
    symbol: symbol || null,
    title,
    reason,
    user_text: reason,
    ai_summary: aiSummary,
    related_run_id: relatedRun,
    review_cycle: cycle,
    custom_review_date: cycle === "CUSTOM" ? customDate : null,
    source,
    evidence_ids: evidenceIds,
  };
}

// A paper-ledger mutation fans out to every account-facing authority. These
// are independent generated-client reads; one slow module cannot block the
// others and no browser-side asset calculation is introduced.
async function refreshAccountAuthorities(source = "account") {
  const jobs = ["daily", "review", "operating", "dashboard", "personal", "reviewloop"];
  return Promise.all(jobs.map((jobKey) => syncModuleJob(jobKey, { source, quiet: true })));
}

async function refreshAll({ source = "manual" } = {}) {
  renderLiveSyncCenter();
  const results = await Promise.all(CORE_SYNC_JOBS.map((jobKey) => syncModuleJob(jobKey, { source, quiet: source === "auto" })));
  const errors = results.filter((result) => !result.ok && !result.skipped).map((result) => `${LIVE_MODULE_LABELS[SYNC_JOB_CONFIG[result.jobKey].moduleKeys[0]]}：${result.error?.message || "同步失败"}`);
  if (errors.length) showError(errors.join("；")); else clearError();
  renderLiveSyncCenter();
  return errors.length === 0;
}

// 盘中候选自动重算使用现有只读研究操作；不触发正式计划、监控或任何模拟订单。
async function refreshCandidatesAutomatically() {
  if (state.candidateRefreshInFlight) return;
  state.candidateRefreshInFlight = true;
  state.candidateAutoNextAt = Date.now() + state.candidateAutoIntervalSeconds * 1000;
  markLiveModule("candidates", "busy", "正在重算全市场候选");
  renderLiveSyncCenter();
  try {
    await apiRequest("refresh_intraday_candidates", { timeoutMs: 300000 });
    markLiveModule("candidates", "ok", `盘中预览 · ${clockLabel()}`);
    await refreshAll({ source: "candidate" });
    showToast("候选榜自动更新", "新的盘中预览已生成；没有提交任何模拟订单。", "success", 3200);
  } catch (error) {
    markLiveModule("candidates", "error", `重算失败 · ${clockLabel()}`);
    showToast("候选榜自动更新失败", error.message, "error", 6500);
  } finally {
    state.candidateRefreshInFlight = false;
    state.candidateAutoNextAt = Date.now() + state.candidateAutoIntervalSeconds * 1000;
    renderLiveSyncCenter();
  }
}

// 通用动作包装器统一处理忙碌态、结果提示和刷新，底层请求仍只走生成客户端。
async function runAction(operation, options = {}, timeoutMs = 180000, feedback = {}) {
  const candidateRefresh = operation === "refresh_intraday_candidates";
  if (candidateRefresh && state.candidateRefreshInFlight) {
    showToast("候选榜正在更新", "请等待当前全市场重算完成，不会重复发起请求。", "info", 3200);
    return;
  }
  if (candidateRefresh) {
    state.candidateRefreshInFlight = true;
    markLiveModule("candidates", "busy", "正在手动重算全市场候选");
  }
  clearError();
  const button = feedback.buttonId ? byId(feedback.buttonId) : null;
  const originalLabel = button?.textContent;
  if (button) {
    button.disabled = true;
    button.textContent = feedback.busyLabel || "处理中…";
  }
  if (feedback.overlay !== false) {
    setActionOverlay(true, feedback.overlayTitle || "正在处理", feedback.overlayMessage || "完成后会自动同步最新状态，请不要重复点击。");
  }
  try {
    await apiRequest(operation, { ...options, timeoutMs });
    if (candidateRefresh) markLiveModule("candidates", "ok", `盘中预览 · ${clockLabel()}`);
    await refreshAll({ source: "action" });
    showToast(feedback.successTitle || "操作完成", feedback.successMessage || "最新状态已同步。", "success");
  } catch (error) {
    if (candidateRefresh) markLiveModule("candidates", "error", `重算失败 · ${clockLabel()}`);
    showError(error.message);
    showToast(feedback.errorTitle || "操作失败", error.message, "error", 6500);
  } finally {
    if (candidateRefresh) {
      state.candidateRefreshInFlight = false;
      state.candidateAutoNextAt = Date.now() + state.candidateAutoIntervalSeconds * 1000;
    }
    if (feedback.overlay !== false) setActionOverlay(false);
    if (button) {
      button.textContent = originalLabel;
      if (operation === "execute_daily_paper_plan") button.disabled = Boolean(state.daily?.account?.kill_switch) || !state.daily?.automation_execution_ready;
      else if (operation === "collect_daily_research") button.disabled = Boolean(state.daily?.account?.kill_switch);
      else button.disabled = false;
    }
  }
}

// 顶部刷新只同步现有状态，不重新采集市场数据。
async function manualRefresh() {
  const button = byId("refreshButton");
  button.disabled = true;
  const original = button.textContent;
  button.textContent = "同步中…";
  try {
    await refreshAll({ source: "manual" });
    showToast("页面已刷新", "研究、行情和模拟账户状态已同步。", "success", 2600);
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

function setLiveSyncEnabled(enabled) {
  state.liveSyncEnabled = Boolean(enabled);
  if (state.liveSyncEnabled) {
    startLiveSync({ immediate: true });
    if (state.activeWorkspace === "market") startQuotePolling();
  } else {
    stopLiveSync();
    stopQuotePolling();
  }
  showToast(
    state.liveSyncEnabled ? "实时同步已开启" : "实时同步已暂停",
    state.liveSyncEnabled ? `页面状态将每${state.liveSyncIntervalSeconds}秒自动更新。` : "行情轮询和候选自动重算也已暂停，手动刷新仍可用。",
    state.liveSyncEnabled ? "success" : "info",
    3000,
  );
}

function changeLiveSyncInterval(seconds) {
  state.liveSyncIntervalSeconds = Math.max(10, Math.min(60, Number(seconds) || 15));
  CORE_SYNC_JOBS.forEach((key) => { state.syncJobs[key].nextAt = Date.now() + syncJobIntervalMs(key); });
  startLiveSync();
  showToast("同步频率已更新", `${CORE_SYNC_JOBS.length}个模块通道每${state.liveSyncIntervalSeconds}秒独立同步。`, "success", 2600);
}

function changeCandidateAutoRefresh(enabled, seconds = state.candidateAutoIntervalSeconds) {
  state.candidateAutoEnabled = Boolean(enabled);
  state.candidateAutoIntervalSeconds = Math.max(300, Math.min(900, Number(seconds) || 300));
  state.candidateAutoNextAt = Date.now() + state.candidateAutoIntervalSeconds * 1000;
  markLiveModule(
    "candidates",
    state.candidateAutoEnabled ? (isMarketMonitoringWindow() ? "ok" : "paused") : "paused",
    state.candidateAutoEnabled ? (isMarketMonitoringWindow() ? `将在${state.candidateAutoIntervalSeconds / 60}分钟内重算` : "等待交易时段 · 只重算不成交") : "盘中自动重算已关闭",
  );
  renderLiveSyncCenter();
}

// 实时中心的单模块刷新保持和自动调度相同的锁、超时与退避规则。
async function refreshLiveModuleNow(jobKey) {
  if (jobKey === "candidates") {
    activateWorkspace("market");
    byId("previewButton").click();
    return;
  }
  if (jobKey === "quote") {
    activateWorkspace("market");
    await fetchLiveQuote();
    return;
  }
  const result = await syncModuleJob(jobKey, { source: "manual", quiet: false });
  if (result.ok) {
    clearError();
    showToast("模块已更新", `${LIVE_MODULE_LABELS[SYNC_JOB_CONFIG[jobKey].moduleKeys[0]]}已同步最新状态。`, "success", 2600);
  } else if (!result.skipped) {
    showError(result.error?.message || "模块同步失败。");
    showToast("模块更新失败", result.error?.message || "请等待自动重试。", "error", 5200);
  }
}

async function toggleSharedKillSwitch() {
  const enabled = !(state.status?.safety?.kill_switch ?? state.daily?.account?.kill_switch ?? false);
  const message = enabled ? "触发后将禁止新的全市场模拟开仓；持仓复盘仍保持只读。继续吗？" : "解除安全停止不会开启实盘。继续吗？";
  if (!window.confirm(message)) return;
  await runAction("set_kill_switch", { body: { enabled } }, 30000);
}

async function configureDeepSeek(event) {
  event.preventDefault();
  clearError();
  const keyInput = byId("deepseekKey");
  const saveButton = byId("modelSaveButton");
  const apiKey = keyInput.value.trim();
  if (!apiKey.startsWith("sk-") || apiKey.length < 20) {
    showError("DeepSeek API Key 格式无效。");
    keyInput.value = ""; keyInput.focus(); return;
  }
  saveButton.disabled = true; saveButton.textContent = "正在验证…";
  try {
    const result = await apiRequest("configure_deepseek", { body: { api_key: apiKey, model: byId("deepseekModel").value }, timeoutMs: 45000 });
    renderModelStatus(result);
    await refreshAll();
  } catch (error) {
    showError(`DeepSeek 配置失败：${error.message}`);
  } finally {
    keyInput.value = ""; saveButton.disabled = false; saveButton.textContent = "保存并连接";
  }
}

const INVESTMENT_REPORT_LABELS = {
  morning_report: "盘古晨报",
  intraday_monitor: "盘中监控",
  closing_review: "盘后投资复盘",
  weekly_report: "盘古周报",
};

const INVESTMENT_REPORT_ICONS = {
  morning_report: "晨",
  intraday_monitor: "监",
  closing_review: "复",
  weekly_report: "周",
};

const INVESTMENT_CONTENT_LABELS = {
  headline: "报告结论",
  title: "报告标题",
  summary: "运营摘要",
  market_environment: "市场环境",
  market_regime: "市场状态",
  position_guidance: "仓位建议",
  opportunities: "今日机会",
  risks: "风险提示",
  position_anomalies: "持仓异常",
  market_anomalies: "市场异常",
  risk_changes: "风险变化",
  returns: "收益表现",
  performance: "收益表现",
  attribution: "收益归因",
  strategy_review: "策略复盘",
  error_analysis: "错误总结",
  strategy_performance: "策略表现",
  position_changes: "持仓变化",
  investment_summary: "投资总结",
  data_gaps: "数据缺口",
};

// Normalize list envelopes from the generated API without introducing a second request layer.
function investmentItems(payload) {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.items)) return payload.items;
  return [];
}

// Format only a server-returned ratio; no asset, return, PnL, or drawdown is derived here.
function serverRatio(value) {
  if (value === undefined || value === null || value === "") return "—";
  if (typeof value === "string") return value;
  return fractionPct(value);
}

// Read one report title from persisted server content without generating any new interpretation.
function investmentReportHeadline(report) {
  const content = report?.content || {};
  return content.headline || content.title || INVESTMENT_REPORT_LABELS[report?.report_type] || report?.report_type || "投资运营报告";
}

// Read one short persisted summary for compact cards; absent evidence stays visibly absent.
function investmentReportSummary(report) {
  const content = report?.content || {};
  const value = content.summary || content.investment_summary || content.message;
  return typeof value === "string" && value.trim() ? value.trim() : "打开查看结构化内容与证据引用。";
}

// Render the aggregate operating snapshot exactly as valued and classified by backend services.
function renderInvestmentOperatingSummary(payload) {
  const root = payload?.dashboard || payload || {};
  state.investmentOS.summary = root;
  const unsafe = root.can_trade === true || root.can_create_orders === true;
  const workflow = root.workflow_state || {};
  const serviceState = root.state || workflow.state || root.scheduler?.state || (workflow.enabled === false || root.enabled === false ? "disabled" : "ready");
  const stateLabels = { ready: "运营中心已就绪", waiting: "等待下一运营时点", due: "有运营任务待执行", running: "任务运行中", degraded: "部分证据不足", disabled: "调度已关闭", error: "运营中心异常" };
  const stateBadge = byId("investmentOsState");
  stateBadge.textContent = unsafe ? "安全合同异常" : (stateLabels[serviceState] || String(serviceState || "运营中心已连接"));
  stateBadge.className = `badge badge-${unsafe ? "danger" : serviceState === "degraded" ? "warning" : "success"}`;

  const market = root.market || root.market_summary || root.market_regime || {};
  const valuation = root.asset_valuation || root.portfolio?.asset_valuation || {};
  const portfolio = root.portfolio || root.portfolio_summary || {};
  const risk = root.risk || root.risk_summary || root.portfolio_risk || {};
  const riskAssessment = risk.assessment || {};
  const opportunities = Array.isArray(root.opportunities) ? root.opportunities : Array.isArray(root.today_opportunities) ? root.today_opportunities : [];
  const counts = root.counts || {};
  const marketState = typeof market === "string" ? market : market.state || market.regime || market.market_state || market.label;
  const marketDetail = typeof market === "object" ? market.detail || market.message || market.trend || [market.plan_state && `计划 ${market.plan_state}`, market.preview_state && `预览 ${market.preview_state}`].filter(Boolean).join(" · ") : null;
  text("osMarketState", marketState || "证据不足");
  text("osMarketDetail", marketDetail || "Market Regime 未提供结论");
  text("osExposure", serverRatio(valuation.exposure_ratio ?? portfolio.exposure_ratio ?? portfolio.current_weight));
  text("osExposureDetail", valuation.valued_at ? `ValuationService · ${time(valuation.valued_at)}` : "只展示后端 ValuationService 结果");
  const weightedRisk = riskAssessment.weighted_security_risk;
  const riskFlags = Array.isArray(risk.flags) ? risk.flags : Array.isArray(riskAssessment.risk_flags) ? riskAssessment.risk_flags : [];
  text("osRiskLevel", risk.level || risk.risk_level || risk.state || (weightedRisk === undefined ? `${riskFlags.length}项风险提示` : `评分 ${Number(weightedRisk).toFixed(1)}`));
  text("osRiskDetail", risk.message || (riskAssessment.current_drawdown === undefined ? "Portfolio & Risk Center" : `当前回撤 ${serverRatio(riskAssessment.current_drawdown)}`));
  text("osOpportunityCount", root.opportunity_count ?? opportunities.length);
  text("osOpportunityDetail", root.opportunity_summary || (opportunities.length ? opportunities.slice(0, 3).map((item) => item.name || item.symbol || item).join(" · ") : "等待 Research Pipeline"));
  const unread = Number(counts.unread_notification_count ?? root.unread_notification_count ?? 0);
  const critical = Number(counts.unread_critical_count ?? root.unread_critical_count ?? 0);
  text("osUnreadCount", String(unread));
  text("osCriticalCount", `${critical} 条 CRITICAL`);
  text("notificationTopCount", String(unread));
  text("notificationPanelCount", String(unread));
  byId("notificationJumpButton").classList.toggle("has-critical", critical > 0);
  const latestReports = root.latest_reports && typeof root.latest_reports === "object" ? Object.values(root.latest_reports).filter(Boolean) : [];
  const latestSummary = latestReports.find((item) => item?.content?.summary)?.content?.summary;
  text("osAiAdvice", root.ai_summary || root.ai_advice || root.operating_summary || latestSummary || "等待报告");
  const operatingAsOf = root.generated_at || root.as_of;
  text("osAsOf", operatingAsOf ? `运营时点 ${time(operatingAsOf)}` : "尚无运营时点");
  const schedulerDisabled = root.scheduler?.enabled === false || root.scheduler_enabled === false || workflow.enabled === false;
  text("osSchedulerState", schedulerDisabled ? "调度已关闭" : workflow.state === "due" ? `${(workflow.due_jobs || []).length}个任务到点` : "调度已启用");
  byId("osSchedulerState").className = `badge badge-${schedulerDisabled ? "warning" : workflow.state === "due" ? "info" : "success"}`;

  const embeddedReports = Array.isArray(root.recent_reports) ? root.recent_reports : Array.isArray(root.latest_reports) ? root.latest_reports : Array.isArray(root.reports) ? root.reports : null;
  const embeddedNotifications = Array.isArray(root.notifications) ? root.notifications : null;
  if (embeddedReports) renderInvestmentReports(embeddedReports);
  if (embeddedNotifications) renderInvestmentNotifications(embeddedNotifications);
  renderInvestmentJobs(root);
}

// Render the four persisted report types as one filterable audit list.
function renderInvestmentReports(items = state.investmentOS.reports) {
  state.investmentOS.reports = Array.isArray(items) ? items : [];
  const filtered = state.investmentOS.reportFilter === "all"
    ? state.investmentOS.reports
    : state.investmentOS.reports.filter((item) => item.report_type === state.investmentOS.reportFilter);
  const container = byId("investmentReportList");
  container.replaceChildren();
  for (const report of filtered) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "os-report-item";
    button.dataset.investmentReportId = report.report_id;
    const icon = document.createElement("span");
    icon.className = "os-report-icon";
    icon.textContent = INVESTMENT_REPORT_ICONS[report.report_type] || "报";
    const copy = document.createElement("span");
    copy.className = "os-report-copy";
    const title = document.createElement("strong");
    const summary = document.createElement("span");
    const meta = document.createElement("small");
    title.textContent = investmentReportHeadline(report);
    summary.textContent = investmentReportSummary(report);
    meta.textContent = `${INVESTMENT_REPORT_LABELS[report.report_type] || report.report_type} · ${report.trade_date || "无交易日"} · ${time(report.created_time)}`;
    copy.append(title, summary, meta);
    const status = document.createElement("span");
    status.className = "os-report-status";
    status.textContent = report.status === "degraded" ? "证据不完整" : "已归档";
    button.append(icon, copy, status);
    container.append(button);
  }
  byId("investmentReportEmpty").classList.toggle("hidden", filtered.length > 0);
}

// Render in-app notifications while preserving the server-owned INFO/WARNING/CRITICAL level.
function renderInvestmentNotifications(items = state.investmentOS.notifications) {
  state.investmentOS.notifications = Array.isArray(items) ? items : [];
  const filter = state.investmentOS.notificationFilter;
  const filtered = state.investmentOS.notifications.filter((item) => (
    filter === "all" || (filter === "unread" && item.status === "unread") || (filter === "critical" && item.level === "CRITICAL")
  ));
  const container = byId("investmentNotificationList");
  container.replaceChildren();
  for (const item of filtered) {
    const card = document.createElement("article");
    card.className = `os-notification-item ${item.level || "INFO"} ${item.status === "read" ? "read" : "unread"}`;
    const marker = document.createElement("i");
    const copy = document.createElement("div");
    copy.className = "os-notification-copy";
    const title = document.createElement("strong");
    const message = document.createElement("span");
    const meta = document.createElement("small");
    title.textContent = `${item.level || "INFO"} · ${item.title || "运营通知"}`;
    message.textContent = item.message || "未提供通知内容。";
    meta.textContent = `${item.source_type || "investment_os"} · ${time(item.created_time)}`;
    copy.append(title, message, meta);
    card.append(marker, copy);
    if (item.status !== "read") {
      const mark = document.createElement("button");
      mark.type = "button";
      mark.textContent = "标为已读";
      mark.dataset.markInvestmentNotification = item.notification_id;
      card.append(mark);
    }
    container.append(card);
  }
  byId("investmentNotificationEmpty").classList.toggle("hidden", filtered.length > 0);
}

// Bind backend scheduler definitions and latest task states to the four fixed workflow cards.
function renderInvestmentJobs(root = {}) {
  const jobs = Array.isArray(root.jobs) ? root.jobs : Array.isArray(root.scheduler?.jobs) ? root.scheduler.jobs : Array.isArray(root.job_definitions) ? root.job_definitions : [];
  const tasks = Array.isArray(root.tasks) ? root.tasks : Array.isArray(root.recent_tasks) ? root.recent_tasks : Array.isArray(root.scheduler?.recent_tasks) ? root.scheduler.recent_tasks : [];
  const latestReports = root.latest_reports && typeof root.latest_reports === "object" ? root.latest_reports : {};
  const jobMap = new Map(jobs.map((item) => [item.job_name, item]));
  for (const card of document.querySelectorAll("[data-os-job]")) {
    const name = card.dataset.osJob;
    const definition = jobMap.get(name);
    const latest = tasks.find((item) => item.job_name === name);
    const latestReport = latestReports[name];
    card.classList.remove("running", "succeeded", "failed", "skipped");
    if (latest?.status) card.classList.add(latest.status);
    const timeNode = card.querySelector(".os-job-time");
    if (definition?.schedule_kind === "interval") timeNode.textContent = `${definition.interval_minutes || 60}m`;
    else if (definition?.schedule_kind === "weekly") timeNode.textContent = `周五 ${definition.time_of_day || "16:00"}`;
    else if (definition?.time_of_day) timeNode.textContent = definition.time_of_day;
    const stateNode = card.querySelector("div:nth-child(2) > span");
    stateNode.textContent = latest
      ? `${latest.status || "unknown"} · ${time(latest.completed_time || latest.created_time)}`
      : latestReport?.created_time ? `最近报告 ${time(latestReport.created_time)}`
        : definition?.next_run_at ? `下次 ${time(definition.next_run_at)}` : "尚未生成报告";
  }
}

// Synchronize the aggregate snapshot, report archive, and notifications through generated operations only.
async function loadInvestmentOperatingCenter({ quiet = false } = {}) {
  if (state.investmentOS.inFlight) return false;
  state.investmentOS.inFlight = true;
  const requests = await Promise.allSettled([
    apiRequest("get_investment_os", { timeoutMs: 20000 }),
    apiRequest("list_investment_reports", { query: { limit: 40 }, timeoutMs: 20000 }),
    apiRequest("list_investment_notifications", { query: { limit: 60 }, timeoutMs: 20000 }),
  ]);
  try {
    if (requests[0].status === "fulfilled") renderInvestmentOperatingSummary(requests[0].value);
    if (requests[1].status === "fulfilled") renderInvestmentReports(investmentItems(requests[1].value));
    if (requests[2].status === "fulfilled") renderInvestmentNotifications(investmentItems(requests[2].value));
    const failures = requests.filter((result) => result.status === "rejected");
    if (failures.length === requests.length && !quiet) {
      showError(`投资运营中心同步失败：${failures[0].reason?.message || "本地服务不可用"}`);
    }
    return failures.length < requests.length;
  } finally {
    state.investmentOS.inFlight = false;
  }
}

// Translate stable report keys into readable headings without interpreting values.
function investmentContentLabel(key) {
  return INVESTMENT_CONTENT_LABELS[key] || String(key).replaceAll("_", " ");
}

// Build safe text-only report sections; model or database content is never injected as HTML.
function appendInvestmentContent(container, key, value, depth = 0) {
  const section = document.createElement(depth ? "div" : "section");
  section.className = "structured-report-section";
  const heading = document.createElement(depth ? "h4" : "h3");
  heading.textContent = investmentContentLabel(key);
  section.append(heading);
  if (Array.isArray(value)) {
    const list = document.createElement("ul");
    list.className = "structured-report-list";
    value.forEach((item) => {
      const entry = document.createElement("li");
      entry.textContent = typeof item === "object" && item !== null
        ? Object.entries(item).map(([itemKey, itemValue]) => `${investmentContentLabel(itemKey)}: ${typeof itemValue === "object" ? JSON.stringify(itemValue) : itemValue}`).join(" · ")
        : String(item);
      list.append(entry);
    });
    section.append(list);
  } else if (value && typeof value === "object") {
    Object.entries(value).forEach(([childKey, childValue]) => appendInvestmentContent(section, childKey, childValue, depth + 1));
  } else {
    const paragraph = document.createElement("p");
    paragraph.textContent = value === undefined || value === null || value === "" ? "证据不足" : String(value);
    section.append(paragraph);
  }
  container.append(section);
}

// Open one persisted report together with its run/evidence provenance and safety flags.
function renderInvestmentReportDialog(report) {
  state.investmentOS.selectedReport = report;
  text("investmentReportDialogTitle", investmentReportHeadline(report));
  text("investmentReportDialogMeta", `${INVESTMENT_REPORT_LABELS[report.report_type] || report.report_type} · ${report.trade_date || "无交易日"} · run_id=${report.run_id || "未记录"} · ${time(report.created_time)}`);
  const content = byId("investmentReportDialogContent");
  content.replaceChildren();
  Object.entries(report.content || {}).forEach(([key, value]) => appendInvestmentContent(content, key, value));
  if (!content.childElementCount) {
    const empty = document.createElement("p");
    empty.className = "empty-inline";
    empty.textContent = "该报告没有可展示内容。";
    content.append(empty);
  }
  const evidenceBox = byId("investmentReportEvidence");
  evidenceBox.replaceChildren();
  for (const item of report.evidence || []) {
    const chip = document.createElement("span");
    chip.className = "investment-evidence-chip";
    chip.textContent = `${item.evidence_id} · ${item.source_type}:${item.source_id}`;
    chip.title = item.observed_at ? `证据时间 ${time(item.observed_at)}` : "未记录证据时间";
    evidenceBox.append(chip);
  }
  byId("investmentReportEvidenceEmpty").classList.toggle("hidden", evidenceBox.childElementCount > 0);
  byId("investmentReportDialog").showModal();
}

// Fetch report detail by id instead of trusting a potentially truncated list payload.
async function openInvestmentReport(reportId) {
  if (!reportId) return;
  try {
    const report = await apiRequest("get_investment_report", { pathParams: { report_id: reportId }, timeoutMs: 20000 });
    renderInvestmentReportDialog(report);
  } catch (error) {
    showError(`投资报告读取失败：${error.message}`);
  }
}

// Explicitly run one non-trading operating job; path/body are both contract-safe inputs.
async function runInvestmentOperatingJob(jobName, button) {
  if (!Object.hasOwn(INVESTMENT_REPORT_LABELS, jobName)) return;
  const original = button?.textContent;
  if (button) { button.disabled = true; button.textContent = "运行中…"; }
  setActionOverlay(true, `正在生成${INVESTMENT_REPORT_LABELS[jobName]}`, "只读取 Data Center、Portfolio & Risk Center 与 AI Copilot 证据，不会创建订单。");
  try {
    const result = await apiRequest("run_investment_os_job", {
      pathParams: { job_name: jobName },
      query: { force: false },
      timeoutMs: 120000,
    });
    if (result?.report) renderInvestmentReportDialog(result.report);
    await loadInvestmentOperatingCenter({ quiet: true });
    showToast("投资运营任务完成", `${INVESTMENT_REPORT_LABELS[jobName]}已写入报告中心。`, "success", 4200);
  } catch (error) {
    showError(`${INVESTMENT_REPORT_LABELS[jobName]}生成失败：${error.message}`);
    showToast("运营任务未完成", error.message, "error", 6500);
  } finally {
    setActionOverlay(false);
    if (button) { button.disabled = false; button.textContent = original; }
  }
}

// Mark notification state through the protected local POST operation only.
async function markInvestmentNotificationRead(notificationId, button) {
  if (!notificationId) return;
  if (button) button.disabled = true;
  try {
    await apiRequest("mark_investment_notification_read", {
      pathParams: { notification_id: notificationId },
      timeoutMs: 15000,
    });
    await loadInvestmentOperatingCenter({ quiet: true });
  } catch (error) {
    showError(`通知状态更新失败：${error.message}`);
    if (button) button.disabled = false;
  }
}

const COPILOT_REPORT_LABELS = {
  morning_report: "盘前报告",
  close_review: "收盘复盘",
  stock_analysis: "个股分析",
  portfolio_analysis: "组合分析",
  risk_alert: "风险提示",
  coach_review: "纪律教练",
};

const COPILOT_MEMORY_LABELS = {
  profile: "投资画像",
  preference: "投资偏好",
  decision: "决策记录",
  error_pattern: "错误模式",
  lesson: "复盘教训",
};

function renderCopilotStatus(status) {
  state.copilot.status = status;
  const cells = byId("copilotStatus").querySelectorAll("dd");
  cells[0].textContent = status?.evidence_ready ? `可用 · ${status.latest_run_id || "最新"}` : "证据不足";
  cells[1].textContent = `${Number(status?.published_report_count || 0)} / ${Number(status?.report_count || 0)}`;
  cells[2].textContent = `${Number(status?.confirmed_memory_count || 0)} / ${Number(status?.memory_count || 0)}`;
  if (status?.model) renderModelStatus(status.model);
  const enabled = status?.model?.state === "connected" && Boolean(status?.evidence_ready);
  document.querySelectorAll("[data-copilot-report]").forEach((button) => { button.disabled = !enabled; });
}

function copilotCitationNode(evidenceIds = []) {
  const node = document.createElement("small");
  node.className = "copilot-citations";
  node.textContent = evidenceIds.length ? evidenceIds.join(" · ") : "无有效证据引用";
  return node;
}

function renderCopilotEntries(containerId, items, { risk = false } = {}) {
  const container = byId(containerId);
  container.replaceChildren();
  for (const item of items || []) {
    const card = document.createElement("article");
    card.className = `copilot-entry ${risk ? item.level || "info" : ""}`.trim();
    const heading = document.createElement("strong");
    const detail = document.createElement("p");
    heading.textContent = risk
      ? ({ high: "高风险", warning: "需关注", info: "信息" }[item.level] || "风险")
      : item.title || "发现";
    detail.textContent = risk ? item.message || "—" : item.detail || "—";
    card.append(heading, detail, copilotCitationNode(item.evidence_ids || []));
    container.append(card);
  }
  if (!container.childElementCount) {
    const empty = document.createElement("p");
    empty.className = "empty-inline";
    empty.textContent = "本报告没有此类条目。";
    container.append(empty);
  }
}

function renderCopilotReport(report) {
  if (!report) return;
  state.copilot.selectedReport = report;
  const content = report.content || {};
  const evaluation = report.evaluation || {};
  text("copilotReportHeadline", content.headline || COPILOT_REPORT_LABELS[report.report_type] || "AI 报告");
  text("copilotReportMeta", `${report.agent_type || "agent"} · ${report.prompt_version || "未记录提示词"} · ${report.model_version || "未记录模型"}`);
  text("modelRunMeta", `run_id=${report.run_id} · ${time(report.created_time)} · evidence_hash=${String(report.evidence_hash || "").slice(0, 16)}…`);
  text("modelSummary", content.summary || "模型未返回摘要。");
  text("copilotDisclaimer", content.disclaimer || "仅解释已保存证据，不构成投资建议。");
  renderCopilotEntries("copilotFindings", content.findings || []);
  renderCopilotEntries("modelRisks", content.risks || [], { risk: true });
  const grounded = report.status === "published" && evaluation?.grounded !== false;
  const groundingBadge = byId("copilotGroundingBadge");
  groundingBadge.className = `badge badge-${grounded ? "success" : "danger"}`;
  groundingBadge.textContent = grounded ? "证据校验通过" : "拒绝发布 / 待检查";
  const evaluationBox = byId("copilotEvaluation");
  evaluationBox.replaceChildren(
    badge(`引用准确率 ${evaluation.citation_accuracy === undefined ? "—" : fractionPct(evaluation.citation_accuracy)}`, grounded ? "success" : "warning"),
    badge(`引用 ${Number(evaluation.valid_citation_count || 0)}/${Number(evaluation.citation_count || 0)}`, "info"),
    badge(`数字声明 ${Number(evaluation.numeric_claim_count || 0)}`, "muted"),
    badge("used_for_execution=false", "muted"),
  );
  if (grounded) {
    const rating = document.createElement("div");
    rating.className = "copilot-rating";
    const label = document.createElement("span");
    label.textContent = evaluation.human_rating ? `你的评分 ${evaluation.human_rating}/5` : "评价报告";
    rating.append(label);
    for (let score = 1; score <= 5; score += 1) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = String(score);
      button.title = `给这份报告 ${score} 分`;
      button.addEventListener("click", () => rateCopilotReport(report.report_id, score));
      rating.append(button);
    }
    evaluationBox.append(rating);
  }
  byId("modelOutput").classList.remove("hidden");
  byId("copilotReportEmpty").classList.add("hidden");
}

function renderCopilotReports(items = []) {
  state.copilot.reports = items;
  const container = byId("copilotReportHistory");
  container.replaceChildren();
  text("copilotReportCount", String(items.length));
  for (const report of items) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "copilot-history-item";
    const title = document.createElement("strong");
    const meta = document.createElement("small");
    title.textContent = report.content?.headline || COPILOT_REPORT_LABELS[report.report_type] || report.report_type;
    meta.textContent = `${COPILOT_REPORT_LABELS[report.report_type] || report.report_type} · ${time(report.created_time)} · ${report.status}`;
    button.append(title, meta);
    button.addEventListener("click", () => openCopilotReport(report.report_id));
    container.append(button);
  }
  byId("copilotHistoryEmpty").classList.toggle("hidden", items.length > 0);
}

function renderCopilotMemories(items = []) {
  state.copilot.memories = items;
  const container = byId("copilotMemoryList");
  container.replaceChildren();
  text("copilotMemoryCount", String(items.length));
  for (const memory of items) {
    const card = document.createElement("article");
    card.className = "copilot-memory-item";
    const header = document.createElement("header");
    const title = document.createElement("strong");
    title.textContent = COPILOT_MEMORY_LABELS[memory.category] || memory.category;
    header.append(title, badge(memory.status === "confirmed" ? "已确认" : "待确认", memory.status === "confirmed" ? "success" : "warning"));
    const content = document.createElement("p");
    content.textContent = memory.content;
    const footer = document.createElement("footer");
    const source = document.createElement("span");
    source.textContent = `${memory.source} · 置信度 ${Number(memory.confidence || 0).toFixed(2)}`;
    footer.append(source);
    if (memory.status === "candidate") {
      const confirm = document.createElement("button");
      confirm.type = "button";
      confirm.className = "button button-ghost";
      confirm.textContent = "确认纳入记忆";
      confirm.dataset.confirmMemory = memory.memory_id;
      footer.append(confirm);
    }
    card.append(header, content, footer);
    container.append(card);
  }
  byId("copilotMemoryEmpty").classList.toggle("hidden", items.length > 0);
}

async function loadCopilotWorkspace({ quiet = false } = {}) {
  if (state.copilot.inFlight) return;
  state.copilot.inFlight = true;
  try {
    const [status, reports, memories] = await Promise.all([
      apiRequest("get_copilot_status"),
      apiRequest("list_copilot_reports", { query: { limit: 30 } }),
      apiRequest("list_copilot_memory", { query: { limit: 100 } }),
    ]);
    renderCopilotStatus(status);
    renderCopilotReports(reports.items || []);
    renderCopilotMemories(memories.items || []);
  } catch (error) {
    if (!quiet) showError(`天机助手同步失败：${error.message}`);
  } finally {
    state.copilot.inFlight = false;
  }
}

async function openCopilotReport(reportId) {
  try {
    renderCopilotReport(await apiRequest("get_copilot_report", { pathParams: { report_id: reportId } }));
  } catch (error) {
    showError(`报告读取失败：${error.message}`);
  }
}

async function runCopilotReport(reportType) {
  const button = document.querySelector(`[data-copilot-report="${reportType}"]`);
  const symbol = reportType === "stock_analysis" ? byId("copilotSymbol").value.trim().toUpperCase() : undefined;
  if (reportType === "stock_analysis" && !/^\d{6}\.(SH|SZ)$/.test(symbol)) {
    showError("个股代码格式应为 600519.SH 或 000001.SZ。");
    byId("copilotSymbol").focus();
    return;
  }
  clearError();
  if (button) button.disabled = true;
  setActionOverlay(true, `正在生成${COPILOT_REPORT_LABELS[reportType] || "报告"}`, "Evidence Reader 正在裁剪证据，模型输出还要通过引用和数字校验。不会产生任何订单。 ");
  let generated = false;
  try {
    const report = await apiRequest("create_copilot_report", {
      body: { report_type: reportType, symbol },
      timeoutMs: 120000,
    });
    renderCopilotReport(report);
    generated = true;
    showToast("证据化报告已发布", "报告已通过引用与数字校验，并写入本地审计档案。", "success", 4200);
  } catch (error) {
    showError(`${COPILOT_REPORT_LABELS[reportType] || "AI报告"}生成失败：${error.message}`);
    showToast("报告未发布", error.message, "error", 6500);
  } finally {
    setActionOverlay(false);
    if (button) button.disabled = false;
  }
  if (generated) await loadCopilotWorkspace({ quiet: true });
}

async function explainDailyResearch() {
  await runCopilotReport("morning_report");
}

async function explainPaperReview() {
  await runCopilotReport("close_review");
}

async function saveCopilotMemory(event) {
  event.preventDefault();
  const button = byId("copilotMemorySaveButton");
  const content = byId("copilotMemoryContent").value.trim();
  if (!content) return;
  button.disabled = true;
  try {
    await apiRequest("create_copilot_memory", {
      body: { category: byId("copilotMemoryCategory").value, content, confidence: 1.0 },
    });
    byId("copilotMemoryContent").value = "";
    showToast("投资记忆已保存", "这条记忆已确认，但不能影响评分、风控或交易。", "success");
    await loadCopilotWorkspace({ quiet: true });
  } catch (error) {
    showError(`保存投资记忆失败：${error.message}`);
  } finally {
    button.disabled = false;
  }
}

async function confirmCopilotMemory(memoryId) {
  try {
    await apiRequest("confirm_copilot_memory", { pathParams: { memory_id: memoryId } });
    showToast("候选记忆已确认", "确认只影响未来 AI 上下文，不影响任何执行引擎。", "success");
    await loadCopilotWorkspace({ quiet: true });
  } catch (error) {
    showError(`确认投资记忆失败：${error.message}`);
  }
}

async function rateCopilotReport(reportId, rating) {
  try {
    const evaluation = await apiRequest("rate_copilot_report", {
      pathParams: { report_id: reportId },
      body: { rating, note: "" },
    });
    if (state.copilot.selectedReport?.report_id === reportId) {
      state.copilot.selectedReport.evaluation = evaluation;
      renderCopilotReport(state.copilot.selectedReport);
    }
    showToast("报告评价已保存", `你给这份报告 ${rating}/5 分。`, "success", 2600);
  } catch (error) {
    showError(`报告评价失败：${error.message}`);
  }
}

async function clearDeepSeek() {
  if (!window.confirm("确认清除本机保存的 DeepSeek API Key 并断开模型吗？")) return;
  clearError();
  try {
    const result = await apiRequest("clear_deepseek");
    byId("modelOutput").classList.add("hidden");
    renderModelStatus(result);
    await refreshAll();
  } catch (error) {
    showError(`清除 DeepSeek 配置失败：${error.message}`);
  }
}

const AI_RESEARCH_SECTION_BY_AGENT = {
  strategy_analyst: "01_strategy_status",
  factor_analyst: "02_factor_change",
  risk_analyst: "03_risk_change",
  market_analyst: "04_market_context",
};

function aiResearchClaimNode(claim) {
  const card = document.createElement("article");
  card.className = "ai-research-claim";
  const header = document.createElement("header");
  const title = document.createElement("h3");
  title.textContent = claim.title || "研究结论";
  const tone = claim.label === "FACT" ? "success" : claim.label === "HYPOTHESIS" ? "warning" : "info";
  header.append(title, badge(claim.label || "UNLABELLED", tone));
  const statement = document.createElement("p");
  statement.textContent = claim.statement || "—";
  const evidence = document.createElement("p");
  evidence.className = "provenance";
  evidence.textContent = `证据：${(claim.evidence_ids || []).join(" · ") || "缺失"}`;
  const metrics = document.createElement("small");
  const serialized = JSON.stringify(claim.metrics || {});
  metrics.textContent = serialized && serialized !== "{}" ? `原值：${serialized.slice(0, 420)}` : "无额外数值声明";
  card.append(header, statement, evidence, metrics);
  return card;
}

function renderAIResearchAgent() {
  const report = state.aiResearch.selectedReport;
  const container = byId("aiResearchClaims");
  container.replaceChildren();
  document.querySelectorAll("[data-ai-agent]").forEach((button) => {
    const active = button.dataset.aiAgent === state.aiResearch.selectedAgent;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
  if (!report) {
    byId("aiResearchClaimsEmpty").classList.remove("hidden");
    return;
  }
  const section = report.sections?.[AI_RESEARCH_SECTION_BY_AGENT[state.aiResearch.selectedAgent]] || {};
  const analysis = section.analysis || section;
  const claims = analysis.claims || [];
  claims.forEach((claim) => container.append(aiResearchClaimNode(claim)));
  byId("aiResearchClaimsEmpty").classList.toggle("hidden", claims.length > 0);
}

function renderAIResearchReport(report) {
  state.aiResearch.selectedReport = report || null;
  const status = report?.status || "unavailable";
  text("aiResearchStatusBadge", status);
  byId("aiResearchStatusBadge").className = `badge badge-${status === "published" ? "success" : status === "degraded" ? "warning" : "muted"}`;
  text("aiResearchReportMeta", report
    ? `${time(report.created_time)} · ${report.strategy_id || "未绑定策略"} · ${report.model_version || "模型不可用"}`
    : "08:00读取最近封存证据；模型失败时只发布确定性降级摘要。");
  const summary = byId("aiResearchSummary");
  summary.replaceChildren();
  const summaryText = report?.model_analysis?.summary
    || report?.sections?.["01_strategy_status"]?.summary
    || "尚无AI量化研究报告。";
  const paragraph = document.createElement("p");
  paragraph.textContent = summaryText;
  summary.append(paragraph);
  const evaluation = byId("aiResearchEvaluation");
  evaluation.replaceChildren();
  if (report) {
    evaluation.append(
      badge(report.evaluation?.deterministic?.grounded ? "确定性证据通过" : "确定性证据异常", report.evaluation?.deterministic?.grounded ? "success" : "danger"),
      badge(report.evaluation?.model?.grounded ? "模型引用通过" : "模型未采用", report.evaluation?.model?.grounded ? "success" : "warning"),
      badge(report.dataset_label || "数据标签缺失", report.dataset_label === "POINT_IN_TIME" ? "info" : "warning"),
    );
  }
  const gaps = [...new Set(report?.data_gaps || [])];
  const gapContainer = byId("aiResearchGaps");
  gapContainer.replaceChildren();
  gaps.forEach((gap) => {
    const item = document.createElement("p");
    item.textContent = gap;
    gapContainer.append(item);
  });
  byId("aiResearchGapsEmpty").classList.toggle("hidden", gaps.length > 0);
  renderAIResearchAgent();
}

function renderAIResearchLists(payload) {
  const questions = payload.questions || [];
  text("aiResearchQuestionBadge", String(questions.length));
  text("aiResearchQuestionCount", String(payload.counts?.open_questions ?? questions.length));
  const questionList = byId("aiResearchQuestionList");
  questionList.replaceChildren();
  questions.forEach((question) => {
    const card = document.createElement("article");
    const header = document.createElement("header");
    const title = document.createElement("strong");
    title.textContent = question.question;
    header.append(title, badge(`${question.priority} · ${question.status}`, question.priority === "HIGH" ? "warning" : "info"));
    const rationale = document.createElement("p");
    rationale.textContent = question.rationale;
    const evidence = document.createElement("small");
    evidence.textContent = `证据：${(question.evidence_ids || []).join(" · ")}`;
    const actions = document.createElement("div");
    actions.className = "button-row";
    if (question.status === "OPEN") {
      const plan = document.createElement("button");
      plan.type = "button";
      plan.className = "button button-ghost button-compact";
      plan.dataset.aiQuestionId = question.question_id;
      plan.dataset.aiQuestionStatus = "PLANNED";
      plan.textContent = "加入研究计划";
      actions.append(plan);
    }
    if (!['ARCHIVED', 'REJECTED'].includes(question.status)) {
      const archive = document.createElement("button");
      archive.type = "button";
      archive.className = "button button-ghost button-compact";
      archive.dataset.aiQuestionId = question.question_id;
      archive.dataset.aiQuestionStatus = "ARCHIVED";
      archive.textContent = "归档";
      actions.append(archive);
    }
    card.append(header, rationale, evidence, actions);
    questionList.append(card);
  });
  byId("aiResearchQuestionEmpty").classList.toggle("hidden", questions.length > 0);

  const memories = payload.memory || [];
  text("aiResearchMemoryBadge", String(memories.length));
  const memoryList = byId("aiResearchMemoryList");
  memoryList.replaceChildren();
  memories.forEach((memory) => {
    const card = document.createElement("article");
    const header = document.createElement("header");
    const title = document.createElement("strong");
    title.textContent = memory.memory_type;
    header.append(title, badge(memory.status, memory.status === "confirmed" ? "success" : "warning"));
    const content = document.createElement("p");
    content.textContent = memory.content;
    const evidence = document.createElement("small");
    evidence.textContent = `证据：${(memory.evidence_ids || []).join(" · ")}`;
    card.append(header, content, evidence);
    if (memory.status === "candidate") {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "button button-ghost button-compact";
      button.dataset.aiMemoryId = memory.memory_id;
      button.textContent = "人工确认观察";
      card.append(button);
    }
    memoryList.append(card);
  });
  byId("aiResearchMemoryEmpty").classList.toggle("hidden", memories.length > 0);

  const reports = payload.reports || [];
  text("aiResearchReportBadge", String(reports.length));
  const reportList = byId("aiResearchReportList");
  reportList.replaceChildren();
  reports.forEach((report) => {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.aiReportId = report.report_id;
    const title = document.createElement("strong");
    title.textContent = `${report.strategy_version || "未知版本"} · ${report.status}`;
    const meta = document.createElement("small");
    meta.textContent = `${time(report.created_time)} · ${report.model_version}`;
    button.append(title, meta);
    reportList.append(button);
  });
  byId("aiResearchReportEmpty").classList.toggle("hidden", reports.length > 0);
}

function renderAIResearchCenter(payload) {
  state.aiResearch.dashboard = payload;
  text("aiResearchStrategyVersion", payload.evidence?.strategy_version || "—");
  text("aiResearchStrategyId", payload.evidence?.strategy_id || "尚未绑定");
  text("aiResearchEvidenceCount", String(payload.evidence?.evidence_count ?? 0));
  text("aiResearchModelState", payload.model?.state || "disabled");
  text("aiResearchModelName", payload.model?.model || "DeepSeek未连接");
  renderAIResearchLists(payload);
  renderAIResearchReport(payload.latest_report);
}

async function loadAIResearchCenter({ quiet = false } = {}) {
  if (state.aiResearch.inFlight) return;
  state.aiResearch.inFlight = true;
  try {
    renderAIResearchCenter(await apiRequest("get_ai_research_center", { timeoutMs: 25000 }));
  } catch (error) {
    if (!quiet) showError(`AI量化研究中心同步失败：${error.message}`);
  } finally {
    state.aiResearch.inFlight = false;
  }
}

async function generateAIResearchBrief() {
  const button = byId("aiResearchGenerateButton");
  button.disabled = true;
  button.textContent = "正在读取证据…";
  try {
    const report = await apiRequest("create_ai_research_brief", {
      body: {}, timeoutMs: 180000,
    });
    renderAIResearchReport(report);
    await loadAIResearchCenter({ quiet: true });
    showToast("研究晨报已生成", report.status === "published" ? "模型结论已通过引用校验。" : "已发布确定性降级摘要。", "success");
  } catch (error) {
    showToast("研究晨报生成失败", error.message, "error", 6500);
  } finally {
    button.disabled = false;
    button.textContent = "立即生成";
  }
}

async function openAIResearchReport(reportId) {
  try {
    renderAIResearchReport(await apiRequest("get_ai_research_report", {
      pathParams: { report_id: reportId }, timeoutMs: 20000,
    }));
  } catch (error) {
    showToast("报告读取失败", error.message, "error");
  }
}

async function confirmAIResearchMemory(memoryId) {
  try {
    await apiRequest("confirm_ai_research_memory", { pathParams: { memory_id: memoryId } });
    await loadAIResearchCenter({ quiet: true });
    showToast("研究观察已确认", "确认不会影响策略、风控或交易。", "success");
  } catch (error) {
    showToast("确认失败", error.message, "error");
  }
}

async function updateAIResearchQuestion(questionId, status) {
  try {
    await apiRequest("update_ai_research_question_status", {
      pathParams: { question_id: questionId }, body: { status },
    });
    await loadAIResearchCenter({ quiet: true });
    showToast("研究问题已更新", "仅更新问题工作流，不会自动启动实验。", "success");
  } catch (error) {
    showToast("更新失败", error.message, "error");
  }
}

const STRATEGY_STATES = ["DRAFT", "RESEARCH", "FACTOR_VALIDATED", "ROBUSTNESS_VALIDATED", "OUT_OF_SAMPLE", "PAPER_TRADING"];
const GATE_LABELS = {
  DATA_INTEGRITY: "数据完整性",
  FACTOR_VALIDITY: "因子有效性",
  ROBUSTNESS: "稳健性",
  OUT_OF_SAMPLE: "样本外",
  PAPER_TRADING: "模拟执行",
};

function strategyTone(value) {
  if (["PASSED", "APPROVED", "PAPER_TRADING"].includes(value)) return "success";
  if (["FAILED", "BLOCKED", "REJECTED"].includes(value)) return "danger";
  if (["PENDING", "UNAVAILABLE"].includes(value)) return "warning";
  return "muted";
}

// Strategy Lab renders only backend-owned evidence and state; it never derives approval eligibility.
function renderStrategyLab(payload) {
  state.strategyLab.dashboard = payload;
  text("strategyLabStrategyCount", payload.counts?.strategies ?? 0);
  text("strategyLabReviewCount", payload.counts?.reviews ?? 0);
  text("strategyLabPendingCount", payload.counts?.pending_approvals ?? 0);
  text("strategyLabRuleVersion", payload.gate_contract?.rule_version || "—");
  text("strategyLabSafetyState", payload.can_auto_promote ? "配置异常" : "can_auto_promote=false");
  byId("strategyLabSafetyState").className = `badge badge-${payload.can_auto_promote ? "danger" : "success"}`;

  const registry = byId("strategyRegistryList");
  registry.replaceChildren();
  (payload.strategies || []).forEach((strategy) => {
    const card = document.createElement("article");
    card.className = "strategy-registry-card";
    const header = document.createElement("header");
    const copy = document.createElement("div");
    const meta = document.createElement("p");
    const title = document.createElement("h3");
    meta.className = "kicker";
    meta.textContent = `${strategy.strategy_id} · ${strategy.active_version}`;
    title.textContent = strategy.name;
    copy.append(meta, title);
    header.append(copy, badge(strategy.current_state, strategyTone(strategy.current_state)));
    const detail = document.createElement("p");
    detail.textContent = strategy.description || "暂无策略说明";
    const track = document.createElement("div");
    track.className = "strategy-state-track";
    track.setAttribute("aria-label", `当前生命周期 ${strategy.current_state}`);
    const currentIndex = STRATEGY_STATES.indexOf(strategy.current_state);
    STRATEGY_STATES.forEach((name, index) => {
      const item = document.createElement("span");
      item.classList.toggle("done", index <= currentIndex);
      item.title = name;
      track.append(item);
    });
    card.append(header, detail, track);
    registry.append(card);
  });
  byId("strategyRegistryEmpty").classList.toggle("hidden", Boolean(payload.strategies?.length));

  const list = byId("strategyReviewList");
  list.replaceChildren();
  (payload.reviews || []).forEach((review) => {
    const card = document.createElement("article");
    card.className = "strategy-review-card";
    const header = document.createElement("header");
    const copy = document.createElement("div");
    const title = document.createElement("h3");
    const meta = document.createElement("p");
    title.textContent = `${review.strategy_id} · ${review.strategy_version}`;
    meta.textContent = `${time(review.created_at)} · ${review.recommendation}`;
    copy.append(title, meta);
    header.append(copy, badge(review.recommended_state || "保持", review.recommended_state ? "info" : "muted"));
    const summary = document.createElement("div");
    summary.className = "strategy-review-summary";
    const pending = (payload.promotion_history || []).find((item) => item.review_id === review.review_id);
    [
      ["当前状态", review.current_state],
      ["Health Score", review.health_score == null ? `部分 ${Number(review.health_partial_score || 0).toFixed(1)}` : Number(review.health_score).toFixed(1)],
      ["证据覆盖", `${(Number(review.health_coverage || 0) * 100).toFixed(0)}%`],
      ["人工审批", pending?.approval_status || "未申请"],
    ].forEach(([label, value]) => {
      const item = document.createElement("div");
      const small = document.createElement("span");
      const strong = document.createElement("strong");
      small.textContent = label;
      strong.textContent = value;
      item.append(small, strong);
      summary.append(item);
    });
    const button = document.createElement("button");
    button.type = "button";
    button.className = "button button-ghost button-compact";
    button.dataset.strategyReviewId = review.review_id;
    button.textContent = "查看证据与审批";
    card.append(header, summary, button);
    list.append(card);
  });
  byId("strategyReviewEmpty").classList.toggle("hidden", Boolean(payload.reviews?.length));
}

async function loadStrategyLab({ quiet = false } = {}) {
  if (state.strategyLab.inFlight) return;
  state.strategyLab.inFlight = true;
  try {
    renderStrategyLab(await apiRequest("get_strategy_lab", { timeoutMs: 20000 }));
  } catch (error) {
    if (!quiet) showError(`策略实验室同步失败：${error.message}`);
  } finally {
    state.strategyLab.inFlight = false;
  }
}

function renderStrategyReviewDetail(review) {
  state.strategyLab.selectedReview = review;
  const promotion = (state.strategyLab.dashboard?.promotion_history || [])
    .find((item) => item.review_id === review.review_id);
  state.strategyLab.selectedPromotion = promotion || null;
  text("strategyReviewDialogTitle", `${review.strategy_id} · ${review.strategy_version}`);
  text("strategyReviewDialogMeta", `${review.current_state} · ${review.recommendation} · ${time(review.created_at)}`);
  const content = byId("strategyReviewDialogContent");
  content.replaceChildren();
  (review.validation_records || []).forEach((record) => {
    const section = document.createElement("section");
    section.className = "strategy-gate-detail";
    const header = document.createElement("header");
    const heading = document.createElement("h3");
    heading.textContent = GATE_LABELS[record.gate_name] || record.gate_name;
    header.append(heading, badge(record.gate_status, strategyTone(record.gate_status)));
    const summary = document.createElement("p");
    summary.textContent = `${record.summary} · 分数 ${record.score == null ? "不可用" : Number(record.score).toFixed(1)}`;
    const checks = document.createElement("ul");
    (record.checks || []).forEach((item) => {
      const row = document.createElement("li");
      row.dataset.pass = String(Boolean(item.passed));
      row.textContent = `${item.passed ? "通过" : "未通过"} · ${item.code} · 实际 ${String(item.actual)} · 要求 ${String(item.expected)}`;
      checks.append(row);
    });
    const evidence = document.createElement("p");
    evidence.className = "provenance";
    evidence.textContent = `证据：${(record.evidence_ids || []).join(" · ") || "未提供"}`;
    section.append(header, summary, checks, evidence);
    content.append(section);
  });

  const panel = byId("strategyApprovalPanel");
  const requestButton = byId("strategyRequestPromotionButton");
  const approveButton = byId("strategyApprovePromotionButton");
  const rejectButton = byId("strategyRejectPromotionButton");
  const recommended = Boolean(review.recommended_state);
  const pending = promotion?.approval_status === "PENDING";
  panel.classList.toggle("hidden", !recommended && !pending);
  requestButton.classList.toggle("hidden", !recommended || Boolean(promotion));
  approveButton.classList.toggle("hidden", !pending);
  rejectButton.classList.toggle("hidden", !pending);
  byId("strategyApprovalReasonLabel").classList.toggle("hidden", !pending);
  text("strategyApprovalGuidance", pending
    ? `待人工决定：${promotion.from_state} → ${promotion.to_state}。批准会改变研究生命周期，但不会创建订单。`
    : `系统建议 ${review.current_state} → ${review.recommended_state}。提交申请本身不会改变状态。`);
  byId("strategyApprovalActor").value = "";
  byId("strategyApprovalReason").value = "";
  byId("strategyReviewDialog").showModal();
}

async function openStrategyReview(reviewId) {
  try {
    renderStrategyReviewDetail(await apiRequest("get_strategy_validation_review", {
      pathParams: { review_id: reviewId }, timeoutMs: 20000,
    }));
  } catch (error) {
    showError(`策略审查读取失败：${error.message}`);
  }
}

async function requestStrategyPromotion() {
  const review = state.strategyLab.selectedReview;
  const actor = byId("strategyApprovalActor").value.trim();
  if (!review || actor.length < 2) return showToast("请填写审批人", "需要可追溯的人工身份。", "error");
  try {
    await apiRequest("request_strategy_promotion", {
      pathParams: { review_id: review.review_id }, body: { requested_by: actor },
    });
    byId("strategyReviewDialog").close();
    await loadStrategyLab();
    showToast("晋级申请已提交", "策略状态未改变，等待人工批准或拒绝。", "success");
  } catch (error) {
    showToast("提交失败", error.message, "error", 6500);
  }
}

async function decideStrategyPromotion(decision) {
  const promotion = state.strategyLab.selectedPromotion;
  const actor = byId("strategyApprovalActor").value.trim();
  const reason = byId("strategyApprovalReason").value.trim();
  if (!promotion || actor.length < 2 || reason.length < 8) {
    return showToast("信息不完整", "审批人至少2字，理由至少8字。", "error");
  }
  try {
    await apiRequest(decision === "approve" ? "approve_strategy_promotion" : "reject_strategy_promotion", {
      pathParams: { promotion_id: promotion.promotion_id }, body: { actor, reason },
    });
    byId("strategyReviewDialog").close();
    await loadStrategyLab();
    showToast(decision === "approve" ? "人工批准已记录" : "人工拒绝已记录", decision === "approve" ? "研究生命周期已按顺序更新；交易能力仍为零。" : "策略状态保持不变。", "success");
  } catch (error) {
    showToast("审批失败", error.message, "error", 6500);
  }
}

// 首页主按钮只把用户带到正确操作位置，不绕过工作台中的显式确认。
function handleNextAction() {
  const action = byId("nextActionButton").dataset.action || "open-market";
  if (action === "open-review") {
    activateWorkspace("etf");
    return;
  }
  activateWorkspace("market");
  if (action === "focus-execute") byId("executeButton").focus();
  if (action === "focus-preview") byId("previewButton").focus();
}

// 卡片、明细行和详情按钮共享同一候选选择语义。
function handleCandidateInteraction(event) {
  const trade = event.target.closest("[data-trade-symbol]");
  const candidate = event.target.closest("[data-candidate-symbol]");
  if (trade) selectCandidate(trade.dataset.tradeSymbol, { openTicket: true });
  else if (candidate) selectCandidate(candidate.dataset.candidateSymbol);
}

byId("liveSyncQuickButton").addEventListener("click", () => setLiveSyncEnabled(!state.liveSyncEnabled));
byId("liveSyncEnabled").addEventListener("change", (event) => setLiveSyncEnabled(event.target.checked));
byId("liveSyncInterval").addEventListener("change", (event) => changeLiveSyncInterval(event.target.value));
byId("candidateAutoEnabled").addEventListener("change", (event) => changeCandidateAutoRefresh(event.target.checked));
byId("candidateAutoInterval").addEventListener("change", (event) => {
  changeCandidateAutoRefresh(state.candidateAutoEnabled, event.target.value);
  showToast("候选重算频率已更新", `交易时段内每${state.candidateAutoIntervalSeconds / 60}分钟生成一次盘中预览。`, "success", 2800);
});
byId("liveModuleGrid").addEventListener("click", (event) => {
  const button = event.target.closest("[data-live-refresh]");
  if (!button || button.disabled) return;
  void refreshLiveModuleNow(button.dataset.liveRefresh);
});
byId("refreshButton").addEventListener("click", manualRefresh);
byId("previewButton").addEventListener("click", () => runAction("refresh_intraday_candidates", {}, 300000, {
  buttonId: "previewButton", busyLabel: "正在扫描全市场…", overlayTitle: "正在刷新候选榜", overlayMessage: "盘古·天机正在读取全市场快照、历史行情与财务指标，通常需要几十秒。", successTitle: "候选榜已更新", successMessage: "新的盘中预览已生成，不会覆盖正式计划。",
}));
byId("collectButton").addEventListener("click", () => runAction("collect_daily_research", { query: { force: true } }, 300000, {
  buttonId: "collectButton", busyLabel: "正在生成计划…", overlayTitle: "正在生成正式收盘计划", overlayMessage: "系统会校验行情日期、财务可见性与候选完整性，失败时不会产生可执行计划。", successTitle: "正式计划已生成", successMessage: "计划仅在下一交易日指定窗口有效。",
}));
byId("executeButton").addEventListener("click", () => runAction("execute_daily_paper_plan", {}, 180000, {
  buttonId: "executeButton", busyLabel: "正在模拟调仓…", overlayTitle: "正在执行本地模拟计划", overlayMessage: "系统先卖后买并重新执行T+1、仓位、现金和幂等检查。", successTitle: "模拟计划执行完成", successMessage: "订单、成交和持仓已写入本地模拟账本。",
}));
byId("monitorButton").addEventListener("click", () => runAction("monitor_daily_paper_positions", {}, 180000, {
  buttonId: "monitorButton", busyLabel: "正在监控…", overlayTitle: "正在检查模拟持仓", overlayMessage: "系统正在更新持仓行情、止损状态与组合回撤。", successTitle: "持仓监控完成", successMessage: "最新风险事件与净值已同步。",
}));
byId("nextActionButton").addEventListener("click", handleNextAction);
byId("quoteRefreshButton").addEventListener("click", () => fetchLiveQuote());
byId("matchOrdersButton").addEventListener("click", () => matchBrokerOrders({ quiet: false }));
byId("manualPaperForm").addEventListener("submit", (event) => submitManualPaperOrder("BUY", event));
byId("paperSellButton").addEventListener("click", (event) => submitManualPaperOrder("SELL", event));
byId("manualSymbol").addEventListener("change", () => {
  state.currentQuote = null;
  clearPaperOrderPreview();
  void fetchLiveQuote();
  startQuotePolling();
});
byId("manualQuantity").addEventListener("input", clearPaperOrderPreview);
byId("manualOrderType").addEventListener("change", (event) => {
  const isLimit = event.target.value === "LIMIT";
  byId("manualLimitPriceGroup").classList.toggle("hidden", !isLimit);
  if (isLimit && state.currentQuote?.last_price) {
    byId("manualLimitPrice").value = Number(state.currentQuote.last_price).toFixed(3);
  }
  clearPaperOrderPreview();
});
byId("manualLimitPrice").addEventListener("input", clearPaperOrderPreview);
byId("rankingCards").addEventListener("click", handleCandidateInteraction);
byId("rankingCards").addEventListener("keydown", (event) => {
  if (!["Enter", " "].includes(event.key)) return;
  event.preventDefault();
  handleCandidateInteraction(event);
});
byId("rankingBody").addEventListener("click", handleCandidateInteraction);
byId("candidateDetail").addEventListener("click", handleCandidateInteraction);
byId("rankingSearch").addEventListener("input", (event) => {
  state.rankingQuery = event.target.value;
  renderRanking(state.currentRanking);
});
byId("rankingFilter").addEventListener("change", (event) => {
  state.rankingFilter = event.target.value;
  renderRanking(state.currentRanking);
});
byId("rankViewCards").addEventListener("click", () => {
  state.rankingView = "cards";
  byId("rankingCards").classList.remove("hidden");
  byId("rankingTableWrap").classList.add("hidden");
  byId("rankViewCards").classList.add("active");
  byId("rankViewTable").classList.remove("active");
  byId("rankViewCards").setAttribute("aria-pressed", "true");
  byId("rankViewTable").setAttribute("aria-pressed", "false");
});
byId("rankViewTable").addEventListener("click", () => {
  state.rankingView = "table";
  byId("rankingCards").classList.add("hidden");
  byId("rankingTableWrap").classList.remove("hidden");
  byId("rankViewCards").classList.remove("active");
  byId("rankViewTable").classList.add("active");
  byId("rankViewCards").setAttribute("aria-pressed", "false");
  byId("rankViewTable").setAttribute("aria-pressed", "true");
});
document.querySelectorAll("[data-quantity]").forEach((button) => button.addEventListener("click", () => {
  byId("manualQuantity").value = button.dataset.quantity;
  clearPaperOrderPreview();
}));
byId("positionBody").addEventListener("click", (event) => {
  const button = event.target.closest("[data-sell-symbol]");
  if (!button || button.disabled) return;
  byId("manualSymbol").value = button.dataset.sellSymbol;
  byId("manualQuantity").value = button.dataset.sellQuantity;
  clearPaperOrderPreview();
  byId("liveTradeTitle").scrollIntoView({ behavior: "smooth", block: "start" });
  void fetchLiveQuote();
  startQuotePolling();
});
byId("paperAuditBody").addEventListener("click", (event) => {
  const button = event.target.closest("[data-cancel-order]");
  if (!button || button.disabled) return;
  void cancelBrokerOrder(button.dataset.cancelOrder);
});
byId("killButton").addEventListener("click", toggleSharedKillSwitch);
byId("modelTestButton").addEventListener("click", () => runAction("test_model_connection", { body: {} }, 45000, { buttonId: "modelTestButton", busyLabel: "测试中…", overlay: false, successTitle: "DeepSeek 连接正常", successMessage: "模型仍保持只读、无交易权限。" }));
byId("deepseekForm").addEventListener("submit", configureDeepSeek);
byId("modelExplainTop10Button").addEventListener("click", explainDailyResearch);
byId("modelExplainReviewButton").addEventListener("click", explainPaperReview);
byId("modelClearButton").addEventListener("click", clearDeepSeek);
byId("investmentOsRefreshButton").addEventListener("click", async () => {
  const button = byId("investmentOsRefreshButton");
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "同步中…";
  try {
    const updated = await loadInvestmentOperatingCenter();
    if (updated) showToast("投资运营中心已同步", "报告、通知与调度状态已更新。", "success", 2800);
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
});
byId("notificationJumpButton").addEventListener("click", () => {
  activateWorkspace("overview");
  state.investmentOS.notificationFilter = "unread";
  document.querySelectorAll("[data-os-notification-filter]").forEach((button) => {
    const active = button.dataset.osNotificationFilter === "unread";
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  renderInvestmentNotifications();
  byId("investmentNotificationPanel").scrollIntoView({ behavior: "smooth", block: "start" });
});
document.querySelectorAll("[data-os-report-filter]").forEach((button) => {
  button.addEventListener("click", () => {
    state.investmentOS.reportFilter = button.dataset.osReportFilter;
    document.querySelectorAll("[data-os-report-filter]").forEach((item) => {
      const active = item === button;
      item.classList.toggle("active", active);
      item.setAttribute("aria-pressed", String(active));
    });
    renderInvestmentReports();
  });
});
document.querySelectorAll("[data-os-notification-filter]").forEach((button) => {
  button.addEventListener("click", () => {
    state.investmentOS.notificationFilter = button.dataset.osNotificationFilter;
    document.querySelectorAll("[data-os-notification-filter]").forEach((item) => {
      const active = item === button;
      item.classList.toggle("active", active);
      item.setAttribute("aria-pressed", String(active));
    });
    renderInvestmentNotifications();
  });
});
byId("investmentReportList").addEventListener("click", (event) => {
  const button = event.target.closest("[data-investment-report-id]");
  if (button) void openInvestmentReport(button.dataset.investmentReportId);
});
byId("investmentNotificationList").addEventListener("click", (event) => {
  const button = event.target.closest("[data-mark-investment-notification]");
  if (button) void markInvestmentNotificationRead(button.dataset.markInvestmentNotification, button);
});
document.querySelectorAll("[data-run-os-job]").forEach((button) => {
  button.addEventListener("click", () => runInvestmentOperatingJob(button.dataset.runOsJob, button));
});
byId("investmentReportDialogClose").addEventListener("click", () => byId("investmentReportDialog").close());
byId("investmentReportDialog").addEventListener("click", (event) => {
  if (event.target === byId("investmentReportDialog")) byId("investmentReportDialog").close();
});
byId("copilotRefreshButton").addEventListener("click", () => loadCopilotWorkspace());
document.querySelectorAll("[data-copilot-report]").forEach((button) => {
  if (["modelExplainTop10Button", "modelExplainReviewButton"].includes(button.id)) return;
  button.addEventListener("click", () => runCopilotReport(button.dataset.copilotReport));
});
byId("copilotMemoryForm").addEventListener("submit", saveCopilotMemory);
byId("copilotMemoryList").addEventListener("click", (event) => {
  const button = event.target.closest("[data-confirm-memory]");
  if (button) void confirmCopilotMemory(button.dataset.confirmMemory);
});
byId("strategyLabRefreshButton").addEventListener("click", () => loadStrategyLab());
byId("strategyReviewList").addEventListener("click", (event) => {
  const button = event.target.closest("[data-strategy-review-id]");
  if (button) void openStrategyReview(button.dataset.strategyReviewId);
});
byId("strategyReviewDialogClose").addEventListener("click", () => byId("strategyReviewDialog").close());
byId("strategyReviewDialog").addEventListener("click", (event) => {
  if (event.target === byId("strategyReviewDialog")) byId("strategyReviewDialog").close();
});
byId("strategyRequestPromotionButton").addEventListener("click", requestStrategyPromotion);
byId("strategyApprovePromotionButton").addEventListener("click", () => decideStrategyPromotion("approve"));
byId("strategyRejectPromotionButton").addEventListener("click", () => decideStrategyPromotion("reject"));
byId("aiResearchRefreshButton").addEventListener("click", () => loadAIResearchCenter());
byId("aiResearchGenerateButton").addEventListener("click", generateAIResearchBrief);
document.querySelectorAll("[data-ai-agent]").forEach((button) => {
  button.addEventListener("click", () => {
    state.aiResearch.selectedAgent = button.dataset.aiAgent;
    renderAIResearchAgent();
  });
});
byId("aiResearchReportList").addEventListener("click", (event) => {
  const button = event.target.closest("[data-ai-report-id]");
  if (button) void openAIResearchReport(button.dataset.aiReportId);
});
byId("aiResearchMemoryList").addEventListener("click", (event) => {
  const button = event.target.closest("[data-ai-memory-id]");
  if (button) void confirmAIResearchMemory(button.dataset.aiMemoryId);
});
byId("aiResearchQuestionList").addEventListener("click", (event) => {
  const button = event.target.closest("[data-ai-question-id]");
  if (button) void updateAIResearchQuestion(button.dataset.aiQuestionId, button.dataset.aiQuestionStatus);
});
byId("dataIntelRefreshButton").addEventListener("click", () => loadDataIntelligence());
byId("dataIntelEvaluateButton").addEventListener("click", evaluateLatestData);
byId("dataIntelIncidentList").addEventListener("click", (event) => {
  const button = event.target.closest("[data-ack-data-incident]");
  if (button) void acknowledgeDataIncident(button.dataset.ackDataIncident, button);
});
byId("evolutionRefreshButton").addEventListener("click", () => loadStrategyEvolution());
byId("evolutionImportForm").addEventListener("submit", importEvolutionBranch);
byId("evolutionEvaluateButton").addEventListener("click", evaluateEvolutionBranch);
byId("evolutionCompareForm").addEventListener("submit", compareEvolutionVersions);
byId("evolutionTransitionForm").addEventListener("submit", requestEvolutionTransition);
byId("evolutionStrategySelect").addEventListener("change", (event) => {
  const option = event.target.selectedOptions[0];
  if (option?.dataset.version) byId("evolutionVersionInput").value = option.dataset.version;
});
byId("evolutionBranchList").addEventListener("click", (event) => {
  const button = event.target.closest("[data-evolution-branch]");
  if (!button) return;
  state.strategyEvolution.selectedBranchKey = button.dataset.evolutionBranch;
  const branch = evolutionSelectedBranch();
  if (branch) {
    byId("evolutionCompareStrategy").value = branch.strategy_id;
    byId("evolutionVersionA").value = branch.version;
    byId("evolutionTransitionStrategy").value = branch.strategy_id;
    byId("evolutionTransitionVersion").value = branch.version;
    if (branch.next_state) byId("evolutionTargetState").value = branch.next_state;
  }
  renderStrategyEvolution(state.strategyEvolution.dashboard || {});
});
byId("evolutionLifecycleList").addEventListener("click", (event) => {
  const button = event.target.closest("[data-evolution-decision]");
  if (button) void decideEvolutionTransition(button.dataset.requestId, button.dataset.evolutionDecision);
});
byId("evolutionReportList").addEventListener("click", (event) => {
  const button = event.target.closest("[data-evolution-report]");
  if (button) void openEvolutionReport(button.dataset.evolutionReport);
});
byId("engineeringRefreshButton").addEventListener("click", () => loadEngineeringCenter());
byId("engineeringHealthButton").addEventListener("click", (event) => runEngineeringAction(
  "run_engineering_health",
  event.currentTarget,
  "正在只读检查数据、数据库、AI 与策略合同。",
  "工程健康快照已保存；未修改任何投资状态。",
));
byId("engineeringBackupButton").addEventListener("click", (event) => runEngineeringAction(
  "create_engineering_backup",
  event.currentTarget,
  "正在复制本地证据并生成 SHA-256 清单。",
  "本地备份已完成并校验；源证据未被覆盖。",
));
byId("observabilityRefreshButton").addEventListener("click", () => loadObservabilityCenter());
byId("observabilityTimeRange").addEventListener("change", () => loadObservabilityCenter());
byId("observabilityEvaluateButton").addEventListener("click", () => runObservabilityOperation(
  "evaluate_observability", { body: {} }, "已有工程证据已采样，SLO与告警已重新评估；未执行自动修复。",
));
byId("observabilityRetentionPlanButton").addEventListener("click", () => runObservabilityOperation(
  "plan_observability_retention", { body: {} }, "Retention计划已保存；尚未删除任何数据。",
));
byId("observabilityAlertList").addEventListener("click", (event) => {
  const button = event.target.closest("[data-observability-ack]");
  if (button) void runObservabilityOperation(
    "acknowledge_observability_alert",
    { pathParams: { alert_id: button.dataset.observabilityAck }, body: { actor: "local-dashboard", note: "用户已在可观测中心确认查看" } },
    "告警已标记为已查看；根因和工程状态未改变。",
  );
});
byId("observabilityTraceList").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-observability-trace]");
  if (!button) return;
  try {
    const trace = await apiRequest("get_observability_trace", { pathParams: { trace_id: button.dataset.observabilityTrace } });
    showToast("Trace链路", `${trace.root_operation} · ${trace.spans?.length || 0}个Span · ${trace.status}`, observabilityTone(trace.status), 7000);
  } catch (error) {
    showToast("Trace读取失败", error.message, "error", 6500);
  }
});
byId("observabilityTraceQuery").addEventListener("change", async (event) => {
  const query = event.target.value.trim();
  if (!query) return loadObservabilityCenter();
  try {
    const payload = await apiRequest("list_observability_traces", { query: { query, limit: 100 } });
    const dashboard = { ...(state.observability.dashboard || {}), traces: payload.items || [] };
    renderObservabilityCenter(dashboard);
  } catch (error) {
    showToast("Trace检索失败", error.message, "error", 6500);
  }
});

document.querySelectorAll(".paper-tab").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll(".paper-tab").forEach((item) => item.classList.remove("active"));
  button.classList.add("active"); state.paperAuditView = button.dataset.view; renderPaperAudit();
}));

document.querySelectorAll(".etf-tab").forEach((button) => button.addEventListener("click", async () => {
  document.querySelectorAll(".etf-tab").forEach((item) => { item.classList.remove("active"); item.setAttribute("aria-selected", "false"); });
  button.classList.add("active"); button.setAttribute("aria-selected", "true"); state.reviewActivityKind = button.dataset.kind;
  renderReviewActivity(state.reviewActivityKind, state.workbench?.activity?.[state.reviewActivityKind] || []);
}));

byId("personalOsRefreshButton").addEventListener("click", () => loadPersonalInvestmentOS());
byId("investmentDashboardRefreshButton").addEventListener("click", () => loadInvestmentDashboard());
byId("personalScoreRefreshButton").addEventListener("click", () => runPersonalAction(
  "refresh_personal_investment_score", { body: {} }, "投资流程评分已按固定权重更新。",
));
byId("personalCoachGenerateButton").addEventListener("click", () => runPersonalAction(
  "create_personal_coach_report", { body: {} }, "个人投资教练报告已归档。",
));
byId("personalWeeklyButton").addEventListener("click", () => runPersonalAction(
  "create_personal_committee_report", { body: {} }, "本周四角色投资委员会报告已归档。",
));
byId("personalMonthlyButton").addEventListener("click", () => runPersonalAction(
  "create_personal_monthly_review", { body: {} }, "月度复盘已按现有证据生成。",
));
byId("personalEventSyncButton").addEventListener("click", () => runPersonalAction(
  "sync_personal_investment_events", { body: {} }, "模拟成交已幂等同步到投资事件链。",
));

byId("personalProfileForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const behavior = byId("personalProfileBehavior").value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
  void runPersonalAction("update_investor_digital_twin", { body: {
    capital: Number(byId("personalProfileCapital").value),
    risk_level: byId("personalProfileRisk").value,
    holding_period: byId("personalProfilePeriod").value,
    investment_style: byId("personalProfileStyle").value,
    max_drawdown: Number(byId("personalProfileDrawdown").value) / 100,
    behavior,
    source_profile_id: state.personalOS.dashboard?.investor_profile?.source_profile_id || null,
  } }, "投资者数字孪生已保存；生产策略与风控未改变。");
});

byId("personalEventForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const symbol = byId("personalEventSymbol").value.trim().toUpperCase() || null;
  void runPersonalAction("create_personal_investment_event", { body: {
    idempotency_key: crypto.randomUUID(),
    event_type: byId("personalEventType").value,
    trade_date: personalToday(), symbol,
    reason: byId("personalEventReason").value.trim(),
    evidence_ids: [],
  } }, "个人观察事件已记录。 ");
  event.currentTarget.reset();
});

byId("personalJournalForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const symbol = byId("personalJournalSymbol").value.trim().toUpperCase() || null;
  void runPersonalAction("create_personal_investment_journal", { body: {
    idempotency_key: crypto.randomUUID(),
    entry_type: byId("personalJournalType").value,
    trade_date: personalToday(), symbol,
    title: byId("personalJournalTitle").value.trim(),
    reason: byId("personalJournalContent").value.trim(),
    user_text: byId("personalJournalContent").value.trim(),
    review_cycle: "NONE",
    source: "USER",
    evidence_ids: [],
  } }, "投资日志已保存，不会成为交易信号。 ");
  event.currentTarget.reset();
});

byId("personalKnowledgeForm").addEventListener("submit", (event) => {
  event.preventDefault();
  void runPersonalAction("create_personal_knowledge", { body: {
    idempotency_key: crypto.randomUUID(),
    category: byId("personalKnowledgeCategory").value,
    subject: byId("personalKnowledgeSubject").value.trim(),
    title: byId("personalKnowledgeTitle").value.trim(),
    content: byId("personalKnowledgeContent").value.trim(),
    evidence_ids: [], confidence: 1,
  } }, "个人知识已归档，不会覆盖 ResearchPipeline 证据。 ");
  event.currentTarget.reset();
});

document.querySelectorAll(".workspace-tab, .workspace-link, [data-open-workspace]").forEach((button) => {
  button.addEventListener("click", () => activateWorkspace(button.dataset.workspace || button.dataset.openWorkspace));
});

document.querySelectorAll("[data-assistant-intent]").forEach((button) => {
  button.addEventListener("click", () => setPersonalAssistantIntent(button.dataset.assistantIntent));
});
byId("personalAssistantForm").addEventListener("submit", (event) => {
  event.preventDefault();
  void queryPersonalAssistant();
});

byId("reviewQuickCycle").addEventListener("change", (event) => {
  byId("reviewQuickCustomDate").classList.toggle("hidden", event.target.value !== "CUSTOM");
});

byId("reviewQuickJournalForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const symbol = byId("reviewQuickSymbol").value.trim().toUpperCase();
  const cycle = byId("reviewQuickCycle").value;
  if (symbol && !validSymbol(symbol)) {
    showToast("股票代码格式不正确", "请输入如 600519.SH 的沪深股票代码。", "warning", 4500);
    return;
  }
  if (cycle === "CUSTOM" && !byId("reviewQuickCustomDate").value) {
    showToast("请选择复盘日期", "自定义周期必须提供日期。", "warning", 4500);
    return;
  }
  try {
    await apiRequest("create_investment_review_journal", { body: journalCreateBody({
      entryType: byId("reviewQuickType").value,
      symbol,
      title: byId("reviewQuickTitle").value.trim(),
      reason: byId("reviewQuickReason").value.trim(),
      cycle,
      customDate: byId("reviewQuickCustomDate").value || null,
    }) });
    event.currentTarget.reset();
    byId("reviewQuickCustomDate").classList.add("hidden");
    await loadInvestmentReviewLoop({ sync: true });
    showToast("投资日志已保存", "你的原话与复盘日期已分开保存；没有触发AI或交易。", "success");
  } catch (error) { showToast("日志保存失败", error.message, "error", 6500); }
});

byId("reviewReminderSyncButton").addEventListener("click", async () => {
  try {
    await apiRequest("sync_investment_review_reminders", { body: {} });
    await loadInvestmentReviewLoop();
    showToast("提醒已刷新", "只比较已保存快照，没有运行研究、AI或订单。", "success");
  } catch (error) { showToast("提醒刷新失败", error.message, "error", 6500); }
});

byId("workspace-review").addEventListener("click", async (event) => {
  const reviewButton = event.target.closest("[data-review-journal-id]");
  if (reviewButton) { await openJournalReview(reviewButton.dataset.reviewJournalId); return; }
  const reminderButton = event.target.closest("[data-reminder-id]");
  if (!reminderButton) return;
  try {
    await apiRequest("update_investment_review_reminder_status", {
      pathParams: { reminder_id: reminderButton.dataset.reminderId },
      body: { status: reminderButton.dataset.reminderStatus },
    });
    await loadInvestmentReviewLoop();
  } catch (error) { showToast("提醒状态更新失败", error.message, "error", 6500); }
});

byId("journalReviewDialogClose").addEventListener("click", () => byId("journalReviewDialog").close());
byId("journalReviewForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const draft = state.investmentReview.draft;
  if (!draft || !state.investmentReview.journalId) return;
  try {
    const result = await apiRequest("confirm_investment_review", {
      pathParams: { journal_id: state.investmentReview.journalId },
      body: {
        idempotency_key: crypto.randomUUID(),
        reviewed_at: new Date().toISOString(),
        facts_changed: Object.keys(draft.facts_changed || {}),
        thesis_status: byId("journalReviewThesis").value,
        risk_status: byId("journalReviewRisk").value.trim(),
        result_summary: byId("journalReviewResult").value.trim(),
        mistakes: splitLines("journalReviewMistakes"),
        good_decisions: splitLines("journalReviewGood"),
        lesson_candidate: byId("journalReviewLesson").value.trim() || null,
        evidence_refs: draft.evidence_refs || [],
        draft,
        user_confirmed: true,
      },
      timeoutMs: 45000,
    });
    byId("journalReviewDialog").close();
    event.currentTarget.reset();
    await loadInvestmentReviewLoop({ sync: true });
    showToast("复盘已确认", result.lesson ? "REVIEW与一条用户确认的LESSON已保存。" : "REVIEW已保存；未自动生成经验。", "success");
  } catch (error) { showToast("复盘保存失败", error.message, "error", 6500); }
});

byId("assistantJournalSaveForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const answer = state.personalAssistant.answer;
  if (!answer?.summary) return;
  const symbol = byId("personalAssistantSymbol").value.trim().toUpperCase();
  const userText = byId("assistantJournalUserText").value.trim();
  try {
    await apiRequest("create_investment_review_journal", { body: journalCreateBody({
      entryType: byId("assistantJournalType").value,
      symbol: validSymbol(symbol) ? symbol : "",
      title: `${byId("personalAssistantHeadline").textContent} · 用户确认`,
      reason: userText,
      cycle: byId("assistantJournalCycle").value,
      source: "AI_SAVED",
      aiSummary: answer.summary,
      evidenceRefs: answer.evidence_refs || [],
    }) });
    event.currentTarget.reset();
    showToast("AI结果已保存为日志", "你的原话与AI摘要分别保存；不是自动决策。", "success");
  } catch (error) { showToast("AI日志保存失败", error.message, "error", 6500); }
});

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-journal-action]");
  if (!button) return;
  openReviewQuickForm({
    entryType: button.dataset.journalAction,
    symbol: button.dataset.journalSymbol,
    title: button.dataset.journalTitle,
    reason: button.dataset.journalReason,
  });
});
byId("settingsTestModelButton").addEventListener("click", () => runAction(
  "test_model_connection", { body: {} }, 45000,
  { buttonId: "settingsTestModelButton", busyLabel: "测试中…", overlay: false, successTitle: "DeepSeek连接正常", successMessage: "模型保持只读且无交易权限。" },
));
byId("settingsShowAISummary").addEventListener("change", (event) => {
  byId("dashboardAICard").classList.toggle("hidden", !event.target.checked);
});

window.addEventListener("hashchange", () => {
  activateWorkspace(location.hash.slice(1), { updateHash: false, scroll: false });
});

window.addEventListener("resize", () => {
  drawEquity(state.equityPoints);
  drawRadar(state.workbench?.discipline?.dimensions || []);
});

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    startLiveSync({ immediate: true });
    if (state.activeWorkspace === "market") {
      startQuotePolling();
      startBrokerMatching();
    }
  } else {
    stopLiveSync();
    stopQuotePolling();
    stopBrokerMatching();
  }
});

window.addEventListener("offline", () => {
  state.networkOnline = false;
  if (state.quotePollingActive) markLiveModule("quote", "paused", "网络离线 · 保留最后快照");
  markLiveModule("candidates", "paused", "网络离线 · 保留最后候选榜");
  renderLiveSyncCenter();
});

window.addEventListener("online", () => {
  state.networkOnline = true;
  if (state.quotePollingActive) state.syncJobs.quote.nextAt = 0;
  state.candidateAutoNextAt = Date.now() + state.candidateAutoIntervalSeconds * 1000;
  tickLiveScheduler();
  showToast("网络已恢复", "本地状态立即同步，行情与候选榜按各自周期恢复。", "success", 3200);
});

// “/”直接聚焦候选搜索，Escape清空搜索，方便高频查看候选榜。
document.addEventListener("keydown", (event) => {
  if (event.key === "/" && state.activeWorkspace === "market" && !["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement?.tagName)) {
    event.preventDefault();
    byId("rankingSearch").focus();
  }
  if (event.key === "Escape" && document.activeElement === byId("rankingSearch") && byId("rankingSearch").value) {
    byId("rankingSearch").value = "";
    state.rankingQuery = "";
    renderRanking(state.currentRanking);
  }
});

window.addEventListener("beforeunload", () => {
  stopLiveSync();
  stopQuotePolling();
  stopBrokerMatching();
});

activateWorkspace(location.hash.slice(1) || "dashboard", { updateHash: false, scroll: false });
changeCandidateAutoRefresh(true, 300);
startLiveSync();
void refreshAll({ source: "initial" });
