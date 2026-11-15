import argparse
import json
import time
from datetime import datetime
from pathlib import Path

from pulse.forecast import train


def cutoff(value):
    date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if date.tzinfo is None:
        raise argparse.ArgumentTypeError("Include a UTC offset or use Z.")
    return date.timestamp()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex-home", type=Path, required=True)
    parser.add_argument("--through", type=cutoff, help="ISO 8601 cutoff for completed turns")
    parser.add_argument("--data", type=Path, default=Path(__file__).resolve().parent/"data"/"replay")
    args = parser.parse_args()
    start = time.perf_counter()
    model = train(args.codex_home, args.data, args.through)
    print(json.dumps({**model["metrics"], "trainSeconds": round(time.perf_counter()-start, 1)},
                     ensure_ascii=False, indent=2))
