// Session-store I/O adapted from GuMu599/-codex-goal-viewer (MIT).
// Upstream commit: 583caed767b085055ecf1d9fb087441640f4fb26.
// Full license and provenance are in THIRD_PARTY.md and licenses/.
import {mkdir,readFile,rename,writeFile} from 'node:fs/promises';
import {dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';

export const pluginRoot = dirname(fileURLToPath(import.meta.url));
export async function readJson(path) {
  try { return JSON.parse(await readFile(path,'utf8')); } catch { return null; }
}
export async function writeJson(path,value) {
  await mkdir(dirname(path),{recursive:true});
  const temporary = `${path}.${process.pid}.tmp`;
  await writeFile(temporary,`${JSON.stringify(value,null,2)}\n`,'utf8');
  await rename(temporary,path);
}
export async function dataDir() {
  const config = await readJson(join(pluginRoot,'runtime-path.json'));
  if (!config?.dataDir) throw new Error('Luma runtime-path.json is missing. Run setup_plugin.py first.');
  return config.dataDir;
}
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export function identity(sessionId,turnId) {
  if (!uuid.test(sessionId) || !uuid.test(turnId)) throw new Error('Valid sessionId and turnId are required.');
}
export async function report(input,dir) {
  identity(input.sessionId,input.turnId);
  const event = await readJson(join(dir,'events',`${input.sessionId}.json`));
  if (!event || event.turnId !== input.turnId) throw new Error('The turn is not bound by a trusted Luma hook.');
  const path=join(dir,'checklists',`${input.sessionId}.json`);
  const previous=await readJson(path);
  const current=previous?.turnId===input.turnId?previous:null;
  const revision=current?.revision||0;
  if (input.expectedRevision!==revision) throw new Error(`Revision conflict: expected ${revision}. Read progress before retrying.`);
  if (!Array.isArray(input.steps)||!input.steps.length||input.steps.length>16) throw new Error('Supply 1–16 weighted deliverables.');
  const ids=new Set();
  const steps=input.steps.map(step=>{
    if(typeof step.id!=='string'||!step.id||step.id.length>64||ids.has(step.id))throw new Error('Step ids must be unique, stable strings.');
    ids.add(step.id);
    if(typeof step.label!=='string'||!step.label.trim()||step.label.length>160)throw new Error('Step label is required (max 160 characters).');
    if(!Number.isInteger(step.weight)||step.weight<=0||step.weight>10000)throw new Error('Weights must be positive integer basis points.');
    if(!['pending','in_progress','completed'].includes(step.status))throw new Error('Invalid step status.');
    if(step.status==='completed'&&(typeof step.evidence!=='string'||!step.evidence.trim()))throw new Error('A completed step requires concrete evidence.');
    const units={};
    if(step.completedUnits!==undefined||step.totalUnits!==undefined){
      if(!Number.isInteger(step.completedUnits)||!Number.isInteger(step.totalUnits)||step.totalUnits<=0||step.totalUnits>10000||step.completedUnits<0||step.completedUnits>step.totalUnits)throw new Error('Supply valid completedUnits and totalUnits together.');
      if(step.status==='pending'&&step.completedUnits!==0)throw new Error('Pending work cannot earn units.');
      if(step.status==='completed'&&step.completedUnits!==step.totalUnits)throw new Error('Completed work must include all units.');
      if(step.completedUnits>0&&!step.evidence?.trim())throw new Error('Earned units require concrete evidence.');
      units.completedUnits=step.completedUnits;units.totalUnits=step.totalUnits;
    }
    return {id:step.id,label:step.label,weight:step.weight,status:step.status,evidence:String(step.evidence||'').slice(0,800),...units};
  });
  if(steps.reduce((s,x)=>s+x.weight,0)!==10000)throw new Error('Weights must total 10000 basis points.');
  const shape=rows=>JSON.stringify(rows.map(({id,weight,label})=>({id,weight,label})));
  if(current&&shape(current.steps)!==shape(steps)&&!input.rescopeReason?.trim())throw new Error('Changing scope, weights or labels requires rescopeReason.');
  const earned=row=>row.status==='completed'?1:row.status==='in_progress'&&row.totalUnits?row.completedUnits/row.totalUnits:0;
  if(current&&!input.rescopeReason?.trim()&&steps.some(row=>{const old=current.steps.find(x=>x.id===row.id);return old&&(earned(row)<earned(old)||old.totalUnits!==undefined&&old.totalUnits!==row.totalUnits);}))throw new Error('Rework or a changed unit total requires rescopeReason.');
  const outcome=input.outcome||'active';
  if(!['active','partial','blocked','completed'].includes(outcome))throw new Error('Invalid outcome.');
  if(outcome==='completed'&&steps.some(x=>x.status!=='completed'))throw new Error('Complete all required deliverables before marking completed.');
  const next={sessionId:input.sessionId,turnId:input.turnId,revision:revision+1,steps,current:String(input.current||'').slice(0,160),outcome,rescopeReason:String(input.rescopeReason||'').slice(0,800),updatedAt:Date.now()};
  await writeJson(path,next);
  return next;
}
