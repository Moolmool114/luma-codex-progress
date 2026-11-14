import copy
import unittest
from pulse.estimator import estimate, duration_forecast

NOW = 2000
TID = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
def snapshot(**changes):
    base = dict(turn_id=TID, status='inProgress',started_at=1000,completed_at=None,prompt='实现一个插件',final='',items=[],checklist={},hook={},source_age=0,partial=False)
    base.update(changes)
    return base

def checklist(statuses=('completed','in_progress','pending')):
    return dict(turnId=TID,revision=1,steps=[dict(id=str(i),label=str(i),weight=w,status=s,evidence='检查通过' if s=='completed' else '') for i,(w,s) in enumerate(zip([2000,6000,2000],statuses))])

class EstimatorTests(unittest.TestCase):
    def test_clock_alone_never_advances_percent(self):
        s=snapshot(items=[dict(type='agentMessage',phase='commentary',text='正在实施',at=1100)])
        self.assertEqual(estimate(s,now=1200)['percent'],estimate(s,now=3000)['percent'])

    def test_weights_are_not_step_counts(self):
        e=estimate(snapshot(checklist=checklist()),now=NOW)
        self.assertEqual(e['percent'],20)  # No arbitrary credit for merely starting a step.
        self.assertEqual(e['basis'],'清单加权')

    def test_finished_checklist_capped_until_turn_ends(self):
        e=estimate(snapshot(checklist=checklist(('completed',)*3)),now=NOW)
        self.assertEqual(e['percent'],95)

    def test_old_turn_checklist_not_used(self):
        c=checklist();c['turnId']='other'
        self.assertEqual(estimate(snapshot(checklist=c),now=NOW)['basis'],'活动推断')

    def test_rework_can_lower_progress(self):
        before=estimate(snapshot(checklist=checklist(('completed','completed','in_progress'))),now=NOW)
        after=estimate(snapshot(checklist=checklist(('completed','in_progress','pending'))),now=NOW)
        self.assertLess(after['percent'],before['percent'])

    def test_interrupt_never_means_done(self):
        e=estimate(snapshot(status='interrupted',completed_at=1800),now=NOW)
        self.assertLess(e['percent'],100);self.assertEqual(e['status'],'interrupted')

    def test_failure_never_means_done(self):
        e=estimate(snapshot(status='failed',completed_at=1800),now=NOW)
        self.assertLess(e['percent'],100);self.assertIsNone(e['eta'])

    def test_partial_final_keeps_unfinished_state(self):
        e=estimate(snapshot(status='completed',final='任务尚未完成，需要后续继续',completed_at=1800),now=NOW)
        self.assertLess(e['percent'],100);self.assertEqual(e['status'],'partial')

    def test_successful_turn_reports_ended_not_verified_goal(self):
        e=estimate(snapshot(status='completed',final='已交付',completed_at=1800),now=NOW)
        self.assertEqual(e['percent'],100);self.assertIn('本轮',e['reason'])

    def test_waiting_suspends_eta(self):
        e=estimate(snapshot(hook=dict(turnId=TID,event='PermissionRequest',at=NOW*1000),checklist=checklist()),now=NOW)
        self.assertEqual(e['status'],'waiting');self.assertIsNone(e['eta'])

    def test_old_turn_approval_ignored(self):
        e=estimate(snapshot(hook=dict(turnId='old',event='PermissionRequest',at=NOW*1000)),now=NOW)
        self.assertEqual(e['status'],'working')

    def test_later_edit_invalidates_test_evidence(self):
        items=[dict(type='fileChange',status='completed',at=1100),dict(type='commandExecution',command='python -m unittest',status='completed',exitCode=0,at=1200)]
        before=estimate(snapshot(items=items),now=NOW)
        after=estimate(snapshot(items=items+[dict(type='fileChange',status='completed',at=1300)]),now=NOW)
        self.assertLess(after['percent'],before['percent'])

    def test_no_history_means_no_invented_eta(self):
        e=estimate(snapshot(),now=NOW)
        self.assertIsNone(e['eta']);self.assertEqual(e['history_count'],0)

    def test_eta_is_positive_after_overrunning_historical_runs(self):
        h=[dict(prompt='实现代码',seconds=s) for s in (60,70,80,90,100,110,120)]
        value,count=duration_forecast(h,'build',500)
        self.assertEqual(count,7);self.assertTrue(0<value[0]<value[1]<value[2])

    def test_reasoning_is_not_used_as_progress_evidence(self):
        a=estimate(snapshot(),now=NOW)
        b=estimate(snapshot(items=[dict(type='reasoning',summary=['already finished'],content=['secret'],at=1400)]),now=NOW)
        self.assertEqual(a['percent'],b['percent']);self.assertEqual(b['activities'],[])

    def test_estimate_stays_inside_its_range(self):
        for statuses in [('completed',)*3,('pending',)*3,('in_progress',)*3]:
            e=estimate(snapshot(checklist=checklist(statuses)),now=NOW)
            self.assertLessEqual(e['range'][0],e['percent']);self.assertGreaterEqual(e['range'][1],e['percent'])

    def test_unreliable_time_model_is_not_replaced_by_clock_guess(self):
        h=[dict(prompt='实现代码',seconds=s) for s in (60,70,80,90,100,110,120)]
        e=estimate(snapshot(),h,now=NOW,time_prediction={'available':False,'reason':'未通过历史校验'})
        self.assertIsNone(e['eta']);self.assertIn('未通过',e['eta_basis'])

    def test_validated_time_range_is_used(self):
        e=estimate(snapshot(),now=NOW,time_prediction={'available':True,'range':[50,120],'reason':'活动模型'})
        self.assertEqual(e['eta'],[50,120])

if __name__=='__main__':unittest.main()
