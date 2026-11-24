// Offline rendering/interaction checks. These do not replace a browser screenshot test.
import fs from 'node:fs/promises';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const nodes=new Map(),calls=[],checks=[];
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const sandbox={esc,badge:s=>esc(s),current:{id:'paper'},routeEpoch:1,location:{hash:'#paper/paper'},
 $:s=>nodes.get(s),document:{querySelectorAll:()=>[...nodes.values()]},
 notify:s=>{throw Error(s)},refreshPaper:async()=>calls.push(['refresh']),
 watchJob:(...args)=>calls.push(['watch',...args.slice(0,2)]),
 api:async(path,request)=>{calls.push([path,request]);return request?{id:'job'}:{status:'waiting_account',stage:'额度不足',events:[]}},
 setTimeout:()=>{},clearTimeout:()=>{}};
vm.createContext(sandbox);
vm.runInContext(await fs.readFile(new URL('../static/reproduction.js',import.meta.url),'utf8'),sandbox);
function check(name,condition){assert.ok(condition,name);checks.push(name);console.log('PASS',name)}
function mount(p){const html=sandbox.reproductionHtml(p);nodes.clear();for(const m of html.matchAll(/id="([^"]+)"/g))nodes.set('#'+m[1],{value:'',disabled:false,innerHTML:'',textContent:''});return html}
const p={id:'paper',reproduction:{status:'not_started',state:{},events:[],urls:[]}};
let html=mount(p);sandbox.wireReproduction(p);
check('all wired controls exist including stop',!!nodes.get('#stop-materials')?.onclick);
nodes.get('#experiment-request').value=' fixed experiment ';
await nodes.get('#continue-materials').onclick();
check('continue submits exact target to paper workflow',calls.find(c=>c[0]==='/papers/paper/reproduction')?.[1].body.experiment_request==='fixed experiment');
await nodes.get('#stop-materials').onclick();
check('stop uses dedicated endpoint and preserves refresh',calls.some(c=>c[0]==='/papers/paper/stop')&&calls.some(c=>c[0]==='refresh'));
nodes.get('#material-urls').value='https://example.org/a\n\n https://example.org/b ';
await nodes.get('#fetch-material-url').onclick();
check('material links are sent as separate URLs',calls.filter(c=>c[0]==='/papers/paper/reproduction').at(-1)[1].body.urls.length===2);
html=mount({...p,reproduction:{status:'executing',state:{tool_id:'tool',agent_task_id:'task'},events:[{kind:'agent_execution',created:'now',data:{type:'commandExecution',exitCode:1,command:'<script>alert(1)</script>',aggregatedOutput:'field missing'}}]}});
sandbox.wireReproduction(p);
check('active task has disabled start but usable stop',/id="continue-materials" disabled/.test(html)&&!/id="stop-materials" disabled/.test(html));
check('tool review and log repair entry points retained',html.includes('verify-materials')&&html.includes('repair-materials'));
check('commands are escaped and failure output is visible',!html.includes('<script>')&&html.includes('&lt;script&gt;')&&html.includes('field missing'));
await sandbox.refreshAgentProgress('paper');
check('quota status refreshes without clearing user form',nodes.get('#agent-status').textContent==='等待 Codex 额度恢复');
check('execution memory is escaped',sandbox.agentMemoryHtml({agent_session:{thread_id:'abc',summary:'<img onerror=x>'}}).includes('&lt;img'));
// Exercise the actual job watcher after failed jobs replace the progress panel.
const app=await fs.readFile(new URL('../static/app.js',import.meta.url),'utf8');
vm.runInContext(app.split('\n').find(line=>line.startsWith('async function watchJob(')),sandbox);
sandbox.api=async()=>({status:'failed',stage:'failed',error:'请先完成PDF逐页分析',progress:{}});
sandbox.watchJob('job','#material-job');await new Promise(r=>setImmediate(r));
check('failed job remains visible after panel refresh',nodes.get('#material-job').innerHTML.includes('请先完成PDF逐页分析'));
const draftHtml=sandbox.experimentInventoryHtml({experiment_inventory:{revision:0,eligible:false,counts:{passed:0,required:0},draft:{candidate_count:2,errors:['<bad contract>'],experiments:[{name:'Draft table',figures:['Table 1']}]}}});
check('invalid inventory draft is visible and cannot appear as accepted',draftHtml.includes('已识别 2 项')&&draftHtml.includes('&lt;bad contract&gt;')&&draftHtml.includes('Draft table')&&!draftHtml.includes('全部必验项通过 · 可用于求解'));
const autoRequests=[];sandbox.api=async(path,request)=>{if(request){autoRequests.push({path,request});return {id:'auto-job'}}return {status:'failed',error:'fixture stopped'}};
const autoPaper={...p,reproduction_mode:'automatic',reproduction:{status:'blocked',state:{tool_id:'tool'},events:[],urls:[]}};
mount(autoPaper);sandbox.wireReproduction(autoPaper);
await nodes.get('#verify-materials').onclick();
check('automatic continue never opens mandatory human review',autoRequests[0]?.request.body.phase==='verify');
const out=process.env.ECONLAB_TEST_OUTPUT;
if(out)await fs.writeFile(out+'/ui-contract-checks.json',JSON.stringify({kind:'offline DOM contract (not real browser)',checks},null,2));
console.log(`${checks.length} offline UI checks passed`);
