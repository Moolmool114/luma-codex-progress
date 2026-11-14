"""Chronological ETA backtest. Writes aggregate metrics, never conversation text.

This measures remaining-time forecasting, not semantic task-completion accuracy.
The 20th–80th percentile range has a nominal 60% coverage, not a guarantee.
"""
import argparse
import json
import statistics
from pathlib import Path
from pulse.source import CodexSource
from pulse.estimator import classify,duration_forecast

parser=argparse.ArgumentParser()
parser.add_argument('--codex-home',type=Path,required=True)
args=parser.parse_args()
root=Path(__file__).resolve().parent
history=list(reversed(CodexSource(args.codex_home,root/'data').history()))
train=[];rows=[]
for record in history:
    for fraction in (.25,.5,.75):
        kind=classify(record['prompt']);elapsed=record['seconds']*fraction
        forecast,count=duration_forecast(train,kind,elapsed)
        if forecast and count>=12:
            actual=record['seconds']-elapsed
            rows.append(dict(covered=forecast[0]<=actual<=forecast[2],relativeError=abs(forecast[1]-actual)/max(1,actual),absoluteError=abs(forecast[1]-actual)))
    train.append(record)
result={'method':'chronological holdout; prior runs only','target':'time to end of turn, not semantic task completion','runs':len(history),'predictions':len(rows),'nominalIntervalCoverage':.6,'observedIntervalCoverage':round(sum(r['covered'] for r in rows)/len(rows),3) if rows else None,'medianRelativeError':round(statistics.median(r['relativeError'] for r in rows),3) if rows else None,'medianAbsoluteErrorSeconds':round(statistics.median(r['absoluteError'] for r in rows)) if rows else None,'limitations':['Related turns may be correlated.','Task category does not fully measure workload.','No measured ground truth for semantic percentage.']}
(root/'data'/'eta-backtest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
