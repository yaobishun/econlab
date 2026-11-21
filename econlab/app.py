"""EconLab: four local research interfaces backed by three linked libraries."""
from contextlib import asynccontextmanager
from pathlib import Path
import asyncio,json,os,webbrowser
from fastapi import FastAPI,Request,UploadFile,File,Form,HTTPException
from fastapi.responses import FileResponse,JSONResponse
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

@app.get('/api/bootstrap')
def bootstrap():return {'version':'3.0.0','data_path':str(store.DATA),'stats':catalog.stats(),'provider':'codex','ocr':False,'model':store.get_setting('model')}
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
    model=body.get('model')
    if model and model not in {m['model'] for m in bridge.models()}:raise ValueError('Unknown Codex model')
    store.set_setting('model',model);return {'ok':True}

@app.get('/api/papers')
def papers(q:str=''):return catalog.listing('paper',q)
@app.post('/api/papers')
async def import_paper(file:UploadFile=File(...),analyze:bool=Form(True)):
    data=await file.read(store.MAX_FILE+1);paper,duplicate=await asyncio.to_thread(catalog.import_paper,file.filename or '',data)
    job=engine.submit('analysis',paper['id'],engine.analyze) if analyze and not duplicate else None
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
    job=engine.submit('attachment_sync_'+store.uid(),pid,engine.chat,'一批新附件已导入：'+', '.join(a['name'] for a in assets)+'。请检查全部最新材料，动态更新相关论文、方法和工具记录。已有代码不要无依据改写；需要封装则生成待审核工具，指出缺失输入及参考结果。',True)
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
    return {'session':s,'messages':recent,'total_count':total}
@app.get('/api/papers/{pid}/transcript')
def transcript(pid:str):
    catalog.get('paper',pid);s=catalog.session('paper',pid)
    return JSONResponse({'session':s,'messages':catalog.messages(s['id'])},headers={'Content-Disposition':'attachment; filename="EconLab-conversation-'+pid+'.json"'})
@app.post('/api/papers/{pid}/analyze')
def analyze(pid:str,body:dict|None=None):return engine.submit('analysis',pid,engine.analyze,bool((body or {}).get('resume',False)))
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
    return engine.submit('validation',tool['sources'][0]['id'],lambda jid,pid:runner.execute(tid,body.get('inputs',{}),True,pid))
@app.post('/api/tools/{tid}/run')
def run(tid:str,body:dict):
    tool=catalog.get('tool',tid)
    return engine.submit('calculation',tool['sources'][0]['id'],lambda jid,pid:runner.execute(tid,body.get('inputs',{}),False,pid))

@app.get('/api/records/{kind}/{identifier}')
def record(kind:str,identifier:str):return catalog.get(kind,identifier)
@app.put('/api/records/{kind}/{identifier}')
def edit(kind:str,identifier:str,body:dict):return catalog.update(kind,identifier,body['patch'],body['revision'],body.get('actor',''),body.get('reason',''),bool(body.get('visual_review',False)))
@app.post('/api/records/{kind}/{identifier}/review')
def review(kind:str,identifier:str,body:dict):return catalog.review(kind,identifier,body['status'],body.get('reviewer',''),body.get('opinion',''),body['revision'])
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
