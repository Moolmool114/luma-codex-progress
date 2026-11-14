# Estimation method

## Workload and evidence

The optional MCP plugin binds to the selected chat and current turn through trusted hooks. It accepts stable deliverable IDs and integer weights totaling 10000. Reports carry a revision; stale updates and cross-turn writes are rejected. Completed work requires evidence.

An in-progress deliverable earns zero work unless it reports completedUnits / totalUnits with evidence. Units should represent comparable, useful outputs. Changing scope, weights, labels, previously earned work or a unit total requires an explanation. There is no monotonic clamp: valid rework can lower an estimate immediately.

The displayed point is the weighted reported completion. Its range is an explanatory scope bound, not a calibrated statistical confidence interval. Unknown future scope, inaccurate weights and mistaken reports can all make the actual percentage different. A local validator cannot prove semantic task completion.

Without a checklist, visible activity gives a broad, low-confidence fallback. This heuristic is not validated against engineering-completion ground truth. It should not be used as a contractual completion metric.

## Remaining time

The time target is the end of the current Codex turn, rather than independent acceptance of a user goal. Features include request category and length, elapsed time, completed activity counts, running-tool age and time since edits or checks. Tool results are visible to training only after their actual completion time.

Up to 300 completed user turns are split chronologically into 60% training, 20% calibration and 20% test. Each turn contributes observations at 15%, 30%, 50%, 70% and 90% of its duration. Quantile gradient-boosted regressors predict log remaining time at 10%, 50% and 90%. A separate calibration residual widens the range. The median model supplies the point estimate; the midpoint of an asymmetric interval would bias it upward.

A historical duration baseline is evaluated on the same held-out observations. The local model must meet its error/coverage guardrails and beat that baseline before it is used. Insufficient history, approval waits, stale activity or elapsed time beyond historical coverage can suppress ETA.

The development-machine example in README is one correlated local workload, not an independent cross-project study. Every user's results differ. The local metrics file also includes mean, P90 and relative error to make the long tail visible. Interval coverage is not task-progress accuracy.

## Optional semantic observer

A language model can help interpret missing scope, but is not automatically a better clock. The observer is opt-in and accepts an explicit endpoint, model and data-sharing consent. Its prompt treats task material as untrusted observations. Responses must be JSON, refer to the same turn and cite supplied public event IDs. Percentages, durations and ordering are validated locally.

It cannot change the checklist, erase earned work, exceed the active work range, or declare completion. Disagreement remains visible, and its time interval is combined with the local interval without claiming an improved calibrated confidence level. Results expire and are isolated by turn, checklist revision and endpoint/model settings.

No real provider result or precision improvement is claimed by the protocol tests. Providers support different structured-output subsets. The implementation follows the Chat Completions response_format convention; see [OpenAI's structured output documentation](https://developers.openai.com/api/docs/guides/structured-outputs).

The most useful next research step is a consented evaluation dataset with independent semantic completion labels, compared across local-only, checklist-only and AI-assisted estimators. Until then, avoid advertising a percentage accuracy.
