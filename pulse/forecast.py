"""Local numeric-only remaining-time model, with chronological calibration.

No network, no LLM, no reasoning content. Conversation text is used in memory
only to classify request type and measure length; saved models contain numeric
features. A held-out backtest determines whether the model is offered at all.
"""
from __future__ import annotations
import json
import math
import statistics
import threading
import time
from bisect import bisect_left
from pathlib import Path

from .estimator import classify, VERIFY, duration_forecast
from .operations import (calibrate_operations, operation_profiles, operation_remaining,
                         running_operations, validate_operations)
from .source import CodexSource, connect_readonly, message_text

VERSION = 3
CHECKPOINTS = (5, 30, 90, 180, 450, 900, 1800, 3600)


def features(prompt, items, start, elapsed):
    cut=start+elapsed
    counts={k:0 for k in ['commandExecution','fileChange','mcpToolCall','agentMessage','tests','failures','research','ui']}
    last=start
    running=0;running_age=0;last_edit=last_test=None
    for item in items:
        began=item.get('started',item.get('at',start))
        if began>cut:continue
        at=item.get('finished',item.get('at',start))
        typ=item.get('type')
        if at is None or at>cut:
            if typ in ('commandExecution','fileChange','mcpToolCall'):
                running+=1;running_age=max(running_age,cut-began)
            continue
        if typ in counts:counts[typ]+=1
        if typ=='commandExecution':
            counts['tests']+=bool(VERIFY.search(item.get('command') or ''))
            counts['failures']+=item.get('exitCode') not in (None,0)
            if VERIFY.search(item.get('command') or ''):last_test=at
        if typ=='fileChange':last_edit=at
        if typ=='mcpToolCall':
            name=(item.get('tool') or '').lower()
            counts['research']+=any(word in name for word in ['search','fetch','read','browse'])
            counts['ui']+=any(word in name for word in ['cua','screenshot','inspect']) or name=='js'
        last=max(last,at)
    kind=classify(prompt)
    return [math.log1p(max(0,elapsed)),math.log1p(len(prompt)),*[float(kind==k) for k in ['build','design','research','general']],*[math.log1p(counts[k]) for k in counts],math.log1p(max(0,cut-last)),math.log1p(running),math.log1p(max(0,running_age)),math.log1p(max(0,cut-last_edit)) if last_edit else -1,math.log1p(max(0,cut-last_test)) if last_test else -1,float(bool(last_edit and last_test and last_edit>last_test))]


def load_runs(home, data):
    history=CodexSource(home,data).history()
    runs=[]
    with connect_readonly(home/'thread_history_1.sqlite') as c:
        for record in reversed(history):
            marks="'commandExecution','fileChange','mcpToolCall','agentMessage','webSearch','imageGeneration','userMessage'"
            rows=c.execute(f"""SELECT item_type,created_at_ms,started_at_ms,completed_at_ms,COALESCE(completed_at_ms,started_at_ms,created_at_ms) AS at,
              substr(json_extract(item_json,'$.command'),1,6000) AS command,
              json_extract(item_json,'$.tool') AS tool,
              json_extract(item_json,'$.exitCode') AS exitCode,
              CASE WHEN item_type='userMessage' THEN substr(json_extract(item_json,'$.content[0].text'),1,2000) END AS request
              FROM thread_items WHERE thread_id=? AND turn_id=? AND item_type IN ({marks})
              ORDER BY rollout_ordinal LIMIT 1000""",(record['thread_id'],record['turn_id'])).fetchall()
            if len(rows)>=1000:continue
            items = []
            requests = []
            for row in rows:
                started = (row["started_at_ms"] or row["created_at_ms"] or row["at"]) / 1000
                if row["item_type"] == "userMessage":
                    requests.append((started, row["request"] or ""))
                    continue
                items.append(dict(type=row["item_type"], at=row["at"]/1000, started=started,
                                  finished=(row["completed_at_ms"] or row["at"])/1000,
                                  command=row["command"], tool=row["tool"], exitCode=row["exitCode"]))
            runs.append({**record, "items": items, "requests": requests})
    return runs


def request_at(run, cut):
    prefixed = [text[:2000] for at, text in run.get("requests", ()) if at <= cut]
    # Projection timestamps can place the initial user message a fraction
    # of a second after the rounded turn start.
    return "\n".join(prefixed)[-6000:] if prefixed else run["prompt"][:2000]


def phase_features(prompt, items, start, elapsed, profiles):
    vector = features(prompt, items, start, elapsed)
    cut = start + elapsed
    running = running_operations(items, cut)
    operation = None
    if running:
        began, kind = running[0]
        operation = operation_remaining(profiles, kind, cut - began)
    observed = [item for item in items if (item.get("finished", item.get("at")) or float("inf")) <= cut]
    recent = [item for item in observed if item.get("finished", item.get("at")) >= cut - 180]
    vector.extend([
        math.log1p(len(recent)),
        math.log1p(sum(item["type"] == "fileChange" for item in recent)),
        math.log1p(sum(item["type"] in ("mcpToolCall", "commandExecution") for item in recent)),
        float(bool(running)),
        math.log1p(operation["median"]) if operation else -1,
        math.log1p(operation["range"][1]) if operation else -1,
    ])
    return vector


def samples(runs, vector=features, fixed=False):
    x = []
    y = []
    for run in runs:
        if fixed:
            checkpoints = [seconds for seconds in CHECKPOINTS if seconds < run["seconds"]]
        else:
            checkpoints = [run["seconds"] * fraction for fraction in (.15, .3, .5, .7, .9)]
        for elapsed in checkpoints:
            start = run["started_at"]
            x.append(vector(request_at(run, start+elapsed), run["items"], start, elapsed))
            y.append(math.log1p(run['seconds']-elapsed))
    return x,y


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
        "kind": classify(run["prompt"][:2000]), "end": run["started_at"]+run["seconds"],
    } for run in runs), key=lambda row: row["end"])


def train(home, data):
    import numpy as np
    from sklearn.ensemble import HistGradientBoostingRegressor
    runs = load_runs(home, data)
    if len(runs) < 80:
        raise ValueError("Not enough completed local turns to validate an ETA model.")
    a, b, c = (int(len(runs)*ratio) for ratio in (.55, .7, .85))
    def finished_before(batch, cut):
        return [run for run in batch if run["started_at"] + run["seconds"] <= cut]

    past = finished_before(runs[:a], runs[a]["started_at"])
    tune = finished_before(runs[a:b], runs[b]["started_at"])
    calibrate = finished_before(runs[b:c], runs[c]["started_at"])
    test = runs[c:]
    if len(past) < 35 or min(len(tune), len(calibrate)) < 8:
        raise ValueError("Too many overlapping turns to validate a model.")
    profiles = operation_profiles(past)
    history = historical_durations(past)
    rolling_history = historical_durations(runs)
    end_times = [row["end"] for row in rolling_history]

    def augmented(prompt, items, start, elapsed):
        return phase_features(prompt, items, start, elapsed, profiles)

    def fit(vector, fixed):
        x, y = samples(past, vector, fixed)
        return [
            HistGradientBoostingRegressor(loss="quantile", quantile=q, max_iter=130, max_leaf_nodes=9,
                                          min_samples_leaf=18, l2_regularization=3, random_state=7)
            .fit(np.asarray(x), np.asarray(y))
            for q in (.1, .5, .9)
        ]

    def replay(name, batch):
        models, vector = candidates[name]
        if models:
            x, y = samples(batch, vector, fixed=True)
            return np.column_stack([model.predict(x) for model in models]), np.asarray(y)
        points = []
        actual = []
        for run in batch:
            for elapsed in CHECKPOINTS:
                if elapsed >= run["seconds"]:
                    break
                prompt = request_at(run, run["started_at"] + elapsed)
                if name in ("recent", "rolling"):
                    cut = run["started_at"]+elapsed
                    past_end = bisect_left(end_times, cut)
                    known = rolling_history[max(0, past_end-165):past_end]
                    forecast = (recent_prior(known, prompt, elapsed, cut) if name=="recent"
                                else duration_prior(known, prompt, elapsed)[0])
                else:
                    forecast, _ = duration_prior(history, prompt, elapsed)
                fallback = statistics.median(row["seconds"] for row in past)
                points.append(forecast or [1, fallback, 7200])
                actual.append(run["seconds"]-elapsed)
        return np.log1p(points), np.log1p(actual)

    def errors(name, batch):
        predictions, actual = replay(name, batch)
        return np.abs(np.expm1(predictions[:, 1]) - np.expm1(actual))

    def score(values):
        return float(np.median(values) + .35*np.mean(values))

    candidates = {
        "duration": (None, None),
        "rolling": (None, None),
        "recent": (None, None),
        "activity": (fit(features, False), features),
        "operations": (fit(augmented, True), augmented),
    }
    selection = {name: score(errors(name, tune)) for name in candidates}
    preferred = min(selection, key=selection.get)
    baseline = errors("duration", test)

    def assess(name):
        forecasts, actual = replay(name, calibrate)
        low, middle, high = forecasts.T
        residual = np.maximum(np.minimum(low, middle)-actual, actual-np.maximum(high, middle))
        level = min(1, math.ceil((len(residual)+1)*.8)/len(residual))
        expansion = max(0, float(np.quantile(residual, level, method="higher")))
        forecasts, actual = replay(name, test)
        low, middle, high = forecasts.T
        values = np.abs(np.expm1(middle)-np.expm1(actual))
        covered = (np.minimum(low, middle)-expansion <= actual) & (actual <= np.maximum(high, middle)+expansion)
        return {
            "expansion": expansion, "errors": values, "coverage": float(np.mean(covered)),
            "relative": float(np.median(values/np.maximum(1, np.expm1(actual)))),
        }

    review = {name: assess(name) for name in candidates}
    selected = preferred
    if selected != "duration" and (score(review[selected]["errors"]) > score(baseline)
                                   or review[selected]["coverage"] < .65):
        selected = "duration"
    chosen = review[selected]
    values = chosen["errors"]
    display_profiles = operation_profiles(past+tune)
    operation_expansions = calibrate_operations(display_profiles, calibrate)
    metrics = {
        "version": VERSION, "trainRuns": len(past), "selectionRuns": len(tune),
        "calibrationRuns": len(calibrate), "testRuns": len(test), "testPredictions": len(values),
        "intervalNominalCoverage": .8,
        "intervalObservedCoverage": round(chosen["coverage"], 3),
        "medianAbsoluteErrorSeconds": round(float(np.median(values)), 1),
        "medianRelativeError": round(chosen["relative"], 3),
        "meanAbsoluteErrorSeconds": round(float(np.mean(values)), 1),
        "p90AbsoluteErrorSeconds": round(float(np.quantile(values, .9)), 1),
        "withinTwoMinutes": round(float(np.mean(values<=120)), 3),
        "baselineMedianAbsoluteErrorSeconds": round(float(np.median(baseline)), 1),
        "rollingMedianAbsoluteErrorSeconds": round(float(np.median(review["rolling"]["errors"])), 1),
        "recentMedianAbsoluteErrorSeconds": round(float(np.median(review["recent"]["errors"])), 1),
        "activityMedianAbsoluteErrorSeconds": round(float(np.median(review["activity"]["errors"])), 1),
        "operationsMedianAbsoluteErrorSeconds": round(float(np.median(review["operations"]["errors"])), 1),
        "preferredMethod": preferred, "selectedMethod": selected,
        "operationBacktest": validate_operations(display_profiles, test, operation_expansions),
        "checkpointsSeconds": list(CHECKPOINTS),
        "method": "chronological selection, calibration and test at fixed elapsed checkpoints",
        "target": "time to end of turn",
    }
    metrics["enabled"] = (metrics["intervalObservedCoverage"] >= .65
                          and metrics["medianRelativeError"] < 1.2)
    model_data = {
        "version": VERSION, "models": candidates[selected][0], "expansion": chosen["expansion"], "metrics": metrics,
        "maxElapsed": max(run["seconds"] for run in past), "profiles": profiles,
        "display_profiles": display_profiles,
        "history": history,
        "operation_expansions": operation_expansions,
        "key": (runs[-1]["thread_id"], runs[-1]["turn_id"]),
    }
    data.mkdir(parents=True, exist_ok=True)
    (data/"activity-eta-backtest.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2),
                                                 encoding="utf-8")
    return model_data


class LocalForecast:
    def __init__(self,home:Path,data:Path):
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
        if self._thread and self._thread.is_alive():
            return
        if self._thread and (not newest or newest == self._latest_turn or time.time()-self._last_attempt < 900):
            return
        self._latest_turn = newest
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
            reason = "正在校验本地时间预测" if self._thread and self._thread.is_alive() else "本地样本不足，暂不预测时间"
            return {"available": False, "reason": reason}
        metrics = model["metrics"]
        if not metrics['enabled']:
            return {'available':False,'reason':'历史回放误差较大，暂不显示时间预测','metrics':metrics}
        if elapsed>model['maxElapsed']:
            return {'available':False,'reason':'本轮超过历史覆盖范围，暂停时间预测','metrics':metrics}
        start = snapshot["started_at"]
        items = snapshot.get("items", ())
        cut = start+elapsed
        requests = [(item["at"], message[:2000]) for item in items
                    if item["type"]=="userMessage" and (message:=message_text(item))]
        prompt = request_at({"requests": requests, "prompt": snapshot.get("prompt", "")}, cut)
        method = metrics["selectedMethod"]
        if method in ("duration", "rolling", "recent"):
            if method in ("rolling", "recent"):
                live = [row for row in historical_durations(history) if row["end"]<cut]
                known = live[-165:] or model["history"]
                prior = (recent_prior(known, prompt, elapsed, cut) if method=="recent"
                         else duration_prior(known, prompt, elapsed)[0])
            else:
                prior, _ = duration_prior(model["history"], prompt, elapsed)
            if not prior:
                return {"available": False, "reason": "同类长轮次样本不足", "metrics": metrics}
            low, middle, high = (math.log1p(value) for value in prior)
        else:
            vector = (phase_features(prompt, items, start, elapsed, model["profiles"])
                      if method=="operations" else features(prompt, items, start, elapsed))
            low, middle, high = (float(m.predict([vector])[0]) for m in model["models"])
        point = max(1, math.expm1(middle))
        lo = max(1, math.expm1(min(low, middle)-model["expansion"]))
        hi = max(point, math.expm1(max(high, middle)+model["expansion"]))
        labels = {"duration": "同类历史时长", "rolling": "更新后的历史时长", "recent": "近期相似轮次",
                  "activity": "整轮活动模型", "operations": "整轮模型（含操作时长）"}
        return {"available": True, "range": [round(lo), round(hi)], "median": round(point),
                "metrics": metrics, "method": method, "reason": f"{labels[method]} · {metrics['testRuns']} 个留出轮次"}

    def operation(self, snapshot, now):
        model = self.model
        if not model or snapshot["status"] not in ("inProgress", "in_progress"):
            return None
        running = running_operations(snapshot.get("items", ()), now)
        for started, kind in running:
            metrics = model["metrics"]["operationBacktest"].get(kind)
            if not metrics or metrics["count"] < 12 or metrics["coverage"] < .7:
                continue
            expansion = model["operation_expansions"].get(kind, 0)
            forecast = operation_remaining(model["display_profiles"], kind, now-started, expansion)
            if forecast:
                return forecast
        return None

    @property
    def trained_at(self):
        return self._trained_at or None

    @property
    def training(self):
        return self._thread is not None and self._thread.is_alive()
