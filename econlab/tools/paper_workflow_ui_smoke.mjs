// Real browser checks against an isolated, already seeded paper fixture.
import fs from 'node:fs/promises';
const base=process.env.ECONLAB_TEST_URL||'http://127.0.0.1:8784';
const debug=process.env.ECONLAB_DEBUG_URL||'http://127.0.0.1:9234';
const output=process.env.ECONLAB_TEST_OUTPUT;if(!output)throw Error('Set ECONLAB_TEST_OUTPUT');
const targets=await(await fetch(debug+'/json/list')).json();
const target=targets.find(t=>t.type==='page');const ws=new WebSocket(target.webSocketDebuggerUrl);
await new Promise(r=>ws.addEventListener('open',r,{once:true}));
let seq=0;const pending=new Map(),checks=[],errors=[],testStarted=Date.now();
ws.addEventListener('message',event=>{const m=JSON.parse(event.data);if(m.method==='Runtime.exceptionThrown'&&m.params.timestamp>=testStarted)errors.push(m.params);if(m.id&&pending.has(m.id)){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result)}});
function call(method,params={}){return new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}))})}
async function evaluate(expression){const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value}
async function wait(expression){const end=Date.now()+20000;while(Date.now()<end){if(await evaluate(expression))return;await new Promise(r=>setTimeout(r,200))}throw Error('Timeout: '+expression)}
function check(name,condition){if(!condition)throw Error(name);checks.push(name);console.log('PASS '+name)}
try{
 await call('Runtime.enable');await call('Page.enable');
 await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1100,deviceScaleFactor:1,mobile:false});
 const papers=await(await fetch(base+'/api/papers')).json();if(papers.length!==1)throw Error('Use a one-paper isolated UI fixture');
 const pid=papers[0].id;
 await call('Network.enable');await call('Network.setCacheDisabled',{cacheDisabled:true});
 await call('Page.navigate',{url:base+'/#paper/'+pid});await call('Page.reload',{ignoreCache:true});
 await wait("!!document.querySelector('#experiment-inventory')&&document.querySelector('#experiment-inventory').textContent.includes('全部必验项通过')");
 check('four main pages preserved',await evaluate('document.querySelectorAll("header nav a").length===4'));
 check('both experiments and figure coverage shown',await evaluate('document.querySelector("#experiment-inventory").textContent.includes("Baseline calibration")&&document.querySelector("#experiment-inventory").textContent.includes("Counterfactual")&&document.querySelector("#experiment-inventory").textContent.includes("Table 2")'));
 check('completed paper qualification visible',await evaluate('document.querySelector("#experiment-inventory").textContent.includes("全部必验项通过")'));
 await evaluate('document.querySelector("#project-memory").click()');await wait('document.querySelector("#modal").open');
 check('unified project memory opens',await evaluate('document.querySelector("#modal-content").textContent.includes("inventory_revision")'));await evaluate('document.querySelector("#modal").close()');
 await evaluate('document.querySelector("#edit-experiment-plan").click()');await wait('!!document.querySelector("#inventory-json")');
 check('manual JSON editor retains full plan',await evaluate('JSON.parse(document.querySelector("#inventory-json").value).experiments.length===2'));
 await evaluate('document.querySelector("#inventory-actor").value="Browser fixture reviewer";document.querySelector("#inventory-reason").value="Check version invalidation";const doc=JSON.parse(document.querySelector("#inventory-json").value);doc.notes="Browser audit update";document.querySelector("#inventory-json").value=JSON.stringify(doc);document.querySelector("#save-inventory").click()');
 await wait('!document.querySelector("#modal").open&&document.querySelector("#experiment-inventory").textContent.includes("变更后需重验")');
 check('editing scope revokes proofs and paper qualification',await evaluate('document.querySelector("#experiment-inventory").textContent.includes("尚未整篇复现完成")'));
 await evaluate('document.querySelector("#approve-experiment-plan").click()');await wait('!!document.querySelector("#batch-reviewer")');
 check('review gate has named reviewer and scope opinion',await evaluate('!!document.querySelector("#batch-opinion")'));await evaluate('document.querySelector("#modal").close()');
 await call('Page.navigate',{url:base+'/#settings'});await wait('!!document.querySelector("#reproduction-mode")');
 check('automatic and manual execution modes available',await evaluate('document.querySelector("#reproduction-mode").options.length===2'));
 await evaluate('document.querySelector("#reproduction-mode").value="automatic";document.querySelector("#save-model").click()');
 await wait('(async()=> (await(await fetch("/api/bootstrap")).json()).reproduction_mode==="automatic")()');
 check('automatic mode is persisted',true);
 await evaluate('document.querySelector("#reproduction-mode").value="manual";document.querySelector("#save-model").click()');
 await wait('(async()=> (await(await fetch("/api/bootstrap")).json()).reproduction_mode==="manual")()');
 await call('Page.navigate',{url:base+'/#paper/'+pid});await wait('!!document.querySelector("#experiment-inventory")');
 const image=await call('Page.captureScreenshot',{format:'png'});await fs.writeFile(output+'/paper-inventory.png',Buffer.from(image.data,'base64'));
 check('no browser JavaScript exceptions',errors.length===0);
}finally{await fs.writeFile(output+'/paper-ui-checks.json',JSON.stringify({checks,errors},null,2));ws.close()}
