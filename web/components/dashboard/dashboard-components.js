/**
 * PANGU-V3.2-001 dashboard components.
 *
 * These renderers only format the backend aggregate contract. They never derive
 * asset values, PnL, exposure, drawdown, factor scores or trading decisions.
 */

function node(tag, className = "", textValue = "") {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (textValue !== "") element.textContent = String(textValue);
  return element;
}

function replace(root, children) {
  root.replaceChildren(...children.filter(Boolean));
}

function badge(label, tone = "muted") {
  return node("span", `badge badge-${tone}`, label);
}

function emptyState(title, detail) {
  const root = node("div", "dashboard-empty");
  root.append(node("strong", "", title), node("p", "", detail));
  return root;
}

function toneForLevel(level) {
  if (["high", "danger", "critical", "error"].includes(String(level).toLowerCase())) return "danger";
  if (["medium", "warning", "degraded"].includes(String(level).toLowerCase())) return "warning";
  if (["low", "success", "normal", "available"].includes(String(level).toLowerCase())) return "success";
  return "muted";
}

export function MarketCard(root, market = {}) {
  const heading = node("div", "dashboard-card-heading");
  const title = node("div");
  title.append(node("span", "dashboard-eyebrow", "市场状态"), node("h2", "", market.label || "研究池状态"));
  heading.append(title, badge(market.availability === "available" ? "证据已同步" : "证据不足", toneForLevel(market.availability)));
  const status = node("strong", "dashboard-primary-value", market.status || "unavailable");
  const statement = node("p", "dashboard-copy", market.statement || "当前没有可验证市场状态。");
  const meta = node("div", "dashboard-meta");
  const age = market.age_seconds == null ? "数据年龄不可用" : `数据年龄 ${Math.round(Number(market.age_seconds) / 60)}分钟`;
  meta.append(node("span", "", market.research_date || "无研究日期"), node("span", "", market.scope || "research_pool"), node("span", "", age));
  replace(root, [heading, status, statement, meta]);
}

export function AssetCard(root, asset = {}, format = {}) {
  const heading = node("div", "dashboard-card-heading");
  const title = node("div");
  title.append(node("span", "dashboard-eyebrow", "资产状态"), node("h2", "", "模拟账户"));
  heading.append(title, badge(asset.service_version || "估值不可用", asset.service_version ? "success" : "muted"));
  const equity = node("strong", "dashboard-primary-value", asset.equity == null ? "—" : format.money(asset.equity));
  const metrics = node("dl", "dashboard-inline-metrics");
  [["累计盈亏", asset.pnl == null ? "—" : format.money(asset.pnl)], ["当前仓位", asset.exposure_ratio == null ? "—" : format.percent(asset.exposure_ratio)], ["当前回撤", asset.drawdown == null ? "—" : format.percent(asset.drawdown)]].forEach(([label, value]) => {
    const row = node("div"); row.append(node("dt", "", label), node("dd", "", value)); metrics.append(row);
  });
  replace(root, [heading, equity, metrics, node("small", "dashboard-source", `估值时点 ${asset.valued_at || "不可用"}`)]);
}

export function RiskCard(root, risk = {}) {
  const heading = node("div", "dashboard-card-heading");
  const title = node("div");
  title.append(node("span", "dashboard-eyebrow", "组合风险"), node("h2", "", "Risk Center"));
  heading.append(title, badge(String(risk.level || "unavailable").toUpperCase(), toneForLevel(risk.level)));
  const score = node("strong", "dashboard-primary-value", risk.score == null ? "—" : `${risk.score}/100`);
  const reasons = node("ul", "dashboard-reason-list");
  (risk.reasons || []).slice(0, 3).forEach((reason) => reasons.append(node("li", "", reason)));
  if (!reasons.children.length) reasons.append(node("li", "", risk.availability === "available" ? "当前未发现活动风险原因" : "组合风险证据不可用"));
  replace(root, [heading, score, reasons]);
}

export function AICard(root, ai = {}) {
  const heading = node("div", "dashboard-card-heading");
  const title = node("div");
  title.append(node("span", "dashboard-eyebrow", "AI投资建议"), node("h2", "", "天机解读"));
  heading.append(title, badge(ai.availability === "available" ? "证据型报告" : "未生成", toneForLevel(ai.availability)));
  const summary = node("p", "dashboard-ai-summary", ai.summary || "当前没有可用AI报告。");
  const refs = node("div", "dashboard-evidence-refs");
  if (ai.report_id) refs.append(node("code", "", `report ${ai.report_id}`));
  if (ai.run_id) refs.append(node("code", "", `run ${ai.run_id}`));
  if (ai.evidence_id) refs.append(node("code", "", `evidence ${ai.evidence_id}`));
  replace(root, [heading, summary, refs, node("small", "dashboard-source", "AI只解释证据，不修改策略、仓位或订单")]);
}

export function StockCard(root, items = []) {
  const heading = node("div", "dashboard-section-heading");
  const title = node("div");
  title.append(node("span", "dashboard-eyebrow", "今日关注"), node("h2", "", "股票候选"));
  heading.append(title, badge(`全部候选 · 当前${items.length}只`, "muted"));
  const grid = node("div", "dashboard-stock-grid");
  items.forEach((item) => {
    const card = node("article", "dashboard-stock-card");
    const top = node("div", "dashboard-stock-top");
    const identity = node("div");
    identity.append(node("strong", "", `${item.rank || "—"}. ${item.name || item.symbol}`), node("small", "", item.symbol || ""));
    top.append(identity, node("b", "", item.score == null ? "—" : Number(item.score).toFixed(1)));
    card.append(top, node("p", "", item.investment_logic || "暂无已保存投资逻辑"));
    const risk = node("div", "dashboard-stock-risk", item.risk || "暂无风险标签");
    const ai = node("small", "dashboard-stock-ai", item.ai_view || "暂无AI观点");
    const footer = node("div", "dashboard-stock-footer");
    footer.append(badge(item.source_mode === "formal_close_plan" ? "正式研究" : "盘中预览", item.source_mode === "formal_close_plan" ? "success" : "warning"), node("span", "", item.used_for_execution ? "计划证据" : "只读观察"));
    card.append(risk, ai, footer); grid.append(card);
  });
  replace(root, [heading, items.length ? grid : emptyState("暂无关注股票", "需要正式研究或盘中预览证据；Dashboard不会自行选股。")]);
}

export function HoldingHealthCard(root, items = [], format = {}) {
  const heading = node("div", "dashboard-section-heading");
  const title = node("div");
  title.append(node("span", "dashboard-eyebrow", "持仓健康"), node("h2", "", "模拟组合"));
  heading.append(title, badge(`${items.length}只持仓`, items.length ? "info" : "muted"));
  const list = node("div", "dashboard-holding-list");
  items.forEach((item) => {
    const card = node("article", "dashboard-holding-row");
    const name = node("div"); name.append(node("strong", "", item.name || item.symbol), node("small", "", item.symbol));
    const health = node("div"); health.append(node("span", "", "健康"), node("b", "", item.health_score == null ? "—" : `${item.health_score}`));
    const pnl = node("div"); pnl.append(node("span", "", "浮动盈亏"), node("b", "", item.pnl == null ? "—" : format.money(item.pnl)));
    const risk = node("div"); risk.append(node("span", "", "风险"), badge(String(item.risk_level || "unavailable").toUpperCase(), toneForLevel(item.risk_level)));
    const advice = node("p", "", item.ai_advice || "暂无已保存建议");
    card.append(name, health, pnl, risk, advice); list.append(card);
  });
  replace(root, [heading, items.length ? list : emptyState("当前空仓", "持仓健康只读取模拟账户实际持仓，不使用固定白名单。")]);
}

export function TaskCard(root, items = []) {
  const heading = node("div", "dashboard-section-heading");
  const title = node("div");
  title.append(node("span", "dashboard-eyebrow", "今日任务"), node("h2", "", "投资行动清单"));
  heading.append(title, badge("只读提醒", "muted"));
  const list = node("div", "dashboard-task-list");
  items.forEach((item) => {
    const row = node("article", "dashboard-task-row");
    const mark = node("span", `dashboard-task-mark state-${item.status || "pending"}`, item.status === "succeeded" ? "✓" : "·");
    const copy = node("div"); copy.append(node("strong", "", item.label), node("p", "", item.description || ""), node("small", "", `${item.source || "Personal OS"}${item.scheduled_for ? ` · ${item.scheduled_for}` : ""}`));
    row.append(mark, copy, badge(item.status || "pending", toneForLevel(item.status))); list.append(row);
  });
  replace(root, [heading, list]);
}
