import argparse,json,time
from pathlib import Path
from pulse.forecast import train
parser=argparse.ArgumentParser();parser.add_argument('--codex-home',type=Path,required=True);args=parser.parse_args()
start=time.perf_counter()
model=train(args.codex_home,Path(__file__).resolve().parent/'data')
print(json.dumps({**model['metrics'],'trainSeconds':round(time.perf_counter()-start,1)},ensure_ascii=False))
