"""Versioned papers, reusable methods and executable tools with shared provenance."""
from __future__ import annotations
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import fitz
import store

METHOD_FIELDS=('problem','assumptions','mechanisms','equations','steps','data_requirements','scope','inputs','outputs','limitations','rationale')
CARD_FIELDS=('research_question','method','assumptions','mechanisms','equations','steps','data_requirements','scope','findings','limitations','rationale','model_sources')
ROLES={'original','appendix','data','code','reference','derivation','other'}
TYPES={'paper':'papers','method':'methods','tool':'tools'}


def init():
    store.init()
    with store.db() as c:
        old=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='documents'").fetchone()
        if old:raise RuntimeError('Legacy database found. Archive it before starting the new three-library edition.')
        c.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS papers(id TEXT PRIMARY KEY,title TEXT NOT NULL,authors TEXT NOT NULL,year TEXT,
            version_label TEXT,doi TEXT,original_asset TEXT,card TEXT NOT NULL,coverage TEXT NOT NULL,
            review_status TEXT NOT NULL,pipeline_status TEXT NOT NULL,revision INTEGER NOT NULL,created TEXT,updated TEXT);
        CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY,paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
            role TEXT,name TEXT,sha256 TEXT,path TEXT,text TEXT,metadata TEXT,created TEXT);
        CREATE INDEX IF NOT EXISTS asset_paper ON assets(paper_id);
        CREATE TABLE IF NOT EXISTS pages(paper_id TEXT REFERENCES papers(id) ON DELETE CASCADE,page INTEGER,text TEXT,
            visual_notes TEXT,quality TEXT,width REAL,height REAL,PRIMARY KEY(paper_id,page));
        CREATE TABLE IF NOT EXISTS methods(id TEXT PRIMARY KEY,name TEXT NOT NULL,identity TEXT,spec TEXT NOT NULL,
            review_status TEXT NOT NULL,revision INTEGER NOT NULL,created TEXT,updated TEXT);
        CREATE TABLE IF NOT EXISTS method_sources(method_id TEXT REFERENCES methods(id) ON DELETE CASCADE,
            paper_id TEXT REFERENCES papers(id) ON DELETE CASCADE,evidence TEXT,PRIMARY KEY(method_id,paper_id));
        CREATE TABLE IF NOT EXISTS tools(id TEXT PRIMARY KEY,method_id TEXT REFERENCES methods(id) ON DELETE CASCADE,
            name TEXT NOT NULL,language TEXT,spec TEXT NOT NULL,code TEXT NOT NULL,review_status TEXT NOT NULL,
            validation_status TEXT NOT NULL,revision INTEGER NOT NULL,created TEXT,updated TEXT);
        CREATE TABLE IF NOT EXISTS tool_sources(tool_id TEXT REFERENCES tools(id) ON DELETE CASCADE,
            paper_id TEXT REFERENCES papers(id) ON DELETE CASCADE,PRIMARY KEY(tool_id,paper_id));
        CREATE TABLE IF NOT EXISTS revisions(id TEXT PRIMARY KEY,entity_type TEXT,entity_id TEXT,revision INTEGER,
            before_data TEXT,after_data TEXT,actor TEXT,reason TEXT,created TEXT);
        CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY,entity_type TEXT,entity_id TEXT,revision INTEGER,
            status TEXT,reviewer TEXT,opinion TEXT,created TEXT);
        CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,scope TEXT,scope_id TEXT,thread_id TEXT,summary TEXT,
            model TEXT,created TEXT,updated TEXT,UNIQUE(scope,scope_id));
        CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
            role TEXT,kind TEXT,text TEXT,metadata TEXT,created TEXT);
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,kind TEXT,paper_id TEXT,status TEXT,stage TEXT,
            progress TEXT,error TEXT,created TEXT,updated TEXT);
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,tool_id TEXT,tool_revision INTEGER,paper_id TEXT,
            kind TEXT,status TEXT,request TEXT,result TEXT,fingerprint TEXT,scope TEXT,created TEXT,updated TEXT);
        CREATE TABLE IF NOT EXISTS solve_tasks(id TEXT PRIMARY KEY,question TEXT,conditions TEXT,plan TEXT,
            result TEXT,status TEXT,created TEXT,updated TEXT);
        CREATE TABLE IF NOT EXISTS deletions(id TEXT PRIMARY KEY,kind TEXT,target_id TEXT,details TEXT,created TEXT);
        ''')
    import materials
    materials.init()
    import execution_agent
    execution_agent.init()


def clean(value):return re.sub(r'\s+',' ',value or '').strip()


def _row(c,table,identifier):
    result=c.execute('SELECT * FROM '+table+' WHERE id=?',(identifier,)).fetchone()
    if not result:raise ValueError('Record not found: '+identifier)
    return dict(result)


def _decode(row):
    result=dict(row)
    for key in ('authors','card','coverage','spec','metadata','progress','request','result','plan','conditions','evidence'):
        if key in result:result[key]=store.decode(result[key],{} if key not in ('authors','evidence') else [])
    return result


def _audit(c,kind,identifier,before,after,actor,reason):
    if not str(actor).strip():raise ValueError('Please provide the editor or reviewer')
    c.execute('INSERT INTO revisions VALUES (?,?,?,?,?,?,?,?,?)',(store.uid(),kind,identifier,after['revision'],
        store.encode(before) if before else None,store.encode(after),actor,reason,store.now()))


def get(kind,identifier):
    if kind not in TYPES:raise ValueError('Unknown library')
    with store.db() as c:
        value=_decode(_row(c,TYPES[kind],identifier))
        value['history']=[_decode(r) for r in c.execute('SELECT * FROM revisions WHERE entity_type=? AND entity_id=? ORDER BY revision DESC',(kind,identifier))]
        value['reviews']=[dict(r) for r in c.execute('SELECT * FROM reviews WHERE entity_type=? AND entity_id=? ORDER BY created DESC',(kind,identifier))]
        if kind=='paper':
            import materials
            value['reproduction']=materials.state(identifier)
            value['assets']=[_decode(r) for r in c.execute('SELECT * FROM assets WHERE paper_id=? ORDER BY created',(identifier,))]
            value['methods']=[_decode(r) for r in c.execute('SELECT m.* FROM methods m JOIN method_sources s ON m.id=s.method_id WHERE s.paper_id=?',(identifier,))]
            value['tools']=[_decode(r) for r in c.execute('SELECT t.* FROM tools t JOIN tool_sources s ON t.id=s.tool_id WHERE s.paper_id=?',(identifier,))]
            value['page_count']=c.execute('SELECT count(*) FROM pages WHERE paper_id=?',(identifier,)).fetchone()[0]
        else:
            source_table='method_sources' if kind=='method' else 'tool_sources'
            key='method_id' if kind=='method' else 'tool_id'
            value['sources']=[dict(r) for r in c.execute('SELECT p.id,p.title,p.revision,p.review_status FROM papers p JOIN '+source_table+' s ON p.id=s.paper_id WHERE s.'+key+'=?',(identifier,))]
            if kind=='method':
                value['tools']=[_decode(r) for r in c.execute('SELECT * FROM tools WHERE method_id=?',(identifier,))]
                value['spec']['evidence']=[ref for row in c.execute('SELECT evidence FROM method_sources WHERE method_id=?',(identifier,)) for ref in store.decode(row[0],[])]
                value['reproduction_plans']=[]
                for row in c.execute('SELECT w.paper_id,w.status,w.stage,w.state FROM material_workflows w JOIN method_sources s ON s.paper_id=w.paper_id WHERE s.method_id=?',(identifier,)):
                    data=store.decode(row['state'],{})
                    if data.get('method_id')==identifier:value['reproduction_plans'].append({'paper_id':row['paper_id'],'status':row['status'],'stage':row['stage'],'plan':data.get('method_plan',{})})
            else:
                value['runs']=[_decode(r) for r in c.execute('SELECT * FROM runs WHERE tool_id=? ORDER BY created DESC LIMIT 30',(identifier,))]
                import environments
                try:value['environment']=environments.status(value)
                except ValueError as exc:value['environment']={'status':'failed','error':str(exc)}
    return value


def listing(kind,q=''):
    if kind not in TYPES:raise ValueError('Unknown library')
    with store.db() as c:rows=[_decode(r) for r in c.execute('SELECT * FROM '+TYPES[kind]+' ORDER BY updated DESC')]
    return [r for r in rows if not q or q.lower() in store.encode(r).lower()]


def stats():
    with store.db() as c:
        counts={name:c.execute('SELECT count(*) FROM '+name).fetchone()[0] for name in ('papers','methods','tools')}
        counts['verified_tools']=c.execute("SELECT count(*) FROM tools WHERE validation_status='verified'").fetchone()[0]
    return counts


def add_asset(paper_id,name,content,role='other'):
    if role not in ROLES:raise ValueError('Unknown attachment role')
    if len(content)>store.MAX_FILE:raise ValueError('Single file exceeds 50 MB')
    name=Path(name.replace('\\','/')).name
    if not name or len(name)>250:raise ValueError('Invalid filename')
    digest=hashlib.sha256(content).hexdigest();suffix=Path(name).suffix.lower()
    if suffix not in {'.pdf','.txt','.md','.py','.m','.json','.csv','.mat','.zip','.rar','.r','.do','.ipynb','.yaml','.yml','.toml','.ini','.lock','.dta','.xlsx','.npy','.npz','.pkl','.pickle','.rda','.rdata','.rds','.parquet','.gz','.tgz','.tar'}:raise ValueError('Unsupported attachment format')
    relative='files/'+digest+suffix;path=store.file_path(relative)
    text=''
    if suffix in {'.txt','.md','.py','.m','.json','.csv','.r','.do','.yaml','.yml','.toml','.ini','.lock'}:
        try:text=content.decode('utf-8-sig')
        except UnicodeDecodeError:text=content.decode('gb18030',errors='replace')
    metadata={'size':len(content),'suffix':suffix}
    with store.db() as c:
        _row(c,'papers',paper_id)
        old=c.execute('SELECT * FROM assets WHERE paper_id=? AND sha256=? AND role=?',(paper_id,digest,role)).fetchone()
        if old:return _decode(old)
        if not path.exists():path.write_bytes(content)
        identifier=store.uid()
        c.execute('INSERT INTO assets VALUES (?,?,?,?,?,?,?,?,?)',(identifier,paper_id,role,name,digest,relative,text,store.encode(metadata),store.now()))
    return asset(identifier)


def asset(identifier):
    with store.db() as c:return _decode(_row(c,'assets',identifier))


def asset_path(identifier):
    value=asset(identifier);path=store.file_path(value['path'])
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=value['sha256']:raise ValueError('Asset missing or hash mismatch: '+value['name'])
    return path


def import_paper(name,content):
    if not name.lower().endswith('.pdf'):raise ValueError('Please import a PDF paper')
    digest=hashlib.sha256(content).hexdigest()
    with store.db() as c:
        previous=c.execute("SELECT p.id FROM papers p JOIN assets a ON a.id=p.original_asset WHERE a.sha256=?",(digest,)).fetchone()
    if previous:return get('paper',previous['id']),True
    if len(content)>store.MAX_FILE:raise ValueError('PDF exceeds 50 MB')
    with fitz.open(stream=content,filetype='pdf') as document:
        if document.needs_pass or not 1<=len(document)<=1200:raise ValueError('Encrypted PDF or unsupported page count')
        metadata=document.metadata or {};rows=[]
        for index,page in enumerate(document):
            text=page.get_text('text',sort=True).strip()
            rows.append((index+1,text,'', 'text' if text else 'visual',page.rect.width,page.rect.height))
    identifier=store.uid();now=store.now();title=metadata.get('title') or Path(name).stem
    if len(title)>500:title=Path(name).stem
    with store.db() as c:
        value={'id':identifier,'title':title,'authors':[metadata['author']] if metadata.get('author') else [],'year':'','version_label':'original','doi':'','original_asset':'',
               'card':{},'coverage':{},'review_status':'draft','pipeline_status':'imported','revision':1,'created':now,'updated':now}
        c.execute('INSERT INTO papers VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',tuple(store.encode(value[k]) if k in ('authors','card','coverage') else value[k] for k in value))
        c.executemany('INSERT INTO pages VALUES (?,?,?,?,?,?,?)',[(identifier,*r) for r in rows])
        _audit(c,'paper',identifier,None,value,'import','Imported immutable original PDF; no OCR')
    original=add_asset(identifier,name,content,'original')
    with store.db() as c:
        c.execute('UPDATE papers SET original_asset=? WHERE id=?',(original['id'],identifier))
        value['original_asset']=original['id']
        c.execute("UPDATE revisions SET after_data=? WHERE entity_type='paper' AND entity_id=? AND revision=1",(store.encode(value),identifier))
    ensure_placeholder(identifier)
    return get('paper',identifier),False


def page(paper_id,number):
    with store.db() as c:
        row=c.execute('SELECT * FROM pages WHERE paper_id=? AND page=?',(paper_id,number)).fetchone()
    if not row:raise ValueError('PDF page not found')
    return dict(row)


def check_evidence(paper_id,refs,allow_visual=False):
    result=[]
    for ref in refs:
        if not isinstance(ref,dict) or type(ref.get('page')) is not int or not isinstance(ref.get('quote'),str) or len(clean(ref['quote']))<8:raise ValueError('Evidence requires a PDF page and at least 8 quoted characters')
        pid=ref.get('paper_id') or paper_id
        if pid!=paper_id:raise ValueError('Evidence must belong to the current source paper')
        p=page(pid,ref['page']);matched=clean(ref['quote']) in clean(p['text'])
        if not matched and not allow_visual:raise ValueError('Quotation does not match the PDF text layer; use explicit visual review')
        result.append({'paper_id':pid,'page':ref['page'],'quote':ref['quote'],'verification':'text' if matched else 'visual'})
    return result


def ensure_placeholder(paper_id):
    paper=get('paper',paper_id)
    if paper['methods']:return
    spec={key:'' for key in METHOD_FIELDS};spec.update(evidence=[],material_gaps=['Awaiting Codex full-PDF extraction'])
    method=create_method(paper_id,'待提取：'+paper['title'],spec,'system')
    create_tool(paper_id,method['id'],'待建设：'+paper['title'],{},'','python','system')


def create_method(paper_id,name,spec,actor='AI'):
    get('paper',paper_id)
    if not isinstance(name,str) or not name.strip() or not isinstance(spec,dict):raise ValueError('A method requires a name and structured specification')
    identifier=store.uid();now=store.now();identity=store.digest({'name':clean(name).lower(),'equations':spec.get('equations',''),'assumptions':spec.get('assumptions','')})
    with store.db() as c:
        existing=c.execute('SELECT id FROM methods WHERE identity=?',(identity,)).fetchone()
        if existing:
            added=not c.execute('SELECT 1 FROM method_sources WHERE method_id=? AND paper_id=?',(existing['id'],paper_id)).fetchone()
            before=_decode(_row(c,'methods',existing['id']))
            c.execute('INSERT OR IGNORE INTO method_sources VALUES (?,?,?)',(existing['id'],paper_id,store.encode(spec.get('evidence',[]))))
            if added:
                combined=copy.deepcopy(before['spec']);combined['evidence']=[ref for row in c.execute('SELECT evidence FROM method_sources WHERE method_id=?',(existing['id'],)) for ref in store.decode(row[0],[])]
                after={**before,'spec':combined,'revision':before['revision']+1,'review_status':'draft','updated':now}
                c.execute("UPDATE methods SET spec=?,revision=?,review_status='draft',updated=? WHERE id=?",(store.encode(combined),after['revision'],now,existing['id']))
                _audit(c,'method',existing['id'],before,after,actor,'Additional source paper linked');_invalidate(c,[existing['id']])
            return _decode(_row(c,'methods',existing['id']))
        value={'id':identifier,'name':name,'identity':identity,'spec':spec,'review_status':'draft','revision':1,'created':now,'updated':now}
        c.execute('INSERT INTO methods VALUES (?,?,?,?,?,?,?,?)',(identifier,name,identity,store.encode(spec),'draft',1,now,now))
        c.execute('INSERT INTO method_sources VALUES (?,?,?)',(identifier,paper_id,store.encode(spec.get('evidence',[]))))
        _audit(c,'method',identifier,None,value,actor,'Method linked to source paper')
    return get('method',identifier)


def check_tool_contract(spec):
    if not isinstance(spec,dict):raise ValueError('Tool configuration must be an object')
    params=spec.get('parameters',[])
    if not isinstance(params,list):raise ValueError('parameters必须是业务参数列表；数组数据放入input_asset文件，无业务参数使用[]')
    names=[]
    for field in params:
        if not isinstance(field,dict) or not isinstance(field.get('name'),str) or not field['name'].strip() or field.get('type','number') not in ('number','integer','boolean','string'):raise ValueError('业务参数需声明name及number/integer/boolean/string类型；数组放入输入文件')
        names.append(field['name'])
    if len(names)!=len(set(names)):raise ValueError('Duplicate tool parameter names')
    paths=spec.get('bundle_paths',{})
    if not isinstance(paths,dict) or any(not isinstance(aid,str) or not (isinstance(value,str) or isinstance(value,list) and all(isinstance(p,str) for p in value)) for aid,value in paths.items()):raise ValueError('bundle_paths必须是附件ID到相对路径的对象，无映射使用{}')
    fields=spec.get('validation_fields',[])
    if not isinstance(fields,list) or any(not isinstance(v,str) or not v for v in fields):raise ValueError('validation_fields must list numeric output field names')


def create_tool(paper_id,method_id,name,spec,code='',language='python',actor='AI'):
    if not isinstance(name,str) or not name.strip() or not isinstance(spec,dict) or not isinstance(code,str) or language not in ('python','matlab'):raise ValueError('Invalid tool name, configuration, code or runtime')
    check_tool_contract(spec)
    import environments
    environments.configuration({'language':language,'spec':spec})
    from runner import VALIDATION_POLICY
    if 'validation_policy' in spec and spec['validation_policy']!=VALIDATION_POLICY:raise ValueError('复现验收采用宿主统一标准，不能在工具说明中更改')
    if spec.get('request_mode','file') not in ('file','json'):raise ValueError('request_mode must be file or json')
    authorized={a['id'] for a in get('paper',paper_id)['assets']}
    ids=list(spec.get('asset_ids',[]))+list(spec.get('author_code_assets',[]))+list(spec.get('code_assets',[]))+[spec[k] for k in ('input_asset','reference_asset') if spec.get(k)]
    if any(a not in authorized for a in ids):raise ValueError('Tool materials must be attached to this source paper')
    if spec.get('source_kind')=='author_adapter' and actor!='bootstrap':raise ValueError('Author provenance must be registered from checked source files')
    with store.db() as c:
        if not c.execute('SELECT 1 FROM method_sources WHERE method_id=? AND paper_id=?',(method_id,paper_id)).fetchone():raise ValueError('Method is not linked to the paper')
        identifier=store.uid();now=store.now()
        value={'id':identifier,'method_id':method_id,'name':name,'language':language,'spec':spec,'code':code,'review_status':'draft','validation_status':'needs_validation' if code else 'missing_material','revision':1,'created':now,'updated':now}
        c.execute('INSERT INTO tools VALUES (?,?,?,?,?,?,?,?,?,?,?)',tuple(store.encode(value[k]) if k=='spec' else value[k] for k in value))
        c.execute('INSERT INTO tool_sources VALUES (?,?)',(identifier,paper_id))
        _audit(c,'tool',identifier,None,value,actor,'Tool candidate created; not yet validated')
    return get('tool',identifier)


def _invalidate(c,method_ids):
    for mid in method_ids:
        c.execute("UPDATE methods SET review_status='draft',updated=? WHERE id=?",(store.now(),mid))
        c.execute("UPDATE tools SET validation_status=CASE WHEN code='' THEN 'missing_material' ELSE 'stale' END,updated=? WHERE method_id=?",(store.now(),mid))

def supersede_candidates(tool_id):
    """Drop untouched automatic placeholders once a real tool has passed; keep human work."""
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE')
        tool=_row(c,'tools',tool_id)
        if tool['validation_status']!='verified':return []
        sources={r[0] for r in c.execute('SELECT paper_id FROM tool_sources WHERE tool_id=?',(tool_id,))}
        removed=[]
        for row in c.execute("SELECT id FROM tools WHERE method_id=? AND id<>? AND code='' AND revision=1 AND review_status='draft'",(tool['method_id'],tool_id)).fetchall():
            origin=c.execute("SELECT actor FROM revisions WHERE entity_type='tool' AND entity_id=? AND revision=1",(row['id'],)).fetchone()
            candidate_sources={r[0] for r in c.execute('SELECT paper_id FROM tool_sources WHERE tool_id=?',(row['id'],))}
            if origin and origin[0]=='Codex' and candidate_sources<=sources:
                c.execute('DELETE FROM tools WHERE id=?',(row['id'],));removed.append(row['id'])
        if removed:c.execute('INSERT INTO deletions VALUES (?,?,?,?,?)',(store.uid(),'superseded_tool_candidates',tool_id,store.encode({'removed':removed,'replacement':tool_id}),store.now()))
    return removed


def update(kind,identifier,patch,expected_revision,actor,reason='',allow_visual=False):
    allowed={'paper':{'title','authors','year','version_label','doi','card'},'method':{'name','spec'},'tool':{'name','language','spec','code','method_id'}}
    if kind not in allowed or not isinstance(patch,dict) or not patch or set(patch)-allowed[kind]:raise ValueError('Unknown or empty editable fields')
    if type(expected_revision) is not int:raise ValueError('A current revision is required')
    before=get(kind,identifier)
    if before['revision']!=expected_revision:raise ValueError('Version conflict: reload the latest record; your input has not been saved')
    patch=copy.deepcopy(patch)
    for structured in ('card','spec'):
        if structured in patch and isinstance(patch[structured],dict):patch[structured]={**before.get(structured,{}),**patch[structured]}
    if kind=='paper':
        if 'authors' in patch and (not isinstance(patch['authors'],list) or any(not isinstance(a,str) for a in patch['authors'])):raise ValueError('Authors must be a list')
        if 'card' in patch:
            if not isinstance(patch['card'],dict) or set(patch['card'])-set(CARD_FIELDS):raise ValueError('Unknown knowledge card fields')
            patch=copy.deepcopy(patch)
            for key,value in patch['card'].items():
                if not isinstance(value,dict) or not isinstance(value.get('text'),str):raise ValueError('Card fields require text and evidence')
                value['evidence']=check_evidence(identifier,value.get('evidence',[]),allow_visual)
    if kind=='method' and 'spec' in patch:
        if not isinstance(patch['spec'],dict):raise ValueError('Method specification must be an object')
        sources={p['id'] for p in before['sources']}
        patch=copy.deepcopy(patch)
        for ref in patch['spec'].get('evidence',[]):
            if ref.get('paper_id') not in sources:raise ValueError('Method evidence must come from a linked source paper')
            check_evidence(ref['paper_id'],[ref],allow_visual)
    if kind=='tool':
        check_tool_contract(patch.get('spec',before['spec']))
        import environments
        environments.configuration({'language':patch.get('language',before['language']),'spec':patch.get('spec',before['spec'])})
        from runner import VALIDATION_POLICY
        value=patch.get('spec',before['spec'])
        if 'validation_policy' in value and value['validation_policy']!=VALIDATION_POLICY:raise ValueError('复现验收采用宿主统一标准，不能在工具说明中更改')
        if patch.get('spec',before['spec']).get('request_mode','file') not in ('file','json'):raise ValueError('request_mode must be file or json')
        if 'method_id' in patch:
            new_method=get('method',patch['method_id'])
            if not {s['id'] for s in before['sources']}<={s['id'] for s in new_method['sources']}:raise ValueError('The destination method must share all of this tool\'s source papers')
        if 'language' in patch and patch['language'] not in ('python','matlab'):raise ValueError('Supported runtimes are Python and MATLAB')
        if 'spec' in patch and not isinstance(patch['spec'],dict):raise ValueError('Tool configuration must be an object')
        if 'code' in patch and not isinstance(patch['code'],str):raise ValueError('Tool code must be text')
        if 'spec' in patch:
            authorized={a['id'] for p in before['sources'] for a in get('paper',p['id'])['assets']}
            ids=list(patch['spec'].get('asset_ids',[]))+list(patch['spec'].get('author_code_assets',[]))+list(patch['spec'].get('code_assets',[]))+[patch['spec'].get(k) for k in ('input_asset','reference_asset') if patch['spec'].get(k)]
            if any(a not in authorized for a in ids):raise ValueError('Tool assets must belong to its linked source papers')
            if patch['spec'].get('source_kind')=='author_adapter' and before['spec'].get('source_kind')!='author_adapter':raise ValueError('Cannot self-assign author provenance')
    after={k:v for k,v in before.items() if k not in ('history','reviews','sources','tools','runs','assets','methods','page_count','reproduction','reproduction_plans','environment')}
    after.update(patch)
    if all(after[k]==before[k] for k in patch):return before
    after.update(revision=expected_revision+1,review_status='draft',updated=store.now())
    if kind=='method':after['identity']=store.digest({'name':clean(after['name']).lower(),'equations':after['spec'].get('equations',''),'assumptions':after['spec'].get('assumptions','')})
    if kind=='tool':after['validation_status']='needs_validation' if after['code'] else 'missing_material'
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE')
        if _row(c,TYPES[kind],identifier)['revision']!=expected_revision:raise ValueError('Version conflict: another editor saved first')
        keys=list(patch)+['revision','review_status','updated']+(['validation_status'] if kind=='tool' else [])+(['identity'] if kind=='method' else [])
        c.execute('UPDATE '+TYPES[kind]+' SET '+','.join(k+'=?' for k in keys)+' WHERE id=?',
                  tuple(store.encode(after[k]) if k in ('card','authors','spec') else after[k] for k in keys)+(identifier,))
        _audit(c,kind,identifier,{k:before[k] for k in after},after,actor,reason)
        if kind=='paper':_invalidate(c,[r[0] for r in c.execute('SELECT method_id FROM method_sources WHERE paper_id=?',(identifier,))])
        elif kind=='method':
            for source in before['sources']:
                refs=[r for r in after['spec'].get('evidence',[]) if r.get('paper_id')==source['id']]
                c.execute('UPDATE method_sources SET evidence=? WHERE method_id=? AND paper_id=?',(store.encode(refs),identifier,source['id']))
            _invalidate(c,[identifier])
    return get(kind,identifier)


def reconcile_extraction(paper_id,expected_revision,card,coverage,method_records,expected_methods):
    """Commit paper, method mappings and tool candidates as one checked transaction."""
    if not method_records:raise ValueError('No method identified; retain the existing linked draft')
    for value in card.values():value['evidence']=check_evidence(paper_id,value.get('evidence',[]),True)
    for record in method_records:record['spec']['evidence']=check_evidence(paper_id,record['spec'].get('evidence',[]),True)
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE');old=_decode(_row(c,'papers',paper_id))
        if old['revision']!=expected_revision:raise ValueError('Paper changed during analysis; original edits retained')
        for mid,revision in expected_methods.items():
            if _row(c,'methods',mid)['revision']!=revision:raise ValueError('Method changed during analysis; edits retained')
        previous=[r[0] for r in c.execute('SELECT method_id FROM method_sources WHERE paper_id=?',(paper_id,))]
        linked=[]
        for record in method_records:
            name=record['name'];spec=record['spec'];identity=store.digest({'name':clean(name).lower(),'equations':spec.get('equations',''),'assumptions':spec.get('assumptions','')})
            explicit=record.get('existing_method_id','')
            if explicit and explicit not in previous:raise ValueError('Existing method ID is outside this paper scope')
            current=c.execute('SELECT * FROM methods WHERE id=?',(explicit,)).fetchone() if explicit else c.execute('SELECT * FROM methods WHERE identity=?',(identity,)).fetchone()
            if not current:
                current=c.execute('''SELECT m.* FROM methods m JOIN method_sources s ON m.id=s.method_id
                    WHERE s.paper_id=? AND m.name=? AND (SELECT count(*) FROM method_sources z WHERE z.method_id=m.id)=1''',(paper_id,name)).fetchone()
            now=store.now()
            if current:
                mid=current['id'];before=_decode(current)
                if (before['spec']!=spec or before['name']!=name) and mid in previous:
                    after={**before,'name':name,'identity':identity,'spec':spec,'review_status':'draft','revision':before['revision']+1,'updated':now}
                    c.execute("UPDATE methods SET name=?,identity=?,spec=?,review_status='draft',revision=?,updated=? WHERE id=?",(name,identity,store.encode(spec),after['revision'],now,mid))
                    _audit(c,'method',mid,before,after,'Codex','Full-paper method reconciliation');_invalidate(c,[mid])
            else:
                mid=store.uid();after={'id':mid,'name':name,'identity':identity,'spec':spec,'review_status':'draft','revision':1,'created':now,'updated':now}
                c.execute('INSERT INTO methods VALUES (?,?,?,?,?,?,?,?)',(mid,name,identity,store.encode(spec),'draft',1,now,now))
                _audit(c,'method',mid,None,after,'Codex','Method extracted from complete PDF')
            c.execute('INSERT OR REPLACE INTO method_sources VALUES (?,?,?)',(mid,paper_id,store.encode(spec.get('evidence',[]))))
            if current and mid not in previous:
                before=_decode(_row(c,'methods',mid));combined=copy.deepcopy(before['spec'])
                combined['evidence']=[ref for row in c.execute('SELECT evidence FROM method_sources WHERE method_id=?',(mid,)) for ref in store.decode(row[0],[])]
                after={**before,'spec':combined,'revision':before['revision']+1,'review_status':'draft','updated':now}
                c.execute("UPDATE methods SET spec=?,revision=?,review_status='draft',updated=? WHERE id=?",(store.encode(combined),after['revision'],now,mid))
                _audit(c,'method',mid,before,after,'Codex','Shared method linked to an additional source');_invalidate(c,[mid])
            linked.append(mid)
            candidates=c.execute('SELECT t.id FROM tools t JOIN tool_sources s ON s.tool_id=t.id WHERE t.method_id=? AND s.paper_id=?',(mid,paper_id)).fetchall()
            if not candidates:
                tid=store.uid();tspec={'material_gaps':record.get('tool_requirements',[]),'parameters':[]}
                tool={'id':tid,'method_id':mid,'name':name+' · 工具候选','language':'python','spec':tspec,'code':'','review_status':'draft','validation_status':'missing_material','revision':1,'created':now,'updated':now}
                c.execute('INSERT INTO tools VALUES (?,?,?,?,?,?,?,?,?,?,?)',(tid,mid,tool['name'],'python',store.encode(tspec),'','draft','missing_material',1,now,now))
                c.execute('INSERT INTO tool_sources VALUES (?,?)',(tid,paper_id));_audit(c,'tool',tid,None,tool,'Codex','Candidate created; missing code/data must be supplied')
        for mid in set(previous)-set(linked):
            before=_decode(_row(c,'methods',mid))
            if not before['name'].startswith('待提取：'):
                spec=copy.deepcopy(before['spec']);spec['reconciliation_note']='重新分析未匹配此已有方法，保留人工内容和工具，需人工核对映射。'
                after={**before,'spec':spec,'review_status':'draft','revision':before['revision']+1,'updated':store.now()}
                c.execute("UPDATE methods SET spec=?,review_status='draft',revision=?,updated=? WHERE id=?",(store.encode(spec),after['revision'],after['updated'],mid))
                _audit(c,'method',mid,before,after,'Codex','Preserve unmatched existing method and its tools for review');_invalidate(c,[mid])
                continue
            for row in c.execute('SELECT id FROM tools WHERE method_id=?',(mid,)).fetchall():
                c.execute('DELETE FROM tool_sources WHERE tool_id=? AND paper_id=?',(row['id'],paper_id))
                if not c.execute('SELECT 1 FROM tool_sources WHERE tool_id=?',(row['id'],)).fetchone():c.execute('DELETE FROM tools WHERE id=?',(row['id'],))
            c.execute('DELETE FROM method_sources WHERE method_id=? AND paper_id=?',(mid,paper_id))
            if not c.execute('SELECT 1 FROM method_sources WHERE method_id=?',(mid,)).fetchone():c.execute('DELETE FROM methods WHERE id=?',(mid,))
        after={**old,'card':card,'coverage':coverage,'pipeline_status':'ready','review_status':'draft','revision':expected_revision+1,'updated':store.now()}
        c.execute("UPDATE papers SET card=?,coverage=?,pipeline_status='ready',review_status='draft',revision=?,updated=? WHERE id=?",(store.encode(card),store.encode(coverage),after['revision'],after['updated'],paper_id))
        _audit(c,'paper',paper_id,old,after,'Codex','Full-PDF reading and three-library update')
    return get('paper',paper_id)


def review(kind,identifier,status,reviewer,opinion,expected_revision):
    if kind not in TYPES or status not in ('draft','confirmed','disputed') or not reviewer.strip():raise ValueError('Invalid review')
    value=get(kind,identifier)
    if value['revision']!=expected_revision:raise ValueError('Version conflict; reload before review')
    if status=='confirmed':
        if not opinion.strip():raise ValueError('Please record what you checked')
        if kind=='method' and (not value['spec'].get('problem') or not value['spec'].get('assumptions') or not value['spec'].get('evidence')):raise ValueError('Method requires problem, assumptions and source evidence before confirmation')
        if kind=='paper' and not value['card']:raise ValueError('Read and complete the knowledge card first')
        if kind=='tool' and not value['code']:raise ValueError('Tool code is missing')
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE')
        if _row(c,TYPES[kind],identifier)['revision']!=expected_revision:raise ValueError('Version conflict')
        c.execute('INSERT INTO reviews VALUES (?,?,?,?,?,?,?,?)',(store.uid(),kind,identifier,expected_revision,status,reviewer,opinion,store.now()))
        c.execute('UPDATE '+TYPES[kind]+' SET review_status=?,updated=? WHERE id=?',(status,store.now(),identifier))
    return get(kind,identifier)


def restore(kind,identifier,revision,expected_revision,actor):
    with store.db() as c:row=c.execute('SELECT after_data FROM revisions WHERE entity_type=? AND entity_id=? AND revision=?',(kind,identifier,revision)).fetchone()
    if not row:raise ValueError('Historical version not found')
    old=store.decode(row['after_data']);fields={'paper':('title','authors','year','version_label','doi','card'),'method':('name','spec'),'tool':('name','spec','code','language')}[kind]
    return update(kind,identifier,{k:old[k] for k in fields},expected_revision,actor,'Restore version '+str(revision),allow_visual=True)


def delete_paper(paper_id):
    removed_methods=[];removed_tools=[];stale_tools=[]
    with store.db() as c:
        c.execute('BEGIN IMMEDIATE');paper=_row(c,'papers',paper_id)
        paths=[r['path'] for r in c.execute('SELECT path FROM assets WHERE paper_id=?',(paper_id,))]
        mids=[r[0] for r in c.execute('SELECT method_id FROM method_sources WHERE paper_id=?',(paper_id,))]
        tids=[r[0] for r in c.execute('SELECT tool_id FROM tool_sources WHERE paper_id=?',(paper_id,))]
        sessions=c.execute('SELECT id,thread_id FROM sessions WHERE scope_id=?',(paper_id,)).fetchall()
        session=next((s for s in sessions if s['thread_id']),None)
        c.execute("UPDATE jobs SET status='cancelled',stage='Paper deleted; three-library links reconciled',updated=? WHERE paper_id=? AND status IN ('queued','running')",(store.now(),paper_id))
        c.execute('DELETE FROM sessions WHERE scope_id=?',(paper_id,))
        c.execute('DELETE FROM papers WHERE id=?',(paper_id,))
        for tid in tids:
            if not c.execute('SELECT 1 FROM tool_sources WHERE tool_id=?',(tid,)).fetchone():c.execute('DELETE FROM tools WHERE id=?',(tid,));removed_tools.append(tid)
            else:c.execute("UPDATE tools SET validation_status='stale',review_status='draft' WHERE id=?",(tid,));stale_tools.append(tid)
        for mid in mids:
            if not c.execute('SELECT 1 FROM method_sources WHERE method_id=?',(mid,)).fetchone():c.execute('DELETE FROM methods WHERE id=?',(mid,));removed_methods.append(mid)
            else:
                before=_decode(_row(c,'methods',mid));spec=copy.deepcopy(before['spec'])
                spec['evidence']=[ref for row in c.execute('SELECT evidence FROM method_sources WHERE method_id=?',(mid,)) for ref in store.decode(row[0],[])]
                after={**before,'spec':spec,'revision':before['revision']+1,'review_status':'draft','updated':store.now()}
                c.execute("UPDATE methods SET spec=?,revision=?,review_status='draft',updated=? WHERE id=?",(store.encode(spec),after['revision'],after['updated'],mid))
                _audit(c,'method',mid,before,after,'system','Deleted paper; remaining source evidence rebuilt')
                c.execute("UPDATE tools SET validation_status='stale',review_status='draft' WHERE method_id=?",(mid,))
        details={'paper_id':paper_id,'title':paper['title'],'removed_methods':removed_methods,'removed_tools':removed_tools,'stale_tools':stale_tools,'thread_id':session['thread_id'] if session else None,'thread_ids':[s['thread_id'] for s in sessions if s['thread_id']]}
        c.execute('INSERT INTO deletions VALUES (?,?,?,?,?)',(store.uid(),'paper',paper_id,store.encode(details),store.now()))
        retained={r[0] for r in c.execute('SELECT path FROM assets')}
    for relative in set(paths)-retained:
        try:store.file_path(relative).unlink(missing_ok=True)
        except OSError:pass
    return details


def session(scope,scope_id):
    with store.db() as c:
        value=c.execute('SELECT * FROM sessions WHERE scope=? AND scope_id=?',(scope,scope_id)).fetchone()
        if value:return dict(value)
        identifier=store.uid();now=store.now()
        c.execute('INSERT OR IGNORE INTO sessions VALUES (?,?,?,?,?,?,?,?)',(identifier,scope,scope_id,None,'','',now,now))
        return dict(c.execute('SELECT * FROM sessions WHERE scope=? AND scope_id=?',(scope,scope_id)).fetchone())


def messages(session_id,kind=None):
    with store.db() as c:
        rows=c.execute('SELECT * FROM messages WHERE session_id=?'+(' AND kind=?' if kind else '')+' ORDER BY rowid', (session_id,kind) if kind else (session_id,)).fetchall()
    return [_decode(r) for r in rows]


def message(session_id,role,text,kind='chat',metadata=None):
    with store.db() as c:c.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?)',(store.uid(),session_id,role,kind,text,store.encode(metadata or {}),store.now()))


def save_session(session_id,thread_id,summary=None,model=None):
    with store.db() as c:
        values={'thread_id':thread_id,'updated':store.now()}
        if summary is not None:values['summary']=summary
        if model is not None:values['model']=model
        c.execute('UPDATE sessions SET '+','.join(k+'=?' for k in values)+' WHERE id=?',(*values.values(),session_id))


def job(kind,paper_id=None):
    identifier=store.uid();now=store.now()
    with store.db() as c:
        if paper_id:
            active=c.execute("SELECT id FROM jobs WHERE paper_id=? AND kind=? AND status IN ('queued','running')",(paper_id,kind)).fetchone()
            if active:raise ValueError('A task for this paper is already running')
        c.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?)',(identifier,kind,paper_id,'queued','等待处理',store.encode({}),'',now,now))
    return job_get(identifier)


def job_get(identifier):
    with store.db() as c:return _decode(_row(c,'jobs',identifier))


def job_update(identifier,**values):
    values['updated']=store.now()
    if 'progress' in values:values['progress']=store.encode(values['progress'])
    with store.db() as c:
        current=_row(c,'jobs',identifier)
        if current['status']=='cancelled':raise ValueError('Task cancelled')
        c.execute('UPDATE jobs SET '+','.join(k+'=?' for k in values)+' WHERE id=?',(*values.values(),identifier))


def jobs(paper_id=None):
    with store.db() as c:rows=c.execute('SELECT * FROM jobs'+(' WHERE paper_id=?' if paper_id else '')+' ORDER BY created DESC LIMIT 100',(paper_id,) if paper_id else ()).fetchall()
    return [_decode(r) for r in rows]


def fingerprint(tool):
    spec=tool['spec'];ids=list(spec.get('asset_ids',[]))+list(spec.get('author_code_assets',[]))+list(spec.get('code_assets',[]))
    for key in ('input_asset','reference_asset'):
        if spec.get(key):ids.append(spec[key])
    sources=[]
    for identifier in sorted(set(ids)):
        item=asset(identifier);asset_path(identifier);sources.append((identifier,item['sha256']))
    method=get('method',tool['method_id'])
    payload={'tool_revision':tool['revision'],'code':tool['code'],'spec':spec,'language':tool['language'],'sources':sources,'method_revision':method['revision'],'method_spec':method['spec']}
    if spec.get('environment',{}).get('requirements'):
        import environments
        runtime=environments.status(tool)
        payload['runtime_environment']={'status':runtime['status'],'detected':runtime.get('detected')}
    return store.digest(payload)


def method_candidates(question,conditions):
    # A named source paper must survive the lexical shortlist. Otherwise common
    # trade/welfare words can push its actual executable method out of the top 8.
    normalize=lambda value:re.sub(r'[\W_]+','',value.lower())
    query=normalize(question)
    named_sources={p['id'] for p in listing('paper') if len(normalize(p['title']))>=8 and normalize(p['title']) in query}
    with store.db() as c:
        named_methods={r['method_id'] for r in c.execute('SELECT method_id,paper_id FROM method_sources') if r['paper_id'] in named_sources}
    terms=re.findall(r'[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2}',question+' '+store.encode(conditions))
    pairs=[('贸易','trade'),('福利','welfare'),('人口','population'),('空间','spatial'),('摩擦','friction'),('成本','cost'),('劳动','labor')]
    for zh,en in pairs:
        if zh in question:terms.append(en)
    ranked=[]
    for method in listing('method'):
        if not method['spec'].get('problem'):continue
        body=store.encode(method).lower();score=sum(min(body.count(t.lower()),5) for t in terms)
        if method['id'] in named_methods:score+=1000
        if score:
            method=get('method',method['id']);method['retrieval_score']=score
            ranked.append(method)
    ranked.sort(key=lambda x:x['retrieval_score'],reverse=True)
    return ranked[:8]
