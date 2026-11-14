from __future__ import annotations

import argparse
import json
import os
import secrets
import sqlite3
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from pulse.estimator import classify, estimate
from pulse.journal import PredictionJournal, TERMINAL
from pulse.learning import calibrate, finish_prediction, replace_time, trust_advisor
from pulse.source import CodexSource, read_json
from pulse.forecast import LocalForecast
from pulse.assessor import Assessor, apply_assessment, endpoint_url

ROOT = Path(__file__).resolve().parent
_mutex = None
SIZES={'ribbon':(360,120),'mini':(360,294),'focus':(420,490),'full':(500,710)}


class Application:
    def __init__(self, home, data, thread_id=None, demo=False):
        self.home, self.data = home, data
        data.mkdir(parents=True, exist_ok=True)
        self.demo=demo
        if demo:
            from pulse.demo import DemoSource,DemoForecast
            self.source=DemoSource();self.forecast=DemoForecast()
        else:
            self.source = CodexSource(home, data)
            self.forecast = LocalForecast(home,data)
        self.config = read_json(data / "settings.json", {})
        self.selected = thread_id or self.config.get("threadId")
        self.window = None
        self.lock = threading.RLock()
        self.topmost = self.config.get("topmost", True)
        self.view_size = self.config.get('size','focus') if self.config.get('size','focus') in SIZES else 'focus'
        self.compact = self.view_size=='ribbon'
        self.scale = 1.0
        self.resizing = False
        self.expanded_height = 680
        self.server = None
        self.token = secrets.token_urlsafe(24)
        self.state_lock = threading.RLock()
        self.journal_error = False
        try:
            self.journal = PredictionJournal(data)
        except (OSError, sqlite3.Error):
            self.journal = None
            self.journal_error = True
        self.assessor = Assessor(data, record=self.record_advice)
        self.last_reconciliation = 0
        self.last_learning = 0
        self.calibration_records = []
        self.observed_scores = None
        self.advisor_scores = {}
        if demo and self.journal:
            from pulse.demo import seed_review
            seed_review(self.journal, self.source)

    def save(self):
        with self.lock:
            self.config.update(threadId=self.selected, topmost=self.topmost)
            target = self.data / "settings.json"
            temp = target.with_suffix(".tmp")
            temp.write_text(json.dumps(self.config, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(target)

    def state(self, thread_id=None):
        with self.state_lock:
            return self._state(thread_id)

    def _state(self, thread_id):
        self.reconcile(time.time())
        selected = thread_id or self.selected
        if not selected:
            threads = self.source.threads(limit=1)
            if not threads:
                return {"empty": True, "message": "还没有本地 Codex 对话", "hint": "在 Codex 开始一次任务后，这里会自动连接。"}
            selected = threads[0]["id"]
            self.selected = selected
            self.save()
        snapshot = self.source.snapshot(selected)
        turn_id = snapshot["turn_id"]
        snapshot["thread_id"] = selected
        now = time.time()
        elapsed = max(0, now - snapshot["started_at"])
        history = self.source.history()
        self.forecast.refresh(history)
        timing = self.forecast.predict(snapshot, elapsed, history)
        operation = self.forecast.operation(snapshot, now)
        snapshot["operation_running"] = operation is not None
        prediction = estimate(snapshot, history, now=now, time_prediction=timing)
        if prediction["status"] != "working":
            operation = None
        raw_local = finish_prediction(prediction)
        method = timing.get("method", "unknown")
        kind = classify(snapshot.get("prompt", ""))
        local, learning = calibrate(self.calibration_records, raw_local, kind, method, elapsed, now)
        if learning["applied"]:
            replace_time(prediction, local, now, prediction["eta_basis"] + " · 已用本机实测校准")
        learning.update(trained_at=self.forecast.trained_at, training=self.forecast.training,
                        observed=self.observed_scores)
        config = self.config.get("assessor", {}) if not self.demo and selected == self.selected else {}
        ai = self.assessor.inspect(config, snapshot, prediction)
        scores = self.advisor_scores.get(self.assessor.provider(config)) if ai["enabled"] else None
        ai["scores"] = scores
        if self.demo:
            ai.update(self.source.demo_advice(snapshot))
        prediction = apply_assessment(prediction, ai, now, use_time=trust_advisor(scores))
        comparison = None
        if self.journal and turn_id:
            try:
                status, ended_at = snapshot["status"], snapshot.get("completed_at")
                if status in TERMINAL:
                    if self.journal.close_turn(selected, turn_id, status, ended_at):
                        self.last_learning = 0
                        self.reconcile(now)
                    comparison = self.journal.comparison(selected, turn_id)
                elif status in ("inProgress", "in_progress"):
                    issued = finish_prediction(prediction)
                    impact = prediction.get("ai_impact")
                    sample = None
                    if issued and prediction["status"] == "working":
                        sample = {
                            "at": now, "displayed": issued, "raw_local": raw_local, "local": local,
                            "local_method": method, "method": method + ("+learned" if learning["applied"] else ""),
                        }
                        if impact and impact["id"]:
                            sample.update(advisor=impact["id"], advisor_used=impact["timing"] == "applied")
                    self.journal.observe(selected, turn_id, kind, snapshot["started_at"], now, sample)
                    if impact and impact["id"]:
                        self.journal.decide(impact["id"], impact)
            except (OSError, sqlite3.Error):
                self.journal_error = True
        learning["observed"] = self.observed_scores
        model = self.forecast.model
        return {
            **prediction, "thread_id": selected, "title": snapshot["title"], "turn_id": turn_id,
            "source": snapshot["source"], "age": round(snapshot["source_age"]), "source_partial": snapshot["partial"],
            "now": now, "topmost": self.topmost, "cooperative": self.config.get("cooperative", True),
            "plugin_installed": (self.home/"plugins"/"cache"/"luma-local"/"luma-progress").is_dir(),
            "size": self.view_size, "reduced_motion": self.config.get("reduced_motion", False),
            "ai": ai, "metrics": model["metrics"] if model else None, "demo": self.demo,
            "learning": learning, "operation": operation, "comparison": comparison,
            "journal_available": self.journal is not None and not self.journal_error,
        }

    def record_advice(self, thread_id, turn_id, provider, event):
        try:
            if self.journal:
                self.journal.assessment_event(thread_id, turn_id, provider, event)
        except (OSError, sqlite3.Error):
            self.journal_error = True

    def reconcile(self, now):
        if not self.journal:
            return
        try:
            if now-self.last_reconciliation >= 12:
                self.last_reconciliation = now
                pending = self.journal.pending()
                if pending:
                    try:
                        outcomes = self.source.outcomes(pending)
                    except (OSError, sqlite3.Error):
                        outcomes = ()  # The selected turn can still close from its rollout.
                    for thread_id, turn_id, status, ended_at in outcomes:
                        if self.journal.close_turn(thread_id, turn_id, status, ended_at):
                            self.last_learning = 0
            if now-self.last_learning >= 30:
                self.calibration_records = self.journal.calibration(now)
                self.observed_scores = self.journal.observed_scores()
                provider = self.assessor.provider(self.config.get("assessor", {}))
                self.advisor_scores = {provider: self.journal.advisor_scores(provider, now)}
                self.last_learning = now
                self.journal_error = False
        except (OSError, sqlite3.Error):
            self.journal_error = True

    def prediction_history(self, thread_id, turn_id=None):
        self.source._thread(thread_id)
        if not self.journal:
            return {"turns": [], "record": None}
        turns = self.journal.recent(thread_id)
        turn_id = turn_id or (turns[0]["turn_id"] if turns else None)
        record = self.journal.review(thread_id, turn_id) if turn_id else None
        if record and record.get("started_at") is None and not record.get("advice"):
            record = None
        return {"turns": turns, "record": record}

    # This bridge is exposed only to the embedded, local UI, never over HTTP.
    def select_thread(self, thread_id):
        self.source._thread(thread_id)
        self.selected = thread_id
        self.save()
        return self.state()

    def toggle_pin(self):
        self.topmost = not self.topmost
        if self.window:
            self.window.on_top = self.topmost
        self.save()
        return self.topmost

    def set_compact(self, compact):
        self.set_size('ribbon' if compact else 'focus')
        return self.compact

    def set_size(self,name,reduce_motion=False):
        if name not in SIZES:raise ValueError('Unknown layout')
        self.view_size=name;self.compact=name=='ribbon'
        self.config['size']=name;self.save()
        if self.window:
            target=round(SIZES[name][1]*self.scale)
            initial=self.window.height
            initial_width=self.window.width
            if reduce_motion or self.config.get('reduced_motion'):
                self.window.resize(round(SIZES[name][0]*self.scale),target)
                return name
            self.resizing=True
            try:
                begin=time.perf_counter()
                while True:
                    t=min(1,(time.perf_counter()-begin)/.28)
                    eased=1-(1-t)**4
                    self.window.resize(round(initial_width+(SIZES[name][0]*self.scale-initial_width)*eased),round(initial+(target-initial)*eased))
                    if t>=1:break
                    time.sleep(1/60)
            finally:
                self.resizing=False
        return name

    def preferences(self):
        cfg=self.config.get('assessor',{})
        return dict(size=self.view_size,reduced_motion=self.config.get('reduced_motion',False),
            assessor={k:cfg.get(k,'' if k in ('endpoint','model') else False) for k in ('enabled','consent','endpoint','model','format')},key_saved=self.assessor.keys.exists())

    def configure_assessor(self,settings):
        if not isinstance(settings,dict):raise ValueError('Invalid settings')
        enabled=bool(settings.get('enabled'))
        endpoint=settings.get('endpoint','').strip()
        if endpoint:endpoint_url(endpoint)
        model=str(settings.get('model','')).strip()[:160]
        if enabled and (not endpoint or not model or settings.get('consent') is not True):
            raise ValueError('启用前请填写服务、模型，并确认发送范围')
        cfg=dict(enabled=enabled,consent=settings.get('consent') is True,endpoint=endpoint,model=model,format='json_object' if settings.get('format')=='json_object' else 'json_schema')
        with self.state_lock:
            key=settings.get('key','')
            if key:
                self.assessor.keys.save(key)
            self.assessor.reset()
            self.config['assessor'] = cfg
            self.last_learning = 0
            self.save()
        return self.preferences()

    def set_reduced_motion(self,value):
        self.config['reduced_motion']=bool(value);self.save();return bool(value)

    def set_cooperative(self, enabled):
        self.config["cooperative"] = bool(enabled)
        self.save()
        return bool(enabled)

    def close(self):
        if self.window:
            self.window.destroy()
        return True

    def minimize(self):
        if self.window:
            self.window.minimize()
        return True


def make_server(app):
    ui = ROOT / "ui"
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def send(self, status, body, mime="application/json; charset=utf-8"):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            # Random capability URL, loopback binding, Host checking, no CORS.
            if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                return self.send(403, {"error": "Invalid host"})
            parsed = urlsplit(self.path)
            prefix = "/" + app.token + "/"
            if not parsed.path.startswith(prefix):
                return self.send(404, {"error": "Not found"})
            name = parsed.path[len(prefix):] or "index.html"
            args = parse_qs(parsed.query)
            try:
                if name == "api/state":
                    return self.send(200, app.state(args.get("thread", [None])[0]))
                if name == "api/threads":
                    rows = app.source.threads(args.get("q", [""])[0])
                    return self.send(200, [{k: row[k] for k in ("id", "title", "updated_at")} for row in rows])
                if name == "api/ping":
                    return self.send(200, {"app": "Luma", "version": "0.3.0"})
                if name == "api/predictions":
                    return self.send(200, app.prediction_history(
                        args.get("thread", [""])[0], args.get("turn", [None])[0]))
                if name == 'api/settings':
                    return self.send(200,app.preferences())
                allowed = {"index.html": "text/html; charset=utf-8", "app.css": "text/css; charset=utf-8", "app.js": "text/javascript; charset=utf-8", "format.js": "text/javascript; charset=utf-8", "review.js": "text/javascript; charset=utf-8"}
                if name in allowed and (ui / name).is_file():
                    return self.send(200, (ui / name).read_bytes(), allowed[name])
                return self.send(404, {"error": "Not found"})
            except (sqlite3.Error, OSError) as error:
                return self.send(503, {"error": "暂时无法读取本地会话", "detail": type(error).__name__, "hint": "面板会自动重试，不会修改 Codex 数据。"})
            except ValueError as error:
                return self.send(404, {"error": str(error)})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    app.server = server
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return f"http://127.0.0.1:{server.server_port}/{app.token}/"


class DesktopBridge:
    """Small explicit JS surface. Never expose the application's internals."""
    def __init__(self, app):
        self._app = app

    def select_thread(self, thread_id):
        return self._app.select_thread(thread_id)

    def toggle_pin(self):
        return self._app.toggle_pin()

    def set_compact(self, compact):
        return self._app.set_compact(compact)

    def set_size(self,name,reduce_motion=False):
        return self._app.set_size(name,reduce_motion)

    def set_reduced_motion(self,value):
        return self._app.set_reduced_motion(value)

    def configure_assessor(self,settings):
        return self._app.configure_assessor(settings)

    def set_cooperative(self, enabled):
        return self._app.set_cooperative(enabled)

    def fit_height(self, height, pixel_ratio=1):
        self._app.scale=max(1.0,min(4.0,float(pixel_ratio)))
        self._app.expanded_height=max(110,min(950,int(height)))
        if self._app.window and not self._app.resizing:
            scale=self._app.scale
            self._app.window.resize(round(SIZES[self._app.view_size][0]*scale),round(self._app.expanded_height*scale))
        return True

    def close(self):
        return self._app.close()

    def minimize(self):
        return self._app.minimize()


def main():
    parser = argparse.ArgumentParser(description="Luma · Codex 对话进度悬浮窗")
    parser.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))))
    parser.add_argument("--data", type=Path, default=ROOT / "data")
    parser.add_argument("--thread")
    parser.add_argument("--serve", action="store_true", help="Browser preview; no native window")
    parser.add_argument('--demo',action='store_true',help='Synthetic showcase; never reads Codex conversations or calls AI')
    args = parser.parse_args()
    global _mutex
    if sys.platform == 'win32' and not args.serve:
        import ctypes
        import hashlib
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel.CreateMutexW.restype = ctypes.c_void_p
        instance = str(ROOT) if not args.demo else str(args.data.resolve()) + "/demo"
        _mutex = kernel.CreateMutexW(None, False, 'Local\\Luma-' + hashlib.sha256(instance.encode()).hexdigest()[:20])
        if ctypes.get_last_error() == 183:
            return
    if args.demo:args.data=args.data/'demo'
    app = Application(args.codex_home, args.data, args.thread,demo=args.demo)
    url = make_server(app)
    (args.data / "runtime.json").write_text(json.dumps({"pid": os.getpid(), "url": url, "started": time.time(), "preview": args.serve}), encoding="utf-8")
    if args.serve:
        print(url, flush=True)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            app.server.shutdown()
        return
    import webview
    width,height=SIZES[app.view_size]
    title = "Luma · 演示" if args.demo else "Luma · Codex 进度"
    app.window = webview.create_window(title, url, js_api=DesktopBridge(app), width=width, height=height, min_size=(320,110), frameless=True, easy_drag=False, resizable=False, on_top=app.topmost, focus=True, background_color="#111111", text_select=False)
    def record_position():
        try:
            app.config.update(x=round(app.window.x), y=round(app.window.y))
            app.save()
        except Exception:
            pass
    app.window.events.closing += record_position
    def native_finish():
        if sys.platform == 'win32':
            try:
                import ctypes
                from System.Drawing import Icon
                if (ROOT/'assets'/'luma.ico').exists():
                    app.window.native.Icon=Icon(str(ROOT/'assets'/'luma.ico'))
                value=ctypes.c_int(2)  # DWMWCP_ROUND: our window only; ignored on Windows 10.
                handle=int(app.window.native.Handle.ToInt64())
                ctypes.windll.dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(handle),33,ctypes.byref(value),ctypes.sizeof(value))
                x,y=app.config.get('x'),app.config.get('y')
                if isinstance(x,int) and isinstance(y,int):
                    ctypes.windll.user32.SetWindowPos(ctypes.c_void_p(handle),None,x,y,0,0,0x15)
            except Exception:
                pass
    app.window.events.shown += native_finish
    try:
        webview.start(gui="edgechromium", private_mode=True, storage_path=str(args.data / "webview"))
    finally:
        app.server.shutdown()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        (ROOT/'data').mkdir(exist_ok=True)
        (ROOT/'data'/'startup-error.log').write_text(traceback.format_exc(),encoding='utf-8')
        if sys.stderr:
            traceback.print_exc()
        raise
