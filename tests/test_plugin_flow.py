import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class PluginFlowTests(unittest.TestCase):
    def test_hook_to_mcp_to_weighted_progress(self):
        session='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';turn='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)/'plugin';shutil.copytree(ROOT/'plugin',folder)
            data=Path(temp)/'data';data.mkdir()
            (folder/'runtime-path.json').write_text(json.dumps({'dataDir':str(data)}),encoding='utf-8')
            (data/'settings.json').write_text(json.dumps({'threadId':session,'cooperative':True}),encoding='utf-8')
            hook=subprocess.run(['node',str(folder/'hooks'/'capture.mjs'),'--inject'],input=json.dumps({'session_id':session,'turn_id':turn,'hook_event_name':'UserPromptSubmit'}).encode(),capture_output=True,timeout=5)
            self.assertEqual(hook.returncode,0)
            self.assertIn('report_progress',json.loads(hook.stdout)['hookSpecificOutput']['additionalContext'])
            args={'sessionId':session,'turnId':turn,'expectedRevision':0,'steps':[{'id':'one','label':'Deliverable','weight':10000,'status':'completed','evidence':'Artifact created and inspected'}]}
            requests=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2024-11-05'}},{'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'report_progress','arguments':args}},{'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'read_progress','arguments':{'sessionId':session,'turnId':turn}}}]
            server=subprocess.run(['node',str(folder/'server.mjs')],input=('\n'.join(json.dumps(r) for r in requests)+'\n').encode(),capture_output=True,timeout=5)
            self.assertEqual(server.returncode,0)
            rows=[json.loads(r) for r in server.stdout.splitlines()]
            self.assertFalse(rows[1]['result'].get('isError',False))
            self.assertEqual(rows[2]['result']['structuredContent']['revision'],1)
            from pulse.estimator import estimate
            checklist=json.loads((data/'checklists'/f'{session}.json').read_text())
            e=estimate(dict(turn_id=turn,started_at=1000,status='inProgress',checklist=checklist,source_age=0),now=2000)
            self.assertEqual(e['percent'],95)

if __name__=='__main__':unittest.main()
