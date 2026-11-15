# Estimation method

## Workload and evidence

The optional MCP plugin binds the checklist to a chat and turn through trusted hooks. Each deliverable has a stable ID and an integer weight. Completed work requires evidence. An in-progress item earns proportional credit when it reports completed units, total units, and evidence. Rework can lower the displayed estimate.

## Whole-turn ETA

Luma predicts wall-clock time to the end of the current Codex turn. The local history uses up to 600 successfully completed user turns with usable timestamps and recorded durations between 0.25 seconds and six hours. Short user turns stay in the data. Automatic approval reviews and subagent turns are excluded from candidate preparation.

Candidates use rolling same-class durations, recency-weighted quantiles, and comparable turns. Comparable turns must still be active at the elapsed time being forecast. Weights reflect request class and length, recency, and shared project or chat. Another candidate also matches the visible event history through that elapsed time, including commands, edits, tests, replies, plan and context events, and active tools. The app evaluates a blend of the history and event-based estimates as well.

The first half of the history seeds the replay. The middle 30% selects a method for each elapsed band and calibrates the lower and upper bounds separately. Each replay uses only turn outcomes already complete at that instant. Steering messages and tool results become eligible at their recorded times. The most recent 20% checks deployment at fixed points starting at 2, 5, 15 and 30 seconds. Each completed turn contributes only points that fall during its execution. The same checkpoint set is used for all reported comparisons.

The point forecast and interval can use different candidates. Selection at launch gives priority to median absolute error within a weighted error budget. Later bands use a combination of turn-balanced mean absolute error and ordinary mean absolute error. Turn balancing gives each turn equal weight across its checkpoints. The interval score penalizes wider intervals and misses outside either endpoint. The desired coverage is 80%, and the measured coverage is reported alongside it. Both are empirical because checkpoints within a turn and changes in workload can affect the calibration.

The final slice compares the policy against the v0.3-style conditional-duration reference on matched points, with each model's interval calibrated under the same protocol. The default requires improvements in both mean errors and checks for regressions in typical error, tail error, closeness within two minutes, and interval score. The app falls back to the duration reference when the candidate fails. These are local retrospective metrics. See the journal for separately recorded results from live turns.

Matching a partially observed sequence and comparing methods on the same checkpoints follow ideas from the event-log [remaining time prediction benchmark](https://github.com/verenich/time-prediction-benchmark). Separately estimating the tails draws on [Conformalized Quantile Regression](https://proceedings.neurips.cc/paper/2019/hash/5103c3584b063c431bd1268e9b5e76fb-Abstract.html).

## Current operation

The operation forecast uses start and finish metadata and a bounded read of pending calls in the local rollout. The historical profiles can use runner-reported durations from older records that only preserve completion. Validation uses observed wall-clock starts and finishes at operation ages of 4, 15 and 60 seconds. A category and age band need at least 12 held-out samples and 70% interval coverage to appear. The whole-turn model uses visible activity patterns. Its timing includes time outside tools and permits overlapping or repeated operations.

[TraceLab](https://github.com/uw-syfi/TraceLab) profiles tool and non-tool time in agent traces and documents the kinds of timing estimates available from traces. [Vidur](https://github.com/microsoft/vidur) uses separate profiling at the serving layer and needs hardware, queuing and batch parameters. The local observer has access to persisted item timing. Queue time, time to first token and token decode speed require serving telemetry or a connected generation stream. See [Codex app-server events](https://learn.chatgpt.com/docs/app-server) and [vLLM metrics](https://docs.vllm.ai/en/latest/design/metrics/).

## Actual results and local learning

Luma records first and last emitted estimates before the actual Codex end time. It stores an overview of intermediate checkpoints, the forecast intervals, and their method. Delayed projections can reveal that a forecast was written after the turn ended. Those readings are discarded. The record is keyed by chat and turn, so late completions can be reconciled even after switching chats. Interrupted and failed turns remain visible in the record and are excluded from the accuracy comparison used for normal completions.

New completed turns trigger background retraining, at most every 15 minutes while the panel runs. If the new fit cannot produce a usable model, the previous usable model remains available. The journal keeps the local prediction and duration reference issued at the same instant. The first comparable pair from each completed turn is scored against the actual end time. The app displays paired results as soon as they are available.

Bias correction uses one observation per elapsed band and completed turn from the current predictor version. After enough data, the median log ratio of actual to predicted remaining time provides a bounded correction. A newer batch checks the correction before it can change the current estimate. The UI shows the training time, the number of comparable turns, and measured first-forecast error.

## AI time advice

The configured provider receives the existing opt-in subset of task data. The journal saves each request outcome, validated structured advice, evidence references, and how the application used it. Run-scoped requests are invalidated by a changed turn, checklist revision, prompt, or provider configuration. AI estimates of work continue to respect scoped completion evidence.

For time, the app records a contemporaneous local prediction and an AI estimate of the same finish time. It also evaluates a blend with 25% weight on AI. The earliest usable comparison from each successfully completed turn contributes to the provider's score for the current predictor version. The blend enters displayed estimates after at least 12 paired turns and improvement in both median and mean absolute error. The review continues to show older assessments. [AgentTime](https://arxiv.org/html/2610.09944v1#S4.SS2) measures the difficulty of estimating agent execution time from the starting state and motivates comparing AI advice to real outcomes.
