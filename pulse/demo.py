"""Synthetic showcase data. No private database access or network requests."""
import time

SESSION='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
TURN='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
REVIEWED='cccccccc-cccc-cccc-cccc-cccccccccccc'

class DemoSource:
    def __init__(self):self.start=time.time()-18*60-32
    def threads(self,query='',limit=80):
        return [{'id':SESSION,'title':'为桌面应用打磨交互细节','updated_at':time.time()}] if not query or query in '为桌面应用打磨交互细节' else []
    def _thread(self,identifier):
        if identifier!=SESSION:raise ValueError('Unknown demo conversation')
    def history(self):return []
    def outcomes(self, keys):return []
    def demo_advice(self, snapshot):
        issued = self.start + 18*60
        return {"enabled": True, "assessment": {
            "at": issued, "age": time.time()-issued, "percent": 72, "id": "demo-live",
            "remaining_seconds": [420, 780, 1560], "evidence_ids": ["demo-3"],
            "explanation": "四种尺寸已联通，剩余工作集中在交互核查与发布。",
        }}
    def snapshot(self,identifier):
        self._thread(identifier);now=time.time()
        steps=[
            dict(id='audit',label='梳理界面与信息层级',weight=1200,status='completed',evidence='完成布局审查'),
            dict(id='foundation',label='建立字体与色彩规范',weight=1800,status='completed',evidence='已实现视觉变量'),
            dict(id='layout',label='适配四种面板尺寸',weight=3400,status='in_progress',completedUnits=4,totalUnits=8,evidence='四种布局已实现，继续逐一核查交互'),
            dict(id='motion',label='打磨数字与状态动画',weight=1800,status='completed',evidence='数字变化与减少动态效果已接通'),
            dict(id='verify',label='核查边界与无障碍操作',weight=1400,status='pending'),
            dict(id='release',label='整理使用说明与发布',weight=400,status='pending')]
        return dict(turn_id=TURN,title='为桌面应用打磨交互细节',status='inProgress',started_at=self.start,completed_at=None,prompt='优化桌面应用的布局、动画与无障碍操作。',final='',source='合成演示数据',source_age=2,partial=False,
            checklist=dict(turnId=TURN,revision=4,steps=steps,current='核查专注布局中的信息密度'),
            hook=dict(turnId=TURN,event='PostToolUse',at=now*1000),items=[
                dict(id='demo-1',type='fileChange',status='completed',at=now-155),
                dict(id='demo-2',type='commandExecution',command='node --test',status='completed',exitCode=0,at=now-90),
                dict(id='demo-3',type='mcpToolCall',tool='browser_screenshot',status='completed',at=now-24)])

class DemoForecast:
    model=None
    trained_at=None
    training=False
    def refresh(self, history):pass
    def operation(self, snapshot, now):
        return {"label": "界面操作", "range": [8, 42], "median": 18, "count": 24}
    def predict(self,snapshot,elapsed,history=()):return dict(available=True,range=[7*60,22*60],median=14*60,method="duration",reason="合成演示")


def seed_review(journal, source):
    """An illustrative completed turn, stored only in the demo profile."""
    if journal.comparison(SESSION, REVIEWED) is None:
        seed_completed_review(journal, source.start - 65*60)
    live = source.demo_advice({})["assessment"]
    journal.assessment_event(SESSION, TURN, "demo", {
        **live, "state": "ready", "received_at": live["at"]+3,
    })


def seed_completed_review(journal, start):
    end = start + 32*60
    for elapsed, estimate in [(45, 23*60), (19*60, 34*60), (26*60, 30*60+40)]:
        at = start + elapsed
        finish = start + estimate
        point = {"finish": finish, "low": finish-4*60, "high": finish+6*60}
        journal.observe(SESSION, REVIEWED, "design", start, at, {
            "at": at, "displayed": point, "local_method": "duration", "method": "duration",
            "raw_local": point, "local": point,
        })
    event = {
        "id": "demo-after-review", "state": "ready", "at": start+18*60+56,
        "received_at": start+19*60, "percent": 68, "remaining_seconds": [600, 784, 1080],
        "explanation": "剩余主要是验证与交付，暂未发现新增实施项。", "evidence_ids": ["demo-3"],
    }
    journal.assessment_event(SESSION, REVIEWED, "demo", event)
    journal.decide(event["id"], {
        "at": start+19*60, "progress": "applied", "base_percent": 62, "suggested_percent": 68,
        "displayed_percent": 68, "timing": "observing", "local_finish": start+34*60,
        "ai_finish": end, "ai_range": [event["at"]+600, event["at"]+1080],
        "blend_finish": start+33*60+30, "displayed_finish": start+34*60,
    })
    journal.close_turn(SESSION, REVIEWED, "completed", end)
