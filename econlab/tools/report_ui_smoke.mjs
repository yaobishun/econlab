// Real-browser report checks against an isolated synthetic fixture; never calls AI.
import fs from 'node:fs/promises';
const base=process.env.ECONLAB_TEST_URL||'http://127.0.0.1:8792';
const debug=process.env.ECONLAB_DEBUG_URL||'http://127.0.0.1:9242';
const output=process.env.ECONLAB_TEST_OUTPUT;if(!output)throw Error('Set ECONLAB_TEST_OUTPUT');
const targets=await(await fetch(debug+'/json/list')).json();
const target=targets.find(t=>t.type==='page');const ws=new WebSocket(target.webSocketDebuggerUrl);
await new Promise(r=>ws.addEventListener('open',r,{once:true}));
let seq=0;const pending=new Map(),checks=[],errors=[];
ws.addEventListener('message',event=>{const m=JSON.parse(event.data);if(m.method==='Runtime.exceptionThrown')errors.push(m.params);if(m.id&&pending.has(m.id)){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result)}});
function call(method,params={}){return new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}))})}
async function ev(expression){const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value}
async function wait(expression){const end=Date.now()+25000;while(Date.now()<end){if(await ev(expression))return;await new Promise(r=>setTimeout(r,200))}throw Error('Timeout: '+expression)}
function check(name,value){if(!value)throw Error(name);checks.push(name);console.log('PASS '+name)}
try{
 await fs.mkdir(output,{recursive:true});await call('Runtime.enable');await call('Page.enable');await call('Network.enable');await call('Network.setCacheDisabled',{cacheDisabled:true});
 await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1100,deviceScaleFactor:1,mobile:false});
 const papers=await(await fetch(base+'/api/papers')).json();if(papers.length!==1)throw Error('Use an isolated one-paper fixture');const pid=papers[0].id;
 await call('Page.navigate',{url:base+'/#paper/'+pid});await wait(`!!document.querySelector('[data-tab="report"]')`);
 await ev(`document.querySelector('[data-tab="report"]').click()`);await wait(`!!document.querySelector('#report-audit')`);
 check('all five report sections exist',await ev(`['models','equations','inputs','results','audit'].every(k=>!!document.querySelector('#report-'+k))`));
 check('report is partial and does not imply complete reproduction',await ev(`document.querySelector('.report-intro').textContent.includes('数值通过 1 项')`));
 check('source and AI derivation labels shown',await ev(`document.querySelector('#report-models').textContent.includes('论文原文')&&document.querySelector('#report-equations').textContent.includes('AI 补充推导')`));
 await wait(`!!document.querySelector('#report-equations .katex')`);check('equations rendered locally with KaTeX',true);
 await ev(`document.querySelector('.report-load-result').click()`);await wait(`!!document.querySelector('.report-chart svg')`);
 check('actual/reference chart and full precision table shown',await ev(`document.querySelector('#report-result-0').textContent.includes('100')&&document.querySelector('#report-result-0').textContent.includes('符号比较')`));
 check('CSV and immutable replay-package links available',await ev(`!!document.querySelector('a[href*="results.csv?"]')&&!!document.querySelector('a[href$="/package"]')`));
 await ev(`document.querySelector('[data-report-anchor="report-results"]').click()`);
 check('section navigation does not leave paper route',await ev(`location.hash==='#paper/${pid}'`));
 await ev(`document.querySelector('#edit-report').click()`);await wait(`document.querySelector('#modal').open`);
 await ev(`document.querySelector('#report-editor').value='Browser reviewer';document.querySelector('#report-edit-reason').value='Check report-only edit';var reportEditFixtureValue=JSON.parse(document.querySelector('#report-edit-models').value);reportEditFixtureValue[0].actors='Browser-edited actors <img src=x onerror=alert(1)>';document.querySelector('#report-edit-models').value=JSON.stringify(reportEditFixtureValue);document.querySelector('#save-report-edit').click()`);
 await wait(`!document.querySelector('#modal').open&&document.querySelector('#report-models')?.textContent.includes('Browser-edited actors')`);
 check('manual report edit persists and escapes HTML',await ev(`!document.querySelector('#report-models img')`));
 await ev(`document.querySelector('#report-reviewer').value='Browser reviewer';document.querySelector('#report-opinion').value='Report is informative; remaining experiment is not yet run.';document.querySelector('#report-review-status').value='accepted';document.querySelector('#review-report').click()`);
 await wait(`document.querySelector('#report-audit')?.textContent.includes('终审已确认')`);
 const state=await(await fetch(base+'/api/papers/'+pid+'/experiments')).json();
 check('report final review does not grant solver eligibility',state.inventory.eligible===false&&state.inventory.counts.passed===1);
 await ev(`document.querySelector('#report-models').scrollIntoView()`);
 const shot=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});await fs.writeFile(output+'/report-models.png',Buffer.from(shot.data,'base64'));
 await ev(`document.querySelector('.report-load-result').click()`);await wait(`!!document.querySelector('.report-chart svg')`);await ev(`document.querySelector('.report-chart').scrollIntoView({block:'start'})`);
 const chart=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});await fs.writeFile(output+'/report-results.png',Buffer.from(chart.data,'base64'));
 await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
 check('report chart has a bounded scrolling container on mobile',await ev(`document.querySelector('.report-chart').clientWidth<=document.querySelector('main').clientWidth`));
 check('no browser JavaScript exceptions',errors.length===0);
 await fs.writeFile(output+'/browser-checks.json',JSON.stringify({checks,errors},null,2));
 console.log(checks.length+' report browser checks passed');
}finally{ws.close()}
