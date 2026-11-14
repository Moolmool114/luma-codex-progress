"""Synthetic showcase data. No private database access or network requests."""
import time

SESSION='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
TURN='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'

class DemoSource:
    def __init__(self):self.start=time.time()-18*60-32
    def threads(self,query='',limit=80):
        return [{'id':SESSION,'title':'为桌面应用打磨交互细节','updated_at':time.time()}] if not query or query in '为桌面应用打磨交互细节' else []
    def _thread(self,identifier):
        if identifier!=SESSION:raise ValueError('Unknown demo conversation')
    def history(self):return []
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
    def predict(self,snapshot,elapsed):return dict(available=True,range=[7*60,22*60],median=14*60,reason='合成演示，不代表实际预测表现')
