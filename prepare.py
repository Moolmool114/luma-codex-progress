"""Prepare portable, local launch configuration; no global configuration edits."""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from setup_plugin import setup

ROOT=Path(__file__).resolve().parent
parser=argparse.ArgumentParser()
parser.add_argument('--codex-home',default=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex'))),type=Path)
parser.add_argument('--thread')
args=parser.parse_args()
pythonw=Path(sys.executable).with_name('pythonw.exe')
runtime=str(pythonw if pythonw.exists() else Path(sys.executable))
(ROOT/'launch-config.json').write_text(json.dumps({'python':runtime,'codexHome':str(args.codex_home.resolve())},indent=2),encoding='utf-8')
data=ROOT/'data';data.mkdir(exist_ok=True)
path=data/'settings.json'
try:settings=json.loads(path.read_text(encoding='utf-8'))
except (OSError,ValueError):settings={}
if args.thread:settings['threadId']=args.thread
settings.setdefault('cooperative',True)
settings.setdefault('topmost',True)
path.write_text(json.dumps(settings,ensure_ascii=False,indent=2),encoding='utf-8')
setup(data)
try:
    from PIL import Image,ImageDraw
    assets=ROOT/'assets';assets.mkdir(exist_ok=True)
    image=Image.new('RGBA',(256,256),(0,0,0,0));draw=ImageDraw.Draw(image)
    draw.rounded_rectangle((0,0,255,255),radius=58,fill='#111111')
    for x,top,bottom,color in [(66,101,155,'#ededed'),(106,61,195,'#80d8bc'),(146,85,171,'#ededed'),(186,111,145,'#b4b4b4')]:
        draw.rounded_rectangle((x-7,top,x+7,bottom),radius=7,fill=color)
    image.save(assets/'luma.ico',sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
except ImportError:
    pass
csc=Path(r'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe')
if csc.exists():
    icon=['/win32icon:'+str(ROOT/'assets'/'luma.ico')] if (ROOT/'assets'/'luma.ico').exists() else []
    subprocess.run([str(csc),'/nologo','/target:winexe','/optimize+',*icon,'/reference:System.Web.Extensions.dll','/reference:System.Windows.Forms.dll','/out:'+str(ROOT/'Luma.exe'),str(ROOT/'Launcher.cs')],check=True)
print('Prepared Luma.exe and the local plugin marketplace.')
