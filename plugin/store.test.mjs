import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {report,writeJson,identity} from './store.mjs';
const sessionId='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',turnId='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb';
const step={id:'build',label:'Build',weight:10000,status:'in_progress'};
async function fixture(run){const dir=await mkdtemp(join(tmpdir(),'luma-test-'));try{await writeJson(join(dir,'events',`${sessionId}.json`),{turnId});await run(dir);}finally{await rm(dir,{recursive:true,force:true});}}
test('completion requires evidence',()=>fixture(async dir=>{await assert.rejects(report({sessionId,turnId,expectedRevision:0,steps:[{...step,status:'completed'}]},dir),/evidence/);}));
test('workload weights must total 10000',()=>fixture(async dir=>{await assert.rejects(report({sessionId,turnId,expectedRevision:0,steps:[{...step,weight:5}]},dir),/10000/);}));
test('stale revisions cannot overwrite work',()=>fixture(async dir=>{const args={sessionId,turnId,expectedRevision:0,steps:[step]};await report(args,dir);await assert.rejects(report(args,dir),/Revision conflict/);}));
test('scope changes must be explicit',()=>fixture(async dir=>{await report({sessionId,turnId,expectedRevision:0,steps:[step]},dir);await assert.rejects(report({sessionId,turnId,expectedRevision:1,steps:[{...step,label:'Another task'}]},dir),/rescopeReason/);}));
test('turn isolation prevents conversation bleed',()=>fixture(async dir=>{await assert.rejects(report({sessionId,turnId:sessionId,expectedRevision:0,steps:[step]},dir),/bound/);}));
test('path traversal rejected',()=>{assert.throws(()=>identity('../../outside','turn'),/required/);});
test('partial work requires evidence and valid units',()=>fixture(async dir=>{
  await assert.rejects(report({sessionId,turnId,expectedRevision:0,steps:[{...step,completedUnits:3,totalUnits:8}]},dir),/evidence/);
  const result=await report({sessionId,turnId,expectedRevision:0,steps:[{...step,completedUnits:3,totalUnits:8,evidence:'Three delivered items'}]},dir);
  assert.equal(result.steps[0].completedUnits,3);
  await assert.rejects(report({sessionId,turnId,expectedRevision:1,steps:[{...step,completedUnits:1,totalUnits:8,evidence:'Rework'}]},dir),/rescopeReason/);
  const revised=await report({sessionId,turnId,expectedRevision:1,rescopeReason:'Two deliverables require rework',steps:[{...step,completedUnits:1,totalUnits:8,evidence:'One item remains valid'}]},dir);
  assert.equal(revised.steps[0].completedUnits,1);
}));
test('completed work must include all reported units',()=>fixture(async dir=>{
  await assert.rejects(report({sessionId,turnId,expectedRevision:0,steps:[{...step,status:'completed',completedUnits:3,totalUnits:8,evidence:'Partial'}]},dir),/all units/);
}));
