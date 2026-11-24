"""Import-time acquisition, grounded implementation, review and unified validation."""
from __future__ import annotations
import json,re,copy
from pathlib import Path
from urllib.parse import urlsplit
import fitz
import catalog,store,materials,environments,runner

def obj(p):return {'type':'object','properties':p,'required':list(p),'additionalProperties':False}
S={'type':'string'}
def arr(p):return {'type':'array','items':p}
REF=obj({'page':{'type':'integer','minimum':1},'quote':{'type':'string','minLength':8}})
SEARCH=obj({'summary':S,'queries':arr(S),'sources':arr(obj({'url':S,'reason':S})),'gaps':arr(S)})
ASSESS=obj({'summary':S,'method_id':S,'experiment':S,'version_notes':arr(S),'selected_asset_ids':arr(S),
 'derivations':arr(obj({'operation':S,'source_asset':S,'name':S,'role':S,'fields':arr(S),'values_json':S,'evidence':arr(REF),'reason':S})),
 'paper_card_updates':arr(obj({'field':S,'text':S,'evidence':arr(REF)})),'gaps':arr(S)})
BUILD=obj({'summary':S,'method_id':S,'tool_id':S,'name':S,'language':S,'code':S,'spec_json':S,'gaps':arr(S)})

def checkpoint(jid,pid,status,stage,**data):
    import engine
    if status in ('searching','downloading','checking','preparing','configuring','validating'):data.setdefault('gaps',[])
    engine.gate(jid,pid);value=materials.progress(pid,status,stage,**data)
    catalog.job_update(jid,stage=stage,progress={'reproduction_status':status,**data})
    return value

def hints(pid):
    paper=catalog.get('paper',pid);urls=[];text=[]
    with fitz.open(catalog.asset_path(paper['original_asset'])) as pdf:
        for page in pdf:
            source=page.get_text();text.append(source)
            urls.extend(re.findall(r'https?://[^\s<>\[\]{}]+',source))
            urls.extend(link['uri'] for link in page.get_links() if link.get('uri','').startswith(('http://','https://')))
    # A scanned PDF has no text-layer URLs. Its actual visual reading notes and
    # cited card text can still contain material links; no OCR is introduced.
    with store.db() as c:notes=[r[0] for r in c.execute("SELECT DISTINCT visual_notes FROM pages WHERE paper_id=? AND visual_notes<>''",(pid,))]
    for value in paper['card'].values():notes.append(value.get('text',''));notes.extend(r.get('quote','') for r in value.get('evidence',[]))
    for note in notes:urls.extend(re.findall(r'https?://[^\s<>\[\]{}"\']+',note))
    return {'links':list(dict.fromkeys(u.rstrip('.,;)]') for u in urls))[:70],'availability_text':'\n'.join(t for t in text if re.search('data|code|supplement|数据|附录|代码',t,re.I))[-22000:]}

def brief(pid):
    p=catalog.get('paper',pid)
    return {k:p[k] for k in ('id','title','authors','year','doi','card','methods')}

def bounded_inventory(pid,selected=None):
    rows=materials.inventory(pid);budget=190000
    if selected:
        wanted=set(selected);prefixes=set()
        def paths(row):return [row['metadata'].get('archive_path',''),*[v.get('archive_path','') for v in row['metadata'].get('origins',[])]]
        def same_folder(row):return any(path.startswith(p) for path in paths(row) for p in prefixes)
        for row in rows:
            if row['id'] in wanted and row['metadata'].get('archive_path'):
                for path in paths(row):
                    folder=str(Path(path).parent).replace('\\','/')
                    if folder!='.':prefixes.add(folder+'/')
        for row in rows:
            if row['role']=='code' and same_folder(row):wanted.add(row['id'])
        # Include declared local function/module dependencies of selected scripts.
        code='\n'.join(r.get('preview','') for r in rows if r['id'] in wanted)
        for row in rows:
            if row['role']=='code' and re.search(r'\b'+re.escape(Path(row['name']).stem)+r'\s*(?:\(|\b)',code):
                equivalents=[r for r in rows if r['name']==row['name'] and same_folder(r)]
                if not equivalents or same_folder(row):wanted.add(row['id'])
        rows.sort(key=lambda r:(r['id'] not in selected,r['id'] not in wanted,r['role']!='code'))
        materials.event(pid,'source_files_read',{'asset_ids':[r['id'] for r in rows if r['id'] in wanted],'note':'Selected experiment source files and local dependencies are read before preparing the implementation'})
        for row in rows:
            if row['id'] not in wanted:row.pop('preview',None)
    for row in rows:
        preview=row.get('preview','')
        row['preview']=preview[:max(0,min(len(preview),budget))];budget-=len(row['preview'])
    return rows

def acquire(jid,pid,options):
    import engine
    check=lambda:engine.gate(jid,pid)
    old=materials.state(pid)['state'];receipts=[];urls=list(dict.fromkeys(options.get('urls',[])))
    if len(urls)>10:raise ValueError('每次最多补充10个材料网址')
    if not old.get('searched') or options.get('refresh_search'):
        checkpoint(jid,pid,'searching','Codex 检索论文、期刊附件与作者代码仓库')
        paper=brief(pid);clues=hints(pid)
        prompt='为宿主的公开网页搜索生成至多3个具体查询词：精确论文题名加code/data/replication，作者主页和补充材料。你当前不直接联网，不声称已经搜索、下载。sources仅可包含给定论文已有链接，不猜测网址；没有线索可为空。论文：'+store.encode(paper)+'\n线索：'+store.encode(clues)
        queries=engine.ask(jid,pid,prompt,SEARCH,kind='material_search')
        results=[materials.search(pid,q,check,options.get('refresh_search',False)) for q in list(dict.fromkeys(queries['queries']))[:3]]
        actual=[item for result in results for item in result['results']]
        if not actual and not clues['links'] and not urls:
            materials.progress(pid,'blocked','公开搜索未取得可用结果',searched=False)
            raise ValueError('公开搜索暂不可用或未找到结果；已保存查询及错误，可补充链接或重试')
        found=engine.ask(jid,pid,'宿主已完成真实公开检索，请从以下搜索结果及论文已有线索中挑选最多8个最可信的材料来源。核对论文、作者、年份，优先期刊附件、作者主页、公开仓库；只给实际出现的URL，不凭记忆猜下载地址，不把页面标题当作代码已下载。sources可以包括含下载链接的作者页面。结果只是搜索摘要，后续宿主会实际访问核对。论文：'+store.encode(paper)+'\n实际搜索收据：'+store.encode(results)+'\n论文线索：'+store.encode(clues),SEARCH,kind='material_search')
        allowed={i['url'] for i in actual}|set(clues['links'])|set(urls)
        found['sources']=[s for s in found['sources'] if s['url'] in allowed]
        materials.event(pid,'search_result',found);materials.progress(pid,'searching','已保存真实检索记录',searched=True,search_summary=found['summary'],search_gaps=found['gaps'])
        urls += [s['url'] for s in found['sources'][:8]]
    elif options.get('retry_failed',not options.get('repair',False)):
        urls += [u['url'] for u in materials.state(pid)['urls'] if u['status']=='failed'][:8]
    # Unpack user uploads as well as downloaded packages; receipts are idempotent.
    for a in catalog.get('paper',pid)['assets']:
        if Path(a['name']).suffix.lower() in ('.zip','.rar','.gz','.tgz','.tar') and a['metadata'].get('archive_inspection_version',0)<3:
            try:materials.unpack(pid,a,check);materials.annotate(a['id'],archive_inspected=True,archive_inspection_version=3)
            except Exception as exc:check();materials.event(pid,'archive_gap',{'asset_id':a['id'],'error':str(exc)})
    for url in list(dict.fromkeys(urls))[:12]:
        checkpoint(jid,pid,'downloading','下载并检查复现材料',current_url=url)
        try:receipts.append(materials.fetch(pid,url,check,options.get('retry_failed',True)))
        except ValueError as exc:materials.event(pid,'url_rejected',{'url':url,'error':str(exc)})
    # Follow real fetched pages once; do not blindly re-search on every continue.
    pages=[r for r in receipts if r.get('kind')=='page']
    if pages:
        checkpoint(jid,pid,'searching','从已取得页面定位实际下载链接')
        prompt='宿主实际访问了以下页面。请依据页面正文和链接选择真正对应本论文的代码、数据、附录下载地址。特别注意作者页面可能通过Dropbox分享zip。只使用已显示的链接，不猜测文件已下载。最多6个来源；找不到明确列缺口。论文：'+store.encode(brief(pid))+'\n已访问页面：'+store.encode(pages)+'\n已经检查的网址：'+store.encode([u['url'] for u in materials.state(pid)['urls']])
        follow=engine.ask(jid,pid,prompt,SEARCH,kind='material_search');materials.event(pid,'follow_links',follow)
        allowed={link['url'] for page in pages for link in page.get('links',[])}
        # Provider share links often use a query flag to serve bytes instead of an HTML preview.
        def shown(url):return any(urlsplit(url)._replace(query='',fragment='')==urlsplit(a)._replace(query='',fragment='') for a in allowed)
        follow['sources']=[s for s in follow['sources'] if shown(s['url'])]
        for item in follow['sources'][:6]:
            checkpoint(jid,pid,'downloading','获取作者页面关联材料',current_url=item['url'])
            try:receipts.append(materials.fetch(pid,item['url'],check,False))
            except ValueError as exc:materials.event(pid,'url_rejected',{'url':item['url'],'error':str(exc)})
    return receipts

def derive(pid,proposal):
    """Only bounded, declared data transformations; never execute a generated script."""
    operation=proposal['operation'];role=proposal['role'];name=proposal['name']
    if not Path(name).suffix and operation in ('paper_values','file_manifest','json_fields','mat_fields'):
        name+='.mat' if operation=='mat_fields' else '.json'
    if role not in ('data','reference'):raise ValueError('Derived materials must be input data or reference results')
    refs=catalog.check_evidence(pid,proposal['evidence'],True)
    if role=='reference':
        if operation=='paper_values':
            aid=proposal['source_asset'] or (refs[0].get('asset_id') if refs else '') or catalog.get('paper',pid)['original_asset']
            source=catalog.asset(aid)
            if source['paper_id']!=pid or source['role'] not in ('original','appendix'):raise ValueError('Paper reference values must cite an original source PDF')
            proposal={**proposal,'source_asset':aid}
        else:
            seen=set()
            def independent(aid):
                if aid in seen:raise ValueError('Reference provenance cycle')
                seen.add(aid);source=catalog.asset(aid);meta=source['metadata']
                if source['paper_id']!=pid or meta.get('task_id') or meta.get('provenance') in ('agent_execution_artifact','agent_inspection_artifact','generated_implementation'):raise ValueError('A generated implementation artifact cannot become its own independent reference')
                if meta.get('provenance')=='declared_transformation':independent(meta['source_asset'])
            independent(proposal['source_asset'])
    if operation=='mat_fields':
        from scipy.io import loadmat,savemat
        import io
        source=catalog.asset(proposal['source_asset'])
        if source['paper_id']!=pid:raise ValueError('Derived material source is outside the paper')
        fields=proposal['fields']
        if not fields or len(fields)>200 or any(not re.fullmatch('[A-Za-z][A-Za-z0-9_]*',f) for f in fields):raise ValueError('Specify existing MATLAB fields')
        values=loadmat(catalog.asset_path(source['id']),variable_names=fields,mat_dtype=True)
        if any(f not in values for f in fields):raise ValueError('Requested MATLAB field does not exist')
        out=io.BytesIO();savemat(out,{f:values[f] for f in sorted(set(fields))},do_compression=True);content=out.getvalue()
        # scipy embeds wall-clock time in the descriptive MAT v5 header. Keep
        # declared field extraction idempotent without altering any numeric bytes.
        content=b'MATLAB 5.0 MAT-file, EconLab declared field extraction'.ljust(116,b' ')+content[116:]
        if Path(name).suffix.lower()!='.mat':raise ValueError('MATLAB subset requires a .mat filename')
    elif operation=='file_manifest':
        if role!='data' or not proposal['fields'] or Path(name).suffix.lower()!='.json':raise ValueError('A data manifest requires actual input asset IDs and a JSON filename')
        files=[]
        for aid in proposal['fields']:
            item=catalog.asset(aid)
            if item['paper_id']!=pid or item['role']!='data':raise ValueError('Input manifest may only reference actual data assets of this paper')
            catalog.asset_path(aid)
            files.append({'asset_id':aid,'name':item['name'],'sha256':item['sha256'],'path':item['metadata'].get('archive_path',item['name'])})
        content=store.encode({'files':files}).encode('utf-8')
    elif operation=='paper_values':
        if not refs:raise ValueError('Paper-derived values require original page evidence and human review')
        value=json.loads(proposal['values_json'])
        if not isinstance(value,dict) or not value:raise ValueError('Paper values must be a named JSON object')
        content=store.encode(value).encode('utf-8')
        if Path(name).suffix.lower()!='.json':raise ValueError('Paper-derived values require a .json filename')
    elif operation=='json_fields':
        source=catalog.asset(proposal['source_asset'])
        if source['paper_id']!=pid:raise ValueError('Derived source is outside paper')
        values=json.loads(catalog.asset_path(source['id']).read_text(encoding='utf-8-sig'));content=store.encode({k:values[k] for k in proposal['fields']}).encode('utf-8')
    else:raise ValueError('Unsupported data transformation; upload prepared real inputs instead of guessing')
    a=catalog.add_asset(pid,name,content,role)
    materials.annotate(a['id'],provenance='declared_transformation',operation=operation,source_asset=proposal['source_asset'],evidence=refs,reason=proposal['reason'],review_required=True)
    materials.event(pid,'derived_asset',{'asset_id':a['id'],'operation':operation,'role':role,'source_asset':proposal['source_asset'],'evidence':refs})
    return a

def verify(jid,pid,tool_id):
    import engine
    t=catalog.get('tool',tool_id)
    if pid not in {p['id'] for p in t['sources']}:raise ValueError('Tool does not belong to this paper')
    if t['review_status']!='confirmed':
        return checkpoint(jid,pid,'awaiting_review','工具和输入参考材料等待人工审核',tool_id=tool_id,gaps=['请审核代码、依赖、模型设置、输入及参考结果后继续复现'])
    checkpoint(jid,pid,'configuring','准备并检测工具运行环境',tool_id=tool_id)
    env=environments.prepare(tool_id,lambda:engine.gate(jid,pid));materials.event(pid,'environment',env)
    if env['status']!='ready':return checkpoint(jid,pid,'blocked','运行环境尚未配置完成',gaps=[env.get('error','环境配置失败')],tool_id=tool_id)
    checkpoint(jid,pid,'validating','实际执行计算并逐项对照参考结果',tool_id=tool_id)
    try:result=runner.execute(tool_id,{},True,pid)
    except ValueError as exc:return checkpoint(jid,pid,'blocked','复现材料或审核条件尚未满足',tool_id=tool_id,gaps=[str(exc)])
    materials.event(pid,'validation',{'run_id':result['run_id'],'status':result['status'],'comparison':result.get('comparison'),'error':result.get('error')})
    if t['spec'].get('execution_task_id'):
        import execution_agent
        execution_agent.record_validation(pid,t,result)
    return checkpoint(jid,pid,'verified' if result['status']=='passed' else 'failed','单项复现通过；能否求解仍以完整实验清单的整篇验收为准' if result['status']=='passed' else '复现未通过，保留日志等待检查或修正',tool_id=tool_id,run_id=result['run_id'],gaps=[] if result['status']=='passed' else [result.get('error','指定输出未满足误差标准')])

def complete(jid,pid,options=None):
    import workflow
    return workflow.run(jid,pid,options)


def save_prepared(jid,pid,built,mid,tools,method_revision):
    if catalog.get('method',mid)['revision']!=method_revision:raise ValueError('Version conflict: method changed while implementation was being prepared')
    if built['method_id']!=mid:raise ValueError('Implementation changed the selected method unexpectedly')
    spec=json.loads(built['spec_json'])
    if not isinstance(spec,dict):raise ValueError('Tool spec must be an object')
    if spec.get('bundle_paths')==[]:spec['bundle_paths']={}
    if spec.get('parameters')=={}:spec['parameters']=[]
    catalog.check_tool_contract(spec)
    if 'requirements' in spec and 'environment' not in spec:spec['environment']={'requirements':spec.pop('requirements')}
    spec['material_gaps']=built['gaps'];spec.pop('source_kind',None)
    if built['language']=='matlab':spec.setdefault('request_mode','json')
    spec['validation_policy']=dict(runner.VALIDATION_POLICY)
    method=catalog.get('method',mid);spec['implementation_evidence']=method['spec'].get('evidence',[])
    workflow=materials.state(pid)['state']
    declared=workflow.get('experiment_request','').strip()
    if not declared:declared=spec.get('pinned_experiment',{}).get('experiment') or '方法：'+method['name']+'；验收输出字段：'+', '.join(spec.get('validation_fields',[]))+'；输入与参考版本见资产映射，实验细节见关联复现方案。'
    spec['validation_scope']=declared+'\n统一验收：使用未舍入的实际输出，全部指定非零输出的相对误差严格小于10%；零参考另设绝对容差。'
    asset_ids={a['id'] for a in catalog.get('paper',pid)['assets']}
    ids=set(spec.get('asset_ids',[]))|set(spec.get('code_assets',[]))|{spec[k] for k in ('input_asset','reference_asset') if spec.get(k)}
    if not ids<=asset_ids:raise ValueError('Implementation references materials outside this paper')
    environments.configuration({'language':built['language'],'spec':spec})
    if built['language'] not in ('python','matlab'):raise ValueError('当前自动执行支持Python和MATLAB')
    if built['code']:
        previous_wrappers={a['id'] for a in catalog.get('paper',pid)['assets'] if a['metadata'].get('provenance')=='generated_implementation' and a['metadata'].get('method_id')==mid}
        spec['code_assets']=[a for a in spec.get('code_assets',[]) if a not in previous_wrappers]
        spec['asset_ids']=[a for a in spec.get('asset_ids',[]) if a not in previous_wrappers]
        spec['bundle_paths']={a:path for a,path in spec.get('bundle_paths',{}).items() if a not in previous_wrappers}
        name='implementation_'+mid+('.m' if built['language']=='matlab' else '.py');code=catalog.add_asset(pid,name,built['code'].encode('utf-8'),'code')
        materials.annotate(code['id'],provenance='generated_implementation',method_id=mid,evidence=spec['implementation_evidence'])
        spec['code_assets']=list(dict.fromkeys([*spec.get('code_assets',[]),code['id']]))
        spec['asset_ids']=list(dict.fromkeys([*spec.get('asset_ids',[]),code['id']]))
        # The runtime writes this implementation as its declared entrypoint.
        spec.setdefault('bundle_paths',{})[code['id']]='implementation-source/'+name
    target=next((t for t in tools if t['id']==built['tool_id']),None)
    if target and target['validation_status']=='verified' and not spec.get('paper_controller'):target=None
    if target:
        tool=catalog.update('tool',target['id'],{k:built[k] for k in ('name','language','code')}|{'spec':spec},target['revision'],'Codex','复现材料补全：准备实现，等待人工审核')
    else:tool=catalog.create_tool(pid,mid,built['name'],spec,built['code'],built['language'],'Codex')
    gaps=list(built['gaps'])
    if not tool['code']:gaps.append('模型算法或代码尚未准备完整')
    if not spec.get('input_asset'):gaps.append('缺少对应实验的真实输入数据')
    if not spec.get('reference_asset'):gaps.append('缺少独立参考结果')
    if not spec.get('validation_fields'):gaps.append('尚未指定需要逐项比较的输出字段')
    materials.event(pid,'implementation_prepared',{'tool_id':tool['id'],'revision':tool['revision'],'summary':built['summary'],'gaps':gaps})
    return checkpoint(jid,pid,'blocked' if gaps else 'awaiting_review','材料待补全，已保存代码草稿' if gaps else '代码、输入与参考结果已准备，等待人工审核',tool_id=tool['id'],prepared_revision=tool['revision'],implementation_summary=built['summary'],gaps=list(dict.fromkeys(gaps)))

def repair(jid,pid,tool_id):
    import execution_agent
    return execution_agent.prepare(jid,pid,{'repair':True,'tool_id':tool_id})
