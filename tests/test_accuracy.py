"""Behavioral checks for evidence boundaries and forecast causality."""
import copy
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch
from pulse.estimator import estimate
from pulse.forecast import features
from pulse.assessor import Assessor,KeyStore,context,endpoint_url,validate_result,apply_assessment,NoRedirect,request_assessment,friendly_error

TURN='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
def snapshot(**values):
    result=dict(turn_id=TURN,started_at=1000,status='inProgress',source_age=0,items=[],prompt='Build the app',checklist={})
    result.update(values);return result
def plan(done=3,total=8):
    return dict(turnId=TURN,revision=1,steps=[dict(id='build',label='Build',weight=10000,status='in_progress',completedUnits=done,totalUnits=total,evidence='Three verified deliverables')])

class AccuracyTests(unittest.TestCase):
    def test_measured_subunits_earn_only_reported_work(self):
        value=estimate(snapshot(checklist=plan()),now=1200)
        self.assertEqual(value['percent'],38)
        self.assertEqual(value['confirmed_percent'],38)

    def test_units_without_evidence_do_not_earn_work(self):
        checklist=plan();checklist['steps'][0]['evidence']=''
        self.assertEqual(estimate(snapshot(checklist=checklist),now=1200)['percent'],0)

    def test_turn_completion_preserves_known_unfinished_work(self):
        value=estimate(snapshot(checklist=plan(),status='completed',completed_at=1200,final='Delivered'),now=1300)
        self.assertEqual(value['status'],'partial');self.assertEqual(value['percent'],38)
        self.assertEqual(value['steps'][0]['status'],'in_progress')

    def test_large_evidence_correction_is_not_suppressed(self):
        before=estimate(snapshot(checklist=plan(7)),now=1200)
        after=estimate(snapshot(checklist=plan(1)),now=1201)
        self.assertGreater(before['percent']-after['percent'],60)

    def test_future_tool_failure_does_not_leak_into_prefix(self):
        live=dict(id='tool',type='commandExecution',command='node --test',started=1050,finished=None,at=1050,status='inProgress')
        completed={**live,'finished':1300,'at':1300,'status':'completed','exitCode':1}
        self.assertEqual(features('Build',[live],1000,100),features('Build',[completed],1000,100))
        self.assertNotEqual(features('Build',[live],1000,400),features('Build',[completed],1000,400))

    def test_eta_uses_predicted_median_not_range_midpoint(self):
        value=estimate(snapshot(),now=1200,time_prediction=dict(available=True,range=[60,600],median=100,reason='Held out model'))
        self.assertEqual(value['eta_median'],100);self.assertEqual(value['finish_at'],1300)

class AssessorTests(unittest.TestCase):
    def setUp(self):
        self.s=snapshot(items=[dict(id='cmd1',type='commandExecution',command='SECRET_COMMAND',aggregatedOutput='SECRET_OUTPUT',status='completed'),dict(id='thought',type='reasoning',text='SECRET_REASONING')])
        self.e=estimate(self.s,now=1200);self.payload=context(self.s,self.e)
        self.valid=dict(turn_id=TURN,percent=50,remaining_seconds=[60,120,300],evidence_ids=['cmd1'],explanation='Visible tool activity')

    def test_context_is_a_deliberate_public_whitelist(self):
        output=json.dumps(self.payload)
        for secret in ['SECRET_COMMAND','SECRET_OUTPUT','SECRET_REASONING','thought']:self.assertNotIn(secret,output)
        self.assertIn('cmd1',output)

    def test_disabled_or_unconsented_never_calls_service(self):
        with tempfile.TemporaryDirectory() as temp,patch('pulse.assessor.request_assessment') as request:
            observer=Assessor(Path(temp))
            for cfg in ({},{'enabled':True,'consent':False},{'enabled':False,'consent':True}):
                self.assertFalse(observer.inspect(cfg,self.s,self.e)['enabled'])
            request.assert_not_called()

    def test_cross_turn_or_invented_evidence_rejected(self):
        for changes in [dict(turn_id='another'),dict(evidence_ids=['invented']),dict(evidence_ids=[])]:
            with self.assertRaises(ValueError):validate_result({**self.valid,**changes},self.payload)

    def test_nonfinite_or_unordered_estimates_rejected(self):
        for changes in [dict(percent=float('nan')),dict(percent=True),dict(percent=100),dict(remaining_seconds=[300,200,400]),dict(remaining_seconds=[1,float('inf'),500])]:
            with self.assertRaises(ValueError):validate_result({**self.valid,**changes},self.payload)

    def test_remote_http_and_embedded_keys_rejected(self):
        for url in ['http://example.com/v1','https://key@example.com/v1','https://example.com/v1?key=secret']:
            with self.assertRaises(ValueError):endpoint_url(url)
        self.assertEqual(endpoint_url('http://127.0.0.1:1234/v1'),'http://127.0.0.1:1234/v1/chat/completions')

    def test_redirects_cannot_forward_credentials(self):
        with self.assertRaises(ValueError):NoRedirect().redirect_request(None,None,302,'',{},'https://another.example')

    def test_ai_cannot_erase_evidence_or_declare_done(self):
        value=estimate(snapshot(checklist=plan(5)),now=1200)
        result=apply_assessment(value,{'assessment':{**self.valid,'percent':10,'age':0}})
        self.assertEqual(result['percent'],value['percent']);self.assertEqual(result['steps'],value['steps'])
        self.assertEqual(result['status'],'working')

    def test_explicit_schema_rejection_falls_back_to_validated_json(self):
        error=urllib.error.HTTPError('https://example.test/v1/chat/completions',400,'Bad request',{},io.BytesIO(b'{"error":{"message":"This response_format type is unavailable now"}}'))
        response=unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value=json.dumps({'choices':[{'message':{'content':json.dumps(self.valid)}}]}).encode()
        opener=unittest.mock.MagicMock();opener.open.side_effect=[error,response]
        with patch('pulse.assessor.urllib.request.build_opener',return_value=opener):
            value=request_assessment(dict(endpoint='https://example.test/v1',model='test',format='json_schema'),'dummy',self.payload)
        self.assertTrue(value['format_fallback'])
        sent=[json.loads(call.args[0].data) for call in opener.open.call_args_list]
        self.assertEqual([x['response_format']['type'] for x in sent],['json_schema','json_object'])
        self.assertEqual(sent[0]['messages'],sent[1]['messages'])

    def test_auth_and_unknown_model_errors_never_trigger_format_retry(self):
        for code,message in [(401,'Invalid key'),(400,'Unknown model')]:
            error=urllib.error.HTTPError('https://example.test',code,'Failure',{},io.BytesIO(message.encode()))
            opener=unittest.mock.MagicMock();opener.open.side_effect=error
            with patch('pulse.assessor.urllib.request.build_opener',return_value=opener),self.assertRaises(urllib.error.HTTPError):
                request_assessment(dict(endpoint='https://example.test/v1',model='test'),'',self.payload)
            self.assertEqual(opener.open.call_count,1)

    def test_service_errors_are_actionable_without_raw_response_data(self):
        error=urllib.error.HTTPError('https://private.test',401,'SECRET_BODY',{},None)
        output=friendly_error(error)
        self.assertIn('401',output);self.assertNotIn('SECRET_BODY',output);self.assertNotIn('private.test',output)

    @unittest.skipUnless(os.name=='nt','Windows DPAPI')
    def test_key_is_encrypted_and_round_trips(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'LUMA_ASSESSOR_KEY':''}):
            store=KeyStore(Path(temp));store.save('test-credential-value')
            self.assertNotIn(b'test-credential-value',store.path.read_bytes())
            self.assertEqual(store.get(),'test-credential-value')

if __name__=='__main__':unittest.main()
