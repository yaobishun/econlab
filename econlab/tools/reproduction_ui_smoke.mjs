// Headless CDP acceptance for the material workflow; use an isolated test server.
import fs from 'node:fs/promises';
const base=process.env.ECONLAB_TEST_URL||'http://127.0.0.1:8780';
const debug=process.env.ECONLAB_DEBUG_URL||'http://127.0.0.1:9230';
const out=process.env.ECONLAB_TEST_OUTPUT;if(!out)throw Error('ECONLAB_TEST_OUTPUT is required');
const target=(await(await fetch(debug+'/json/list')).json()).find(t=>t.type==='page');
const ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise(r=>ws.addEventListener('open',r,{once:true}));
let n=0;const pending=new Map(),checks=[],errors=[];
function call(method,params={}){return new Promise((resolve,reject)=>{const id=++n;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}))})}
ws.addEventListener('message',e=>{const m=JSON.parse(e.data);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p?.reject(Error(m.error.message)):p?.resolve(m.result)}if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails)});
async function ev(expression){const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(r.exceptionDetails.exception?.description||r.exceptionDetails.text);return r.result?.value}
async function wait(expression){const until=Date.now()+20000;while(Date.now()<until){if(await ev(expression))return;await new Promise(r=>setTimeout(r,180))}throw Error('Timeout: '+expression)}
function check(name,value){checks.push({name,passed:!!value});if(!value)throw Error(name);console.log('PASS',name)}
async function api(path,body,method){const r=await fetch(base+'/api'+path,{method:method||(body?'POST':'GET'),headers:{'X-EconLab':'1',...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined});const value=await r.json();if(!r.ok)throw Error(JSON.stringify(value));return value}
let fixture;
try{
 await call('Runtime.enable');await call('Page.enable');await call('Network.enable');await call('Network.setCacheDisabled',{cacheDisabled:true});await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1100,deviceScaleFactor:1,mobile:false});
 await call('Page.navigate',{url:base+'/#papers'});await wait(`document.querySelector('#import')`);
 check('four business pages retained',await ev(`document.querySelectorAll('nav a').length===4`));
 await ev(`document.querySelector('#import').click()`);await wait(`document.querySelector('#complete-materials')`);
 check('import defaults to automatic material preparation',await ev(`document.querySelector('#complete-materials').checked&&document.querySelector('#analyze').checked`));await ev(`document.querySelector('#modal').close()`);
 const form=new FormData();form.append('file',new Blob([await fs.readFile(out+'/ui-fixture.pdf')],{type:'application/pdf'}),'Controlled UI fixture.pdf');form.append('analyze','false');
 fixture=(await(await fetch(base+'/api/papers',{method:'POST',headers:{'X-EconLab':'1'},body:form})).json()).paper;
 await ev(`location.hash='paper/${fixture.id}'`);await wait(`document.querySelector('#continue-materials')`);
 check('paper has continue, fresh search and source history',await ev(`!!document.querySelector('#refresh-materials')&&!!document.querySelector('#material-urls')&&document.querySelector('#reproduction-panel').textContent.includes('来源与搜索')`));
 await ev(`document.querySelector('#continue-materials').click()`);await wait(`document.querySelector('#material-job').textContent.includes('先完成PDF')`);
 check('incomplete PDF is blocked and button recovers',await ev(`!document.querySelector('#continue-materials').disabled`));
 const screenshot=await call('Page.captureScreenshot',{format:'png'});await fs.writeFile(out+'/ui-reproduction-panel.png',Buffer.from(screenshot.data,'base64'));
 const tid=fixture.tools[0].id;await ev(`location.hash='tool/${tid}'`);await wait(`document.querySelector('#prepare-environment')`);
 check('unified validation label and environment setup shown',await ev(`document.querySelector('#validate-tool').textContent==='复现验证'`));
 await ev(`document.querySelector('#edit-main').click()`);await wait(`document.querySelector('#edit-requirements')`);
 await ev(`document.querySelector('#edit-name').value='Controlled UI implementation';document.querySelector('#edit-code').value="def run(inputs, workspace):\\n return {'x':[1]}\\n";document.querySelector('#edit-requirements').value='packaging==25.0';document.querySelector('#edit-actor').value='UI test';document.querySelector('#edit-reason').value='Controlled form roundtrip';document.querySelector('#edit-save').click()`);
 await wait(`!document.querySelector('#modal').open&&document.querySelector('h1')?.textContent==='Controlled UI implementation'`);
 const updated=await api('/records/tool/'+tid);check('environment configuration saved as versioned tool contract',updated.spec.environment.requirements[0]==='packaging==25.0'&&updated.review_status==='draft');
 await ev(`document.querySelector('#prepare-environment').click()`);await wait(`document.querySelector('#tool-job').textContent.includes('先人工审核')`);
 check('runtime setup requires review',await ev(`document.querySelector('#tool-job').textContent.includes('依赖配置')`));
 check('no JavaScript exceptions',errors.length===0);
}finally{
 if(fixture)await api('/papers/'+fixture.id,undefined,'DELETE');
 await fs.writeFile(out+'/ui-checks.json',JSON.stringify({checks,errors},null,2));ws.close();
}
