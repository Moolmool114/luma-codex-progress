import {createInterface} from 'node:readline';
import {join} from 'node:path';
import {dataDir,identity,readJson,report} from './store.mjs';
const idProps={sessionId:{type:'string'},turnId:{type:'string'}};
const tools=[
 {name:'read_progress',description:'Read the evidence-weighted checklist for the current Luma-bound task. Use the session and turn IDs supplied by the trusted hook.',annotations:{readOnlyHint:true},inputSchema:{type:'object',properties:idProps,required:['sessionId','turnId'],additionalProperties:false}},
 {name:'report_progress',description:'Initialize or update the current task deliverables. Weighted work, not elapsed time or tokens. Completed items require evidence. Scope/weight changes require an explicit reason. Does not edit the user task or Codex Goal.',annotations:{readOnlyHint:false,destructiveHint:false,idempotentHint:false,openWorldHint:false},inputSchema:{type:'object',properties:{...idProps,expectedRevision:{type:'integer',minimum:0},current:{type:'string'},rescopeReason:{type:'string'},outcome:{type:'string',enum:['active','partial','blocked','completed']},steps:{type:'array',minItems:1,maxItems:16,items:{type:'object',properties:{id:{type:'string'},label:{type:'string'},weight:{type:'integer',minimum:1,maximum:10000},status:{type:'string',enum:['pending','in_progress','completed']},evidence:{type:'string'},completedUnits:{type:'integer',minimum:0},totalUnits:{type:'integer',minimum:1,maximum:10000}},required:['id','label','weight','status'],additionalProperties:false}}},required:['sessionId','turnId','expectedRevision','steps'],additionalProperties:false}}
];
function send(value){process.stdout.write(JSON.stringify(value)+'\n');}
async function handle(message){
 if(message.id===undefined)return;
 try{
  let result;
  switch(message.method){
   case 'initialize':result={protocolVersion:message.params?.protocolVersion||'2024-11-05',capabilities:{tools:{}},serverInfo:{name:'luma-progress',version:'0.2.0'}};break;
   case 'ping':result={};break;
   case 'tools/list':result={tools};break;
   case 'tools/call':{
    const {name,arguments:args={}}=message.params||{};
    try{
     identity(args.sessionId,args.turnId);
     const dir=await dataDir();
     let state;
     if(name==='read_progress'){
      state=await readJson(join(dir,'checklists',`${args.sessionId}.json`));
      if(state?.turnId!==args.turnId)state={revision:0,steps:[]};
     }else if(name==='report_progress')state=await report(args,dir);
     else throw new Error('Unknown tool');
     result={content:[{type:'text',text:JSON.stringify(state)}],structuredContent:state};
    }catch(error){result={isError:true,content:[{type:'text',text:error.message}]};}
    break;
   }
   default:return send({jsonrpc:'2.0',id:message.id,error:{code:-32601,message:'Method not found'}});
  }
  send({jsonrpc:'2.0',id:message.id,result});
 }catch(error){send({jsonrpc:'2.0',id:message.id,error:{code:-32603,message:error.message}});}
}
// Sequential dispatch provides atomic revision checks for this single writer.
const lines=createInterface({input:process.stdin,crlfDelay:Infinity});
for await(const line of lines){try{if(line.length<=1024*1024)await handle(JSON.parse(line));}catch{send({jsonrpc:'2.0',id:null,error:{code:-32700,message:'Parse error'}});}}
