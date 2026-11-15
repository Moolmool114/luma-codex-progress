import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from pulse.source import connect_readonly, CodexSource

class SourceTests(unittest.TestCase):
    def test_connection_cannot_write(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'db.sqlite'
            with closing(sqlite3.connect(p)) as c:
                c.execute('create table x(v)');c.commit()
            with connect_readonly(p) as c:
                with self.assertRaises(sqlite3.OperationalError):c.execute('insert into x values(1)')

    def test_rollout_resets_state_at_turn_boundary(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'session.jsonl'
            events=[('task_started','one'),('task_complete','one'),('task_started','two')]
            p.write_text('\n'.join(json.dumps(dict(type='event_msg',timestamp='2026-11-14T12:00:00Z',payload=dict(type=t,turn_id=i))) for t,i in events),encoding='utf-8')
            state=CodexSource(Path(d),Path(d))._from_rollout(p)
            self.assertEqual(state['turn_id'],'two');self.assertEqual(state['status'],'inProgress');self.assertIsNone(state['completed_at'])

    def test_truncated_json_line_does_not_end_task(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'session.jsonl'
            p.write_text(json.dumps(dict(type='event_msg',timestamp='2026-11-14T12:00:00Z',payload=dict(type='task_started',turn_id='two')))+'\n{"incomplete":',encoding='utf-8')
            state=CodexSource(Path(d),Path(d))._from_rollout(p)
            self.assertEqual(state['status'],'inProgress')

    def test_internal_review_workers_not_offered_as_user_chats(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'state_5.sqlite'
            with closing(sqlite3.connect(p)) as c:
                c.execute('CREATE TABLE threads(id,title,cwd,updated_at,rollout_path,archived,agent_role,thread_source)')
                c.executemany('INSERT INTO threads VALUES(?,?,?,?,?,?,?,?)',[
                    ('user','My request','.',1,'x',0,None,'user'),
                    ('worker','Guardian review','.',2,'x',0,None,'subagent')])
                c.commit()
            rows=CodexSource(Path(d),Path(d)).threads()
            self.assertEqual([r['id'] for r in rows],['user'])

    def test_short_user_turns_count_and_guardian_reviews_do_not_train(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            with closing(sqlite3.connect(home/"state_5.sqlite")) as db, db:
                db.execute("CREATE TABLE threads(id,cwd,updated_at,agent_role,thread_source)")
                db.executemany("INSERT INTO threads VALUES(?,'.',100,NULL,?)",
                               [("user", "user"), ("reviewer", "guardian_review")])
            with closing(sqlite3.connect(home/"thread_history_1.sqlite")) as db, db:
                db.execute("CREATE TABLE thread_turns(thread_id,turn_id,started_at,completed_at,duration_ms,status,first_user_item_id)")
                db.execute("CREATE TABLE thread_items(thread_id,item_id,item_json)")
                db.executemany("INSERT INTO thread_turns VALUES(?,?,?,?,?,'completed','req')",
                               [("user","short",100,103,3000), ("user","future",110,125,15000),
                                ("reviewer","approval",100,120,20000)])
                db.executemany("INSERT INTO thread_items VALUES(?,'req',?)",
                               [(thread, '{"content":[{"text":"hello"}]}') for thread in ("user", "reviewer")])
            history = CodexSource(home, home).history(122)
            self.assertEqual([(r["turn_id"], r["seconds"]) for r in history], [("short", 3)])

if __name__=='__main__':unittest.main()
