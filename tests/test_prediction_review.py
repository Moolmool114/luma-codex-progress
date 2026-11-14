import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app import Application
from pulse.assessor import apply_assessment
from pulse.forecast import phase_features, samples
from pulse.journal import PredictionJournal
from pulse.learning import calibrate, trust_advisor
from pulse.operations import operation_remaining
from pulse.source import CodexSource


def forecast(at, finish):
    bounds = {"finish": finish, "low": finish-30, "high": finish+30}
    return {"at": at, "local_method": "duration", "method": "duration",
            "raw_local": bounds, "displayed": bounds}


class PredictionReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.journal = PredictionJournal(self.data)

    def test_reopen_keeps_first_last_and_excludes_lagged_forecast(self):
        for at in (101, 104, 145, 150, 170, 201, 204):
            self.journal.observe("chat", "turn", "general", 100, at, forecast(at, at+35))
        reopened = PredictionJournal(self.data)
        self.assertTrue(reopened.close_turn("chat", "turn", "completed", 200))
        self.assertFalse(reopened.close_turn("chat", "turn", "completed", 205))
        record = reopened.review("chat", "turn")
        self.assertEqual(record["first"]["at"], 101)
        self.assertEqual(record["last"]["at"], 170)
        self.assertEqual(record["ended_at"], 200)
        self.assertEqual(reopened.observed_scores()["count"], 1)
        self.assertEqual(len(reopened.calibration(250)), 1)
        self.assertEqual(reopened.review("other-chat", "turn")["checkpoints"], [])

    def test_switching_chat_still_closes_the_previous_turn(self):
        with closing(sqlite3.connect(self.data / "thread_history_1.sqlite")) as db, db:
            db.execute("CREATE TABLE thread_turns(thread_id,turn_id,status,completed_at)")
            db.execute("INSERT INTO thread_turns VALUES('previous-chat','turn','completed',200)")
        self.journal.observe("previous-chat", "turn", "general", 100, 140, forecast(140, 200))
        app = Application(self.data, self.data, demo=True)
        app.source = CodexSource(self.data, self.data)
        app.selected = "another-chat"
        app.reconcile(10000)
        self.assertEqual(app.journal.review("previous-chat", "turn")["ended_at"], 200)

    def test_projection_fallback_can_find_a_superseded_turn(self):
        rollout = self.data / "session.jsonl"
        rollout.write_text("\n".join(json.dumps({
            "type": "event_msg", "timestamp": "2026-11-14T12:00:00Z", "payload": payload,
        }) for payload in [
            {"type": "task_started", "turn_id": "one", "started_at": 100},
            {"type": "task_complete", "turn_id": "one", "completed_at": 200},
            {"type": "task_started", "turn_id": "two", "started_at": 205},
        ]), encoding="utf-8")
        with closing(sqlite3.connect(self.data / "state_5.sqlite")) as db, db:
            db.execute("CREATE TABLE threads(id,rollout_path)")
            db.execute("INSERT INTO threads VALUES(?,?)", ("chat", str(rollout)))
        source = CodexSource(self.data, self.data)
        self.assertEqual(source.outcomes([("chat", "one")]), [("chat", "one", "completed", 200)])

    def test_tool_start_from_rollout_is_available_before_projection_completion(self):
        rollout = self.data/"session.jsonl"
        state = {"started_at": 1794657600, "turn_id": "two", "items": []}
        begin = {"type": "response_item", "timestamp": "2026-11-14T12:00:05Z", "payload": {
            "type": "function_call", "call_id": "operation", "name": "search", "namespace": "mcp__fixture",
            "arguments": "private operation arguments",
        }}
        rollout.write_text(json.dumps(begin)+"\n", encoding="utf-8")
        source = CodexSource(self.data, self.data)
        running = source._live_tool_calls(rollout, state)
        self.assertEqual([row["id"] for row in running], ["operation"])
        self.assertNotIn("arguments", running[0])
        with rollout.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"type": "response_item", "timestamp": "2026-11-14T12:00:08Z",
                                     "payload": {"type": "function_call_output", "call_id": "operation"}})+"\n")
        self.assertEqual(source._live_tool_calls(rollout, state), [])

    def test_advisor_scoring_uses_one_pair_per_successful_turn(self):
        for index, status in enumerate(("completed", "interrupted", "completed")):
            turn = str(index)
            self.journal.observe("chat", turn, "general", 100, 120, forecast(120, 230))
            for call in range(3):
                assessment_id = turn + str(call)
                self.journal.assessment_event("chat", turn, "provider", {
                    "id": assessment_id, "at": 120+call, "received_at": 125 if index != 2 else 230,
                    "state": "ready",
                })
                self.journal.decide(assessment_id, {
                    "at": 130+call, "local_finish": 230, "ai_finish": 210, "blend_finish": 225,
                })
            self.journal.close_turn("chat", turn, status, 200)
        scores = self.journal.advisor_scores("provider", 240)
        self.assertEqual(scores["local"]["count"], 1)
        self.assertEqual(scores["local"]["median"], 30)
        self.assertEqual(scores["ai"]["median"], 10)
        self.assertEqual(scores["blend"]["median"], 25)
        self.assertFalse(trust_advisor(scores))

    def test_advice_keeps_earlier_influence_after_it_expires(self):
        self.journal.observe("chat", "turn", "general", 100, 120, forecast(120, 240))
        self.journal.assessment_event("chat", "turn", "provider", {
            "id": "advice", "at": 120, "received_at": 125, "state": "ready",
        })
        self.journal.decide("advice", {
            "at": 130, "local_finish": 240, "ai_finish": 210, "blend_finish": 233,
            "displayed_finish": 233, "timing": "applied",
        })
        self.journal.decide("advice", {"at": 215, "local_finish": 240, "timing": "overdue"})
        self.journal.close_turn("chat", "turn", "completed", 230)
        advice = self.journal.review("chat", "turn")["advice"][0]
        self.assertEqual(advice["decision"]["timing"], "overdue")
        self.assertEqual(advice["applications"]["timing"]["after"], 233)
        self.assertEqual(advice["comparison"]["at"], 130)

    def test_validated_calibration_corrects_systematic_bias(self):
        records = [{**forecast(100, 200), "kind": "build", "band": "early", "actual": 250}
                   for _ in range(24)]
        raw = forecast(800, 900)["raw_local"]
        adjusted, info = calibrate(records, raw, "build", "duration", 45, 800)
        self.assertTrue(info["applied"])
        self.assertEqual(adjusted["finish"], 950)
        self.assertEqual(info["learned_error"], 0)

    def test_recent_failures_in_validation_keep_original_estimate(self):
        records = [{**forecast(100, 200), "kind": "build", "band": "early", "actual": outcome}
                   for outcome in [200]*8+[250]*16]
        raw = forecast(800, 900)["raw_local"]
        adjusted, info = calibrate(records, raw, "build", "duration", 45, 800)
        self.assertFalse(info["applied"])
        self.assertEqual(adjusted, raw)

    def test_ai_shows_an_experiment_before_using_it_for_eta(self):
        base = {
            "status": "working", "finish_at": 1500, "finish_range": [1400, 1750],
            "percent": 40, "confirmed_percent": 40, "range": [35, 78], "has_checklist": True,
        }
        status = {"assessment": {
            "percent": 55, "at": 1000, "remaining_seconds": [180, 300, 400],
            "explanation": "Evidence found",
        }}
        result = apply_assessment(base, status, 1050)
        self.assertEqual(result["finish_at"], 1500)
        self.assertEqual(result["percent"], 55)
        self.assertEqual(result["ai_impact"]["timing"], "observing")
        self.assertEqual(result["ai_impact"]["blend_finish"], 1450)
        combined = apply_assessment(base, status, 1050, use_time=True)
        self.assertEqual(combined["finish_at"], 1450)
        self.assertEqual(combined["ai_impact"]["timing"], "applied")
        expired = apply_assessment(base, status, 1350, use_time=True)
        self.assertEqual(expired["finish_at"], 1500)
        self.assertIsNone(expired["ai_impact"]["blend_finish"])

    def test_operation_profile_conditions_on_elapsed_age(self):
        profile = {"command": [2]*30+[20]*20}
        prediction = operation_remaining(profile, "command", 10)
        self.assertEqual(prediction["median"], 10)
        self.assertEqual(prediction["count"], 20)
        self.assertIsNone(operation_remaining(profile, "command", 20))

    def test_fixed_checkpoints_cannot_see_future_steering_or_operation_outcomes(self):
        command = {"type": "commandExecution", "started": 102, "finished": 130, "at": 130, "exitCode": 1}
        running = {**command, "finished": None, "at": 102, "exitCode": None}
        run = {"prompt": "question", "requests": [(100, "question"), (130, "implement app")],
               "items": [command], "started_at": 100, "seconds": 60}
        profiles = {"command": [20]*20}
        observed, _ = samples([run], lambda p, i, s, e: phase_features(p, i, s, e, profiles), fixed=True)
        live = phase_features("question", [running], 100, 5, profiles)
        self.assertEqual(observed[0], live)


if __name__ == "__main__":
    unittest.main()
