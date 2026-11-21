import fs from 'node:fs/promises';
const base='http://127.0.0.1:8778',debug='http://127.0.0.1:9224';
const tabs=await(await fetch(debug+'/json/list')).json(),target=tabs.find(t=>t.type==='page');
const ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise(r=>ws.addEventListener('open',r,{once:true}));
let id=0;const pending=new Map(),checks=[],errors=[];
function call(method,params={}){return new Promise((resolve,reject)=>{const n=++id;pending.set(n,{resolve,reject});ws.send(JSON.stringify({id:n,method,params}))})}
ws.addEventListener('message',e=>{const m=JSON.parse(e.data);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p?.reject(Error(m.error.message)):p?.resolve(m.result)}if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails);if(m.method==='Page.javascriptDialogOpening')call('Page.handleJavaScriptDialog',{accept:true})});
async function ev(expression){const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(r.exceptionDetails.exception?.description||r.exceptionDetails.text);return r.result?.value}
async function wait(expression,ms=20000){const deadline=Date.now()+ms;while(Date.now()<deadline){if(await ev(expression))return;await new Promise(r=>setTimeout(r,180))}throw Error('Timeout '+expression)}
function check(name,value){checks.push({name,passed:!!value});if(!value)throw Error(name);console.log('PASS',name)}
const api=async(path,body,method)=>{const r=await fetch(base+'/api'+path,{method:method||(body?'POST':'GET'),headers:{'X-EconLab':'1',...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined});const data=await r.json();if(!r.ok)throw Error(JSON.stringify(data));return data};
const out=new URL('../.local/ui-preview/',import.meta.url);let fixture;
try{
 await call('Runtime.enable');await call('Page.enable');await call('Network.enable');await call('Network.setCacheDisabled',{cacheDisabled:true});await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1100,deviceScaleFactor:1,mobile:false});
 await call('Page.navigate',{url:base+'/#papers'});await call('Page.reload',{ignoreCache:true});await wait(`document.querySelector('#import')`);
 check('exactly four business navigation entries',await ev(`document.querySelectorAll('nav a').length===4`));
 check('only one actual pilot in preview', (await api('/papers')).length===1);
 await ev(`document.querySelector('#import').click()`);await wait(`document.querySelector('#pdf')`);
 check('import uses full-PDF Codex and no OCR',await ev(`document.querySelector('#modal-content').textContent.includes('不使用 OCR')&&document.querySelector('#analyze').checked`));
 await ev(`document.querySelector('#modal').close()`);
 const pdf=await fs.readFile(new URL('fixture.pdf',out)),form=new FormData();form.append('file',new Blob([pdf],{type:'application/pdf'}),'UI fixture.pdf');form.append('analyze','false');const imported=await(await fetch(base+'/api/papers',{method:'POST',headers:{'X-EconLab':'1'},body:form})).json();fixture=imported.paper;
 check('import creates linked method and tool candidates',fixture.methods.length===1&&fixture.tools.length===1);
 await ev(`location.hash='paper/${fixture.id}'`);await wait(`document.querySelector('#edit-card')`);
 check('paper detail displays original PDF',await ev(`!!document.querySelector('iframe[src*="/api/assets/"]')`));
 check('resume action available',await ev(`!!document.querySelector('#resume-analysis')`));
 await ev(`document.querySelector('#edit-card').click()`);await wait(`document.querySelector('#edit-title')`);
 await ev(`document.querySelector('#edit-title').value='UI fixture edited';document.querySelector('[data-card="research_question"]').value='Synthetic fixture for interface verification';document.querySelector('#edit-actor').value='UI automated test';document.querySelector('#edit-reason').value='Test manual knowledge-card editing';document.querySelector('#edit-save').click()`);
 await wait(`!document.querySelector('#modal').open&&document.querySelector('h1')?.textContent==='UI fixture edited'`);
 check('manual edit saved as a new revision',(await api('/records/paper/'+fixture.id)).revision===2);
 await ev(`document.querySelector('[data-tab="review"]').click()`);await wait(`document.querySelector('#review-save')`);
 await ev(`document.querySelector('#reviewer').value='UI automated test';document.querySelector('#review-status').value='confirmed';document.querySelector('#opinion').value='Checked synthetic fixture only';document.querySelector('#review-save').click()`);
 await wait(`document.querySelector('#edit-card')`);check('manual review stored',(await api('/records/paper/'+fixture.id)).review_status==='confirmed');
 await ev(`document.querySelector('[data-tab="chat"]').click()`);await wait(`document.querySelector('#chat-input')`);
 check('per-paper memory and auto-update controls present',await ev(`!!document.querySelector('#auto-apply')&&document.querySelector('#paper-content').textContent.includes('持续记忆')`));
 await ev(`document.querySelector('#chat-input').value='请将知识卡 limitations 的文字改为“仅用于界面自动化测试，不作经济学结论”。这是用户明确要求的备注，不需要原文引用。保留其他字段。';document.querySelector('#send').click()`);
 await wait(`document.querySelector('#chat-log')?.textContent.includes('assistant')||document.querySelector('#chat-log')?.textContent.includes('已')||document.querySelector('#chat-job')?.textContent.includes('失败')`,120000).catch(()=>{});
 let deadline=Date.now()+120000,session;
 while(Date.now()<deadline){const js=await api('/jobs?paper_id='+fixture.id);if(js[0]?.status==='failed')throw Error('Chat failed: '+js[0].error);if(js[0]?.status==='completed'){session=(await api('/papers/'+fixture.id+'/messages')).session;break}await new Promise(r=>setTimeout(r,1000))}
 check('real Codex chat applies scoped knowledge-card update',(await api('/records/paper/'+fixture.id)).card.limitations?.text==='仅用于界面自动化测试，不作经济学结论');
 check('persistent Codex thread saved',!!session?.thread_id);
 const chat2=await api('/papers/'+fixture.id+'/chat',{text:'我刚才要求写入哪一句备注？只复述它，不修改任何记录。',auto_apply:false});
 deadline=Date.now()+120000;let answer;
 while(Date.now()<deadline){const j=await api('/jobs/'+chat2.id);if(j.status==='failed')throw Error(j.error);if(j.status==='completed'){answer=j.progress.result;break}await new Promise(r=>setTimeout(r,900))}
 const memory=await api('/papers/'+fixture.id+'/messages');check('second conversation resumes same native thread',memory.session.thread_id===session.thread_id);check('prior research note recalled',answer?.answer?.includes('仅用于界面自动化测试'));
 await ev(`location.hash='methods'`);await wait(`document.querySelector('#records .item')`);await ev(`document.querySelector('#records .item').click()`);await wait(`document.querySelector('#edit-main')`);check('method has human editing and source mappings',await ev(`document.querySelector('#app').textContent.includes('来源文献')&&!!document.querySelector('#review-save')`));
 await ev(`location.hash='tools'`);await wait(`document.querySelector('#records .item')`);await ev(`document.querySelector('#records .item').click()`);await wait(`document.querySelector('#validate-tool')`);check('tool has separate review and numerical validation',await ev(`!!document.querySelector('#review-save')&&!!document.querySelector('#validate-tool')`));
 await ev(`location.hash='solve'`);await wait(`document.querySelector('#plan')`);check('unified solve interface requests question and data',await ev(`!!document.querySelector('#question')&&!!document.querySelector('#data')&&!!document.querySelector('#goals')`));
 await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});check('mobile has no horizontal overflow',await ev(`document.documentElement.scrollWidth<=innerWidth+1`));
 await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1100,deviceScaleFactor:1,mobile:false});await ev(`location.hash='paper/${fixture.id}'`);await wait(`document.querySelector('#delete-paper')`);await ev(`document.querySelector('#delete-paper').click()`);await wait(`location.hash==='#papers'&&document.querySelector('#import')`);
 check('paper delete reconciles linked placeholders',!(await api('/papers')).some(p=>p.id===fixture.id)&&!(await api('/methods')).some(m=>m.id===fixture.methods[0].id)&&!(await api('/tools')).some(t=>t.id===fixture.tools[0].id));
 check('deleted conversation removed locally',(await api('/jobs?paper_id='+fixture.id)).every(j=>!['running','queued'].includes(j.status)));
 check('no browser JavaScript exceptions',errors.length===0);
 const shot=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});await fs.writeFile(new URL('current-ui.png',out),Buffer.from(shot.data,'base64'));
}finally{await fs.writeFile(new URL('browser-checks.json',out),JSON.stringify({checks,errors},null,2));ws.close()}
