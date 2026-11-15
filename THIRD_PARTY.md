# Sources and reuse

This local application is named **Luma**, to distinguish it from the existing Codex Pulse project. The containing folder was created before that project was discovered.

The plugin's atomic JSON read/write and session-binding structure are adapted from **GuMu599/-codex-goal-viewer**, MIT, commit `583caed767b085055ecf1d9fb087441640f4fb26`. The required notice is in `licenses/goal-viewer-MIT.txt`.

Source: https://github.com/GuMu599/-codex-goal-viewer

The weighted deliverable approach was informed by **ezra-y/codex-goal-progress**, inspected at commit `cdb6d9deac0545c0e7378edca58af13e7e1b5b32`. No code from that project's UI injection, CDP adapter, macOS helper, or progress core is included. Luma never injects into Codex.

The survey also examined **wudongshu2002/codex-pulse**, commit `b8732a4a98081559dd5f8f7ca417a7b7a486d8de`. Its elapsed-time/activity-count percentage formula is not used here. No code or assets from that project are included.

Other surveyed projects: **LH-03/agent-monitor-hud**, **StevenSong77/codexstar**. They provide Windows activity/status UIs rather than the requested task-completion estimate.

The timing work draws on [TraceLab](https://github.com/uw-syfi/TraceLab), [AgentTimeBench](https://github.com/michaelofengenden/agenttimebench), and [the remaining-time benchmark](https://github.com/verenich/time-prediction-benchmark). These contributions are methodological references. Details are in [Estimation method](docs/ESTIMATION.md).

Runtime dependencies: Python 3, pywebview (BSD), pythonnet (MIT), NumPy (BSD), Microsoft Edge WebView2 Runtime. The app uses installed runtime dependencies.
