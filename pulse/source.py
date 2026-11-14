from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path


VISIBLE_TYPES = ("userMessage", "agentMessage", "commandExecution", "fileChange", "mcpToolCall", "plan", "webSearch", "imageGeneration")


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default


@contextmanager
def connect_readonly(path: Path):
    # Do not use immutable=1: it would hide the live WAL and return stale data.
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        yield connection
    finally:
        connection.close()


def message_text(item):
    if item.get("type") == "agentMessage":
        return item.get("text", "")
    content = item.get("content", [])
    return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") in ("text", "input_text", "output_text")) if isinstance(content, list) else ""


class CodexSource:
    def __init__(self, home: Path, data: Path):
        self.home, self.data = home, data
        self._history = []
        self._history_at = 0
        self._lock = threading.RLock()

    def threads(self, search="", limit=50):
        with connect_readonly(self.home / "state_5.sqlite") as c:
            cols = {r[1] for r in c.execute("PRAGMA table_info(threads)")}
            name = "COALESCE(NULLIF(name,''),title)" if "name" in cols else "title"
            query = f"SELECT id,{name} AS title,cwd,updated_at,rollout_path FROM threads WHERE archived=0"
            args = []
            if "agent_role" in cols:
                query += " AND agent_role IS NULL"
            if "thread_source" in cols:
                query += " AND COALESCE(thread_source,'')<>'subagent'"
            if search:
                query += f" AND ({name} LIKE ? OR id=?)"
                args += ["%" + search[:100] + "%", search]
            query += " ORDER BY updated_at DESC LIMIT ?"
            args.append(min(max(limit, 1), 100))
            return [dict(r) for r in c.execute(query, args)]

    def _thread(self, thread_id):
        with connect_readonly(self.home / "state_5.sqlite") as c:
            row = c.execute("SELECT * FROM threads WHERE id=?", (thread_id,)).fetchone()
            if not row:
                raise ValueError("没有找到这条本地对话")
            return dict(row)

    def snapshot(self, thread_id):
        with self._lock:
            thread = self._thread(thread_id)
            path = Path(thread["rollout_path"])
            try:
                state = self._from_projection(thread_id)
            except (sqlite3.Error, OSError):
                state = None
            # Legacy logs and projection lag are handled by a bounded, read-only adapter.
            if not state:
                state = self._from_rollout(path)
            state.update(thread_id=thread_id, title=thread.get("name") or thread.get("title") or "未命名对话", cwd=thread.get("cwd", ""))
            state["hook"] = read_json(self.data / "events" / f"{thread_id}.json", {})
            state["checklist"] = read_json(self.data / "checklists" / f"{thread_id}.json", {})
            state["source_age"] = max(0, time.time() - state.get("last_activity", time.time()))
            return state

    def _from_projection(self, thread_id):
        with connect_readonly(self.home / "thread_history_1.sqlite") as c:
            turn = c.execute("SELECT * FROM thread_turns WHERE thread_id=? ORDER BY rollout_ordinal DESC LIMIT 1", (thread_id,)).fetchone()
            if not turn:
                return None
            turn = dict(turn)
            marks = ",".join("?" for _ in VISIBLE_TYPES)
            # Project public fields in SQLite. Large tool results and diffs never
            # cross into the observer process, and reasoning rows are excluded.
            projection = """json_object(
              'type',item_type,'id',item_id,
              'status',json_extract(item_json,'$.status'),
              'tool',json_extract(item_json,'$.tool'),
              'command',substr(json_extract(item_json,'$.command'),1,6000),
              'exitCode',json_extract(item_json,'$.exitCode'),
              'test_output_failed',CASE WHEN instr(COALESCE(json_extract(item_json,'$.aggregatedOutput'),''),'FAILED (')>0 OR instr(COALESCE(json_extract(item_json,'$.aggregatedOutput'),''),'Build FAILED')>0 THEN 1 ELSE 0 END,
              'phase',json_extract(item_json,'$.phase'),
              'text',substr(json_extract(item_json,'$.text'),1,10000),
              'content',CASE WHEN item_type='userMessage' THEN json_array(json_object('type','text','text',substr(json_extract(item_json,'$.content[0].text'),1,6000))) ELSE json_array() END,
              'arguments',CASE WHEN json_extract(item_json,'$.tool') LIKE '%update_plan%' THEN json_extract(item_json,'$.arguments') ELSE json_object() END
            ) AS item_json"""
            rows = c.execute(f"SELECT {projection},created_at_ms,started_at_ms,completed_at_ms FROM thread_items WHERE thread_id=? AND turn_id=? AND item_type IN ({marks}) ORDER BY rollout_ordinal DESC LIMIT 600", (thread_id, turn["turn_id"], *VISIBLE_TYPES)).fetchall()
            items = []
            for row in reversed(rows):
                try:
                    item = json.loads(row["item_json"])
                except (ValueError, TypeError):
                    continue
                # No reasoning content is loaded, displayed or used for prediction.
                item["at"] = (row["completed_at_ms"] or row["started_at_ms"] or row["created_at_ms"]) / 1000
                item["started"] = (row["started_at_ms"] or row["created_at_ms"]) / 1000
                item['finished'] = row['completed_at_ms']/1000 if row['completed_at_ms'] else None
                items.append(item)
            prompt = '\n\n后续说明：\n'.join(message_text(i) for i in items if i['type']=='userMessage')
            final = next((message_text(i) for i in reversed(items) if i["type"] == "agentMessage" and i.get("phase") == "final_answer"), "")
            return {
                "turn_id": turn["turn_id"], "status": turn["status"],
                "started_at": turn["started_at"] or min((i["at"] for i in items), default=time.time()),
                "completed_at": turn["completed_at"], "prompt": prompt, "final": final,
                "items": items, "last_activity": max((i["at"] for i in items), default=turn["started_at"] or time.time()),
                "partial": len(rows) >= 600, "source": "本地会话数据库",
            }

    def _from_rollout(self, path):
        state = {"turn_id": "", "status": "unknown", "started_at": time.time(), "completed_at": None, "prompt": "", "final": "", "items": [], "last_activity": path.stat().st_mtime, "partial": True, "source": "本地执行记录"}
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 16 * 1024 * 1024))
            if stream.tell():
                stream.readline()
            for line in stream:
                try:
                    record = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                from datetime import datetime
                try:
                    at = datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00")).timestamp()
                except (ValueError, KeyError):
                    continue
                p = record.get("payload", {})
                if record.get("type") == "event_msg":
                    event = p.get("type")
                    if event == "task_started":
                        state.update(turn_id=p.get("turn_id", ""), status="inProgress", started_at=at, completed_at=None, prompt="", final="", items=[], partial=False)
                    elif event == "task_complete":
                        state.update(status="completed", completed_at=at, final=p.get("last_agent_message", ""))
                    elif event in ("turn_aborted", "task_failed"):
                        state.update(status="interrupted" if event == "turn_aborted" else "failed", completed_at=at)
                    elif event == "item_completed":
                        item = p.get("item", {})
                        if item.get("type") in VISIBLE_TYPES:
                            state["items"].append({**item, "at": at, "started": at})
                elif record.get("type") == "response_item" and p.get("type") == "message":
                    role = p.get("role")
                    text = "\n".join(x.get("text", "") for x in p.get("content", []) if x.get("type") in ("input_text", "output_text"))
                    if role == "user":
                        state["prompt"] = text
                    elif role == "assistant":
                        state["items"].append({"type": "agentMessage", "text": text, "phase": p.get("phase"), "id": p.get("id"), "at": at})
                state["last_activity"] = at
        return state

    def history(self):
        with self._lock:
            if time.time() - self._history_at < 120:
                return self._history
            try:
                # Internal approval reviewers and worker agents are not ordinary
                # user requests and must not contaminate duration calibration.
                with connect_readonly(self.home/'state_5.sqlite') as state_db:
                    columns={r[1] for r in state_db.execute('PRAGMA table_info(threads)')}
                    where="WHERE agent_role IS NULL" if 'agent_role' in columns else 'WHERE 1=1'
                    if 'thread_source' in columns:
                        where+=" AND COALESCE(thread_source,'')<>'subagent'"
                    ids=[r[0] for r in state_db.execute(f'SELECT id FROM threads {where} ORDER BY updated_at DESC LIMIT 500')]
                if not ids:
                    return []
                with connect_readonly(self.home / "thread_history_1.sqlite") as c:
                    slots=','.join('?' for _ in ids)
                    rows = c.execute(f"""SELECT t.thread_id,t.turn_id,t.started_at,t.duration_ms,substr(json_extract(i.item_json,'$.content[0].text'),1,2000) AS prompt FROM thread_turns t
                      JOIN thread_items i ON i.thread_id=t.thread_id AND i.item_id=t.first_user_item_id
                      WHERE t.status='completed' AND t.duration_ms BETWEEN 15000 AND 21600000
                      AND t.thread_id IN ({slots})
                      ORDER BY t.started_at DESC LIMIT 300""",ids).fetchall()
                    self._history = [{"seconds": r["duration_ms"] / 1000, "prompt": r['prompt'] or '',"thread_id":r['thread_id'],"turn_id":r['turn_id'],"started_at":r['started_at'] or 0} for r in rows]
            except (sqlite3.Error, OSError, ValueError):
                self._history = []
            self._history_at = time.time()
            return self._history
