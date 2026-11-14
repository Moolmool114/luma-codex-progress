"""The local record of forecasts as issued and independently observed outcomes."""
from __future__ import annotations

import json
import math
import sqlite3
from contextlib import contextmanager
from statistics import median

TERMINAL = ("completed", "interrupted", "failed")


def error_stats(pairs):
    """Pairs contain one prospective forecast per turn for the comparison."""
    if not pairs:
        return None
    errors = [abs(point - actual) for point, actual in pairs]
    return {
        "count": len(errors),
        "median": round(median(errors), 1),
        "mean": round(sum(errors) / len(errors), 1),
        "within_two_minutes": sum(error <= 120 for error in errors) / len(errors),
    }


class PredictionJournal:
    def __init__(self, data):
        self.path = data / "predictions.sqlite"
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS observed_turns (
                    thread_id TEXT NOT NULL, turn_id TEXT NOT NULL, kind TEXT NOT NULL,
                    started_at REAL NOT NULL, seen_at REAL NOT NULL,
                    ended_at REAL, status TEXT,
                    PRIMARY KEY(thread_id, turn_id)
                );
                CREATE INDEX IF NOT EXISTS observed_outcomes ON observed_turns(status, ended_at);
                CREATE TABLE IF NOT EXISTS predictions (
                    thread_id TEXT NOT NULL, turn_id TEXT NOT NULL, at REAL NOT NULL,
                    sample TEXT NOT NULL,
                    PRIMARY KEY(thread_id, turn_id, at)
                );
                CREATE TABLE IF NOT EXISTS assessments (
                    id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, turn_id TEXT NOT NULL,
                    provider TEXT NOT NULL, at REAL NOT NULL, event TEXT NOT NULL,
                    decision TEXT, latest_decision TEXT
                );
                CREATE INDEX IF NOT EXISTS assessments_turn ON assessments(thread_id, turn_id, at);
                CREATE INDEX IF NOT EXISTS assessments_provider ON assessments(provider, at);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def observe(self, thread_id, turn_id, kind, start, now, sample):
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO observed_turns VALUES(?,?,?,?,?,NULL,NULL)",
                       (thread_id, turn_id, kind, start, now))
            if sample:
                db.execute("""
                    INSERT OR IGNORE INTO predictions
                    SELECT ?,?,?,? WHERE EXISTS(
                        SELECT 1 FROM observed_turns
                        WHERE thread_id=? AND turn_id=? AND ended_at IS NULL
                    )
                """, (thread_id, turn_id, sample["at"], json.dumps(sample), thread_id, turn_id))

    def pending(self):
        with self.connect() as db:
            return [(row["thread_id"], row["turn_id"]) for row in db.execute("""
                SELECT thread_id,turn_id FROM observed_turns WHERE ended_at IS NULL
                ORDER BY seen_at DESC
            """)]

    def close_turn(self, thread_id, turn_id, status, ended_at):
        if status not in TERMINAL or not ended_at or not math.isfinite(ended_at):
            return False
        with self.connect() as db:
            changed = db.execute("""
                UPDATE observed_turns SET ended_at=?, status=?
                WHERE thread_id=? AND turn_id=? AND ended_at IS NULL AND started_at<=?
            """, (ended_at, status, thread_id, turn_id, ended_at))
            if not changed.rowcount:
                return False
            db.execute("DELETE FROM predictions WHERE thread_id=? AND turn_id=? AND at>=?",
                       (thread_id, turn_id, ended_at))
            # Preserve the first, the exact last, and one reading per 45 seconds.
            # Each elapsed-band boundary is a multiple of 45 seconds.
            db.execute("""
                DELETE FROM predictions WHERE thread_id=? AND turn_id=? AND at IN (
                    SELECT at FROM (
                        SELECT at, row_number() OVER (PARTITION BY CAST((at-started_at)/45 AS INTEGER)
                                                       ORDER BY at) AS rank,
                                   max(at) OVER () AS last
                        FROM predictions JOIN observed_turns USING(thread_id,turn_id)
                        WHERE thread_id=? AND turn_id=?
                    ) WHERE rank>1 AND at<>last
                )
            """, (thread_id, turn_id, thread_id, turn_id))
            return True

    def assessment_event(self, thread_id, turn_id, provider, event):
        # Provider is a local digest. The body contains only selected result fields.
        with self.connect() as db:
            db.execute("""
                INSERT INTO assessments VALUES(?,?,?,?,?,?,NULL,NULL)
                ON CONFLICT(id) DO UPDATE SET event=excluded.event
            """, (event["id"], thread_id, turn_id, provider, event["at"], json.dumps(event)))

    def decide(self, assessment_id, decision):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""
                SELECT a.latest_decision FROM assessments a
                LEFT JOIN observed_turns t USING(thread_id,turn_id)
                WHERE a.id=? AND t.ended_at IS NULL
            """, (assessment_id,)).fetchone()
            if row is None:
                return
            previous = json.loads(row[0]) if row[0] else {}
            applications = previous.get("applications", {})
            changes = {
                "progress": ("base_percent", "displayed_percent"),
                "timing": ("local_finish", "displayed_finish"),
            }
            for field, (before, after) in changes.items():
                if decision.get(field) == "applied" and field not in applications:
                    applications[field] = {"at": decision["at"], "before": decision[before],
                                           "after": decision[after]}
            db.execute("""
                UPDATE assessments SET
                  decision=CASE WHEN decision IS NULL
                      OR (json_extract(decision,'$.local_finish') IS NULL AND ? IS NOT NULL)
                      THEN ? ELSE decision END,
                  latest_decision=?
                WHERE id=?
            """, (decision["local_finish"], json.dumps(decision),
                  json.dumps({**decision, "applications": applications}), assessment_id))

    def review(self, thread_id, turn_id):
        with self.connect() as db:
            turn = db.execute("SELECT * FROM observed_turns WHERE thread_id=? AND turn_id=?",
                              (thread_id, turn_id)).fetchone()
            ended = turn["ended_at"] if turn else None
            limit = ended if ended else float("inf")
            key = (thread_id, turn_id, limit)
            checkpoints = [json.loads(row[0]) for row in db.execute("""
                SELECT sample FROM (
                    SELECT sample,at,row_number() OVER (PARTITION BY CAST(at/45 AS INTEGER)
                                                         ORDER BY at DESC) AS rank
                    FROM predictions WHERE thread_id=? AND turn_id=? AND at<?
                ) WHERE rank=1 ORDER BY at DESC LIMIT 40
            """, key)]
            first = db.execute("""
                SELECT sample FROM predictions WHERE thread_id=? AND turn_id=? AND at<?
                ORDER BY at LIMIT 1
            """, key).fetchone()
            advice = []
            for row in db.execute("""
                SELECT event,decision,latest_decision FROM assessments WHERE thread_id=? AND turn_id=?
                ORDER BY at DESC LIMIT 50
            """, (thread_id, turn_id)):
                event = json.loads(row["event"])
                decision = json.loads(row["decision"]) if row["decision"] else None
                latest_decision = json.loads(row["latest_decision"]) if row["latest_decision"] else None
                applications = (latest_decision or {}).get("applications", {})
                if ended:
                    applications = {key: value for key, value in applications.items() if value["at"] < ended}
                if ended and latest_decision and latest_decision["at"] >= ended:
                    latest_decision = None
                if ended and decision and decision["at"] >= ended:
                    decision = None
                if ended and event.get("received_at", 0) >= ended:
                    event["after_end"] = True
                advice.append({**event, "decision": latest_decision or decision,
                               "comparison": decision, "applications": applications})

        if not turn:
            return {"turn_id": turn_id, "checkpoints": [], "advice": advice}
        initial = json.loads(first[0]) if first else None
        latest = checkpoints[0] if checkpoints else None
        return {
            "turn_id": turn_id, "started_at": turn["started_at"], "ended_at": ended,
            "status": turn["status"], "first": initial, "last": latest,
            "checkpoints": checkpoints, "advice": advice,
        }

    def recent(self, thread_id):
        with self.connect() as db:
            return [dict(row) for row in db.execute("""
                SELECT turn_id,started_at,ended_at,status FROM observed_turns
                WHERE thread_id=? ORDER BY started_at DESC LIMIT 40
            """, (thread_id,))]

    def comparison(self, thread_id, turn_id):
        with self.connect() as db:
            row = db.execute("""
                SELECT ended_at,status,
                       (SELECT sample FROM predictions p WHERE p.thread_id=t.thread_id
                        AND p.turn_id=t.turn_id ORDER BY at LIMIT 1) AS first,
                       (SELECT sample FROM predictions p WHERE p.thread_id=t.thread_id
                        AND p.turn_id=t.turn_id ORDER BY at DESC LIMIT 1) AS last
                FROM observed_turns t WHERE thread_id=? AND turn_id=? AND ended_at IS NOT NULL
            """, (thread_id, turn_id)).fetchone()
        if not row:
            return None
        return {"ended_at": row["ended_at"], "status": row["status"],
                "first": json.loads(row["first"]) if row["first"] else None,
                "last": json.loads(row["last"]) if row["last"] else None}

    def calibration(self, now):
        """One checkpoint per prior completed turn and elapsed band."""
        with self.connect() as db:
            rows = db.execute("""
                SELECT t.kind,t.started_at,t.ended_at,p.thread_id,p.turn_id,p.sample
                FROM (SELECT * FROM observed_turns WHERE status='completed' AND ended_at<?
                      ORDER BY ended_at DESC LIMIT 180) t
                JOIN predictions p ON p.thread_id=t.thread_id AND p.turn_id=t.turn_id
                WHERE p.at<t.ended_at ORDER BY t.ended_at DESC,p.at LIMIT 12000
            """, (now,)).fetchall()
        seen = set()
        records = []
        for row in rows:
            sample = json.loads(row["sample"])
            if not sample.get("raw_local"):
                continue
            band = forecast_age_band(sample["at"] - row["started_at"])
            key = (row["thread_id"], row["turn_id"], band)
            if key not in seen:
                seen.add(key)
                records.append({
                    "kind": row["kind"], "band": band, "actual": row["ended_at"],
                    "turn": key[:2], **sample,
                })
        return records

    def advisor_scores(self, provider, now):
        """Compare AI and the local forecast first paired in each successful turn."""
        with self.connect() as db:
            rows = db.execute("""
                SELECT a.thread_id,a.turn_id,a.event,a.decision,t.ended_at
                FROM assessments a JOIN observed_turns t USING (thread_id,turn_id)
                WHERE a.provider=? AND a.decision IS NOT NULL AND t.status='completed' AND t.ended_at<?
                ORDER BY t.ended_at DESC, a.at LIMIT 800
            """, (provider, now)).fetchall()
        seen = set()
        groups = {"local": [], "ai": [], "blend": []}
        for row in rows:
            decision = json.loads(row["decision"])
            event = json.loads(row["event"])
            actual = row["ended_at"]
            key = (row["thread_id"], row["turn_id"])
            if key in seen or not decision.get("local_finish") or not decision.get("blend_finish"):
                continue
            if not event.get("received_at") or max(decision["at"], event["received_at"]) >= actual:
                continue
            seen.add(key)
            groups["local"].append((decision["local_finish"], actual))
            groups["ai"].append((decision["ai_finish"], actual))
            groups["blend"].append((decision["blend_finish"], actual))
            if len(seen) == 60:
                break
        return {name: error_stats(values) for name, values in groups.items()}

    def observed_scores(self):
        with self.connect() as db:
            rows = db.execute("""
                SELECT t.ended_at,(SELECT p.sample FROM predictions p
                    WHERE p.thread_id=t.thread_id AND p.turn_id=t.turn_id AND p.at<t.ended_at
                    ORDER BY p.at LIMIT 1) AS first
                FROM observed_turns t WHERE t.status='completed'
                ORDER BY t.ended_at DESC LIMIT 180
            """).fetchall()
        pairs = []
        covered = 0
        for row in rows:
            if not row["first"]:
                continue
            first = json.loads(row["first"])["displayed"]
            actual = row["ended_at"]
            pairs.append((first["finish"], actual))
            covered += first["low"] <= actual <= first["high"]
        stats = error_stats(pairs)
        if stats:
            stats["coverage"] = covered / len(pairs)
        return stats


def forecast_age_band(elapsed):
    if elapsed < 90:
        return "early"
    if elapsed < 450:
        return "middle"
    return "long"
