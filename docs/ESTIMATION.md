# Estimation method

## Workload and evidence

The optional MCP plugin binds the checklist to a chat and turn through trusted hooks. Each deliverable has a stable ID and an integer weight. Completed work requires evidence. An in-progress item earns proportional credit when it reports completed units, total units, and evidence. Rework can lower the displayed estimate.

## Whole-turn ETA

Luma records wall-clock time to the end of a Codex turn. The task class and request length, known tool starts and finishes, edits, checks, and recent activity feed the candidates for the overall forecast.

The candidates include conditional historical duration, rolling historical duration, recency-weighted empirical quantiles, gradient-boosted activity, and activity with operation-duration features. The empirical estimates condition on a turn still running at the observed age. The learned candidates predict log remaining time at three quantiles.

Up to 300 completed turns are divided chronologically for training, model selection, interval calibration, and final validation. Earlier partitions only use outcomes available by the partition boundary. Validation uses predetermined elapsed times while each turn is still active, starting at 5 and 30 seconds. Tool failures and steering messages enter a feature vector when their timestamps become visible.

If the selected alternative fails on the final validation split, the application falls back to the historical duration baseline. The split compares alternatives and checks deployment eligibility, so these results are retrospective validation. The separate journal measures prospective performance.

## Current operation

The operation forecast uses start and finish metadata where available. A bounded read of the local rollout also picks up pending MCP calls when their projection appears only upon completion. Each category fits an empirical remaining-time distribution conditioned on the operation having already lasted this long. Calibration widens intervals using a separate time slice. Categories need enough historical examples and adequate held-out interval coverage to appear. Tool operations can overlap or repeat, so the whole-turn candidate uses operation estimates as features. It also captures work outside known tool boundaries.

Server-side TTFT, queue time, and per-token decode speeds would require provider telemetry or a connected generation stream. The local observer measures public item wall times. A completed reply's duration can help estimate other observable replies but cannot account for remaining tools or upstream load. Related primary references are the [Codex app-server event docs](https://learn.chatgpt.com/docs/app-server), [vLLM metrics](https://docs.vllm.ai/en/latest/design/metrics/), and [Efficient Interactive LLM Serving with Proxy Model-based Sequence Length Prediction](https://arxiv.org/abs/2404.08509).

## Actual results and local learning

Luma records first and last emitted estimates before the actual Codex end time. It stores an overview of intermediate checkpoints, the forecast intervals, and their method. Delayed projections can reveal that a forecast was written after the turn ended. Those readings are discarded. The record is keyed by chat and turn, so late completions can be reconciled even after switching chats. Interrupted and failed turns remain visible in the record and are excluded from the accuracy comparison used for normal completions.

New completed turns trigger background retraining, at most every 15 minutes while the panel runs. On a failed validation, the previous validated model is retained. The journal contributes one record per elapsed-time band and completed turn. With enough data, the median log ratio of actual to predicted remaining time provides a bounded bias correction. A newer batch tests the correction before it changes the current estimate. The UI shows the latest training timestamp, the number of comparable runs, and measured first-forecast error. The interval method uses separate empirical calibration, inspired by [conformal prediction under distribution shift](https://arxiv.org/abs/2106.00170).

## AI time advice

The configured provider receives the existing opt-in subset of task data. The journal saves each request outcome, validated structured advice, evidence references, and how the application used it. Run-scoped requests are invalidated by a changed turn, checklist revision, prompt, or provider configuration. AI estimates of work continue to respect scoped completion evidence.

For time, the app records a contemporaneous local prediction and an AI estimate of the same finish time. It also evaluates a candidate blend with 25% weight on AI. The earliest usable comparison in each successfully completed turn contributes to the provider's score. The candidate blend enters displayed estimates only after at least 12 paired turns and improvement in both median and mean absolute error. All recorded advice is still available for inspection.
