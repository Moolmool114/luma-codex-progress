"""Timings for observable operations within a Codex turn."""
from bisect import bisect_right
from collections import defaultdict
from math import ceil, expm1, log1p
from statistics import median

from .estimator import VERIFY

LABELS = {
    "test": "构建／检查命令",
    "command": "本地命令",
    "lookup": "检索与查询",
    "interface": "界面操作",
    "image": "图像生成",
    "audio": "音频生成",
    "video": "视频生成",
    "message": "回复输出",
    "tool": "外部工具",
}


def operation_kind(item):
    kind = item["type"]
    if kind == "commandExecution":
        return "test" if VERIFY.search(item.get("command") or "") else "command"
    if kind == "webSearch":
        return "lookup"
    if kind == "imageGeneration":
        return "image"
    if kind == "agentMessage":
        return "message"
    if kind != "mcpToolCall":
        return None
    tool = (item.get("tool") or "").lower()
    if any(word in tool for word in ("imagegen", "generate_image", "image_gen")):
        return "image"
    if "generate_video" in tool:
        return "video"
    if "generate_audio" in tool:
        return "audio"
    if "cua" in tool or "screenshot" in tool or tool == "js":
        return "interface"
    if any(word in tool for word in ("search", "fetch", "browse", "read")):
        return "lookup"
    return "tool"


def running_operations(items, cut):
    running = []
    for item in items:
        start = item.get("started", item.get("at", cut))
        finish = item.get("finished", item.get("at", start))
        if start > cut or (finish is not None and finish <= cut):
            continue
        kind = operation_kind(item)
        if kind:
            running.append((start, kind))
    return sorted(running)


def operation_profiles(runs):
    durations = defaultdict(list)
    for run in runs:
        for item in run["items"]:
            kind = operation_kind(item)
            finish = item.get("finished")
            if kind and finish is not None:
                duration = finish - item["started"]
                # Older projections store completion only. Runner duration can
                # seed a profile, but never creates an artificial live start.
                if duration < .01:
                    duration = item.get("reported_duration", 0)
                if duration > 0 and finish <= run["started_at"] + run["seconds"] + 1:
                    durations[kind].append(duration)
    return {kind: sorted(values) for kind, values in durations.items()}


def quantile(values, probability):
    return values[max(0, min(len(values) - 1, ceil(probability * len(values)) - 1))]


def operation_remaining(profiles, kind, age, expansion=0, minimum=12):
    """Condition on the operation still running at the age we are observing."""
    durations = profiles.get(kind, ())
    alive = durations[bisect_right(durations, age):]
    if len(alive) < minimum:
        return None
    low, point, high = [quantile(alive, p) - age for p in (.1, .5, .9)]
    low = max(0, expm1(log1p(low)-expansion))
    high = max(low, expm1(log1p(high)+expansion))
    low, high = max(1, int(low)), max(1, ceil(high))
    point = min(high, max(low, round(point)))
    return {"kind": kind, "label": LABELS[kind], "age": round(age),
            "range": [low, high], "median": point, "count": len(alive)}


def operation_band(age):
    return "under15" if age < 15 else "under60" if age < 60 else "over60"


def operation_replay(profiles, runs, expansions=None):
    """Score fixed ages using observed wall times as labels."""
    expansions = expansions or {}
    groups = defaultdict(list)
    for run in runs:
        for item in run["items"]:
            kind = operation_kind(item)
            finish = item.get("finished")
            if not kind or finish is None:
                continue
            duration = finish - item["started"]
            for age in (4, 15, 60):
                if duration <= age:
                    break
                key = (kind, operation_band(age))
                forecast = operation_remaining(profiles, kind, age, expansions.get(key, 0))
                if forecast:
                    groups[key].append((forecast, duration-age))
    return groups


def calibrate_operations(profiles, runs):
    expansions = {}
    for key, rows in operation_replay(profiles, runs).items():
        if len(rows) < 12:
            continue
        errors = [max(log1p(f["range"][0])-log1p(actual), log1p(actual)-log1p(f["range"][1]))
                  for f, actual in rows]
        expansions[key] = max(0, quantile(sorted(errors), min(1, .8*(len(rows)+1)/len(rows))))
    return expansions


def validate_operations(profiles, runs, expansions):
    groups = operation_replay(profiles, runs, expansions)
    results = {}
    for (kind, band), rows in groups.items():
        hits = sum(forecast["range"][0] <= actual <= forecast["range"][1] for forecast, actual in rows)
        errors = [abs(forecast["median"]-actual) for forecast, actual in rows]
        results.setdefault(kind, {})[band] = {
            "count": len(rows), "coverage": round(hits/len(rows), 3),
            "median_error": round(median(errors), 1),
        }
    return results
