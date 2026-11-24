"""EconLab: four local research interfaces backed by three linked libraries."""
from contextlib import asynccontextmanager
from pathlib import Path
import asyncio,json,os,webbrowser
from fastapi import FastAPI,Request,UploadFile,File,Form,HTTPException
from fastapi.responses import FileResponse,JSONResponse,Response
from fastapi.staticfiles import StaticFiles
import catalog,engine,runner,store
from provider_bridge import bridge

@asynccontextmanager
async def lifespan(app):
    catalog.init();engine.recover()
    yield
    bridge.close()
app=FastAPI(title='EconLab 三库研究工作台',lifespan=lifespan)

@app.middleware('http')
async def local_guard(request,call_next):
    host=request.headers.get('host','').split(':')[0].lower()
    if host not in ('127.0.0.1','localhost','testserver','[::1]'):return JSONResponse({'detail':'Local access only'},403)
    if request.method not in ('GET','HEAD','OPTIONS'):
        origin=request.headers.get('origin')
        if origin and origin not in ('http://'+request.headers.get('host',''),'https://'+request.headers.get('host','')):return JSONResponse({'detail':'Cross-origin writes blocked'},403)
        if request.headers.get('x-econlab')!='1':return JSONResponse({'detail':'Missing local application header'},403)
    try:return await call_next(request)
    except ValueError as e:return JSONResponse({'detail':str(e)},409 if 'conflict' in str(e).lower() else 400)

@app.exception_handler(ValueError)
async def bad(request,error):return JSONResponse({'detail':str(error)},409 if 'conflict' in str(error).lower() else 400)

@app.exception_handler(RuntimeError)
async def unavailable(request,error):return JSONResponse({'detail':str(error)},503)

@app.get('/api/bootstrap')
def bootstrap():return {'version':store.VERSION,'data_path':str(store.DATA),'stats':catalog.stats(),'provider':'codex','ocr':False,'model':store.get_setting('model'),'reproduction_mode':store.get_setting('reproduction_mode','automatic')}
@app.get('/api/connection')
def connection():return bridge.status()
@app.get('/api/models')
def models():return bridge.models()
@app.post('/api/connection/test')
def test_connection():return bridge.test_connection()
@app.post('/api/login')
def login():
    result=bridge.rpc('account/login/start',{'type':'chatgpt'})
    if result.get('authUrl'):webbrowser.open(result['authUrl'])
    return result
@app.post('/api/settings')
def settings(body:dict):
    mode=body.get('reproduction_mode')
    if mode is not None:
        if mode not in ('automatic','manual'):raise ValueError('Unknown reproduction mode')
    model=body.get('model')
    if model and model not in {m['model'] for m in bridge.models()}:raise ValueError('Unknown Codex model')
    with store.db() as c:
        for key,value in [('reproduction_mode',mode),('model',model)]:
            if key in body:c.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,store.encode(value)))
    return {'ok':True}

@app.get('/api/papers')
def papers(q:str=''):return catalog.listing('paper',q)
@app.post('/api/papers')
async def import_paper(file:UploadFile=File(...),analyze:bool=Form(True),complete:bool=Form(True)):
    data=await file.read(store.MAX_FILE+1);paper,duplicate=await asyncio.to_thread(catalog.import_paper,file.filename or '',data)
    job=engine.submit('analysis',paper['id'],engine.import_workflow,False,complete) if analyze and not duplicate else None
    return {'paper':paper,'duplicate':duplicate,'job':job}
@app.delete('/api/papers/{pid}')
def delete_paper(pid:str):return engine.delete_paper(pid)
@app.post('/api/papers/{pid}/assets')
async def asset_upload(pid:str,file:UploadFile|None=File(None),files:list[UploadFile]|None=File(None),role:str=Form('other')):
    if role=='original':raise ValueError('Original PDF is immutable; import a separate version')
    incoming=([file] if file else [])+(files or [])
    if not 1<=len(incoming)<=20:raise ValueError('Upload 1–20 attachments in one batch')
    payloads=[]
    for item in incoming:
        content=await item.read(store.MAX_FILE+1)
        if len(content)>store.MAX_FILE:raise ValueError('Single file exceeds 50 MB')
        payloads.append((item.filename or '',content))
    assets=[catalog.add_asset(pid,name,content,role) for name,content in payloads]
    job=engine.submit('attachment_sync',pid,engine.attachment_workflow,[a['name'] for a in assets])
    return {'asset':assets[0],'assets':assets,'job':job}
@app.get('/api/assets/{aid}/file')
def asset_file(aid:str):
    a=catalog.asset(aid);path=catalog.asset_path(aid)
    return FileResponse(path,filename=a['name'],content_disposition_type='inline' if path.suffix=='.pdf' else 'attachment')
@app.get('/api/papers/{pid}/pages/{number}')
def page(pid:str,number:int):return catalog.page(pid,number)
@app.get('/api/papers/{pid}/messages')
def messages(pid:str):
    catalog.get('paper',pid);s=catalog.session('paper',pid)
    with store.db() as c:
        rows=c.execute("SELECT * FROM messages WHERE session_id=? AND (kind='display' OR (kind='chat' AND role='user')) ORDER BY rowid DESC LIMIT 100",(s['id'],)).fetchall()
        total=c.execute('SELECT count(*) FROM messages WHERE session_id=?',(s['id'],)).fetchone()[0]
    recent=[catalog._decode(r) for r in reversed(rows)]
    for m in recent:
        if m['role']=='user':m['text']=m['text'].split('\n当前三个库')[0]
    execution=catalog.session('paper_agent',pid)
    return {'session':s,'messages':recent,'total_count':total,'agent_session':execution}
@app.get('/api/papers/{pid}/transcript')
def transcript(pid:str):
    catalog.get('paper',pid);s=catalog.session('paper',pid)
    execution=catalog.session('paper_agent',pid)
    return JSONResponse({'session':s,'messages':catalog.messages(s['id']),'agent_session':execution,'agent_messages':catalog.messages(execution['id'])},headers={'Content-Disposition':'attachment; filename="EconLab-conversation-'+pid+'.json"'})
@app.post('/api/papers/{pid}/analyze')
def analyze(pid:str,body:dict|None=None):return engine.submit('analysis',pid,engine.import_workflow,bool((body or {}).get('resume',False)),bool((body or {}).get('complete',True)))

@app.get('/api/papers/{pid}/reproduction')
def reproduction_state(pid:str):
    import materials
    catalog.get('paper',pid);return materials.state(pid)

@app.get('/api/papers/{pid}/report')
def paper_report(pid:str):
    import reproduction_report
    return reproduction_report.collect(pid)

@app.put('/api/papers/{pid}/report')
def save_paper_report(pid:str,body:dict):
    import reproduction_report
    return reproduction_report.save(pid,body['document'],body['revision'],body['basis'],body.get('actor',''),body.get('reason',''))

@app.post('/api/papers/{pid}/report/generate')
def generate_paper_report(pid:str,body:dict):
    import reproduction_report
    catalog.get('paper',pid)
    return engine.submit('reproduction_report',pid,reproduction_report.generate,body['revision'],body['basis'])

@app.post('/api/papers/{pid}/report/review')
def review_paper_report(pid:str,body:dict):
    import reproduction_report
    return reproduction_report.review(pid,body['revision'],body['basis'],body['status'],body.get('reviewer',''),body.get('opinion',''))

@app.get('/api/papers/{pid}/report/results')
def paper_report_results(pid:str,experiment_id:str,field:str,offset:int=0,limit:int=100):
    import reproduction_report
    return reproduction_report.result_rows(pid,experiment_id,field,offset,limit)

@app.get('/api/papers/{pid}/report/results.csv')
def paper_report_csv(pid:str,experiment_id:str,field:str):
    import reproduction_report
    return Response(reproduction_report.result_csv(pid,experiment_id,field),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="EconLab-comparison.csv"'})

@app.get('/api/papers/{pid}/report/export')
def paper_report_export(pid:str):
    import reproduction_report
    return JSONResponse(reproduction_report.export_document(pid),headers={'Content-Disposition':'attachment; filename="EconLab-reproduction-'+pid+'.json"'})

@app.get('/api/papers/{pid}/report/runs/{rid}/package')
def paper_report_package(pid:str,rid:str):
    import reproduction_report
    return Response(reproduction_report.replay_package(pid,rid),media_type='application/zip',headers={'Content-Disposition':'attachment; filename="EconLab-run-'+rid+'.zip"'})

@app.post('/api/papers/{pid}/reproduction')
def complete_materials(pid:str,body:dict|None=None):
    import reproduction
    body=body or {}
    if set(body)-{'urls','refresh_search','retry_failed','repair','phase','tool_id','experiment_request'}:raise ValueError('Unknown material workflow option')
    if not isinstance(body.get('experiment_request',''),str) or len(body.get('experiment_request',''))>4000:raise ValueError('实验与输出目标最多4000字')
    urls=body.get('urls',[])
    if not isinstance(urls,list) or any(not isinstance(u,str) for u in urls) or len(urls)>10:raise ValueError('请提供至多10个网址')
    for key in ('refresh_search','retry_failed','repair'):
        if key in body and type(body[key]) is not bool:raise ValueError('Invalid workflow option')
    if body.get('phase','prepare') not in ('prepare','verify'):raise ValueError('Unknown workflow phase')
    return engine.submit('reproduction',pid,reproduction.complete,body)
@app.post('/api/papers/{pid}/stop')
def stop_paper(pid:str):return engine.stop_paper(pid)

@app.get('/api/papers/{pid}/experiments')
def experiment_inventory(pid:str):
    import experiments,workflow
    catalog.get('paper',pid)
    return {'inventory':experiments.public_summary(pid),'controller':workflow.state(pid),'memory':experiments.memory(pid)}

@app.put('/api/papers/{pid}/experiments')
def edit_experiments(pid:str,body:dict):
    import experiments
    experiments.save(pid,body['document'],body['revision'],body.get('actor',''),body.get('reason',''))
    return experiment_inventory(pid)

@app.post('/api/papers/{pid}/experiments/review')
def review_experiments(pid:str,body:dict):
    import experiments
    return experiments.review(pid,body['revision'],body.get('reviewer',''),body.get('opinion',''))

@app.get('/api/papers/{pid}/experiments/history')
def experiment_history(pid:str):
    catalog.get('paper',pid)
    import automation
    with store.db() as c:
        return {'revisions':[dict(r)|{'document':store.decode(r['document'])} for r in c.execute('SELECT * FROM experiment_plan_history WHERE paper_id=? ORDER BY revision DESC',(pid,))],
                'approvals':[dict(r) for r in c.execute('SELECT * FROM experiment_approvals WHERE paper_id=? ORDER BY created DESC',(pid,))],
                'qualitative_checks':[dict(r) for r in c.execute('SELECT * FROM qualitative_checks WHERE paper_id=? ORDER BY created DESC',(pid,))],
                'automatic_checks':automation.history(pid)}

@app.post('/api/papers/{pid}/experiments/qualitative')
def qualitative_check(pid:str,body:dict):
    import experiments
    return experiments.check_qualitative(pid,body['experiment_id'],body['revision'],body.get('reviewer',''),body.get('opinion',''))

@app.get('/api/papers/{pid}/agent')
def agent_task(pid:str):
    import execution_agent
    catalog.get('paper',pid);return execution_agent.latest(pid)
@app.post('/api/papers/{pid}/chat')
def chat(pid:str,body:dict):
    text=body.get('text','').strip()
    if not text or len(text)>20000:raise ValueError('请输入 1—20000 字的追问')
    return engine.submit('chat',pid,engine.chat,text,bool(body.get('auto_apply',True)))

@app.get('/api/methods')
def methods(q:str=''):return catalog.listing('method',q)
@app.get('/api/tools')
def tools(q:str=''):return catalog.listing('tool',q)
@app.post('/api/papers/{pid}/methods')
def create_method(pid:str,body:dict):
    spec=body.get('spec',{})
    spec['evidence']=catalog.check_evidence(pid,spec.get('evidence',[]),bool(body.get('visual_review',False)))
    record=catalog.create_method(pid,body.get('name',''),spec,'manual:'+body.get('actor','researcher'))
    if not catalog.get('method',record['id'])['tools']:catalog.create_tool(pid,record['id'],record['name']+' · 工具候选',{},actor='manual')
    return catalog.get('method',record['id'])
@app.post('/api/methods/{mid}/tools')
def create_tool(mid:str,body:dict):
    return catalog.create_tool(body.get('paper_id',''),mid,body.get('name',''),body.get('spec',{}),body.get('code',''),body.get('language','python'),'manual:'+body.get('actor','researcher'))
@app.post('/api/tools/{tid}/validate')
def validate(tid:str,body:dict):
    tool=catalog.get('tool',tid)
    return engine.submit('validation',tool['sources'][0]['id'],engine.validate_tool,tid,body.get('inputs',{}))

@app.get('/api/tools/{tid}/environment')
def environment_status(tid:str):
    import environments
    return environments.status(catalog.get('tool',tid))

@app.post('/api/tools/{tid}/environment')
def environment_prepare(tid:str):
    import environments
    tool=catalog.get('tool',tid)
    return engine.submit('environment',tool['sources'][0]['id'],lambda jid,pid:environments.prepare(tid,lambda:engine.gate(jid,pid)))
@app.post('/api/tools/{tid}/run')
def run(tid:str,body:dict):
    tool=catalog.get('tool',tid)
    return engine.submit('calculation',tool['sources'][0]['id'],lambda jid,pid:runner.execute(tid,body.get('inputs',{}),False,pid))

@app.get('/api/records/{kind}/{identifier}')
def record(kind:str,identifier:str):
    result=catalog.get(kind,identifier)
    import experiments,workflow
    if kind=='paper':
        result['reproduction_mode']=store.get_setting('reproduction_mode','automatic')
        result['experiment_inventory']=experiments.public_summary(identifier)
        result['controller']=workflow.state(identifier)
        for asset in result['assets']:asset.pop('text',None)
        for url in result.get('reproduction',{}).get('urls',[]):url.get('result',{}).pop('text',None)
    elif kind=='method':result['paper_experiments']=[{'paper_id':p['id'],'title':p['title'],'inventory':experiments.public_summary(p['id'])} for p in result['sources']]
    elif kind=='tool':
        try:
            eligibility=experiments.require_eligible(result)
            result['execution_eligible']=True;result['paper_coverage']=eligibility['counts']
        except ValueError as exc:result['execution_eligible']=False;result['eligibility_gap']=str(exc)
    if kind in ('method','tool'):
        import automation
        try:result['automatic_check_current']=automation.checked(kind,identifier,automation.binding(kind,result))
        except (ValueError,OSError):result['automatic_check_current']=False
    return result
@app.put('/api/records/{kind}/{identifier}')
def edit(kind:str,identifier:str,body:dict):
    result=catalog.update(kind,identifier,body['patch'],body['revision'],body.get('actor',''),body.get('reason',''),bool(body.get('visual_review',False)))
    record_decision(kind,result,{'kind':'manual_edit','actor':body.get('actor',''),'reason':body.get('reason',''),'revision':result['revision']})
    return result
@app.post('/api/records/{kind}/{identifier}/review')
def review(kind:str,identifier:str,body:dict):
    result=catalog.review(kind,identifier,body['status'],body.get('reviewer',''),body.get('opinion',''),body['revision'])
    record_decision(kind,result,{'kind':'human_review','reviewer':body.get('reviewer',''),'opinion':body.get('opinion',''),'status':body['status'],'revision':result['revision']})
    return result

def record_decision(kind,result,event):
    import experiments
    pids=[result['id']] if kind=='paper' else [p['id'] for p in result['sources']]
    for pid in pids:experiments.remember(pid,event={**event,'entity_type':kind,'entity_id':result['id']})
@app.post('/api/records/{kind}/{identifier}/restore')
def restore(kind:str,identifier:str,body:dict):return catalog.restore(kind,identifier,body['restore_revision'],body['revision'],body.get('actor',''))
@app.get('/api/jobs')
def jobs(paper_id:str|None=None):return catalog.jobs(paper_id)
@app.get('/api/jobs/{jid}')
def job(jid:str):return catalog.job_get(jid)
@app.get('/api/runs/{rid}')
def run_get(rid:str):
    with store.db() as c:return catalog._decode(catalog._row(c,'runs',rid))
@app.get('/api/runs/{rid}/report')
def report(rid:str):
    run_get(rid);return FileResponse(store.file_path('runs/'+rid+'/report.json'),filename='EconLab-'+rid+'.json')
@app.post('/api/solve')
def solve(body:dict):
    question=body.get('question','').strip()
    if not question or len(question)>20000:raise ValueError('请输入研究问题')
    return engine.submit('solve',None,engine.solve_plan,question,body.get('conditions',{}))
@app.get('/api/solve/{sid}')
def solve_get(sid:str):
    with store.db() as c:return catalog._decode(catalog._row(c,'solve_tasks',sid))
@app.get('/api/solve/{sid}/report')
def solve_report(sid:str):
    task=solve_get(sid)
    return JSONResponse(task,headers={'Content-Disposition':'attachment; filename="EconLab-solve-'+sid+'.json"'})
@app.post('/api/solve/{sid}/execute')
def solve_execute(sid:str,body:dict):return engine.submit('solve_execute',None,engine.solve_execute,sid,body.get('inputs',{}),body.get('confirmed',False))

app.mount('/static',StaticFiles(directory=store.ROOT/'static'),name='static')
@app.get('/')
def index():return FileResponse(store.ROOT/'static'/'index.html')
