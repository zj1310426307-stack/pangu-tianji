import { apiRequest, OPERATIONS } from "../generated/client.js";

const TOKEN_KEY = "pangu.mobile.access_token";
const FOREGROUND_SYNC_MS = 60_000;
const VIEW_NAMES = new Set(["dashboard", "portfolio", "stock", "assistant", "reports", "journal"]);
const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "::1", "[::1]"]);

const state = {
  activeView: "dashboard",
  previousView: null,
  token: sessionStorage.getItem(TOKEN_KEY) || "",
  session: null,
  authStatus: null,
  pairingId: "",
  dashboard: null,
  portfolio: null,
  stock: null,
  stockSymbol: "",
  reports: [],
  reportFilter: "all",
  notifications: [],
  notificationFilter: "all",
  journal: [],
  journalCounts: {},
  editingJournal: null,
  busy: new Set(),
  syncTimer: null,
  toastTimer: null,
};

const elements = {};

/** Cache one DOM element by id so render functions remain concise and explicit. */
function byId(id) {
  if (!elements[id]) elements[id] = document.getElementById(id);
  return elements[id];
}

/** Escape untrusted API text before it is inserted into templated markup. */
function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

/** Return the first present value without treating valid zero values as missing. */
function firstValue(...values) {
  return values.find((value) => value !== undefined && value !== null && value !== "");
}

/** Format a server-provided monetary value without deriving any account metric. */
function formatMoney(value) {
  if (value === undefined || value === null || value === "") return "—";
  if (typeof value === "string" && /[¥￥元]/.test(value)) return value;
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency: "CNY",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(numeric);
}

/** Format a server-provided ratio solely for display; no portfolio value is derived. */
function formatPercent(value) {
  if (value === undefined || value === null || value === "") return "—";
  if (typeof value === "string" && value.includes("%")) return value;
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  if (Math.abs(numeric) <= 1) {
    return new Intl.NumberFormat("zh-CN", {
      style: "percent",
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(numeric);
  }
  return `${numeric.toFixed(2)}%`;
}

/** Format a server timestamp for compact mobile display. */
function formatTime(value, includeDate = false) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", {
    month: includeDate ? "2-digit" : undefined,
    day: includeDate ? "2-digit" : undefined,
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
}

/** Return a visual tone from a server value while leaving the value untouched. */
function numericTone(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric === 0) return "tone-neutral";
  return numeric > 0 ? "tone-positive" : "tone-negative";
}

/** Set text content and an optional visual tone on one field. */
function setText(id, value, tone = "") {
  const node = byId(id);
  if (!node) return;
  node.textContent = value ?? "—";
  node.classList.remove("tone-positive", "tone-negative", "tone-warning", "tone-neutral");
  if (tone) node.classList.add(tone);
}

/** Show a short non-blocking message near the mobile navigation. */
function showToast(message, isError = false) {
  const toast = byId("toast");
  window.clearTimeout(state.toastTimer);
  toast.textContent = message;
  toast.classList.toggle("is-error", isError);
  toast.classList.add("is-visible");
  state.toastTimer = window.setTimeout(() => toast.classList.remove("is-visible"), 2800);
}

/** Guard operation use so a stale generated client fails visibly instead of hand-writing a URL. */
function requireOperation(operationId) {
  if (!OPERATIONS[operationId]) {
    throw new Error(`移动接口尚未同步：${operationId}`);
  }
}

/** Call one generated OpenAPI operation and attach the session-only bearer token. */
async function mobileRequest(operationId, options = {}, useToken = true) {
  requireOperation(operationId);
  const requestOptions = { ...options };
  if (useToken && state.token) requestOptions.accessToken = state.token;
  return apiRequest(operationId, requestOptions);
}

/** Reject any response that unexpectedly grants trading or configuration authority. */
function assertReadOnly(payload) {
  const safety = payload?.safety || {};
  const forbidden = [
    payload?.can_trade,
    payload?.can_create_orders,
    safety.can_trade,
    safety.can_create_orders,
    safety.can_modify_strategy,
    safety.can_modify_portfolio,
    safety.can_modify_risk,
  ];
  if (forbidden.some((value) => value === true)) {
    throw new Error("移动接口返回了越界能力，页面已拒绝展示");
  }
  return payload;
}

/** Detect whether the page is opened from the local desktop service. */
function isLoopback() {
  return LOOPBACK_HOSTS.has(window.location.hostname.toLowerCase());
}

/** Update top-level connectivity without implying market-data freshness. */
function renderConnection(status, label) {
  const dot = byId("connectionDot");
  dot.classList.remove("is-loading", "is-offline");
  if (status === "loading") dot.classList.add("is-loading");
  if (status === "offline") dot.classList.add("is-offline");
  setText("sessionLabel", label);
}

/** Render immutable mobile safety capabilities from the authenticated session. */
function renderSafety(payload) {
  assertReadOnly(payload || {});
  const role = state.session?.role === "admin" ? "个人主人" : "只读访客";
  const suffix = state.session ? ` · ${role}` : "";
  setText("safetyText", `移动端只读${suffix} · 不能创建任何订单（含模拟/实盘）`);
}

/** Return whether this session may write only non-executable mobile audit records. */
function canWriteMobile() {
  return state.session?.role === "admin";
}

/** Reflect viewer/admin mobile scopes without exposing any trading controls. */
function renderSessionCapabilities() {
  const writable = canWriteMobile();
  setText("assistantState", writable ? "个人主人 · 证据问答" : "只读访客 · 仅看历史");
  byId("chatInput").disabled = !writable;
  byId("chatSubmit").disabled = !writable;
  byId("newJournalButton").classList.toggle("is-hidden", !writable);
  document.querySelectorAll("[data-prompt]").forEach((button) => { button.disabled = !writable; });
}

/** Present authentication controls appropriate to loopback or a phone on LAN. */
function renderPairingDialog() {
  const loopback = isLoopback();
  const disabled = state.authStatus?.enabled === false;
  byId("loopbackPairing").classList.toggle("is-hidden", disabled || !loopback || Boolean(state.session));
  byId("pairDeviceForm").classList.toggle("is-hidden", disabled || Boolean(state.session));
  byId("pairedSession").classList.toggle("is-hidden", !state.session);
  if (disabled && !state.session) {
    setText("pairingDescription", "移动助手尚未启用。请在电脑端使用《启动移动助手.cmd》或 server.py --mobile 启动；当前不能生成或输入配对码。");
    return;
  }
  if (state.session) {
    const role = state.session.role === "admin" ? "个人主人" : "只读访客";
    setText("sessionDetail", `${state.session.device_name || "当前设备"} · ${role} · 到期 ${formatTime(state.session.expires_at, true)}`);
  }
  if (loopback && !state.session) {
    setText("pairingDescription", "本机可生成一次性配对码，也可在当前浏览器完成配对。个人主人仅能写入日志、AI提问和通知已读，仍不能修改策略、风控或创建任何订单（含模拟/实盘）。");
  }
}

/** Open the modal used to pair, inspect, or forget the current mobile session. */
function openPairingDialog() {
  renderPairingDialog();
  const dialog = byId("pairingDialog");
  if (!dialog.open) dialog.showModal();
}

/** Read public mobile availability and validate an existing session token. */
async function initializeSession() {
  renderConnection("loading", "连接中");
  try {
    state.authStatus = assertReadOnly(await mobileRequest("get_mobile_auth_status", {}, false));
    if (!state.authStatus.enabled) {
      stopForegroundSync();
      renderConnection("offline", "未启用");
      throw new Error(state.authStatus.message || "移动助手未启用");
    }
    if (!state.token) {
      stopForegroundSync();
      renderConnection("offline", "待配对");
      renderSafety(state.authStatus);
      openPairingDialog();
      return false;
    }
    state.session = assertReadOnly(await mobileRequest("get_mobile_session"));
    renderConnection("ready", state.session.role === "admin" ? "个人主人" : "只读访客");
    renderSafety(state.session);
    renderSessionCapabilities();
    startForegroundSync();
    return true;
  } catch (error) {
    stopForegroundSync();
    if (state.token) {
      sessionStorage.removeItem(TOKEN_KEY);
      state.token = "";
      state.session = null;
    }
    renderConnection("offline", state.authStatus?.enabled ? "待配对" : "未连接");
    showServiceError(error);
    renderPairingDialog();
    return false;
  }
}

/** Generate a single-use pairing challenge through the loopback-only operation. */
async function createPairingCode() {
  const button = byId("createPairingCodeButton");
  button.disabled = true;
  try {
    const result = assertReadOnly(await mobileRequest(
      "create_mobile_pairing_code",
      { body: { role: "admin" } },
      false,
    ));
    state.pairingId = String(result.pairing_id || "");
    setText("pairingCode", result.pairing_code || "—");
    setText("pairingExpires", `配对标识 ${state.pairingId} · 有效至 ${formatTime(result.expires_at)}`);
    byId("pairingIdInput").value = state.pairingId;
    byId("pairingCodeInput").value = result.pairing_code || "";
    byId("pairingCodeResult").classList.remove("is-hidden");
    showToast("一次性配对码已生成");
  } catch (error) {
    showToast(error.message || "配对码生成失败", true);
  } finally {
    button.disabled = false;
  }
}

/** Exchange a one-time challenge for a JWT stored only in sessionStorage. */
async function pairDevice(event) {
  event.preventDefault();
  const button = byId("pairDeviceButton");
  button.disabled = true;
  try {
    const pairingId = byId("pairingIdInput").value.trim();
    const body = {
      pairing_code: byId("pairingCodeInput").value.trim(),
      device_name: byId("deviceNameInput").value.trim(),
    };
    if (pairingId) body.pairing_id = pairingId;
    const result = assertReadOnly(await mobileRequest(
      "pair_mobile_device",
      { body },
      false,
    ));
    if (!result.access_token) throw new Error("配对响应缺少访问令牌");
    state.token = String(result.access_token);
    sessionStorage.setItem(TOKEN_KEY, state.token);
    state.session = null;
    const ready = await initializeSession();
    if (!ready) throw new Error("移动会话校验失败");
    renderPairingDialog();
    showToast("设备已安全连接");
    byId("pairingDialog").close();
    await refreshView(state.activeView);
  } catch (error) {
    showToast(error.message || "设备配对失败", true);
  } finally {
    button.disabled = false;
  }
}

/** Forget only the browser-session token; the server retains no raw token. */
function forgetSession() {
  stopForegroundSync();
  sessionStorage.removeItem(TOKEN_KEY);
  state.token = "";
  state.session = null;
  renderSessionCapabilities();
  renderConnection("offline", "待配对");
  renderPairingDialog();
  showToast("当前浏览器会话已断开");
}

/** Surface API failures while keeping previously rendered evidence visible. */
function showServiceError(error) {
  const banner = byId("offlineBanner");
  setText("offlineMessage", error?.message || "本地服务暂时不可用");
  banner.classList.remove("is-hidden");
}

/** Clear connectivity errors after a successful authenticated refresh. */
function clearServiceError() {
  byId("offlineBanner").classList.add("is-hidden");
  renderConnection("ready", state.session?.role === "admin" ? "个人主人" : "只读访客");
}

/** Change SPA view with hash history and a mobile-friendly back affordance. */
function navigate(view, { replace = false } = {}) {
  const normalized = VIEW_NAMES.has(view) ? view : "dashboard";
  if (state.activeView !== normalized) state.previousView = state.activeView;
  state.activeView = normalized;
  document.querySelectorAll(".mobile-view").forEach((node) => {
    const active = node.dataset.view === normalized;
    node.classList.toggle("is-active", active);
    node.hidden = !active;
  });
  document.querySelectorAll(".bottom-nav [data-route]").forEach((button) => {
    const active = button.dataset.route === normalized;
    button.classList.toggle("is-active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  byId("backButton").classList.toggle("is-hidden", !["reports"].includes(normalized));
  const nextHash = `#${normalized}`;
  if (window.location.hash !== nextHash) {
    if (replace) window.history.replaceState(null, "", nextHash);
    else window.history.pushState(null, "", nextHash);
  }
  window.scrollTo({ top: 0, behavior: "auto" });
  if (state.token) refreshView(normalized);
}

/** Refresh only the active module so mobile interactions remain responsive. */
async function refreshView(view) {
  if (!state.token) {
    openPairingDialog();
    return;
  }
  if (state.busy.has(view)) return;
  state.busy.add(view);
  try {
    if (view === "dashboard") await loadDashboard();
    if (view === "portfolio") await loadPortfolio();
    if (view === "stock" && state.stockSymbol) await loadStock(state.stockSymbol);
    if (view === "assistant") await loadChatHistory();
    if (view === "reports") await loadReports();
    if (view === "journal") await loadJournal();
    clearServiceError();
  } catch (error) {
    showServiceError(error);
  } finally {
    state.busy.delete(view);
  }
}

/** Load the server-composed mobile dashboard; asset fields remain authoritative. */
async function loadDashboard() {
  state.dashboard = assertReadOnly(await mobileRequest("get_mobile_dashboard"));
  renderDashboard(state.dashboard);
}

/** Render server-valued assets, allocation, risk, opportunities and latest report. */
function renderDashboard(data) {
  const valuation = data.asset_valuation || {};
  const performance = data.daily_performance || {};
  const allocation = data.allocation || {};
  const market = data.market || {};
  const risk = data.risk || {};
  const notification = data.notification_summary || {};
  const generatedAt = firstValue(data.generated_at, valuation.valued_at);

  setText("todayLabel", firstValue(data.trade_date, "今日") + " · 今日投资驾驶舱");
  setText("dashboardTitle", firstValue(data.greeting, "今日投资概览"));
  setText("dashboardAsOf", generatedAt ? `更新 ${formatTime(generatedAt)}` : "—");
  setText("dashboardEquity", firstValue(valuation.equity_display, formatMoney(valuation.equity)));
  setText("dashboardCash", firstValue(valuation.cash_display, formatMoney(valuation.cash)));
  setText("dashboardMarketValue", firstValue(valuation.market_value_display, formatMoney(valuation.market_value)));
  setText("dashboardExposure", firstValue(allocation.current_exposure_display, allocation.current_exposure_pct !== undefined ? formatPercent(allocation.current_exposure_pct) : null, formatPercent(allocation.current_exposure_ratio)));
  setText("dashboardDailyPnl", firstValue(performance.daily_pnl_display, formatMoney(performance.daily_pnl)), numericTone(performance.daily_pnl));
  setText("dashboardDailyReturn", firstValue(performance.daily_return_display, formatPercent(performance.daily_return)), numericTone(performance.daily_return));

  const marketLabel = firstValue(market.label, market.status, market.regime, "市场状态暂无证据");
  setText("marketStatus", marketLabel);
  const marketNode = byId("marketStatus");
  marketNode.classList.remove("market-neutral", "market-positive", "market-negative");
  const marketTone = String(firstValue(market.tone, "neutral")).toLowerCase();
  marketNode.classList.add(marketTone === "positive" ? "market-positive" : marketTone === "negative" ? "market-negative" : "market-neutral");

  const riskScore = firstValue(risk.score, risk.risk_score);
  setText("riskScore", riskScore ?? "—");
  setText("riskLevel", firstValue(risk.level, risk.risk_level, risk.availability === "available" ? "已评估" : "待评估"), riskTone(risk.level));
  const riskFlag = Array.isArray(risk.flags) ? risk.flags[0] : null;
  setText("riskSummary", firstValue(risk.summary, risk.message, riskFlag?.message, risk.availability === "available" ? "Risk Center 证据已同步" : "等待 Risk Center 证据"));
  byId("riskProgress").style.width = Number.isFinite(Number(riskScore)) ? `${Math.max(0, Math.min(100, Number(riskScore)))}%` : "0%";

  const exposurePct = firstValue(allocation.current_exposure_pct, allocation.current_exposure_percent);
  setText("exposureRingValue", firstValue(allocation.current_exposure_display, exposurePct !== undefined ? formatPercent(exposurePct) : null, formatPercent(allocation.current_exposure_ratio)));
  if (Number.isFinite(Number(exposurePct))) byId("exposureRingValue").parentElement.style.setProperty("--progress-pct", String(exposurePct));
  setText("targetExposure", firstValue(allocation.target_exposure_display, allocation.target_exposure_pct !== undefined ? formatPercent(allocation.target_exposure_pct) : null, formatPercent(allocation.target_exposure_ratio)));
  setText("exposureState", firstValue(allocation.status, allocation.label, "—"));

  const aiSummary = typeof data.ai_summary === "string" ? data.ai_summary : firstValue(data.ai_summary?.summary, data.ai_summary?.text, data.ai_summary?.message);
  setText("aiBrief", aiSummary || "当前没有可引用的 AI 运营摘要。");
  setText("aiBriefSource", firstValue(data.ai_summary?.source_label, data.ai_summary?.source, "基于已存档投资证据"));

  const opportunities = Array.isArray(data.opportunities) ? data.opportunities : [];
  renderOpportunities(opportunities);
  const latest = firstValue(
    data.latest_report,
    data.latest_reports?.closing_review,
    data.latest_reports?.morning_report,
    data.latest_reports?.intraday_monitor,
    data.latest_reports?.weekly_report,
    data.ai_summary?.source_report_id ? {
      report_id: data.ai_summary.source_report_id,
      report_type: data.ai_summary.report_type,
      title: "最新投资运营报告",
      summary: data.ai_summary.text,
      created_time: data.ai_summary.generated_at,
      status: data.ai_summary.ai_state || "published",
    } : null,
  );
  renderDashboardReport(latest);

  const unread = Number(firstValue(notification.unread_count, notification.unread, 0));
  renderUnreadCount(unread);
  renderSafety(data);
}

/** Map a server-provided risk level to a display tone. */
function riskTone(level) {
  const normalized = String(level || "").toUpperCase();
  if (["LOW", "SAFE", "NORMAL"].includes(normalized)) return "tone-positive";
  if (["HIGH", "CRITICAL", "DANGER"].includes(normalized)) return "tone-negative";
  if (["MEDIUM", "WARNING"].includes(normalized)) return "tone-warning";
  return "tone-neutral";
}

/** Render ranked research opportunities without producing a recommendation locally. */
function renderOpportunities(items) {
  const root = byId("focusList");
  if (!items.length) {
    root.innerHTML = '<div class="empty-card">暂无可用候选证据</div>';
    return;
  }
  root.innerHTML = items.slice(0, 5).map((item) => `
    <button class="focus-card" type="button" data-stock-symbol="${escapeHtml(item.symbol)}">
      <header><strong>${escapeHtml(firstValue(item.name, item.symbol, "未命名股票"))}</strong><small>${escapeHtml(item.symbol || "")}${item.rank ? ` · 第${escapeHtml(item.rank)}名` : ""}</small></header>
      <span class="focus-score">${escapeHtml(firstValue(item.score, item.total_score, "—"))}</span>
      <p>${escapeHtml(firstValue(item.reason, item.summary, item.risk_label, "查看确定性评分证据"))}</p>
    </button>
  `).join("");
}

/** Render a compact link to one immutable Daily Investment OS report. */
function renderDashboardReport(report) {
  const root = byId("dashboardReport");
  if (!report) {
    root.innerHTML = '<div class="empty-card">今日尚未生成运营报告</div>';
    return;
  }
  root.innerHTML = reportCardMarkup(report);
}

/** Load the portfolio DTO whose valuation is composed entirely on the server. */
async function loadPortfolio() {
  state.portfolio = assertReadOnly(await mobileRequest("get_mobile_portfolio"));
  renderPortfolio(state.portfolio);
}

/** Render server-valued positions, exit evidence and exposure snapshots. */
function renderPortfolio(data) {
  const valuation = data.asset_valuation || {};
  const risk = data.risk || {};
  const positions = Array.isArray(data.positions) ? data.positions : [];
  setText("portfolioEquity", firstValue(valuation.equity_display, formatMoney(valuation.equity)));
  setText("portfolioValuedAt", valuation.valued_at ? `估值时点 ${formatTime(valuation.valued_at, true)} · ${valuation.service_version || "ValuationService"}` : "估值时点待更新");
  setText("portfolioPnl", firstValue(valuation.pnl_display, formatMoney(valuation.pnl)), numericTone(valuation.pnl));
  setText("portfolioDrawdown", firstValue(valuation.drawdown_display, formatPercent(valuation.drawdown)));
  setText("portfolioRisk", firstValue(risk.score, risk.risk_score, "—"), riskTone(risk.level));
  setText("positionCount", `${firstValue(data.position_count, positions.length)} 只`);
  renderPositions(positions);
  renderExposure(firstValue(risk.industry_exposure_items, data.industry_exposure_items, []));
  renderSafety(data);
}

/** Render position cards from backend PnL, weight, risk and exit fields only. */
function renderPositions(items) {
  const root = byId("positionList");
  if (!items.length) {
    root.innerHTML = '<div class="empty-card">当前没有模拟持仓</div>';
    return;
  }
  root.innerHTML = items.map((item) => {
    const pnlPct = firstValue(item.unrealized_pnl_pct_display, formatPercent(item.unrealized_pnl_pct));
    const securityRisk = item.security_risk || {};
    const exitSignal = item.exit_signal || {};
    const risk = firstValue(item.risk_level, item.risk_state, securityRisk.level, securityRisk.risk_level, securityRisk.risk_score, "—");
    return `
      <article class="position-card" role="button" tabindex="0" aria-label="查看 ${escapeHtml(firstValue(item.name, item.symbol))} 的研究证据" data-stock-symbol="${escapeHtml(item.symbol)}">
        <header><div><strong>${escapeHtml(firstValue(item.name, item.symbol))}</strong><small>${escapeHtml(item.symbol || "")} · ${escapeHtml(item.quantity ?? "—")}股</small></div><div class="position-return ${numericTone(item.unrealized_pnl_pct)}">${escapeHtml(pnlPct)}</div></header>
        <div class="position-metrics">
          <span><small>持仓市值</small><b>${escapeHtml(firstValue(item.market_value_display, formatMoney(item.market_value)))}</b></span>
          <span><small>当前权重</small><b>${escapeHtml(firstValue(item.weight_display, formatPercent(item.weight)))}</b></span>
          <span><small>单股风险</small><b class="${riskTone(risk)}">${escapeHtml(risk)}</b></span>
        </div>
        <p class="position-advice">${escapeHtml(firstValue(item.exit_summary, item.exit_action, exitSignal.summary, exitSignal.action, Array.isArray(exitSignal.reasons) ? exitSignal.reasons.join("；") : null, "当前没有退出证据"))}</p>
      </article>`;
  }).join("");
}

/** Render backend-computed exposure percentages without aggregating holdings. */
function renderExposure(exposure) {
  const root = byId("industryExposure");
  const items = Array.isArray(exposure) ? exposure : [];
  if (!items.length) {
    root.innerHTML = '<p class="muted">暂无服务端格式化的行业暴露证据。</p>';
    return;
  }
  root.innerHTML = items.map((item) => {
    const pct = firstValue(item.percent, item.pct);
    const width = Number.isFinite(Number(pct)) ? Math.max(0, Math.min(100, Number(pct))) : 0;
    return `<div class="bar-item"><span>${escapeHtml(firstValue(item.label, item.industry, "未分类"))}</span><div class="bar-track"><i style="width:${width}%"></i></div><b>${escapeHtml(firstValue(item.display, pct !== undefined ? `${pct}%` : "—"))}</b></div>`;
  }).join("");
}

/** Load one stock evidence page using only the generated stock-detail operation. */
async function loadStock(symbol) {
  const normalized = String(symbol || "").trim().toUpperCase();
  if (!normalized) return;
  state.stock = assertReadOnly(await mobileRequest("get_mobile_stock_detail", { pathParams: { symbol: normalized } }));
  state.stockSymbol = normalized;
  renderStock(state.stock);
}

/** Render stock score, factors, risk, and an evidence-grounded explanation. */
function renderStock(data) {
  const stock = data.stock || data;
  const ranking = data.ranking || stock.ranking || {};
  const risk = data.risk_analysis || data.risk || stock.risk || {};
  const symbol = firstValue(data.symbol, stock.symbol, state.stockSymbol);
  setText("stockTitle", firstValue(data.name, stock.name, ranking.name, symbol, "股票研究"));
  setText("stockSymbol", symbol);
  setText("stockScore", firstValue(ranking.score, ranking.total_score, stock.score, stock.total_score, "—"));
  const rank = firstValue(ranking.rank, stock.rank);
  setText("stockRank", rank ? `研究排名 #${rank}` : "暂无排名");
  setText("stockPrice", firstValue(ranking.last_price_display, stock.price_display, stock.latest_price_display, formatMoney(firstValue(ranking.last_price, stock.price, stock.latest_price))));
  setText("stockChange", firstValue(ranking.change_display, stock.change_display, formatPercent(firstValue(ranking.change_pct, stock.change_pct, stock.change_ratio))));
  renderFactors(firstValue(data.factor_analysis, data.factors, ranking.factor_scores, stock.factor_scores, stock.factors, {}));
  renderStockRisk(risk, firstValue(data.exit_signal, data.exit, stock.exit));
  const explanation = typeof data.ai_explanation === "string" ? data.ai_explanation : firstValue(data.ai_explanation?.content?.summary, data.ai_explanation?.content?.analysis, data.ai_explanation?.summary, data.ai_summary, stock.explanation);
  setText("stockAiExplanation", explanation || "当前没有已发布的 AI 证据解释。");
  byId("askAboutStockButton").disabled = !state.stockSymbol;
  renderSafety(data);
}

/** Render backend factor components as score bars without rescoring the stock. */
function renderFactors(factors) {
  const root = byId("factorList");
  const items = Array.isArray(factors)
    ? factors
    : Object.entries(factors || {}).map(([label, score]) => ({ label, score }));
  if (!items.length) {
    root.innerHTML = '<p class="muted">当前股票没有可用因子快照。</p>';
    return;
  }
  root.innerHTML = items.map((item) => {
    const score = firstValue(item.score, item.value);
    const width = Number.isFinite(Number(score)) ? Math.max(0, Math.min(100, Number(score))) : 0;
    return `<div class="factor-row"><span>${escapeHtml(firstValue(item.label, item.name, item.key))}</span><div class="bar-track"><i style="width:${width}%"></i></div><b>${escapeHtml(score ?? "—")}</b></div>`;
  }).join("");
}

/** Render deterministic risk and exit reasons as narrative evidence. */
function renderStockRisk(risk, exit) {
  const root = byId("stockRiskCard");
  const reasons = [
    ...(Array.isArray(risk.reasons) ? risk.reasons : []),
    ...(Array.isArray(exit?.reasons) ? exit.reasons : []),
  ];
  const summary = firstValue(risk.summary, risk.message, exit?.summary, exit?.action);
  if (!summary && !reasons.length) {
    root.innerHTML = "<p>当前没有可用单股风险与退出证据。</p>";
    return;
  }
  root.innerHTML = `${summary ? `<p>${escapeHtml(summary)}</p>` : ""}${reasons.length ? `<ul>${reasons.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : ""}`;
}

/** Load recent audited Copilot interactions without replaying any model call. */
async function loadChatHistory() {
  const result = assertReadOnly(await mobileRequest("list_mobile_copilot_history", { query: { limit: 20 } }));
  const items = Array.isArray(result) ? result : result.items || result.history || [];
  renderChatHistory(items);
}

/** Render recent questions as audit context; answers are only shown when returned. */
function renderChatHistory(items) {
  const root = byId("chatThread");
  const greeting = '<article class="chat-message assistant"><span class="chat-avatar">机</span><div><p>我只使用 Data Center、Research Snapshot 和 Risk Snapshot 中的已存档证据回答，不会修改策略、风控，也不能创建任何订单（含模拟/实盘）。</p></div></article>';
  const history = items.slice().reverse().map((item) => {
    const availability = item.answer_availability || (item.answer ? "available" : "unavailable");
    const answer = availability === "available" ? plainText(item.answer) : "";
    const answerBody = answer || (availability === "unavailable"
      ? "该历史回答当前不可用，请查看已存档证据报告。"
      : "回答状态待后端确认。");
    return `
      <article class="chat-message user"><div><p>${escapeHtml(item.question)}</p></div></article>
      <article class="chat-message assistant"><span class="chat-avatar">机</span><div><p>${escapeHtml(answerBody)}</p><footer>${escapeHtml(availability === "available" ? "证据回答可用" : "证据回答不可用")} · run_id ${escapeHtml(item.run_id || "—")} · ${escapeHtml(formatTime(item.created_time, true))}</footer></div></article>
    `;
  }).join("");
  root.innerHTML = greeting + history;
  root.scrollTop = root.scrollHeight;
}

/** Submit a non-executable question to the evidence-first mobile Copilot. */
async function submitChat(event) {
  event.preventDefault();
  const input = byId("chatInput");
  const question = input.value.trim();
  if (!question) return;
  const root = byId("chatThread");
  root.insertAdjacentHTML("beforeend", `<article class="chat-message user"><div><p>${escapeHtml(question)}</p></div></article><article id="pendingChat" class="chat-message assistant is-loading"><span class="chat-avatar">机</span><div><p>正在读取投资证据</p></div></article>`);
  input.value = "";
  byId("chatSubmit").disabled = true;
  root.scrollTop = root.scrollHeight;
  try {
    const result = assertReadOnly(await mobileRequest("create_mobile_copilot_chat", {
      body: {
        question,
        intent: inferCopilotIntent(question),
        symbol: state.stockSymbol || null,
      },
      timeoutMs: 60000,
    }));
    const pending = byId("pendingChat");
    const answer = plainText(firstValue(result.answer, result.content, result.summary, "没有生成可发布的证据回答。"));
    pending?.classList.remove("is-loading");
    if (pending) pending.innerHTML = `<span class="chat-avatar">机</span><div><p>${escapeHtml(answer)}</p><footer>run_id ${escapeHtml(result.run_id || "—")} · 证据 ${escapeHtml((result.evidence_ids || result.evidence || []).length)} 项</footer></div>`;
    setText("chatAnnouncer", `天机已回答：${answer}`);
  } catch (error) {
    byId("pendingChat")?.remove();
    root.insertAdjacentHTML("beforeend", `<article class="chat-message assistant"><span class="chat-avatar">机</span><div><p>${escapeHtml(error.message || "AI证据解释失败")}</p></div></article>`);
    setText("chatAnnouncer", error.message || "AI证据解释失败");
  } finally {
    byId("chatSubmit").disabled = false;
    root.scrollTop = root.scrollHeight;
  }
}

/** Reduce structured AI report content to readable chat text without inventing facts. */
function plainText(value) {
  if (value === undefined || value === null) return "";
  if (typeof value !== "object") return String(value);
  const preferred = firstValue(value.summary, value.answer, value.analysis, value.conclusion, value.message);
  if (preferred !== undefined && preferred !== value) return plainText(preferred);
  return Object.entries(value).map(([key, item]) => `${key.replaceAll("_", " ")}：${Array.isArray(item) ? item.join("；") : typeof item === "object" ? JSON.stringify(item) : item}`).join("\n");
}

/** Classify a shortcut intent for backend routing without creating investment logic. */
function inferCopilotIntent(question) {
  if (state.stockSymbol && /为什么买|为什么关注|为什么选/.test(question)) return "why_selected";
  if (state.stockSymbol) return "stock_analysis";
  if (/风险|回撤|暴露/.test(question)) return "risk_consultation";
  if (/复盘|报告/.test(question)) return "daily_review";
  return "portfolio_analysis";
}

/** Load immutable mobile report summaries. */
async function loadReports() {
  const query = state.reportFilter === "all" ? { limit: 100 } : { limit: 100, report_type: state.reportFilter };
  const result = assertReadOnly(await mobileRequest("list_mobile_reports", { query }));
  state.reports = Array.isArray(result) ? result : result.items || result.reports || [];
  renderReports();
}

/** Render the active report filter from backend-owned report metadata. */
function renderReports() {
  const root = byId("reportList");
  const items = state.reportFilter === "all" ? state.reports : state.reports.filter((item) => item.report_type === state.reportFilter);
  root.innerHTML = items.length ? items.map(reportCardMarkup).join("") : '<div class="empty-card">该类型暂无投资运营报告</div>';
}

/** Build one safe report summary card shared by dashboard and report center. */
function reportCardMarkup(report) {
  const meta = reportTypeMeta(report.report_type);
  const summary = firstValue(report.summary, report.content?.summary, report.content?.headline, "点击查看完整证据报告");
  return `<button class="report-card" type="button" data-report-id="${escapeHtml(report.report_id)}"><header><span class="report-icon">${meta.icon}</span><span><strong>${escapeHtml(firstValue(report.title, meta.label))}</strong><small>${escapeHtml(report.trade_date || "")} · ${escapeHtml(formatTime(report.created_time))}</small></span><span class="report-state">${escapeHtml(report.status || "published")}</span></header><p>${escapeHtml(summary)}</p></button>`;
}

/** Return stable mobile labels for the four Daily Investment OS reports. */
function reportTypeMeta(reportType) {
  const values = {
    morning_report: { label: "盘古晨报", icon: "晨" },
    intraday_monitor: { label: "盘中监控", icon: "盘" },
    closing_review: { label: "盘后复盘", icon: "复" },
    weekly_report: { label: "盘古周报", icon: "周" },
  };
  return values[reportType] || { label: "投资报告", icon: "报" };
}

/** Fetch and display one evidence-linked report in a bottom sheet. */
async function openReport(reportId) {
  if (!reportId) return;
  try {
    const report = assertReadOnly(await mobileRequest("get_mobile_report", { pathParams: { report_id: reportId } }));
    const meta = reportTypeMeta(report.report_type);
    setText("reportDialogType", `${meta.label.toUpperCase()} · ${report.trade_date || ""}`);
    setText("reportDialogTitle", firstValue(report.title, meta.label));
    byId("reportDialogBody").innerHTML = reportContentMarkup(report.content || report);
    const evidence = Array.isArray(report.evidence) ? report.evidence : [];
    byId("reportEvidence").innerHTML = evidence.length
      ? evidence.map((item) => `<span class="evidence-chip">${escapeHtml(firstValue(item.source_type, item.evidence_type, "evidence"))} · ${escapeHtml(firstValue(item.source_id, item.evidence_id, item.hash, "—"))}</span>`).join("")
      : '<span class="evidence-chip">无可展示证据引用</span>';
    const dialog = byId("reportDialog");
    if (!dialog.open) dialog.showModal();
  } catch (error) {
    showToast(error.message || "报告读取失败", true);
  }
}

/** Convert a structured report object into readable escaped sections. */
function reportContentMarkup(content) {
  if (typeof content === "string") return `<p>${escapeHtml(content)}</p>`;
  const hiddenKeys = new Set(["evidence", "evidence_ids", "can_trade", "can_create_orders", "safety"]);
  return Object.entries(content || {}).filter(([key]) => !hiddenKeys.has(key)).map(([key, value]) => {
    const label = String(key).replaceAll("_", " ");
    if (Array.isArray(value)) return `<h3>${escapeHtml(label)}</h3><ul>${value.map((item) => `<li>${escapeHtml(typeof item === "object" ? firstValue(item.summary, item.message, item.reason, JSON.stringify(item)) : item)}</li>`).join("")}</ul>`;
    if (value && typeof value === "object") return `<h3>${escapeHtml(label)}</h3><p>${escapeHtml(JSON.stringify(value, null, 2))}</p>`;
    return `<h3>${escapeHtml(label)}</h3><p>${escapeHtml(value)}</p>`;
  }).join("");
}

/** Load notification cards and unread counts from the mobile boundary. */
async function loadNotifications() {
  const result = assertReadOnly(await mobileRequest("list_mobile_notifications", { query: { limit: 100 } }));
  state.notifications = Array.isArray(result) ? result : result.items || result.notifications || [];
  renderNotifications();
}

/** Refresh notification GET data once while preventing overlapping foreground polls. */
async function refreshNotifications() {
  const busyKey = "notifications";
  if (!state.token || !state.session || state.busy.has(busyKey)) return;
  state.busy.add(busyKey);
  try {
    await loadNotifications();
  } finally {
    state.busy.delete(busyKey);
  }
}

/** Synchronize only GET-backed visible evidence; never trigger AI, journal, or order writes. */
async function syncVisibleState() {
  if (!state.token || !state.session || document.visibilityState !== "visible") return;
  await Promise.allSettled([
    refreshView(state.activeView),
    refreshNotifications(),
  ]);
}

/** Start one foreground-only synchronization timer for the authenticated session. */
function startForegroundSync() {
  stopForegroundSync();
  if (!state.token || !state.session || document.visibilityState !== "visible") return;
  state.syncTimer = window.setInterval(() => { void syncVisibleState(); }, FOREGROUND_SYNC_MS);
}

/** Stop foreground synchronization on hide, disconnect, or session failure. */
function stopForegroundSync() {
  if (state.syncTimer !== null) window.clearInterval(state.syncTimer);
  state.syncTimer = null;
}

/** Render INFO/WARNING/CRITICAL events and preserve server read state. */
function renderNotifications() {
  const root = byId("notificationList");
  const filtered = state.notifications.filter((item) => {
    if (state.notificationFilter === "unread") return item.status === "unread";
    if (state.notificationFilter === "critical") return String(item.level).toUpperCase() === "CRITICAL";
    return true;
  });
  const unread = state.notifications.filter((item) => item.status === "unread").length;
  renderUnreadCount(unread);
  root.innerHTML = filtered.length ? filtered.map((item) => {
    const level = String(item.level || "INFO").toLowerCase();
    return `<article class="notification-card ${item.status === "unread" ? "is-unread" : ""}"><header><span class="notification-level ${escapeHtml(level)}"></span><strong>${escapeHtml(item.title || "投资通知")}</strong><time>${escapeHtml(formatTime(item.created_time, true))}</time></header><p>${escapeHtml(item.message || "")}</p>${item.status === "unread" && canWriteMobile() ? `<button type="button" data-read-notification="${escapeHtml(item.notification_id)}">标记已读</button>` : ""}</article>`;
  }).join("") : '<div class="empty-card">当前筛选下没有通知</div>';
}

/** Keep the visual badge and screen-reader notification count synchronized. */
function renderUnreadCount(unreadCount) {
  const unread = Math.max(0, Number(unreadCount) || 0);
  setText("unreadBadge", unread > 99 ? "99+" : String(unread));
  byId("unreadBadge").classList.toggle("is-hidden", unread <= 0);
  byId("notificationButton").setAttribute(
    "aria-label",
    unread > 0 ? `打开通知中心，${unread}条未读` : "打开通知中心，0条未读",
  );
}

/** Mark one notification read through the generated non-trading mobile operation. */
async function markNotificationRead(notificationId) {
  try {
    assertReadOnly(await mobileRequest("mark_mobile_notification_read", { pathParams: { notification_id: notificationId } }));
    await loadNotifications();
  } catch (error) {
    showToast(error.message || "通知状态更新失败", true);
  }
}

/** Load immutable journal records and server-computed journal counts. */
async function loadJournal() {
  const result = assertReadOnly(await mobileRequest("list_mobile_journal", { query: { limit: 100 } }));
  state.journal = Array.isArray(result) ? result : result.items || result.entries || [];
  state.journalCounts = result.counts || {};
  renderJournal();
}

/** Render journal records without inferring investment success or review state. */
function renderJournal() {
  setText("journalMonthCount", firstValue(state.journalCounts.month_count, state.journalCounts.total, "—"));
  setText("journalPendingCount", firstValue(state.journalCounts.pending_review, "—"));
  setText("journalReviewedCount", firstValue(state.journalCounts.reviewed, "—"));
  const root = byId("journalList");
  root.innerHTML = state.journal.length ? state.journal.map((item) => `
    <article class="journal-card" role="button" tabindex="0" data-journal-id="${escapeHtml(item.entry_id)}"><header><div><strong>${escapeHtml(firstValue(item.title, journalTypeLabel(item.entry_type)))}</strong><small>${escapeHtml(item.trade_date || "")} ${item.symbol ? `· ${escapeHtml(item.symbol)}` : ""}</small></div><span class="journal-action">${escapeHtml(journalTypeLabel(item.entry_type))}</span></header><p>${escapeHtml(item.content || "")}</p>${item.review_due_date ? `<footer>计划复盘 · ${escapeHtml(item.review_due_date)}</footer>` : ""}</article>
  `).join("") : '<div class="empty-card">还没有投资日志。记录理由，让未来的你能验证当时的决策。</div>';
}

/** Map journal record types to plain-language labels. */
function journalTypeLabel(entryType) {
  return ({ general: "观察", buy_reason: "买入理由", sell_reason: "卖出理由", review: "复盘结果" })[entryType] || "投资记录";
}

/** Open a journal editor for a new note or one immutable versioned record. */
function openJournalEditor(entry = null) {
  if (!entry && !canWriteMobile()) {
    showToast("只读访客不能新建投资日志", true);
    return;
  }
  state.editingJournal = entry;
  const editing = Boolean(entry);
  const readOnly = !canWriteMobile();
  setText("journalDialogTitle", editing ? "查看与编辑投资日志" : "记录投资理由");
  byId("journalAction").value = entry?.entry_type || "general";
  byId("journalAction").disabled = editing || readOnly;
  byId("journalSymbol").value = entry?.symbol || "";
  byId("journalSymbol").disabled = editing || readOnly;
  byId("journalEntryTitle").value = entry?.title || "";
  byId("journalEntryTitle").disabled = readOnly;
  byId("journalReason").value = entry?.content || "";
  byId("journalReason").disabled = readOnly;
  byId("journalReviewDate").value = entry?.review_due_date || "";
  byId("journalReviewDate").disabled = readOnly;
  byId("journalArchiveButton").classList.toggle("is-hidden", !editing || readOnly);
  byId("journalSubmit").classList.toggle("is-hidden", readOnly);
  setText("journalSubmit", editing ? "保存修改" : "保存到投资记忆");
  const dialog = byId("journalDialog");
  if (!dialog.open) dialog.showModal();
}

/** Fetch one journal entry before editing so optimistic version checks are valid. */
async function openJournalEntry(entryId) {
  try {
    const entry = assertReadOnly(await mobileRequest("get_mobile_journal_entry", { pathParams: { entry_id: entryId } }));
    openJournalEditor(entry);
  } catch (error) {
    showToast(error.message || "日志详情读取失败", true);
  }
}

/** Persist or update one non-executable journal record with optimistic locking. */
async function submitJournal(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const entryType = byId("journalAction").value;
  const symbol = byId("journalSymbol").value.trim().toUpperCase();
  const content = byId("journalReason").value.trim();
  const reviewDate = byId("journalReviewDate").value;
  const title = byId("journalEntryTitle").value.trim() || `${journalTypeLabel(entryType)}${symbol ? ` · ${symbol}` : ""}`;
  const button = byId("journalSubmit");
  button.disabled = true;
  try {
    if (state.editingJournal) {
      assertReadOnly(await mobileRequest("update_mobile_journal_entry", {
        pathParams: { entry_id: state.editingJournal.entry_id },
        body: {
          idempotency_key: crypto.randomUUID(),
          expected_version: firstValue(state.editingJournal.version, state.editingJournal.revision, 1),
          title,
          content,
          review_due_date: reviewDate || null,
        },
      }));
    } else {
      assertReadOnly(await mobileRequest("create_mobile_journal_entry", {
        body: {
          idempotency_key: crypto.randomUUID(),
          entry_type: entryType,
          trade_date: new Date().toLocaleDateString("sv-SE", { timeZone: "Asia/Shanghai" }),
          title,
          content,
          symbol: symbol || null,
          linked_report_id: null,
          review_due_date: reviewDate || null,
        },
      }));
    }
    form.reset();
    state.editingJournal = null;
    byId("journalDialog").close();
    showToast("投资日志已保存并留存审计版本");
    await loadJournal();
  } catch (error) {
    showToast(error.message || "投资日志保存失败", true);
  } finally {
    button.disabled = false;
  }
}

/** Soft-archive the current journal version while preserving its revision audit. */
async function archiveJournal() {
  const entry = state.editingJournal;
  if (!entry) return;
  const button = byId("journalArchiveButton");
  button.disabled = true;
  try {
    assertReadOnly(await mobileRequest("archive_mobile_journal_entry", {
      pathParams: { entry_id: entry.entry_id },
      body: { idempotency_key: crypto.randomUUID(), expected_version: firstValue(entry.version, entry.revision, 1) },
    }));
    state.editingJournal = null;
    byId("journalDialog").close();
    showToast("日志已归档，历史修订仍保留");
    await loadJournal();
  } catch (error) {
    showToast(error.message || "日志归档失败", true);
  } finally {
    button.disabled = false;
  }
}

/** Auto-grow the chat input while preserving a bounded composer. */
function resizeChatInput() {
  const input = byId("chatInput");
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 115)}px`;
}

/** Wire delegated navigation, report, stock, notification, and dialog actions. */
function bindDelegatedActions() {
  document.addEventListener("click", (event) => {
    const route = event.target.closest("[data-route]")?.dataset.route;
    if (route) navigate(route);

    const stock = event.target.closest("[data-stock-symbol]")?.dataset.stockSymbol;
    if (stock) {
      state.stockSymbol = stock;
      byId("stockSearchInput").value = stock;
      navigate("stock");
    }

    const reportId = event.target.closest("[data-report-id]")?.dataset.reportId;
    if (reportId) openReport(reportId);

    const notificationId = event.target.closest("[data-read-notification]")?.dataset.readNotification;
    if (notificationId) markNotificationRead(notificationId);

    const journalId = event.target.closest("[data-journal-id]")?.dataset.journalId;
    if (journalId) openJournalEntry(journalId);

    const closeId = event.target.closest("[data-close-dialog]")?.dataset.closeDialog;
    if (closeId) byId(closeId)?.close();

    const refresh = event.target.closest("[data-refresh-view]")?.dataset.refreshView;
    if (refresh) refreshView(refresh);

    const prompt = event.target.closest("[data-prompt]")?.dataset.prompt;
    if (prompt) {
      byId("chatInput").value = prompt;
      resizeChatInput();
      byId("chatInput").focus();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (!['Enter', ' '].includes(event.key)) return;
    const target = event.target.closest("[data-stock-symbol], [data-journal-id]");
    if (!target || target.matches("button, a, input, select, textarea")) return;
    event.preventDefault();
    target.click();
  });
}

/** Wire direct controls whose semantics are not suitable for event delegation. */
function bindControls() {
  byId("sessionButton").addEventListener("click", openPairingDialog);
  byId("createPairingCodeButton").addEventListener("click", createPairingCode);
  byId("pairDeviceForm").addEventListener("submit", pairDevice);
  byId("forgetSessionButton").addEventListener("click", forgetSession);
  byId("retryButton").addEventListener("click", async () => {
    const ready = await initializeSession();
    if (ready) refreshView(state.activeView);
  });
  byId("notificationButton").addEventListener("click", async () => {
    if (!state.token) return openPairingDialog();
    const dialog = byId("notificationSheet");
    if (!dialog.open) dialog.showModal();
    try { await refreshNotifications(); } catch (error) { showToast(error.message || "通知读取失败", true); }
  });
  byId("backButton").addEventListener("click", () => navigate(state.previousView || "dashboard"));
  byId("stockSearchButton").addEventListener("click", () => searchStock());
  byId("stockSearchInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); searchStock(); }
  });
  byId("askAboutStockButton").addEventListener("click", () => {
    const symbol = state.stockSymbol;
    if (!symbol) return showToast("请先选择股票", true);
    byId("chatInput").value = `请基于证据分析 ${symbol}，解释评分、主要风险和失效条件`;
    navigate("assistant");
  });
  byId("chatForm").addEventListener("submit", submitChat);
  byId("chatInput").addEventListener("input", resizeChatInput);
  byId("newJournalButton").addEventListener("click", () => openJournalEditor());
  byId("journalForm").addEventListener("submit", submitJournal);
  byId("journalArchiveButton").addEventListener("click", archiveJournal);

  document.querySelectorAll("[data-report-filter]").forEach((button) => button.addEventListener("click", () => {
    state.reportFilter = button.dataset.reportFilter;
    document.querySelectorAll("[data-report-filter]").forEach((item) => {
      const active = item === button;
      item.classList.toggle("is-active", active);
      item.setAttribute("aria-pressed", String(active));
    });
    loadReports().catch((error) => showToast(error.message || "报告读取失败", true));
  }));

  document.querySelectorAll("[data-notification-filter]").forEach((button) => button.addEventListener("click", () => {
    state.notificationFilter = button.dataset.notificationFilter;
    document.querySelectorAll("[data-notification-filter]").forEach((item) => {
      const active = item === button;
      item.classList.toggle("is-active", active);
      item.setAttribute("aria-pressed", String(active));
    });
    renderNotifications();
  }));

  window.addEventListener("hashchange", () => navigate(window.location.hash.slice(1), { replace: true }));
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      startForegroundSync();
      void syncVisibleState();
    } else {
      stopForegroundSync();
    }
  });
  window.addEventListener("pagehide", stopForegroundSync);
}

/** Validate and load a canonical A-share symbol supplied by the user. */
function searchStock() {
  const raw = byId("stockSearchInput").value.trim().toUpperCase();
  const symbol = /^\d{6}\.(SH|SZ)$/.test(raw)
    ? raw
    : /^\d{6}$/.test(raw)
      ? `${raw}.${raw.startsWith("6") ? "SH" : "SZ"}`
      : "";
  if (!symbol) return showToast("请输入6位沪深股票代码", true);
  state.stockSymbol = symbol;
  loadStock(symbol).catch((error) => showToast(error.message || "股票详情读取失败", true));
}

/** Start the mobile client only after generated operations and auth are available. */
async function bootstrap() {
  bindDelegatedActions();
  bindControls();
  renderSessionCapabilities();
  const initialView = VIEW_NAMES.has(window.location.hash.slice(1)) ? window.location.hash.slice(1) : "dashboard";
  navigate(initialView, { replace: true });
  const ready = await initializeSession();
  if (ready) {
    clearServiceError();
    await Promise.allSettled([refreshView(initialView), refreshNotifications()]);
  }
}

bootstrap().catch((error) => showServiceError(error));
