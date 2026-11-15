"""Local remaining-time estimation, with walk-forward replay and safe fallback."""
from __future__ import annotations

import json
import math
import threading
import time
from bisect import bisect_left, bisect_right
from collections import Counter
from pathlib import Path
from statistics import mean, median

from .analogues import Analogues, LANDMARKS, request_at
from .estimator import classify, duration_forecast
from .operations import (calibrate_operations, operation_band, operation_profiles, operation_remaining,
                         running_operations, validate_operations)
from .source import CodexSource, connect_readonly, message_text

VERSION = 4
BANDS = ("start", "active", "long", "extended")
METHODS = {
    "duration": "同类历史时长",
    "rolling": "近期同类时长",
    "recent": "近期相似轮次",
    "neighbours": "相似轮次",
    "trace": "相似执行轨迹",
    "blend": "历史时长＋相似轨迹",
}


def age_band(elapsed):
    return BANDS[bisect_right((30, 240, 900), elapsed)]


def load_runs(home, data, through=None):
    history = CodexSource(home, data).history(through)
    runs = []
    with connect_readonly(home/'thread_history_1.sqlite') as c:
        for record in reversed(history):
            marks="'commandExecution','fileChange','mcpToolCall','agentMessage','webSearch','imageGeneration','userMessage','plan','collabAgentToolCall','contextCompaction'"
            rows=c.execute(f"""SELECT item_type,created_at_ms,started_at_ms,completed_at_ms,COALESCE(completed_at_ms,started_at_ms,created_at_ms) AS at,
              substr(json_extract(item_json,'$.command'),1,6000) AS command,
              json_extract(item_json,'$.tool') AS tool,
              json_extract(item_json,'$.phase') AS phase,
              json_extract(item_json,'$.durationMs') AS duration_ms,
              json_extract(item_json,'$.exitCode') AS exitCode,
              CASE WHEN item_type='userMessage' THEN substr(json_extract(item_json,'$.content[0].text'),1,2000) END AS request
              FROM thread_items WHERE thread_id=? AND turn_id=? AND item_type IN ({marks})
              ORDER BY rollout_ordinal LIMIT 1000""",(record['thread_id'],record['turn_id'])).fetchall()
            items = []
            requests = []
            for row in rows:
                started = (row["started_at_ms"] or row["created_at_ms"] or row["at"]) / 1000
                if row["item_type"] == "userMessage":
                    requests.append((started, row["request"] or ""))
                    continue
                finished = (row["completed_at_ms"] or row["at"])/1000
                items.append(dict(type=row["item_type"], started=started,
                                  finished=finished,
                                  command=row["command"], tool=row["tool"], exitCode=row["exitCode"],
                                  phase=row["phase"], reported_duration=(row["duration_ms"] or 0)/1000))
            runs.append({**record, "items": items, "requests": requests})
    return runs


def duration_prior(history, prompt, elapsed):
    forecast, count = duration_forecast(history, classify(prompt), elapsed)
    if forecast:
        return forecast, count
    survived = sorted(row["seconds"]-elapsed for row in history if row["seconds"]>elapsed)
    if len(survived) < 6:
        return None, len(survived)
    return [survived[round((len(survived)-1)*q)] for q in (.2, .5, .8)], len(survived)


def recent_prior(history, prompt, elapsed, now):
    kind = classify(prompt)
    matched = [row for row in history if row["seconds"]>elapsed and row["kind"]==kind]
    pool = matched if len(matched) >= 12 else [row for row in history if row["seconds"]>elapsed]
    if len(pool) < 8:
        return duration_prior(history, prompt, elapsed)[0]
    # Exponential recency weighting discounts changes in workload and conditions.
    weighted = sorted((row["seconds"]-elapsed, .1+.9*2**(-max(0, now-row["end"])/604800))
                      for row in pool)
    total = sum(weight for _, weight in weighted)
    values = []
    for probability in (.1, .5, .9):
        target = probability*total
        cumulative = 0
        for remaining, weight in weighted:
            cumulative += weight
            if cumulative >= target:
                values.append(remaining)
                break
    return values


def historical_durations(runs):
    return sorted(({
        "prompt": run["prompt"][:2000], "seconds": run["seconds"],
        "kind": classify(run["prompt"][:2000]), "end": run["completed_at"],
    } for run in runs), key=lambda row: row["end"])


def reference_prior(old_history, fallback, prompt, elapsed, now):
    pool = [row for row in old_history if row["end"] < now]
    if len(pool) < 6:
        pool = [row for row in fallback if row["end"] < now]
    return duration_prior(pool, prompt, elapsed)[0]


class TurnModel:
    def __init__(self, runs):
        self.analogues = Analogues(runs)
        self.history = historical_durations(runs)
        self.ends = [row["end"] for row in self.history]

    def candidates(self, run, elapsed):
        now = run["started_at"]+elapsed
        prompt = request_at(run, now)
        stop = bisect_left(self.ends, now)
        known = self.history[max(0, stop-220):stop]
        rolling = duration_prior(known, prompt, elapsed)[0]
        context, trace = self.analogues.predict(run, elapsed)
        forecasts = {
            "rolling": rolling,
            "recent": recent_prior(known, prompt, elapsed, now),
            "neighbours": context,
            "trace": trace,
        }
        best_trace = trace or context
        forecasts["blend"] = [(a+b)/2 for a, b in zip(rolling, best_trace)] if rolling and best_trace else None
        return forecasts


def replay(model, turns, old_history, baseline_history):
    """Predict each landmark; prior turns can enter only after their actual end."""
    for run in turns:
        for elapsed in LANDMARKS:
            if elapsed >= run["seconds"]:
                break
            now = run["started_at"]+elapsed
            prompt = request_at(run, now)
            old = reference_prior(old_history, baseline_history, prompt, elapsed, now)
            if not old:
                continue
            yield {
                "turn": run["turn_id"], "band": age_band(elapsed), "elapsed": elapsed,
                "actual": run["seconds"]-elapsed,
                "predictions": {**model.candidates(run, elapsed), "duration": old},
            }


def values_for(rows, policy):
    return [row["predictions"].get(policy[row["band"]]) or row["predictions"]["duration"] for row in rows]


def bounds_for(point, limits):
    return (limits or point)[0], point[1], (limits or point)[2]


def forecasts_for(rows, policy, interval_policy):
    return [bounds_for(point, limits) for point, limits in zip(values_for(rows, policy),
                                                             values_for(rows, interval_policy))]


def weights_for(rows):
    counts = Counter(row["turn"] for row in rows)
    return [1/counts[row["turn"]] for row in rows]


def point_errors(rows, policy):
    return [abs(prediction[1]-row["actual"]) for prediction, row in zip(values_for(rows, policy), rows)]


def loss_for(rows, policy):
    errors = point_errors(rows, policy)
    weights = weights_for(rows)
    balanced = sum(w*error for w, error in zip(weights, errors))/sum(weights)
    return .7*balanced + .3*mean(errors)


def choose_policy(rows):
    policy = dict.fromkeys(BANDS, "duration")
    for band in BANDS:
        group = [row for row in rows if row["band"] == band]
        if len({row["turn"] for row in group}) < 12:
            continue
        baseline = loss_for(group, policy)
        scores = {
            method: loss_for(group, {band: method})
            for method in METHODS
            if method != "duration" and sum(bool(row["predictions"][method]) for row in group) >= .8*len(group)
        }
        if band == "start":
            # At launch prioritize typical error within the same weighted error budget.
            medians = {method: median(point_errors(group, {band: method}))
                       for method, score in scores.items() if score <= baseline}
            if medians:
                best = min(medians, key=medians.get)
                if medians[best] < .97*median(point_errors(group, policy)):
                    policy[band] = best
        elif scores:
            best = min(scores, key=scores.get)
            if scores[best] < .97*baseline:
                policy[band] = best
    return policy


def fit_offsets(rows, forecasts):
    import numpy as np
    observed = np.log1p([row["actual"] for row in rows])
    lower, _, upper = np.log1p(forecasts).T
    # Estimate each tail separately to account for asymmetric misses.
    p = max(.01, .1-1/(len(rows)+1))
    return float(np.quantile(observed-lower, p)), float(np.quantile(observed-upper, 1-p))


def interval_offsets(rows, policy, interval_policy):
    base = {}
    for label, subset in [("global", rows), *((band, [r for r in rows if r["band"]==band]) for band in BANDS)]:
        if len({row["turn"] for row in subset}) < 12:
            continue
        base[label] = fit_offsets(subset, forecasts_for(subset, policy, interval_policy))
    return base


def interval(prediction, offsets):
    low, point, high = prediction
    point = max(1, point)
    return (max(1, min(point, math.expm1(math.log1p(low)+offsets[0]))), point,
            max(point, math.expm1(math.log1p(high)+offsets[1])))


def measure(rows, policy, interval_policy, offsets):
    import numpy as np
    bounds = np.asarray([
        interval(prediction, offsets.get(row["band"], offsets.get("global", (0, 0))))
        for prediction, row in zip(forecasts_for(rows, policy, interval_policy), rows)
    ])
    actual = np.asarray([row["actual"] for row in rows])
    error = np.abs(bounds[:, 1]-actual)
    lo, hi = bounds[:, 0], bounds[:, 2]
    score = hi-lo + 10*(np.maximum(0, lo-actual)+np.maximum(0, actual-hi))
    return {
        "median": round(float(np.median(error)), 1),
        "mean": round(float(np.mean(error)), 1),
        "balancedMean": round(float(np.average(error, weights=weights_for(rows))), 1),
        "p90": round(float(np.quantile(error, .9)), 1),
        "coverage": round(float(np.mean((lo<=actual)&(actual<=hi))), 3),
        "width": round(float(np.median(hi-lo)), 1),
        "intervalScore": round(float(np.mean(score)), 1),
        "withinTwoMinutes": round(float(np.mean(error<=120)), 3),
        "medianRelativeError": round(float(np.median(error/np.maximum(1, actual))), 3),
    }


def choose_interval_policy(rows, policy):
    selected = dict(policy)
    for band in BANDS:
        subset = [row for row in rows if row["band"] == band]
        if len({row["turn"] for row in subset}) < 12:
            continue
        scores = {}
        for method in METHODS:
            candidate = {band: method}
            estimates = forecasts_for(subset, policy, candidate)
            offset = fit_offsets(subset, estimates)
            scores[method] = measure(subset, policy, candidate, {band: offset})["intervalScore"]
        selected[band] = min(scores, key=scores.get)
    return selected


def train(home, data, through=None):
    runs = load_runs(home, data, through)
    if len(runs) < 80:
        raise ValueError("Not enough completed local turns to validate an ETA model.")
    first, last = (int(len(runs)*ratio) for ratio in (.50, .80))
    old = CodexSource(home, data).history(through, legacy=True)
    original = historical_durations(list(reversed(old))[:int(len(old)*.55)])
    baseline = historical_durations(runs[:first])
    cut = runs[last]["started_at"]
    development = [row for row in runs[first:last] if row["completed_at"] < cut]
    holdout = runs[last:]
    model = TurnModel(runs)
    validation = list(replay(model, development, original, baseline))
    if len({row["turn"] for row in validation}) < 24:
        raise ValueError("Not enough completed turns for model selection.")
    policy = choose_policy(validation)
    interval_policy = choose_interval_policy(validation, policy)
    reference = dict.fromkeys(BANDS, "duration")
    offsets = interval_offsets(validation, policy, interval_policy)
    reference_offsets = interval_offsets(validation, reference, reference)
    observations = list(replay(model, holdout, original, baseline))
    samples = len({row["turn"] for row in observations})
    if samples < 12:
        raise ValueError("Not enough replayable held-out turns.")
    results = measure(observations, policy, interval_policy, offsets)
    previous = measure(observations, reference, reference, reference_offsets)
    per_age = {}
    for band in BANDS:
        group = [row for row in observations if row["band"] == band]
        if group:
            per_age[band] = {
                "count": len(group),
                "method": policy[band],
                "model": measure(group, policy, interval_policy, offsets),
                "baseline": measure(group, reference, reference, reference_offsets),
            }
    improve = (results["balancedMean"] < previous["balancedMean"]
               and results["mean"] <= previous["mean"]
               and results["median"] <= 1.025*previous["median"]
               and results["p90"] <= 1.05*previous["p90"]
               and results["withinTwoMinutes"] >= previous["withinTwoMinutes"]-.02
               and results["intervalScore"] <= 1.05*previous["intervalScore"]
               and results["coverage"] >= .7)
    shown = results if improve else previous
    display_profiles = operation_profiles([row for row in runs[:first]
                                           if row["completed_at"] < runs[first]["started_at"]])
    operation_expansions = calibrate_operations(display_profiles, development)
    metrics = {
        "version": VERSION, "sourceRuns": len(runs), "validationRuns": len(development),
        "testRuns": samples, "testPredictions": len(observations),
        "eligibleTestPredictions": sum(age < row["seconds"] for row in holdout for age in LANDMARKS),
        "selectedMethod": "adaptive" if improve else "duration",
        "policy": policy if improve else reference,
        "intervalPolicy": interval_policy if improve else reference,
        "candidatePolicy": policy, "byElapsed": per_age,
        "deployed": shown, "candidate": results, "baseline": previous,
        "medianAbsoluteErrorSeconds": shown["median"], "meanAbsoluteErrorSeconds": shown["mean"],
        "p90AbsoluteErrorSeconds": shown["p90"], "withinTwoMinutes": shown["withinTwoMinutes"],
        "intervalObservedCoverage": shown["coverage"], "intervalNominalCoverage": .8,
        "operationBacktest": validate_operations(display_profiles, holdout, operation_expansions),
        "checkpointsSeconds": list(LANDMARKS),
        "enabled": shown["coverage"] >= .65 and shown["medianRelativeError"] < 1.2,
        "method": "chronological, forecast-weighted and turn-balanced errors; deploy validation",
    }
    data.mkdir(parents=True, exist_ok=True)
    report = data/"activity-eta-backtest.json"
    temporary = report.with_suffix(".tmp")
    temporary.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(report)
    return {
        "predictor": model, "policy": policy if improve else reference,
        "interval_policy": interval_policy if improve else reference,
        "offsets": offsets if improve else reference_offsets,
        "reference": original, "baseline": baseline, "metrics": metrics,
        "maxElapsed": max(run["seconds"] for run in runs),
        "display_profiles": display_profiles, "operation_expansions": operation_expansions,
        "key": (runs[-1]["thread_id"], runs[-1]["turn_id"]),
    }


class LocalForecast:
    def __init__(self, home: Path, data: Path):
        self.home, self.data = home, data
        self.model = None
        self.error = None
        self._thread = None
        self._trained_at = 0
        self._last_attempt = 0
        self._latest_turn = None
        self.refresh(())

    def refresh(self, history):
        newest = (history[0]["thread_id"], history[0]["turn_id"]) if history else None
        if self.training:
            return
        if self._thread and (not newest or newest == self._latest_turn or time.time()-self._last_attempt < 900):
            return
        self._last_attempt = time.time()
        self._thread = threading.Thread(target=self._train, daemon=True)
        self._thread.start()

    def _train(self):
        try:
            candidate = train(self.home, self.data)
            self._latest_turn = candidate["key"]
            if self.model is None or not self.model["metrics"]["enabled"] or candidate["metrics"]["enabled"]:
                self.model = candidate
                self._trained_at = time.time()
            self.error = None
        except Exception as error:
            self.error = type(error).__name__

    def predict(self, snapshot, elapsed, history=()):
        model = self.model
        if model is None:
            reason = "正在校验本地时间预测" if self.training else "本地样本不足，暂不预测时间"
            return {"available": False, "reason": reason}
        metrics = model["metrics"]
        if not metrics["enabled"] or elapsed > model["maxElapsed"]:
            return {"available": False, "reason": "历史样本不足以支持本轮时间预测", "metrics": metrics}
        requests = [(item["at"], message) for item in snapshot.get("items", ())
                    if item["type"]=="userMessage" and (message:=message_text(item))]
        run = {**snapshot, "requests": requests, "thread_id": snapshot.get("thread_id"),
               "prompt": snapshot.get("prompt", ""), "items": snapshot.get("items", ())}
        band = age_band(elapsed)
        method = model["policy"][band]
        now = snapshot["started_at"]+elapsed
        prompt = request_at(run, now)
        reference = reference_prior(model["reference"], model["baseline"], prompt, elapsed, now)
        interval_method = model["interval_policy"][band]
        candidates = {"duration": reference}
        if method != "duration" or interval_method != "duration":
            candidates.update(model["predictor"].candidates(run, elapsed))
        prediction = candidates[method]
        if not prediction:
            method = "duration"
            prediction = reference
        if not prediction:
            return {"available": False, "reason": "同类长轮次样本不足", "metrics": metrics}
        bounds = bounds_for(prediction, candidates[interval_method] or reference)
        low, point, high = interval(bounds, model["offsets"].get(band, model["offsets"].get("global", (0, 0))))
        return {"available": True, "range": [math.floor(low), math.ceil(high)], "median": round(point),
                "reference_seconds": round(reference[1]) if reference else None,
                "metrics": metrics, "method": method,
                "reason": f"{METHODS[method]} · {metrics['testRuns']} 个留出轮次"}

    def operation(self, snapshot, now):
        model = self.model
        if not model or snapshot["status"] not in ("inProgress", "in_progress"):
            return None
        for started, kind in running_operations(snapshot.get("items", ()), now):
            age = now-started
            band = operation_band(age)
            metrics = model["metrics"]["operationBacktest"].get(kind, {}).get(band)
            if not metrics or metrics["count"] < 12 or metrics["coverage"] < .7:
                continue
            expansion = model["operation_expansions"].get((kind, band))
            if expansion is None:
                continue
            forecast = operation_remaining(model["display_profiles"], kind, age, expansion)
            if forecast:
                return forecast
        return None

    @property
    def trained_at(self):
        return self._trained_at or None

    @property
    def training(self):
        return self._thread is not None and self._thread.is_alive()
