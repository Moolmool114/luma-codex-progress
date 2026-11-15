"""Weighted work evidence plus censored historical duration estimates.

Percentage is an estimate of the current request, not token usage. Structured
checklists take precedence. The clock alone never advances the progress bar.
"""
from __future__ import annotations

import math
import re
import statistics
import time

from .source import message_text


TASK_PATTERNS = {
    "build": r"实现|开发|修复|代码|插件|开工|做吧|制作|搭建|implement|build|fix|code|develop|create.*app",
    "design": r"设计|界面|排版|美化|动画|海报|design|redesign|mockup|animation",
    "research": r"搜索|搜一下|检索|调研|可行|查一下|查询|推荐|分析|比较|research|search|investigate|compare",
}
VERIFY = re.compile(r"\b(?:pytest|unittest|vitest|jest|ruff\s+check|tsc\s+--noEmit|dotnet\s+(?:test|build)|npm\s+(?:run\s+)?(?:test|build|check)|node\s+--test)\b", re.I)
FAIL_WORDS = re.compile(r"未能完成|无法完成|尚未完成|仍未完成|没有完成|工作尚未结束|任务未完成|could not complete|unable to complete|remaining work|blocked by", re.I)
PLAN_REGEX = re.compile(r'"plan"\s*:\s*(\[.*?\])', re.S)


def classify(prompt):
    scores = {kind: len(re.findall(pattern, prompt, re.I)) for kind, pattern in TASK_PATTERNS.items()}
    return max(scores, key=scores.get) if max(scores.values(), default=0) else "general"


def duration_forecast(history, kind, elapsed):
    similar = [r["seconds"] for r in history if (r.get("kind") or classify(r["prompt"])) == kind]
    # A forecast is conditional on the run still being alive; never subtract the
    # elapsed time from an unconditional mean and get a permanently overdue ETA.
    if len(similar) < 6:
        return None, len(similar)
    logs = sorted(math.log(max(1, d)) for d in similar)
    lo, hi = int(len(logs) * .05), max(1, math.ceil(len(logs) * .95))
    trimmed = logs[lo:hi]
    mu = statistics.mean(trimmed)
    sigma = max(.45, statistics.pstdev(trimmed))
    normal = statistics.NormalDist(mu, sigma)
    floor = min(.999999, normal.cdf(math.log(max(1, elapsed))))
    result = []
    for q in (.2, .5, .8):
        p = min(.9999999, floor + (1 - floor) * q)
        result.append(max(1, math.exp(normal.inv_cdf(p)) - elapsed))
    return result, len(similar)


def public_plan(items):
    """Use only explicit, parseable tool arguments; no prose or reasoning scrape."""
    latest = []
    for item in items:
        if item.get("type") != "mcpToolCall":
            continue
        name = item.get("tool") or ""
        args = item.get("arguments", {})
        if "update_plan" in name and isinstance(args, dict) and isinstance(args.get("plan"), list):
            latest = args["plan"]
    return [{"id": str(i), "label": p.get("step", ""), "status": p.get("status", "pending"), "weight": 1, "evidence": "Codex 结构化计划"} for i, p in enumerate(latest) if isinstance(p, dict)]


def step_fraction(step):
    """Earned work only. Starting a step does not automatically earn 25%."""
    if step.get('status') == 'completed':
        return 1.0
    if step.get('status') not in ('in_progress', 'inProgress'):
        return 0.0
    done, total = step.get('completedUnits'), step.get('totalUnits')
    if isinstance(done, int) and not isinstance(done, bool) and isinstance(total, int) and total > 0 and 0 <= done <= total and step.get('evidence'):
        return done / total
    return 0.0


def estimate(snapshot, history=(), now=None, time_prediction=None):
    now = time.time() if now is None else now
    start = snapshot["started_at"]
    end = snapshot.get("completed_at") or now
    elapsed = max(0, end - start)
    items = snapshot.get("items", [])
    kind = classify(snapshot.get("prompt", ""))
    checklist = snapshot.get("checklist", {})
    hook = snapshot.get("hook", {})
    hook_valid = hook.get("turnId") == snapshot.get("turn_id")
    if not hook_valid:
        hook = {}
    if checklist.get("turnId") != snapshot.get("turn_id"):
        checklist = {}
    steps = checklist.get("steps") or public_plan(items)
    structured = bool(steps)
    active = snapshot.get("status") in ("inProgress", "in_progress")
    status = "working" if active else "ended"
    if snapshot.get("status") in ("interrupted", "failed", "unknown"):
        status = {"interrupted": "interrupted", "failed": "failed", "unknown": "unknown"}[snapshot["status"]]
    hook_age = max(0, now - hook.get("at", 0) / 1000)
    if active and hook.get("event") == "PermissionRequest" and hook_age < 3600:
        status = "waiting"
    if active and hook.get("event") == "Interrupt":
        status = "interrupted"

    failures = 0
    activities = []
    edited = verified = researching = False
    last_evidence = start
    stage = 0
    for item in items:
        typ = item.get("type")
        at = item.get("at", start)
        label = ""
        ok = item.get("status") in ("completed", "succeeded", "success")
        if typ == "commandExecution":
            command = item.get("command") or ""
            failed = item.get("exitCode") not in (None, 0) or item.get("status") in ("failed", "declined")
            if failed:
                failures += 1
                verified = False
                label = "命令未通过，正在处理"
            elif VERIFY.search(command):
                passed = ok and item.get('exitCode') == 0 and not item.get('test_output_failed')
                label = "构建或测试命令结束" if ok else "运行构建或测试"
                verified = passed
            elif re.search(r"(?:^|\s)(?:rg|Get-Content|Get-ChildItem|git\s+(?:diff|status)|cat|ls)\b", command, re.I):
                researching = True
                label = "检查项目与本地资料"
            elif ok:
                label = "本地命令执行完成"
            else:
                label = "正在运行本地命令"
        elif typ == "fileChange":
            if ok:
                edited = True
                verified = False  # A later edit invalidates previous test evidence.
            label = "更新项目文件" if ok else "正在修改文件"
        elif typ == "mcpToolCall":
            tool = item.get("tool") or ""
            if re.search(r"search|fetch|browse|read", tool, re.I):
                researching = True
                label = "检索与核查资料"
            elif "apply_patch" in tool:
                if ok:
                    edited, verified = True, False
                label = "更新项目文件"
            elif re.search(r"cua|screenshot|inspect|^js$", tool, re.I):
                label = "检查真实界面与交互"
            else:
                label = "调用工具 · " + tool.split(".")[-1][:34]
            if item.get("status") == "failed":
                failures += 1
        elif typ == "webSearch":
            researching = True
            label = "搜索公开资料"
        elif typ == "agentMessage" and item.get("phase") == "commentary":
            label = message_text(item).strip().replace("\n", " ")[:150]
        if label:
            if activities and activities[-1]['text'] == label:
                activities[-1].update(at=at,count=activities[-1].get('count',1)+1,id=item.get('id'))
            else:
                activities.append({"id":item.get('id'),"text": label, "at": at, "count":1,"kind": "error" if "未通过" in label else "activity"})
            last_evidence = max(last_evidence, at)

    forecast, sample_count = duration_forecast(history, kind, elapsed)
    evidence_elapsed = max(0, last_evidence - start)
    frozen_forecast, _ = duration_forecast(history, kind, evidence_elapsed)
    inferred = [
        {"label": "理解与准备", "status": "in_progress", "weight": 12},
        {"label": "实施与产出" if kind in ("build", "design") else "检索与分析", "status": "pending", "weight": 53},
        {"label": "检查与验证", "status": "pending", "weight": 25},
        {"label": "整理与交付", "status": "pending", "weight": 10},
    ]
    if researching:
        inferred[0]["status"] = "completed"
        inferred[1]["status"] = "in_progress"
        stage = 1
    if edited:
        inferred[0]["status"] = "completed"
        inferred[1]["status"] = "in_progress"
        stage = 1
    if verified:
        inferred[1]["status"] = "completed" if edited else "in_progress"
        inferred[2]["status"] = "in_progress"
        stage = 2

    basis = "清单加权" if checklist else "结构化计划" if steps else "活动推断"
    if steps:
        valid = [s for s in steps if isinstance(s, dict) and isinstance(s.get("weight", 1), (int, float)) and math.isfinite(s.get("weight", 1)) and s.get("weight", 1) > 0]
        total = sum(s.get("weight", 1) for s in valid)
        done = sum(s.get('weight',1)*step_fraction(s) for s in valid)
        doing = sum(s.get('weight',1)*(1-step_fraction(s)) for s in valid if s.get('status') in ('in_progress','inProgress'))
        lower = done / total * 100 if total else 0
        upper = (done + doing) / total * 100 if total else 100
        point = lower
        # Completed evidence is a lower-bound to this checklist, not to an unknown future scope.
        lower, upper = max(0, lower - 5), min(98, upper + 5)
        confidence = "中" if len(valid) >= 3 else "偏低"
        reason = f"按 {len(valid)} 个交付项的工作量加权；仅计入已报告的完成量，开始步骤不预先增加进度。"
        steps = valid
    else:
        steps = inferred
        semantic = 8 + (14 if researching else 0) + (25 if edited else 0) + (22 if verified else 0)
        prior = evidence_elapsed / (evidence_elapsed + frozen_forecast[1]) * 100 if frozen_forecast else semantic
        point = min(85, semantic * .75 + prior * .25)
        lower, upper = max(0, point - 22), min(95, point + 25)
        confidence = "低"
        reason = "尚无明确工作量清单；根据可见活动推断，误差可能较大。"
    point = min(95, max(0, point))
    partial = bool(FAIL_WORDS.search(snapshot.get("final", ""))) or checklist.get("outcome") in ("partial", "blocked") or bool(structured and any(step_fraction(s)<1 for s in steps))
    if status == "ended":
        if partial:
            status = "partial"
            reason = "本轮已结束，答复中仍有未完成事项。"
            point = min(point, 95)
        else:
            point, lower, upper = 100, 100, 100
            reason = "本轮执行已结束；交付结果请以 Codex 的最终答复为准。"
    elif status in ("failed", "interrupted"):
        point = min(point, 95)
        reason = "执行失败或被中断，保留最后的估计。"
    if snapshot.get("partial"):
        confidence = "低"
        reason += " 记录覆盖不完整。"

    active_step = next((s.get("label", s.get("step", "")) for s in steps if s.get("status") in ("in_progress", "inProgress")), "")
    current = checklist.get("current") or active_step or (activities[-1]["text"] if activities else "正在等待可见活动")
    if activities and not checklist:
        current = activities[-1]["text"]
    if status in ("ended", "partial", "failed", "interrupted"):
        current = {"ended": "本轮执行已结束", "partial": "本轮结束，仍有待办", "failed": "执行遇到错误", "interrupted": "执行已中断"}[status]

    eta = None
    eta_basis = "同类历史不足，暂不估计时间"
    if status == "working" and time_prediction is not None and (
        snapshot.get("source_age", 0) < 180 or snapshot.get("operation_running")
    ):
        eta = time_prediction.get('range') if time_prediction.get('available') else None
        eta_basis = time_prediction['reason']
    elif status == "working" and time_prediction is None and forecast and snapshot.get("source_age", 0) < 180:
        eta = [round(forecast[0]), round(forecast[2])]
        eta_basis = f"参考 {sample_count} 次同类本地执行；预测本轮结束时间"
    elif status == "working" and time_prediction is None and steps and checklist and point > 10:
        remaining = elapsed * (100 - point) / point
        eta = [round(remaining * .5), round(remaining * 1.8)]
        eta_basis = "按清单速度外推，尚未经过历史校准"
    if status == "waiting":
        current, eta_basis = "等待你批准操作", "等待期间不估计剩余时间"
    elif status == "working" and time_prediction is not None:
        if snapshot.get("source_age", 0) >= 180 and not snapshot.get("operation_running"):
            eta_basis = "超过三分钟未收到可见活动，暂停时间预测"
    elif status in ("ended", "partial", "interrupted", "failed"):
        eta_basis = "预测记录保留本轮时间估计"
    eta_mid = None
    if eta:
        eta_mid = (time_prediction or {}).get('median',round((eta[0]+eta[1])/2))
        eta_mid = max(eta[0],min(eta[1],eta_mid))
    completed_count = sum(s.get('status')=='completed' for s in steps)
    return {
        "percent": round(point), "range": [round(min(lower, point)), round(max(upper, point))],
        "confidence": confidence, "basis": basis, "reason": reason, "steps": steps,
        "current": current, "status": status, "elapsed": round(elapsed), "eta": eta,
        "eta_basis": eta_basis, "history_count": sample_count, "activities": activities[-4:][::-1],
        "eta_median":eta_mid,"finish_at": now+eta_mid if eta_mid is not None else None,"finish_range":[now+x for x in eta] if eta else None,
        "started_at":start,"completed_at":snapshot.get('completed_at'),"confirmed_percent":round(sum(s.get('weight',1)*step_fraction(s) for s in steps)/max(1,sum(s.get('weight',1) for s in steps))*100) if checklist else None,
        "completed_count":completed_count,"total_count":len(steps),"has_checklist":bool(checklist),"change_reason":checklist.get('rescopeReason') or '',
        "failures": failures, "plugin_connected": hook_valid and hook_age < 300,
        "checklist_revision": checklist.get("revision"), "estimated": active or status != "ended",
    }
