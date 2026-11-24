"""Durable material acquisition. Codex proposes sources; the host fetches real files."""
from __future__ import annotations
import hashlib,io,ipaddress,json,re,socket,stat,subprocess,zipfile
from html.parser import HTMLParser
from pathlib import Path,PurePosixPath
from urllib.parse import urljoin,urlsplit,urlunsplit,unquote,urlencode,parse_qs,quote
import xml.etree.ElementTree as ET
import httpx
import catalog,store

MAX_EXPANDED=180*1024*1024
FORMATS={'.pdf','.txt','.md','.py','.m','.json','.csv','.mat','.zip','.rar','.r','.do','.ipynb','.yaml','.yml','.toml','.ini','.lock','.dta','.xlsx','.npy','.npz','.pkl','.pickle','.rda','.rdata','.rds','.parquet','.gz','.tgz','.tar'}

def init():
    with store.db() as c:c.executescript('''
    CREATE TABLE IF NOT EXISTS material_workflows(paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
      status TEXT,stage TEXT,state TEXT,updated TEXT);
    CREATE TABLE IF NOT EXISTS material_events(id TEXT PRIMARY KEY,paper_id TEXT REFERENCES papers(id) ON DELETE CASCADE,
      kind TEXT,data TEXT,created TEXT);
    CREATE TABLE IF NOT EXISTS material_urls(paper_id TEXT REFERENCES papers(id) ON DELETE CASCADE,url TEXT,
      status TEXT,result TEXT,updated TEXT,PRIMARY KEY(paper_id,url));
    ''')

def state(pid):
    with store.db() as c:
        row=c.execute('SELECT * FROM material_workflows WHERE paper_id=?',(pid,)).fetchone()
        events=c.execute('SELECT * FROM material_events WHERE paper_id=? ORDER BY rowid DESC LIMIT 80',(pid,)).fetchall()
        urls=c.execute('SELECT * FROM material_urls WHERE paper_id=? ORDER BY updated DESC',(pid,)).fetchall()
    value=dict(row) if row else {'paper_id':pid,'status':'not_started','stage':'尚未开始材料补全','state':{},'updated':None}
    if row:value['state']=store.decode(value['state'],{})
    value['events']=[{**dict(r),'data':store.decode(r['data'],{})} for r in events]
    value['urls']=[{**dict(r),'result':store.decode(r['result'],{})} for r in urls]
    return value

def progress(pid,status,stage,**values):
    with store.db() as c:
        row=c.execute('SELECT state FROM material_workflows WHERE paper_id=?',(pid,)).fetchone()
        data={**(store.decode(row['state'],{}) if row else {}),**values}
        c.execute('INSERT INTO material_workflows VALUES(?,?,?,?,?) ON CONFLICT(paper_id) DO UPDATE SET status=excluded.status,stage=excluded.stage,state=excluded.state,updated=excluded.updated',(pid,status,stage,store.encode(data),store.now()))
    return state(pid)

def event(pid,kind,data):
    with store.db() as c:c.execute('INSERT INTO material_events VALUES(?,?,?,?,?)',(store.uid(),pid,kind,store.encode(data),store.now()))

def public_url(url):
    if not isinstance(url,str) or len(url)>4000:raise ValueError('Invalid material URL')
    p=urlsplit(url.strip())
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password or p.port not in (None,80,443):raise ValueError('Only public HTTP(S) material URLs are supported')
    host=p.hostname.lower()
    if host=='localhost' or host.endswith(('.localhost','.local','.internal')):raise ValueError('Local material URL is not allowed')
    try:addresses=socket.getaddrinfo(host,p.port or (443 if p.scheme=='https' else 80),type=socket.SOCK_STREAM)
    except OSError as exc:raise ValueError('Cannot resolve material host: '+host) from exc
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):raise ValueError('Material host must resolve to public addresses')
    return urlunsplit((p.scheme,p.netloc,p.path or '/',p.query,''))

class Page(HTMLParser):
    def __init__(self,base):super().__init__();self.base=base;self.parts=[];self.links=[];self.skip=0;self.href=None;self.label=[]
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag in ('script','style','noscript'):self.skip+=1
        if tag=='a':self.href=attrs.get('href');self.label=[]
    def handle_endtag(self,tag):
        if tag in ('script','style','noscript'):self.skip=max(0,self.skip-1)
        if tag=='a' and self.href:
            url=urljoin(self.base,self.href)
            if url.startswith(('http://','https://')) and len(self.links)<160:self.links.append({'url':url,'text':' '.join(self.label).strip()[:200]})
            self.href=None
    def handle_data(self,data):
        if not self.skip:
            self.parts.append(data)
            if self.href:self.label.append(data)

def role_for(name,content=None):
    p=Path(name);s=p.suffix.lower();n=p.name.lower()
    if s in ('.py','.m','.r','.do','.ipynb'):return 'code'
    if s=='.pdf':return 'appendix'
    if s in ('.mat','.csv','.dta','.xlsx','.npy','.npz','.pkl','.pickle','.rda','.rdata','.rds','.parquet'):return 'data'
    if s=='.txt' and content:
        try:
            line=next(v for v in content[:4000].decode('utf-8-sig').splitlines() if v.strip())
            tokens=re.split(r'[\s,;]+',line.strip())
            for token in tokens:float(token)
            return 'data'
        except (ValueError,UnicodeError,StopIteration):pass
    return 'other'

def annotate(aid,**values):
    a=catalog.asset(aid)
    metadata={**a['metadata'],**values}
    if values.get('source_url') or values.get('archive_path'):
        origin={k:metadata[k] for k in ('source_url','archive_asset','archive_path','provenance') if metadata.get(k)}
        origins=a['metadata'].get('origins',[])
        if origin not in origins:origins=[*origins,origin]
        metadata['origins']=origins
    with store.db() as c:c.execute('UPDATE assets SET metadata=? WHERE id=?',(store.encode(metadata),aid))

def search(pid,query,check=lambda:None,refresh=False):
    """Public search results fetched by the app; no dependency on a model's web tool."""
    if not isinstance(query,str) or not 3<=len(query)<=500:raise ValueError('Search query must be 3–500 characters')
    if not refresh:
        with store.db() as c:past=c.execute("SELECT data FROM material_events WHERE paper_id=? AND kind='search_engine' ORDER BY rowid DESC",(pid,)).fetchall()
        for row in past:
            data=store.decode(row['data'],{})
            if data.get('query')==query and data.get('results') and data.get('provider_version')==2:return {**data,'cached':True}
    errors=[]
    quoted=re.search(r'"([^"]{8,})"',query)
    github_query=(quoted.group(1) if quoted else re.sub(r'\b(?:github|code|codes|data|download|replication|homepage|author)\b',' ',query,flags=re.I)).strip()
    providers=[('github','https://api.github.com/search/repositories?'+urlencode({'q':github_query+' in:readme','per_page':8})),('duckduckgo','https://html.duckduckgo.com/html/?'+urlencode({'q':query})),('bing','https://www.bing.com/search?'+urlencode({'q':query,'format':'rss'}))]
    for provider,url in providers:
        try:
            check()
            with httpx.Client(timeout=30,trust_env=False,follow_redirects=False,headers={'User-Agent':'Mozilla/5.0 (compatible; EconLab/3.1)'}) as client:
                for _ in range(5):
                    url=public_url(url)
                    with client.stream('GET',url) as response:
                        if response.status_code in (301,302,303,307,308):url=urljoin(url,response.headers.get('location',''));continue
                        response.raise_for_status();data=b''
                        for chunk in response.iter_bytes():
                            check();data+=chunk
                            if len(data)>2*1024*1024:raise ValueError('Search page too large')
                        break
                else:raise ValueError('Search redirect limit')
            results=[]
            if provider=='github':
                for item in json.loads(data).get('items',[]):results.append({'url':item['html_url'],'title':item['full_name'],'snippet':(item.get('description') or '')[:1200],'default_branch':item.get('default_branch'),'homepage':item.get('homepage'),'updated_at':item.get('updated_at')})
            elif provider=='bing':
                for item in ET.fromstring(data).findall('./channel/item')[:12]:
                    link=item.findtext('link','')
                    if link.startswith(('http://','https://')):results.append({'url':link,'title':item.findtext('title','')[:300],'snippet':item.findtext('description','')[:1200]})
            else:
                page=Page(url);page.feed(data.decode('utf-8',errors='replace'))
                for item in page.links:
                    link=item['url'];wrapped=parse_qs(urlsplit(link).query).get('uddg')
                    if wrapped:link=wrapped[0]
                    if urlsplit(link).hostname not in ('duckduckgo.com','html.duckduckgo.com') and item['text']:results.append({'url':link,'title':item['text'],'snippet':''})
                results=list({v['url']:v for v in results}.values())[:12]
            if not results:raise ValueError('Search returned no usable results or a challenge page')
            receipt={'provider':provider,'provider_version':2,'query':query,'url':url,'results':results,'errors':errors,'sha256':hashlib.sha256(data).hexdigest(),'retrieved':store.now()};event(pid,'search_engine',receipt);return receipt
        except Exception as exc:check();errors.append(provider+': '+str(exc)[:300])
    receipt={'query':query,'results':[],'errors':errors,'retrieved':store.now()};event(pid,'search_engine',receipt);return receipt

def unpack(pid,asset,check=lambda:None):
    """Inspect archive members; never execute installation scripts or trust paths."""
    root=catalog.asset_path(asset['id']);s=root.suffix.lower()
    if s not in ('.zip','.rar','.gz','.tgz','.tar'):return []
    result=[];total=0
    def accept(name,content):
        nonlocal total
        check();p=PurePosixPath(name.replace('\\','/'))
        if p.is_absolute() or '..' in p.parts or any(':' in x for x in p.parts):raise ValueError('Unsafe archive member path')
        if any(x in ('__MACOSX','.git','.venv','node_modules') for x in p.parts) or p.name.startswith('._'):return
        if p.suffix.lower() not in FORMATS or p.suffix.lower() in ('.zip','.rar','.gz','.tgz','.tar'):return
        if len(content)>store.MAX_FILE:raise ValueError('Archive member exceeds 50 MB')
        total+=len(content)
        if total>MAX_EXPANDED or len(result)>=600:raise ValueError('Archive expansion limit reached')
        a=catalog.add_asset(pid,p.name,content,role_for(p.name,content))
        annotate(a['id'],archive_asset=asset['id'],archive_path=str(p),source_url=asset['metadata'].get('requested_url') or asset['metadata'].get('source_url'),provenance='downloaded_archive')
        result.append(a['id'])
    if s=='.zip':
        with zipfile.ZipFile(root) as z:
            for item in z.infolist():
                check()
                if item.is_dir():continue
                if stat.S_ISLNK(item.external_attr>>16):raise ValueError('Archive symlink is not supported')
                if item.file_size>store.MAX_FILE or item.file_size+total>MAX_EXPANDED:raise ValueError('Archive expansion limit reached')
                accept(item.filename,z.read(item))
    else:
        # Windows bsdtar supports RAR too. Members are streamed, never extracted to a trusted path.
        listed=subprocess.run(['tar','-tf',str(root)],capture_output=True,timeout=40)
        if listed.returncode:raise ValueError('Archive listing failed; retain package for manual inspection')
        names=listed.stdout.decode('utf-8',errors='replace').splitlines()
        if len(names)>1500:raise ValueError('Too many archive members')
        for name in names:
            check()
            if name.endswith('/') or PurePosixPath(name).suffix.lower() not in FORMATS:continue
            p=PurePosixPath(name.replace('\\','/'))
            if p.is_absolute() or '..' in p.parts or any(':' in x for x in p.parts):raise ValueError('Unsafe archive member path')
            with subprocess.Popen(['tar','-xOf',str(root),name],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL) as proc:
                data=proc.stdout.read(min(store.MAX_FILE,MAX_EXPANDED-total)+1)
                if len(data)>min(store.MAX_FILE,MAX_EXPANDED-total):proc.kill();raise ValueError('Archive expansion limit reached')
                if proc.wait(timeout=40):raise ValueError('Archive member read failed')
            accept(name,data)
    event(pid,'archive_inspected',{'asset_id':asset['id'],'members':len(result),'bytes':total})
    return result

def fetch(pid,url,check=lambda:None,retry=False):
    """A successful receipt means actual HTTP bytes were received, not an AI claim."""
    url=public_url(url)
    with store.db() as c:old=c.execute('SELECT status,result FROM material_urls WHERE paper_id=? AND url=?',(pid,url)).fetchone()
    if old and old['status']=='acquired':return {**store.decode(old['result'],{}),'cached':True}
    if old and not retry:return {**store.decode(old['result'],{}),'cached':True}
    try:
        final=url
        with httpx.Client(timeout=httpx.Timeout(35,connect=15),follow_redirects=False,trust_env=False,headers={'User-Agent':'EconLab/3.1 academic replication material fetcher'}) as client:
            for step in range(7):
                check();final=public_url(final)
                with client.stream('GET',final) as response:
                    if response.status_code in (301,302,303,307,308):
                        final=urljoin(final,response.headers.get('location',''));continue
                    response.raise_for_status();chunks=[];size=0
                    for chunk in response.iter_bytes():
                        check();size+=len(chunk)
                        if size>store.MAX_FILE:raise ValueError('Download exceeds 50 MB')
                        chunks.append(chunk)
                    data=b''.join(chunks);ctype=response.headers.get('content-type','');disp=response.headers.get('content-disposition','');break
            else:raise ValueError('Too many download redirects')
        if not data:raise ValueError('Empty material response')
        if 'html' in ctype or data.lstrip().lower().startswith((b'<!doctype html',b'<html')):
            page=Page(final);page.feed(data.decode('utf-8',errors='replace'))
            receipt={'url':url,'final_url':final,'kind':'page','sha256':hashlib.sha256(data).hexdigest(),'text':re.sub(r'\s+',' ',' '.join(page.parts))[:26000],'links':page.links,'asset_ids':[]}
            repo=urlsplit(final);parts=repo.path.strip('/').split('/')
            if repo.hostname=='github.com' and len(parts)==2:
                # GitHub's Code button is dynamic. Obtain the actual default branch
                # and README via its public API, rather than guessing archive URLs.
                base='https://api.github.com/repos/'+parts[0]+'/'+parts[1]
                with httpx.Client(timeout=30,trust_env=False,headers={'User-Agent':'EconLab/3.1','Accept':'application/vnd.github+json'}) as github:
                    meta=github.get(public_url(base));meta.raise_for_status();metadata=meta.json()
                    branch=metadata['default_branch'];archive='https://codeload.github.com/'+metadata['full_name']+'/zip/refs/heads/'+quote(branch,safe='')
                    receipt['links'].insert(0,{'url':archive,'text':'Repository source archive, actual default branch: '+branch})
                    if metadata.get('homepage'):receipt['links'].insert(0,{'url':metadata['homepage'],'text':'Repository project homepage'})
                    readme=github.get(public_url(base+'/readme'))
                    if readme.status_code==200:
                        import base64
                        doc=readme.json();text=base64.b64decode(doc.get('content','')).decode('utf-8',errors='replace')[:50000]
                        receipt['text']='README from public repository API:\n'+text+'\n'+receipt['text'][:6000]
                        for link in re.findall(r'https?://[^\s<>"\)\]]+',text)[:80]:receipt['links'].append({'url':link.rstrip('.,;'),'text':'README source link'})
                    receipt['repository']={k:metadata.get(k) for k in ('full_name','default_branch','pushed_at','html_url','homepage','license')}
        else:
            match=re.search(r"filename\*?=(?:UTF-8''|\")?([^\";]+)",disp,re.I)
            name=Path(unquote(match.group(1) if match else urlsplit(final).path).replace('\\','/')).name
            if data.startswith(b'PK\x03\x04') and Path(name).suffix.lower() not in ('.zip','.xlsx','.npz'):name='materials.zip'
            if Path(name).suffix.lower() not in FORMATS:raise ValueError('Unsupported download type; retain source link for manual inspection')
            a=catalog.add_asset(pid,name,data,role_for(name,data));annotate(a['id'],source_url=final,requested_url=url,retrieved=store.now(),provenance='public_download')
            a=catalog.asset(a['id']);members=[];archive_error=None
            try:members=unpack(pid,a,check)
            except (ValueError,OSError,subprocess.SubprocessError,zipfile.BadZipFile) as exc:archive_error=str(exc)
            if not archive_error:annotate(a['id'],archive_inspected=True,archive_inspection_version=3)
            receipt={'url':url,'final_url':final,'kind':'file','name':a['name'],'sha256':a['sha256'],'bytes':len(data),'asset_ids':[a['id'],*members],'archive_error':archive_error}
        status='acquired'
    except Exception as exc:
        check();status='failed';receipt={'url':url,'error':str(exc)[:1500],'asset_ids':[]}
    with store.db() as c:c.execute('INSERT INTO material_urls VALUES(?,?,?,?,?) ON CONFLICT(paper_id,url) DO UPDATE SET status=excluded.status,result=excluded.result,updated=excluded.updated',(pid,url,status,store.encode(receipt),store.now()))
    event(pid,'fetch_'+status,{k:v for k,v in receipt.items() if k not in ('text','links')})
    return receipt

def inventory(pid,asset_ids=None):
    """Bounded content/schema snapshots; binary inputs remain real local assets."""
    result=[]
    for a in catalog.get('paper',pid)['assets']:
        if asset_ids is not None and a['id'] not in asset_ids:continue
        row={k:a[k] for k in ('id','name','role','sha256','metadata')};path=catalog.asset_path(a['id']);s=path.suffix.lower()
        try:
            if s=='.mat':
                from scipy.io import loadmat,whosmat
                import numpy as np
                import math
                try:
                    headers=whosmat(path)
                    row['variables']={k:{'shape':list(shape),'dtype':kind} for k,shape,kind in headers if isinstance(k,str)}
                    names=[k for k,shape,kind in headers if isinstance(k,str) and math.prod(shape)<=64 and kind not in ('opaque','object','struct','cell')]
                    values=loadmat(path,variable_names=names,mat_dtype=True)
                except Exception:
                    if a['metadata']['size']>8*1024*1024:raise ValueError('Large MAT schema requires MATLAB inspection; no full-array fallback was attempted')
                    values=loadmat(path,mat_dtype=True)
                    row['variables']={k:{'shape':list(v.shape),'dtype':str(v.dtype)} for k,v in values.items() if isinstance(k,str) and not k.startswith('__') and hasattr(v,'shape')}
                for k,v in values.items():
                    if k in row['variables'] and hasattr(v,'dtype') and v.size<=64 and v.dtype.kind in 'biuf':
                        if np.isfinite(v).all():row['variables'][k]['value']=v.tolist()
                        else:row['variables'][k]['inspection_note']='输入含非有限数值，保留原件，需明确缺失值处理规则；不猜测数值。'
            elif s=='.npz':
                import zipfile,numpy as np
                row['variables']={}
                with zipfile.ZipFile(path) as archive:
                    for name in archive.namelist():
                        if not name.endswith('.npy'):continue
                        with archive.open(name) as stream:
                            version=np.lib.format.read_magic(stream)
                            shape,fortran,dtype=np.lib.format._read_array_header(stream,version)
                        row['variables'][name[:-4]]={'shape':list(shape),'dtype':str(dtype),'fortran_order':fortran}
                row['inspection_note']='Array headers inspected without loading arrays or executing pickle payloads'
            elif s=='.json' and a['metadata']['size']<200000:row['preview']=a['text'][:8000]
            elif s in ('.pkl','.pickle'):
                # Read opcode strings only. Unpickling is code execution and belongs
                # to reviewed tools, never this automatic inventory phase.
                import pickletools
                strings=[]
                for op,arg,_ in pickletools.genops(path.read_bytes()):
                    if op.name in ('SHORT_BINUNICODE','BINUNICODE','UNICODE') and isinstance(arg,str) and len(arg)<200:
                        strings.append(arg)
                        if len(strings)>=100:break
                row['serialized_strings']=strings;row['inspection_note']='Opcode inspection only; no pickle code executed'
            elif a['text'] and a['role']!='original':row['preview']=a['text'][:(22000 if a['role']=='code' else 6000)]
        except Exception as exc:row['inspection_error']=str(exc)[:300]
        result.append(row)
    # Keep source indices for every file; focus code content in the next selected-method turn.
    return result
