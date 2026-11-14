"use strict";
const $ = id => document.getElementById(id);
const F = window.LumaFormat;
const widget = document.querySelector(".widget");
const systemReduced = matchMedia("(prefers-reduced-motion: reduce)");
const native = () => window.pywebview?.api;
let state = null, selected = null, polling = false, size = "focus", nativeInitialized = false;
let reduced = localStorage.getItem("luma.reduced") === "true", previousFocus, noticeTimer, fitTimer;
let settingsLoaded = false, searchTimer, searchSerial = 0, receivedAt = 0;
let reviewUpdatedAt = 0;
const canAnimate = () => !reduced && !systemReduced.matches;
const api = async (path, signal) => {
  const response = await fetch(new URL(path, location.href), {cache: "no-store", signal});
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "无法读取本地会话");
  return body;
};
function text(id, value) { $(id).textContent = value ?? "—"; }
function animateText(element, value, first = false) {
  value = String(value);
  if (element.dataset.value === value) return;
  const old = element.dataset.value;
  element.dataset.value = value;
  element.setAttribute("aria-label", value);
  if (first || !canAnimate() || !old || old.length !== value.length) {
    element.replaceChildren(...Array.from(value, char => {
      const slot = document.createElement("span");
      slot.className = "digit-slot" + (/\d/.test(char) ? " numeric" : char === " " ? " space" : "");
      slot.setAttribute("aria-hidden", "true");
      const glyph = document.createElement("span"); glyph.textContent = char; slot.append(glyph);
      return slot;
    }));
    return;
  }
  [...element.children].forEach((slot, i) => {
    if (old[i] === value[i]) return;
    slot.getAnimations({subtree: true}).forEach(a => a.cancel());
    const departed = document.createElement("span");
    departed.textContent = old[i]; departed.className = "departing";
    const glyph = document.createElement("span"); glyph.textContent = value[i];
    slot.replaceChildren(departed, glyph);
    slot.className = "digit-slot" + (/\d/.test(value[i]) ? " numeric" : value[i] === " " ? " space" : "");
    const options = {duration: 390, easing: "cubic-bezier(.22,1,.36,1)"};
    departed.animate([{transform: "translateY(0)", opacity: 1}, {transform: "translateY(-80%)", opacity: 0}], options).finished.then(() => departed.remove()).catch(() => departed.remove());
    glyph.animate([{transform: "translateY(80%)", opacity: 0}, {transform: "translateY(0)", opacity: 1}], options);
  });
}
function notice(message) {
  text("notice", message); $("notice").hidden = false; clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { $("notice").hidden = true; }, 4500);
}
function setReduced(value) {
  reduced = value;
  document.body.classList.toggle("reduced", value || systemReduced.matches);
  $("reduce-motion").checked = value;
  localStorage.setItem("luma.reduced", String(value));
}
function fitWindow() {
  clearTimeout(fitTimer);
  fitTimer = setTimeout(() => {
    if (native()) native().fit_height(Math.min(screen.availHeight - 36, Math.ceil(widget.scrollHeight)), devicePixelRatio);
  }, 100);
}
async function setSize(value, save = true) {
  if (!["ribbon", "mini", "focus", "full"].includes(value)) return;
  size = value; document.body.dataset.size = value;
  document.querySelectorAll("[data-layout]").forEach(button => button.setAttribute("aria-checked", String(button.dataset.layout === value)));
  closeSize();
  if (state) renderSteps(state.steps || []);
  if (save) {
    localStorage.setItem("luma.size", value);
    if (native()) await native().set_size(value, !canAnimate());
  }
  fitWindow();
}
function closeSize() {
  $("size-menu").hidden = true;
  $("size-button").setAttribute("aria-expanded", "false");
  document.body.classList.remove("size-open"); fitWindow();
}
$("size-button").addEventListener("click", () => {
  const open = $("size-menu").hidden;
  $("size-menu").hidden = !open;
  $("size-button").setAttribute("aria-expanded", String(open));
  document.body.classList.toggle("size-open", open);
  if (open) $("size-menu").querySelector('[aria-checked="true"]')?.focus();
  fitWindow();
});
document.querySelectorAll("[data-layout]").forEach(button => button.addEventListener("click", () => setSize(button.dataset.layout)));
document.addEventListener("pointerdown", event => {
  if (!$("size-menu").hidden && !$("size-menu").contains(event.target) && !$("size-button").contains(event.target)) closeSize();
});
function pulseChange(element) {
  if (canAnimate()) element.animate([{opacity: .45, transform: "translateY(3px)"}, {opacity: 1, transform: "translateY(0)"}], {duration: 280, easing: "ease-out"});
}
function renderSteps(rows) {
  const totalWeight = rows.reduce((sum, row) => sum + (row.weight || 1), 0);
  let visible = rows;
  if (size !== "full" && rows.length > 3) {
    const current = rows.findIndex(row => ["in_progress", "inProgress"].includes(row.status));
    const firstPending = rows.findIndex(row => row.status === "pending");
    const anchor = current >= 0 ? current : firstPending >= 0 ? firstPending : rows.length - 1;
    const before = rows.slice(0, anchor).findLast(row => row.status === 'completed');
    const after = rows.slice(anchor + 1).find(row => row.status === 'pending');
    visible = [before, rows[anchor], after].filter(Boolean);
  }
  const signature = JSON.stringify(visible);
  if ($("steps").dataset.signature === signature) return;
  const previous = new Map([...$("steps").children].map(row => [row.dataset.id, row.dataset.status]));
  $("steps").dataset.signature = signature;
  $("steps").replaceChildren(...visible.map(row => {
    const li = document.createElement("li"), active = ["in_progress", "inProgress"].includes(row.status);
    li.className = "step " + row.status;
    li.dataset.id = row.id || row.label || row.step; li.dataset.status = row.status;
    if (previous.has(li.dataset.id) && previous.get(li.dataset.id) !== row.status) li.classList.add("changed");
    const mark = document.createElement("span"); mark.className = "step-mark"; mark.setAttribute("aria-hidden", "true");
    if (row.status === "completed") mark.innerHTML = '<svg viewBox="0 0 16 16"><path d="m3.5 8 3 3 6-6"/></svg>';
    const label = document.createElement("span"); label.className = "step-label"; label.textContent = row.label || row.step;
    label.title = row.evidence || label.textContent;
    const detail = document.createElement("span"); detail.className = "step-detail";
    detail.textContent = row.totalUnits ? (row.completedUnits || 0) + "/" + row.totalUnits : row.status === "completed" ? "完成" : active ? "当前" : "待办";
    li.title = state?.has_checklist || state?.basis === '结构化计划' ? "工作量权重 " + Math.round((row.weight || 1) / totalWeight * 100) + "%" + (row.evidence ? " · " + row.evidence : "") : "由可见活动推断的阶段";
    li.append(mark, label, detail); return li;
  }));
}
function renderActivities(rows) {
  const previous = new Set([...$("activities").children].map(row => row.dataset.id));
  const signature = JSON.stringify(rows.map(row => [row.id, row.text, row.count, row.at]));
  if ($("activities").dataset.signature === signature) return;
  $("activities").dataset.signature = signature;
  $("activities").replaceChildren(...rows.slice(0, 4).map(row => {
    const li = document.createElement("li"); li.dataset.id = row.id || row.at + row.text;
    li.className = (row.kind || "") + (previous.size && !previous.has(li.dataset.id) ? " arriving" : "");
    const dot = document.createElement("span"); dot.className = "event-point";
    const label = document.createElement("span"); label.className = "activity-text";
    label.textContent = row.text + (row.count > 1 ? " ×" + row.count : ""); label.title = label.textContent;
    const time = document.createElement("time"); time.dataset.at = row.at; time.dateTime = new Date(row.at * 1000).toISOString();
    li.append(dot, label, time); return li;
  }));
  if (!rows.length) $("activities").textContent = "等待可见执行事件";
}
function tick() {
  if (!state || state.empty) return;
  const now = state.now + (Date.now() - receivedAt) / 1000;
  const working = state.status === "working";
  const elapsed = working || state.status === "waiting" ? Math.max(0, now - state.started_at) : state.elapsed;
  animateText($("elapsed"), F.elapsed(elapsed));
  const unavailable = {waiting: "等待操作", ended: "已结束", partial: "本轮结束", interrupted: "已中断", failed: "执行失败", unknown: "待估算"};
  const terminal = ["ended", "partial", "interrupted", "failed"].includes(state.status);
  const estimateAvailable = working && Number.isFinite(state.finish_at);
  const ended = terminal && Number.isFinite(state.completed_at);
  const previousForecast = state.comparison?.last?.displayed.finish;
  const eta = ended ? F.clock(state.completed_at, now) :
    estimateAvailable ? F.remaining(state.finish_at - now) : unavailable[state.status] || "待估算";
  const endLabel = {interrupted: "中断时刻", failed: "失败时刻"}[state.status] || "实际结束";
  text("eta-label", ended ? endLabel : terminal ? "本轮结果" : "预计剩余");
  animateText($("eta"), eta);
  const showComparison = ended && Number.isFinite(previousForecast);
  $("finish-clock").parentElement.hidden = !estimateAvailable && !showComparison;
  text("finish-prefix", showComparison ? "曾预计" : "预计");
  text("finish-suffix", "结束");
  if (estimateAvailable || showComparison) {
    animateText($("finish-clock"), F.clock(showComparison ? previousForecast : state.finish_at, now));
  }
  text("freshness", state.age < 30 ? "刚刚更新" : F.age(state.now - state.age, now) + "更新");
  document.querySelectorAll("#activities time").forEach(el => { el.textContent = F.age(Number(el.dataset.at), now); });
}
function render(next) {
  const changedTurn = !state || state.thread_id !== next.thread_id || state.turn_id !== next.turn_id;
  const old = state; state = next; receivedAt = Date.now();
  if (next.empty) {
    text("conversation-title", next.message); text("status-label", "等待本地对话");
    text("current-step", "等待任务开始"); text("current", next.hint);
    return;
  }
  if (!nativeInitialized && native()) {
    nativeInitialized = true; setSize(next.size || "focus", false); setReduced(!!next.reduced_motion);
  }
  widget.classList.remove("working", "waiting", "ended", "partial", "interrupted", "failed", "unknown");
  widget.classList.add(next.status);
  const status = {working: "正在执行", waiting: "等待批准", ended: "本轮已结束", partial: "本轮结束 · 仍有待办", interrupted: "已中断", failed: "执行失败", unknown: "状态待确认"}[next.status] || "状态待确认";
  if (old && old.status !== next.status) pulseChange($("status-label"));
  text("status-label", status); text("conversation-title", next.title); $("conversation-title").title = next.title;
  $("demo-label").hidden = !next.demo;
  text("basis", next.basis); $("approx").hidden = !next.estimated;
  animateText($("percent"), next.percent, changedTurn);
  const range = next.range || [0, 100];
  $("progress-fill").style.transform = "scaleX(" + next.percent / 100 + ")";
  $("scan-lane").style.width = next.percent + "%";
  $("scan-lane").style.setProperty("--scan-travel", Math.max(0, $("progress-track").clientWidth * next.percent / 100) + "px");
  $("progress-track").setAttribute("aria-valuenow", next.percent);
  $("progress-track").setAttribute("aria-valuetext", "估计 " + next.percent + "%，范围 " + range[0] + " 至 " + range[1] + "%");
  $("uncertainty").style.left = range[0] + "%"; $("uncertainty").style.width = Math.max(0, range[1] - range[0]) + "%";
  $("confirmed-tick").hidden = !next.ai_reference || !Number.isFinite(next.confirmed_percent);
  $("confirmed-tick").style.left = (next.confirmed_percent || 0) + "%";
  $("confirmed-tick").title = "清单已报告 " + next.confirmed_percent + "%";
  text("range-text", next.status === "ended" ? "执行已结束" : "估计范围 " + range[0] + "–" + range[1] + "%");
  const impact = next.ai_impact;
  let confidence = next.has_checklist ? "按已报告工作量" : "活动线索 · 低把握";
  if (impact) {
    if (impact.progress === "applied") confidence = "AI 调整进度";
    if (impact.timing === "applied") confidence = impact.progress === "applied" ? "AI 调整进度和时间" : "AI 校正时间";
    if (impact.progress !== "applied" && impact.timing !== "applied") confidence = "已有 AI 建议";
  }
  text("confidence", confidence);
  $("review-button").hidden = !next.comparison && !impact;
  text("review-label", next.comparison ? "预测复盘" : "AI 记录");
  const steps = next.steps || [], active = steps.find(row => ["in_progress", "inProgress"].includes(row.status));
  text('stages-title', next.has_checklist ? '交付清单' : next.basis === '结构化计划' ? '执行计划' : '可见阶段');
  text("progress-context", next.has_checklist ? "已完成部分按工作量加权" : "依据可见活动估计");
  text("step-count", next.has_checklist ? (next.completed_count || 0) + " / " + (next.total_count || steps.length) : "未接入协作清单");
  const currentTitle = next.status !== "working" ? next.current : active?.label || active?.step || next.current || "等待执行事件";
  if (!changedTurn && $("current-step").textContent !== currentTitle) pulseChange($("current-step"));
  text("current-step", currentTitle);
  const currentDetail = next.current && next.current !== currentTitle ? next.current : next.activities?.find(row => row.text !== currentTitle)?.text || "";
  text("current", currentDetail); $("current").hidden = !currentDetail || currentDetail === currentTitle;
  const op = next.operation;
  const opRange = op ? F.span(op.range[0]) + " – " + F.span(op.range[1]) : "";
  $("operation-eta").hidden = !op;
  text("operation-eta", op ? op.label + " · 预计还需 " + opRange : "");
  renderSteps(steps); renderActivities(next.activities || []);
  text("detail-basis", next.basis); text("detail-reason", next.reason);
  text("confirmed-note", next.has_checklist ? "已报告完成量 " + (next.confirmed_percent ?? next.percent) + "%。进行中的项目只有明确完成的子单元才计入。" : "没有协作清单时，活动无法证明工程完成比例。此时百分比仅是低把握的参考。");
  text("revision-note", next.change_reason || "范围和权重变化会更新估计；不会为了动画抑制修正。");
  text("eta-basis", next.eta_basis);
  text("finish-range", next.finish_range ? "预计结束区间 " + F.clock(next.finish_range[0], next.now) + " – " + F.clock(next.finish_range[1], next.now) + "（剩余 " + F.range(next.eta) + "）" : "当前没有可用的时间区间。");
  text("operation-details", op ? op.label + "预计还需 " + opRange + "。参考 " + op.count + " 次已运行到这一时长的同类操作。" : "");
  const learning = next.learning;
  text("learning-details", learning?.applied ? "本机学习已校准当前时间。近期对照中位误差 " +
    F.span(learning.baseline_error) + " → " + F.span(learning.learned_error) + "。" :
    learning?.count ? "已积累 " + learning.count + " 条可比较的本机记录。" : "");
  const m = next.metrics;
  text("metric-error", m ? (m.medianAbsoluteErrorSeconds / 60).toFixed(1) + " 分钟" : "数据不足");
  text("metric-two", m ? Math.round(m.withinTwoMinutes * 100) + "%" : "—");
  text("metric-coverage", m ? Math.round(m.intervalObservedCoverage * 100) + "%" : "—");
  if (m) $("metric-coverage").title = "目标覆盖80%；留出 " + m.testRuns + " 个轮次。多次截点评估互相关联。";
  text("source-name", next.source);
  text("plugin-status", next.plugin_connected ? "已连接" : next.plugin_installed ? "已安装 · 等待新任务" : "未连接 · 只读模式");
  const ai = next.ai || {};
  let advisorStatus = ai.enabled ? "等待评估" : "关闭";
  if (ai.enabled && ai.assessment) advisorStatus = ai.assessment.format_fallback ? "已收到 · JSON 兼容模式" : "已收到建议";
  if (ai.enabled && ai.busy) advisorStatus = "正在评估";
  if (ai.enabled && ai.error) advisorStatus = ai.error;
  text("ai-status", advisorStatus);
  if (!settingsLoaded) $("cooperative").checked = next.cooperative;
  $("pin").setAttribute("aria-pressed", String(next.topmost));
  $("pin").setAttribute("aria-label", next.topmost ? "取消置顶" : "始终置顶");
  $("pin").title = next.topmost ? "取消置顶" : "始终置顶";
  if (!$("review-sheet").hidden && (changedTurn || Date.now() - reviewUpdatedAt > 4000)) {
    reviewUpdatedAt = Date.now();
    review.load(next, null, true);
  }
  tick(); fitWindow();
}
async function poll() {
  if (polling) return;
  polling = true;
  const abort = new AbortController(), timeout = setTimeout(() => abort.abort(), 7000);
  try { render(await api("api/state" + (selected ? "?thread=" + encodeURIComponent(selected) : ""), abort.signal)); }
  catch (error) {
    text("status-label", "暂时无法读取 · 自动重试");
    widget.classList.remove("working");
    if (!state) text("current", error.message);
  } finally { clearTimeout(timeout); polling = false; }
}
function openSheet(id) {
  closeSize();
  if (document.querySelector(".sheet:not([hidden])") === null) previousFocus = document.activeElement;
  document.querySelectorAll(".sheet").forEach(el => { el.hidden = el.id !== id; });
  document.body.classList.add("sheet-open");
  $("panel-content").inert = true;
  document.querySelector(".titlebar").inert = true;
  $(id).querySelector("button,input,select")?.focus();
  fitWindow();
}
function closeSheets() {
  document.querySelectorAll(".sheet").forEach(el => { el.hidden = true; });
  document.body.classList.remove("sheet-open");
  $("panel-content").inert = false; document.querySelector(".titlebar").inert = false;
  previousFocus?.focus(); fitWindow();
}
document.querySelectorAll(".dismiss").forEach(button => button.addEventListener("click", closeSheets));
document.addEventListener("keydown", event => {
  if (event.key === "Escape") { closeSize(); closeSheets(); }
  const sheet = [...document.querySelectorAll(".sheet")].find(el => !el.hidden);
  if (event.key === "Tab" && sheet) {
    const nodes = [...sheet.querySelectorAll("button,input,select,summary")].filter(el => !el.disabled && el.offsetParent);
    if (event.shiftKey && document.activeElement === nodes[0]) { event.preventDefault(); nodes.at(-1)?.focus(); }
    else if (!event.shiftKey && document.activeElement === nodes.at(-1)) { event.preventDefault(); nodes[0]?.focus(); }
  }
  if (!$("size-menu").hidden && ["ArrowUp", "ArrowDown"].includes(event.key)) {
    event.preventDefault(); const rows = [...$("size-menu").querySelectorAll("button")];
    const index = rows.indexOf(document.activeElement), offset = event.key === "ArrowDown" ? 1 : -1;
    rows[(index + offset + rows.length) % rows.length].focus();
  }
});
$("details").addEventListener("click", () => openSheet("detail-sheet"));
function openReview() {
  if (!state?.turn_id) return;
  openSheet("review-sheet");
  reviewUpdatedAt = Date.now();
  review.load(state, state.turn_id);
}
["eta-review", "review-button", "detail-review"].forEach(id => $(id).addEventListener("click", openReview));
$("review-turn").addEventListener("change", event => review.load(state, event.target.value));
async function listThreads(query = "") {
  const serial = ++searchSerial;
  try {
    const rows = await api("api/threads?q=" + encodeURIComponent(query));
    if (serial !== searchSerial) return;
    $("thread-list").replaceChildren(...rows.map(row => {
      const button = document.createElement("button"); button.className = "thread-row" + (row.id === state?.thread_id ? " selected" : "");
      button.setAttribute("aria-pressed", String(row.id === state?.thread_id));
      const title = document.createElement("strong"); title.textContent = row.title;
      const date = document.createElement("span"); date.textContent = row.id === state?.thread_id ? "正在关注" : new Date(row.updated_at * 1000).toLocaleString("zh-CN", {month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"});
      button.append(title, date);
      button.addEventListener("click", async () => {
        selected = row.id;
        if (native()) await native().select_thread(row.id);
        closeSheets(); await poll();
      });
      return button;
    }));
    if (!rows.length) $("thread-list").textContent = "没有找到匹配的本地对话";
  } catch (error) { $("thread-list").textContent = error.message; }
}
$("choose").addEventListener("click", () => { openSheet("thread-sheet"); listThreads($("thread-search").value); });
$("thread-search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => listThreads($("thread-search").value), 180); });
async function loadSettings() {
  try {
    const cfg = await api("api/settings"), ai = cfg.assessor || {};
    $("ai-endpoint").value = ai.endpoint || ""; $("ai-model").value = ai.model || "";
    $("ai-key").value = ""; $("ai-key").placeholder = cfg.key_saved ? "已在本机加密保存；留空保留" : "本机服务可留空";
    $("ai-format").value = ai.format || "json_schema";
    $("ai-consent").checked = !!ai.consent; $("ai-enabled").checked = !!ai.enabled;
    settingsLoaded = true; text("settings-result", "");
  } catch (error) { text("settings-result", error.message); }
}
$("settings-button").addEventListener("click", () => { openSheet("settings-sheet"); loadSettings(); });
$("reduce-motion").addEventListener("change", async () => { setReduced($("reduce-motion").checked); if (native()) await native().set_reduced_motion(reduced); });
systemReduced.addEventListener("change", () => setReduced(reduced));
$("cooperative").addEventListener("change", async () => { if (native()) await native().set_cooperative($("cooperative").checked); else notice("清单协作设置在桌面面板中保存"); });
$("assessor-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (!native()) { text("settings-result", "浏览器为只读预览；请在桌面面板中保存设置。"); return; }
  const settings = {endpoint: $("ai-endpoint").value.trim(), model: $("ai-model").value.trim(), key: $("ai-key").value, format: $("ai-format").value, enabled: $("ai-enabled").checked, consent: $("ai-consent").checked};
  if (settings.enabled && (!settings.endpoint || !settings.model || !settings.consent)) {
    text("settings-result", "请填写服务与模型，并确认发送范围。"); return;
  }
  const button = event.currentTarget.querySelector('[type="submit"]'); button.disabled = true;
  try {
    await native().configure_assessor(settings);
    $("ai-key").value = ""; text("settings-result", settings.enabled ? "已保存，接下来将按上述范围评估。" : "已保存，AI 参考已关闭。");
    await poll();
  } catch (error) { text("settings-result", error.message || "保存失败，请检查服务设置。"); }
  finally { button.disabled = false; }
});
$("pin").addEventListener("click", async () => {
  if (!native()) return notice("浏览器为预览；桌面面板支持置顶。");
  const value = await native().toggle_pin(); if (state) render({...state, now: state.now + (Date.now() - receivedAt) / 1000, topmost: value});
});
$("close").addEventListener("click", () => native() ? native().close() : notice("这是浏览器预览，可直接关闭标签页。"));
setReduced(reduced);
setSize(localStorage.getItem("luma.size") || "focus", false);
poll(); setInterval(poll, 2000); setInterval(tick, 250);
new ResizeObserver(fitWindow).observe(widget);
window.addEventListener("pywebviewready", () => { nativeInitialized = false; poll(); fitWindow(); });
window.addEventListener("resize", fitWindow);

