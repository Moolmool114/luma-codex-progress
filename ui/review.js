"use strict";

const review = (() => {
  const el = id => document.getElementById(id);
  const F = window.LumaFormat;
  const states = {completed: "已结束", interrupted: "已中断", failed: "执行失败"};
  const methods = {duration: "同类时长", rolling: "近期同类时长", recent: "近期相似轮次",
    neighbours: "相似轮次", trace: "相似执行轨迹", blend: "历史时长＋相似轨迹",
    activity: "整轮活动", operations: "含操作时长"};
  const progressNotes = {same: "进度相符", outside_scope: "进度保留本地值", applied: "修改了进度"};
  const timingNotes = {
    observing: "ETA 建议，评估中",
    no_baseline: "ETA 建议，缺少本地对照",
    overdue: "ETA 已过建议时刻",
    applied: "ETA 参与校正 · 25%",
  };
  let serial = 0;
  let activeThread, activeTurn, signature;

  function node(tag, content, className) {
    const result = document.createElement(tag);
    if (content !== null) result.textContent = content;
    if (className) result.className = className;
    return result;
  }

  function timestamp(at, now, seconds = true) {
    if (!Number.isFinite(at)) return "—";
    const atDate = new Date(at*1000);
    const nowDate = new Date(now*1000);
    const day = atDate.toDateString() === nowDate.toDateString() ? "" :
      (atDate.getMonth()+1) + "/" + atDate.getDate() + " ";
    const clock = new Intl.DateTimeFormat("zh-CN", {hour: "2-digit", minute: "2-digit",
      ...(seconds ? {second: "2-digit"} : {}), hourCycle: "h23"}).format(atDate);
    return day + clock;
  }

  function verdict(finish, actual) {
    if (!Number.isFinite(finish) || !Number.isFinite(actual)) return "";
    const difference = Math.round(finish-actual);
    if (!difference) return "与实际相符";
    return (difference < 0 ? "早 " : "晚 ") + F.span(Math.abs(difference));
  }

  function addMetric(list, label, value, note) {
    const item = node("div", null);
    item.append(node("dt", label), node("dd", value));
    if (note) item.append(node("small", note));
    list.append(item);
  }

  function drawComparison(record, now) {
    const ended = record.ended_at;
    const completed = record.status === "completed";
    const entries = [];
    if (record.first) entries.push([record.first.at === record.last?.at ? "本轮预测" : "首次预测", record.first]);
    if (record.last && record.last.at !== record.first?.at) entries.push(["最后预测", record.last]);
    el("review-comparison").hidden = !ended && !entries.length;
    const title = {interrupted: "中断时刻", failed: "失败时刻"}[record.status] || "实际结束";
    el("review-result-title").textContent = ended ? title : "最近一次预测";
    el("review-ended").textContent = timestamp(ended || record.last?.displayed.finish, now);
    el("review-verdict").textContent = completed ? verdict(record.last?.displayed.finish, ended) : "";
    el("review-verdict").hidden = !el("review-verdict").textContent;
    const metrics = el("review-forecasts");
    metrics.replaceChildren();
    const baseline = el("review-baseline");
    const pair = record.reference_pair;
    baseline.hidden = !completed || !pair;
    if (!baseline.hidden) {
      baseline.textContent = timestamp(pair.at, now, false) + " 的同刻预测中，旧式基线" +
        verdict(pair.reference_finish, ended) + "，当前本地模型" + verdict(pair.raw_local.finish, ended) + "。";
    }
    const chart = el("review-chart");
    chart.replaceChildren();
    chart.hidden = !ended || !entries.length;
    for (const [label, checkpoint] of entries) {
      const f = checkpoint.displayed;
      const labelNote = "记录于 " + timestamp(checkpoint.at, now);
      const error = completed ? verdict(f.finish, ended) + (f.low <= ended && ended <= f.high ? " · 区间命中" : " · 超出区间") : "";
      addMetric(metrics, label, timestamp(f.finish, now), error ? error + "\n" + labelNote : labelNote);
    }
    if (chart.hidden) return;
    const left = Math.min(ended, ...entries.map(([, checkpoint]) => checkpoint.displayed.low));
    const right = Math.max(ended, ...entries.map(([, checkpoint]) => checkpoint.displayed.high));
    const at = value => 3 + 94*(value-left)/Math.max(1, right-left) + "%";
    chart.setAttribute("aria-label", "各次预测的结束区间。竖线为实际结束时刻。");
    const plot = node("div", null, "review-plot");
    const actual = node("span", "", "review-actual-line");
    actual.style.left = at(ended);
    plot.append(actual);
    for (const [label, checkpoint] of entries) {
      const f = checkpoint.displayed;
      const lane = node("div", null, "review-lane");
      lane.title = label + "区间 " + timestamp(f.low, now) + " – " + timestamp(f.high, now);
      const band = node("span", "", "review-band");
      band.style.left = at(f.low);
      band.style.right = "calc(100% - " + at(f.high) + ")";
      const marker = node("span", "", "review-estimate-dot");
      marker.style.left = at(f.finish);
      lane.append(band, marker);
      plot.append(lane);
    }
    const axis = node("div", null, "review-axis");
    axis.append(node("span", timestamp(left, now, false)), node("span", timestamp(right, now, false)));
    const caption = node("p", "竖线为实际结束，圆点为预计，横线为区间。", "review-chart-note");
    chart.append(plot, axis, caption);
  }

  function describeImpact(impact, now) {
    const progress = impact.progress === "applied"
      ? impact.base_percent + "% → " + impact.displayed_percent + "%"
      : progressNotes[impact.progress];
    const time = impact.timing === "applied" ?
      "ETA " + timestamp(impact.local_finish, now) + " → " + timestamp(impact.displayed_finish, now) :
      timingNotes[impact.timing];
    return [progress, time].filter(Boolean).join(" · ");
  }

  function aiHelp(event, record) {
    const comparison = event.comparison;
    const actual = record.ended_at;
    if (record.status !== "completed" || !actual || !comparison?.local_finish) return "";
    const base = Math.abs(comparison.local_finish-actual);
    const advisor = Math.abs(comparison.ai_finish-actual);
    const difference = Math.round(base-advisor);
    const value = difference === 0 ? "两者误差相同" :
      difference > 0 ? "AI 更接近 " + F.span(difference) : "AI 多偏离 " + F.span(-difference);
    return "同一时刻 · 本地误差 " + F.span(base) + "，AI 误差 " + F.span(advisor) + "。 " + value + "。";
  }

  function renderAdvice(record, data) {
    const events = record.advice || [];
    const scores = data.ai?.scores;
    el("review-ai").hidden = !events.length && !data.ai?.enabled;
    el("review-ai-count").textContent = events.length ? "最近 " + events.length + " 条" : "本轮";
    el("review-ai-score").textContent = scores?.local
      ? scores.local.count + " 轮同点对比 · 本地中位误差 " + F.span(scores.local.median) +
        "，AI " + F.span(scores.ai.median) + "，25% 校正 " + F.span(scores.blend.median)
      : events.length ? "" : "等待第一条 AI 评估。";
    const list = el("review-ai-events");
    list.replaceChildren(...events.map((event, index) => {
      const item = node("details", null, "review-event");
      item.dataset.id = event.id;
      item.open = index === 0;
      const header = node("summary", null);
      const status = {ready: "收到建议", error: "评估失败", pending: "未收到结果", superseded: "配置已变更"}[event.state];
      header.append(node("time", timestamp(event.received_at || event.at, data.now)), node("span", status || "收到建议"));
      item.append(header);
      if (event.decision) item.append(node("p", describeImpact(event.decision, data.now), "review-impact"));
      else if (event.state === "ready") item.append(node("p",
        event.after_end ? "结果在本轮结束后返回。" : "本轮没有采用这条建议。", "review-copy"));
      const applied = event.applications || {};
      if (applied.progress && event.decision?.progress !== "applied") {
        item.append(node("p", "曾调整进度 " + applied.progress.before + "% → " +
          applied.progress.after + "%。", "review-copy"));
      }
      if (applied.timing && event.decision?.timing !== "applied") {
        item.append(node("p", "曾校正预计结束 " + timestamp(applied.timing.before, data.now) + " → " +
          timestamp(applied.timing.after, data.now) + "。", "review-copy"));
      }
      if (event.state === "ready") {
        const est = event.at + event.remaining_seconds[1];
        item.append(node("p", "建议 " + event.percent + "%，预计 " + timestamp(est, data.now) + " 结束。", "review-copy"));
        const compared = event.comparison?.local_finish;
        if (!record.ended_at && Number.isFinite(compared)) {
          const delta = Math.round(est - compared);
          const description = delta === 0 ? "与当时的本地预计相同。" :
            "比当时的本地预计" + (delta < 0 ? "早 " : "晚 ") + F.span(Math.abs(delta)) + "。";
          item.append(node("p", description, "review-copy"));
        }
        if (event.explanation) item.append(node("p", event.explanation, "review-reason"));
        if (event.evidence_ids?.length) item.append(node("p",
          "引用 " + event.evidence_ids.length + " 条可见事件" +
          (event.evidence_labels?.length ? " · " + event.evidence_labels.join("、") : ""), "review-copy"));
        const evaluated = aiHelp(event, record);
        if (evaluated) item.append(node("p", evaluated, "review-evaluated"));
      }
      if (event.error) item.append(node("p", event.error, "review-copy"));
      return item;
    }));
  }

  function renderLearning(data) {
    const learning = data.learning;
    el("review-learning").hidden = !learning;
    if (!learning) return;
    const metrics = el("review-learn-metrics");
    metrics.replaceChildren();
    if (learning.observed) {
      const m = learning.observed;
      addMetric(metrics, "实测轮次", String(m.count));
      addMetric(metrics, "首次预测误差", F.span(m.median), "绝对误差中位数");
      addMetric(metrics, "首次区间命中", Math.round(m.coverage*100) + "%");
    }
    const paired = learning.reference;
    const pairedNote = el("review-reference-scores");
    pairedNote.hidden = !paired;
    pairedNote.textContent = paired ? paired.local.count + " 轮实时对照中，旧式基线平均误差 " +
      F.span(paired.baseline.mean) + "，当前本地模型 " + F.span(paired.local.mean) + "。" : "";
    el("review-learn-status").textContent = learning.applied ? "已校准当前预测" :
      learning.training ? "正在更新模型" : "持续记录";
    const trained = learning.trained_at ? "模型更新于 " + timestamp(learning.trained_at, data.now, false) + "。" : "";
    const evaluated = learning.test_count
      ? "最近 " + learning.test_count + " 轮校验，中位误差 " + F.span(learning.baseline_error) +
        " → " + F.span(learning.learned_error) + "。"
      : "累计 " + learning.count + " / " + learning.minimum + " 轮可比较的实测后开始校验偏差。";
    el("review-learn-detail").textContent = trained + evaluated;
  }

  function restoreDetails(list, openStates) {
    for (const item of list.children) {
      if (openStates.has(item.dataset.id)) item.open = openStates.get(item.dataset.id);
    }
  }

  function renderHistory(record, now) {
    const checkpoints = record.checkpoints || [];
    el("review-history-block").hidden = checkpoints.length === 0;
    el("review-history-count").textContent = "最近 " + checkpoints.length + " 条";
    el("review-checkpoints").replaceChildren(...checkpoints.map(checkpoint => {
      const item = node("li", null);
      const line = node("div", null);
      const base = methods[checkpoint.local_method] || "本地预测";
      const label = base + (checkpoint.method.endsWith("+learned") ? " · 本机校准" : "") +
        (checkpoint.advisor_used ? " · AI 校正" : "");
      line.append(node("span", timestamp(checkpoint.at, now)), node("strong", timestamp(checkpoint.displayed.finish, now)));
      item.append(line, node("small", label + " · " + timestamp(checkpoint.displayed.low, now, false) +
        " – " + timestamp(checkpoint.displayed.high, now, false)));
      return item;
    }));
  }

  function render(response, data) {
    const picker = el("review-turn");
    const turns = response.turns;
    el("review-picker").hidden = !turns.length;
    const active = turns.some(turn => turn.turn_id === activeTurn);
    const options = turns.map(turn => {
      const label = timestamp(turn.started_at, data.now, false) + " 开始 · " +
        (states[turn.status] || (turn.turn_id === data.turn_id ? "当前轮次" : "等待结果"));
      const option = node("option", label);
      option.value = turn.turn_id;
      return option;
    });
    if (!active && activeTurn === data.turn_id) {
      const option = node("option", "当前轮次");
      option.value = activeTurn;
      options.unshift(option);
    }
    if (JSON.stringify(options.map(option => [option.value, option.textContent])) !== picker.dataset.options) {
      picker.dataset.options = JSON.stringify(options.map(option => [option.value, option.textContent]));
      picker.replaceChildren(...options);
    }
    picker.value = activeTurn;
    const record = response.record || {advice: [], checkpoints: []};
    const message = el("review-message");
    const knownEnd = record.ended_at || (activeTurn === data.turn_id && data.completed_at);
    if (knownEnd && !record.ended_at) {
      record.ended_at = knownEnd;
      record.status = {failed: "failed", interrupted: "interrupted"}[data.status] || "completed";
    }
    message.textContent = !data.journal_available ? "暂时无法读取本机预测记录。" :
      !record.first && record.ended_at ? "本轮结束前没有保存时间预测。" :
      !response.record ? "本轮还没有预测记录。" : "";
    message.hidden = !message.textContent;
    drawComparison(record, data.now);
    renderAdvice(record, data);
    renderLearning(data);
    renderHistory(record, data.now);
  }

  async function load(data, requested, quiet = false) {
    if (!data?.thread_id || !data.turn_id) return;
    const id = ++serial;
    const threadChanged = activeThread !== data.thread_id;
    activeThread = data.thread_id;
    const selection = requested || (threadChanged ? data.turn_id : activeTurn) || data.turn_id;
    if (selection !== activeTurn || threadChanged) el("review-body").scrollTop = 0;
    activeTurn = selection;
    const body = el("review-body");
    if (!quiet) body.setAttribute("aria-busy", "true");
    try {
      const query = new URLSearchParams({thread: activeThread, turn: activeTurn});
      const response = await api("api/predictions?" + query);
      if (id !== serial) return;
      const stable = JSON.stringify([response.turns, response.record?.ended_at, response.record?.first,
        Math.round(response.record?.last?.displayed.finish / 15), response.record?.advice?.map(event => ({
          ...event, decision: event.decision && {...event.decision, at: 0},
        })), data.learning, data.ai?.scores]);
      if (quiet && stable === signature) return;
      signature = stable;
      const adviceList = el("review-ai-events");
      const expanded = new Map([...adviceList.children].map(item => [item.dataset.id, item.open]));
      const focusedAdvice = document.activeElement.closest(".review-event")?.dataset.id;
      render(response, data);
      if (quiet) restoreDetails(adviceList, expanded);
      if (quiet && focusedAdvice) {
        const item = [...adviceList.children].find(row => row.dataset.id === focusedAdvice);
        item?.querySelector("summary").focus({preventScroll: true});
      }
    } catch (error) {
      if (id !== serial || quiet) return;
      el("review-message").hidden = false;
      el("review-message").textContent = error.message;
    } finally {
      if (id === serial) body.setAttribute("aria-busy", "false");
    }
  }

  return {load};
})();
