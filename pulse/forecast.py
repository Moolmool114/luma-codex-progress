"""Local numeric-only remaining-time model, with chronological calibration.

No network, no LLM, no reasoning content. Conversation text is used in memory
only to classify request type and measure length; saved models contain numeric
features. A held-out backtest determines whether the model is offered at all.
"""
from __future__ import annotations
import json
import math
import statistics
import threading
from pathlib import Path

from .estimator import classify, VERIFY, duration_forecast
from .source import CodexSource, connect_readonly

VERSION = 2


def features(prompt, items, start, elapsed):
    cut=start+elapsed
    counts={k:0 for k in ['commandExecution','fileChange','mcpToolCall','agentMessage','tests','failures','research','ui']}
    last=start
    running=0;running_age=0;last_edit=last_test=None
    for item in items:
        began=item.get('started',item.get('at',start))
        if began>cut:continue
        at=item.get('finished',item.get('at',start))
        typ=item.get('type')
        if at is None or at>cut:
            if typ in ('commandExecution','fileChange','mcpToolCall'):
                running+=1;running_age=max(running_age,cut-began)
            continue
        if typ in counts:counts[typ]+=1
        if typ=='commandExecution':
            counts['tests']+=bool(VERIFY.search(item.get('command') or ''))
            counts['failures']+=item.get('exitCode') not in (None,0)
            if VERIFY.search(item.get('command') or ''):last_test=at
        if typ=='fileChange':last_edit=at
        if typ=='mcpToolCall':
            name=(item.get('tool') or '').lower()
            counts['research']+=any(word in name for word in ['search','fetch','read','browse'])
            counts['ui']+=any(word in name for word in ['cua','screenshot','inspect']) or name=='js'
        last=max(last,at)
    kind=classify(prompt)
    return [math.log1p(max(0,elapsed)),math.log1p(len(prompt)),*[float(kind==k) for k in ['build','design','research','general']],*[math.log1p(counts[k]) for k in counts],math.log1p(max(0,cut-last)),math.log1p(running),math.log1p(max(0,running_age)),math.log1p(max(0,cut-last_edit)) if last_edit else -1,math.log1p(max(0,cut-last_test)) if last_test else -1,float(bool(last_edit and last_test and last_edit>last_test))]


def load_runs(home, data):
    history=CodexSource(home,data).history()
    runs=[]
    with connect_readonly(home/'thread_history_1.sqlite') as c:
        for record in reversed(history):
            marks="'commandExecution','fileChange','mcpToolCall','agentMessage'"
            rows=c.execute(f"""SELECT item_type,created_at_ms,started_at_ms,completed_at_ms,COALESCE(completed_at_ms,started_at_ms,created_at_ms) AS at,
              substr(json_extract(item_json,'$.command'),1,6000) AS command,
              json_extract(item_json,'$.tool') AS tool,
              json_extract(item_json,'$.exitCode') AS exitCode
              FROM thread_items WHERE thread_id=? AND turn_id=? AND item_type IN ({marks})
              ORDER BY rollout_ordinal LIMIT 1000""",(record['thread_id'],record['turn_id'])).fetchall()
            if len(rows)>=1000:continue
            items=[dict(type=r['item_type'],at=r['at']/1000,started=(r['started_at_ms'] or r['created_at_ms'])/1000,finished=(r['completed_at_ms'] or r['at'])/1000,command=r['command'],tool=r['tool'],exitCode=r['exitCode']) for r in rows]
            runs.append({**record,'items':items})
    return runs


def samples(runs):
    x=[];y=[]
    for run in runs:
        for fraction in (.15,.3,.5,.7,.9):
            elapsed=run['seconds']*fraction
            x.append(features(run['prompt'],run['items'],run['started_at'],elapsed))
            y.append(math.log1p(run['seconds']-elapsed))
    return x,y


def train(home, data):
    import numpy as np
    from sklearn.ensemble import HistGradientBoostingRegressor
    runs=load_runs(home,data)
    if len(runs)<80:raise ValueError('Not enough completed local turns to validate an ETA model.')
    n=len(runs);a=int(n*.6);b=int(n*.8)
    x,y=samples(runs[:a]);cx,cy=samples(runs[a:b]);tx,ty=samples(runs[b:])
    models=[]
    for quantile in (.1,.5,.9):
        model=HistGradientBoostingRegressor(loss='quantile',quantile=quantile,max_iter=130,max_leaf_nodes=9,min_samples_leaf=18,l2_regularization=3,random_state=7)
        model.fit(np.array(x),np.array(y));models.append(model)
    cal=np.sort(np.array([m.predict(cx) for m in models]).T,axis=1)
    # Conformal expansion on separate calibration runs. Never tune on test turns.
    residual=np.maximum(cal[:,0]-np.array(cy),np.array(cy)-cal[:,2])
    level=min(1,math.ceil((len(cy)+1)*.8)/len(cy))
    expansion=max(0,float(np.quantile(residual,level,method='higher')))
    pred=np.sort(np.array([m.predict(tx) for m in models]).T,axis=1)
    actual=np.expm1(ty)
    lower=np.expm1(pred[:,0]-expansion);upper=np.expm1(pred[:,2]+expansion);middle=np.expm1(pred[:,1])
    metrics={'version':VERSION,'trainRuns':a,'calibrationRuns':b-a,'testRuns':n-b,'testPredictions':len(ty),'intervalNominalCoverage':.8,'intervalObservedCoverage':round(float(np.mean((lower<=actual)&(actual<=upper))),3),'medianRelativeError':round(float(np.median(np.abs(middle-actual)/np.maximum(1,actual))),3),'medianAbsoluteErrorSeconds':round(float(np.median(np.abs(middle-actual)))),'method':'chronological train/calibration/test split; visible activity prefix only; no same-turn future features','target':'time to end of turn; not semantic task completion'}
    errors=np.abs(middle-actual)
    baseline=[];prior=list(runs[:b])
    for run in runs[b:]:
        for fraction in (.15,.3,.5,.7,.9):
            elapsed=run['seconds']*fraction
            forecast,_=duration_forecast(prior,classify(run['prompt']),elapsed)
            guess=forecast[1] if forecast else statistics.median(r['seconds'] for r in prior)
            baseline.append(abs(guess-run['seconds']*(1-fraction)))
        prior.append(run)
    metrics.update(meanAbsoluteErrorSeconds=round(float(np.mean(errors))),p90AbsoluteErrorSeconds=round(float(np.quantile(errors,.9))),withinTwoMinutes=round(float(np.mean(errors<=120)),3),baselineMedianAbsoluteErrorSeconds=round(float(np.median(baseline))),method='chronological train/calibration/test; running tools reconstructed from start/end timestamps',limitations=['Related turns can be correlated.','No semantic task-completion ground truth.','Long tasks remain uncertain.'])
    metrics['enabled']=metrics['medianRelativeError']<1.2 and metrics['intervalObservedCoverage']>=.65 and metrics['medianAbsoluteErrorSeconds']<=metrics['baselineMedianAbsoluteErrorSeconds']
    model_data={'version':VERSION,'models':models,'expansion':expansion,'metrics':metrics,'maxElapsed':max(r['seconds'] for r in runs[:a])}
    data.mkdir(parents=True,exist_ok=True)
    (data/'activity-eta-backtest.json').write_text(json.dumps(metrics,ensure_ascii=False,indent=2),encoding='utf-8')
    return model_data


class LocalForecast:
    def __init__(self,home:Path,data:Path):
        self.home,self.data=home,data
        self.model=None
        self.error=None
        self._thread=threading.Thread(target=self._train,daemon=True)
        self._thread.start()

    def _train(self):
        try:self.model=train(self.home,self.data)
        except Exception as error:self.error=type(error).__name__

    def predict(self,snapshot,elapsed):
        if self.model is None:
            return {'available':False,'reason':'正在校验本地时间预测' if self._thread.is_alive() else '本地样本不足，暂不预测时间'}
        metrics=self.model['metrics']
        if not metrics['enabled']:
            return {'available':False,'reason':'历史回放误差较大，暂不显示时间预测','metrics':metrics}
        if elapsed>self.model['maxElapsed']:
            return {'available':False,'reason':'本轮超过历史覆盖范围，暂停时间预测','metrics':metrics}
        vector=features(snapshot.get('prompt',''),snapshot.get('items',[]),snapshot['started_at'],elapsed)
        values=sorted(float(m.predict([vector])[0]) for m in self.model['models'])
        lo=max(1,math.expm1(values[0]-self.model['expansion']))
        hi=max(lo+1,math.expm1(values[2]+self.model['expansion']))
        median=max(lo,min(hi,math.expm1(values[1])))
        return {'available':True,'range':[round(lo),round(hi)],'median':round(median),'metrics':metrics,'reason':f"本地活动模型 · {metrics['testRuns']} 个留出轮次；跨任务效果可能不同，区间并非保证"}
