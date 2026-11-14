"""Prepare a local marketplace. Does not install or trust anything globally."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parent


def setup(data=None):
    data=Path(data or ROOT/'data').resolve()
    (ROOT/'plugin'/'runtime-path.json').write_text(json.dumps({'dataDir':str(data)},indent=2),encoding='utf-8')
    catalog=ROOT/'.agents'/'plugins'
    catalog.mkdir(parents=True,exist_ok=True)
    # Local sources are relative to the marketplace root. Keep this whole app together.
    marketplace={'name':'luma-local','interface':{'displayName':'Luma 本地插件'},'plugins':[{'name':'luma-progress','source':{'source':'local','path':'./plugin'},'policy':{'installation':'AVAILABLE','authentication':'ON_INSTALL'},'category':'Productivity'}]}
    (catalog/'marketplace.json').write_text(json.dumps(marketplace,ensure_ascii=False,indent=2),encoding='utf-8')
    return catalog


if __name__=='__main__':
    print(setup())
