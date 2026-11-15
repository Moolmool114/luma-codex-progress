"""Conditional remaining time from comparable completed Codex turns."""

from math import log1p

from .estimator import VERIFY, classify

LANDMARKS = (2, 5, 15, 30, 60, 120, 240, 480, 900, 1800, 3600)
PREFIX_WEIGHTS = (.42, 1.8, .36, .75, .22, .70, .38, .40, .21, .30, .34, .24, .08, .70)


def request_at(run, cut):
    """Initial request plus steering that was already visible at the cut."""
    requests = [text[:2000] for at, text in run.get("requests", ()) if at <= cut]
    return "\n".join(requests)[-6000:] if requests else run["prompt"][:2000]


def prefix_state(items, start, elapsed):
    cut = start+elapsed
    counts = [0] * 10
    last = start
    running = 0
    oldest = 0
    used_tool = False
    for item in items:
        began = item.get("started", item.get("at", start))
        if began > cut:
            continue
        kind = item["type"]
        end = item.get("finished", item.get("at", began))
        tool = kind in ("commandExecution", "mcpToolCall", "webSearch", "imageGeneration", "collabAgentToolCall")
        if end is None or end > cut:
            if tool:
                running += 1
                oldest = max(oldest, cut-began)
            continue
        last = max(last, end)
        used_tool |= tool
        if kind == "agentMessage":
            counts[0] += item.get("phase") != "final_answer"
            counts[1] += item.get("phase") == "final_answer"
        if kind == "commandExecution":
            counts[2] += 1
            counts[3] += bool(VERIFY.search(item.get("command") or ""))
            counts[4] += item.get("exitCode") not in (None, 0)
        if kind == "fileChange":
            counts[5] += 1
        if kind in ("mcpToolCall", "webSearch", "collabAgentToolCall", "imageGeneration"):
            counts[6] += 1
        if kind in ("plan", "contextCompaction"):
            counts[7] += 1
        if end >= cut-90:
            counts[8] += 1
        if tool and end >= cut-45:
            counts[9] += 1
    # Coarse stage distances complement the raw event counts. An active
    # command can be seen without knowing its eventual output or exit code.
    return [*(log1p(x) for x in counts), log1p(max(0, cut-last)), log1p(running),
            log1p(oldest), float(used_tool)]


class Analogues:
    def __init__(self, runs):
        import numpy as np
        self.np = np
        self.runs = runs
        self.tables = {elapsed: self.features([run for run in runs if run["seconds"] > elapsed], elapsed)
                       for elapsed in LANDMARKS}

    def features(self, rows, elapsed):
        np = self.np
        prompts = [request_at(run, run["started_at"]+elapsed) for run in rows]
        return {
            "end": np.array([run["completed_at"] for run in rows]),
            "duration": np.array([run["seconds"] for run in rows]),
            "thread": np.array([run["thread_id"] for run in rows], dtype=object),
            "project": np.array([run.get("project") or "" for run in rows], dtype=object),
            "kind": np.array([classify(prompt) for prompt in prompts], dtype=object),
            "length": np.array([log1p(len(prompt)) for prompt in prompts]),
            "prefix": np.array([prefix_state(run["items"], run["started_at"], elapsed) for run in rows])
            .reshape(-1, len(PREFIX_WEIGHTS)),
        }

    def predict(self, run, elapsed):
        np = self.np
        start = run["started_at"]
        now = start + elapsed
        table = self.tables.get(elapsed)
        if table is None:
            rows = [past for past in self.runs if past["completed_at"] < now and past["seconds"] > elapsed]
            table = self.features(rows, elapsed)
        mask = table["end"] < now
        if np.count_nonzero(mask) < 8:
            return None, None
        project = run.get("project") or ""
        prompt = request_at(run, now)
        durations = table["duration"][mask]
        age_days = (now-table["end"][mask])/86400
        weights = .15+.85*np.exp2(-age_days/35)
        weights *= np.where(table["kind"][mask] == classify(prompt), 2.2, .85)
        weights *= np.exp(-.38*np.abs(table["length"][mask]-log1p(len(prompt))))
        if project:
            weights *= np.where(table["project"][mask] == project, 2.8, 1)
        weights *= np.where(table["thread"][mask] == run["thread_id"], 4.0, 1)
        signs = np.array(prefix_state(run["items"], start, elapsed))
        deltas = np.abs(table["prefix"][mask]-signs)
        trace = weights*np.exp(-(deltas @ PREFIX_WEIGHTS))
        order = np.argsort(durations, kind="stable")
        return tuple(self.remaining(durations[order], weighted[order], elapsed) for weighted in (weights, trace))

    def remaining(self, ordered, weights, elapsed):
        # Shrink a narrow local distribution toward the full risk set.
        np = self.np
        weights = .86*weights/np.sum(weights) + .14/len(weights)
        distribution = np.cumsum(weights)
        indexes = np.minimum(np.searchsorted(distribution, (.1, .5, .9)), len(ordered)-1)
        return np.maximum(1, ordered[indexes]-elapsed).tolist()
