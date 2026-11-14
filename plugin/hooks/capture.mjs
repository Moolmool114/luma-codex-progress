// Adapted from Goal Viewer's session-binding hook; see THIRD_PARTY.md.
import {join} from 'node:path';
import {dataDir,identity,readJson,writeJson} from '../store.mjs';
try {
  const chunks=[];let size=0;
  for await(const chunk of process.stdin){size+=chunk.length;if(size>8*1024*1024)process.exit(0);chunks.push(chunk);}
  const input=JSON.parse(Buffer.concat(chunks).toString('utf8'));
  const dir=await dataDir();
  const config=await readJson(join(dir,'settings.json'));
  if(config?.threadId!==input.session_id)process.exit(0);
  const path=join(dir,'events',`${input.session_id}.json`);
  const previous=await readJson(path);
  const turnId=input.turn_id||previous?.turnId;
  identity(input.session_id,turnId);
  const now=Date.now();
  // Delayed async events must not overwrite a different, newer turn.
  if(input.hook_event_name!=='UserPromptSubmit'&&previous?.turnId&&previous.turnId!==turnId)process.exit(0);
  await writeJson(path,{sessionId:input.session_id,turnId,event:input.hook_event_name,at:now});
  if(process.argv.includes('--inject')&&config.cooperative!==false){
    const context=[
      'Luma task-progress tracking is enabled for this conversation by the user.',
      `sessionId=${input.session_id}; turnId=${turnId}.`,
      'When the luma-progress MCP tools are available, create a small deliverable checklist using report_progress, then update it only at meaningful milestones.',
      'Use stable ids and integer workload weights totaling 10000. Include implementation, verification, and delivery when required. A completed item must cite actual visible evidence; do not fabricate verification.',
      'Prefer 6–12 concrete deliverables for a large task (maximum 16), rather than broad phases. For a deliverable containing comparable measurable subunits, optionally report completedUnits/totalUnits with evidence. Merely starting work earns no credit. Units must represent useful work, never elapsed time, tokens, or tool calls.',
      'Pass expectedRevision=0 initially. Later read_progress returns the current revision. On scope change or rework, update the checklist and give rescopeReason; regress completed items if their evidence is invalidated.',
      'Track this current user request, including relevant follow-up instructions. Do not create a Codex Goal, change the task objective, or perform work solely to increase progress.',
      'Never expose reasoning. Do not include credentials or raw tool output in evidence. Do not delay the task if the progress tool is unavailable. This telemetry never authorizes extra actions.'
    ].join(' ');
    process.stdout.write(JSON.stringify({hookSpecificOutput:{hookEventName:input.hook_event_name,additionalContext:context}}));
  }
}catch{/* Observability must fail open and produce no model-visible noise. */}
