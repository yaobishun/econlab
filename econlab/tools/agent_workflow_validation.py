"""Fresh isolated app task using a previously visually read PDF; no adapter/tool is copied.

This measures the execution stage, not PDF-only discovery or a fresh visual read.
Usage: python tools/agent_workflow_validation.py --paper ID --url PUBLIC_AUTHOR_URL --experiment TEXT
"""
import argparse,copy,json,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import catalog,store,engine,reproduction,execution_agent
from provider_bridge import bridge

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--paper',required=True);parser.add_argument('--source-data');parser.add_argument('--url',action='append',default=[]);parser.add_argument('--experiment',default='');args=parser.parse_args()
    if args.source_data:store.DATA=Path(args.source_data).resolve()
    catalog.init();source=catalog.get('paper',args.paper);pdf=catalog.asset_path(source['original_asset']).read_bytes()
    methods=[catalog.get('method',m['id']) for m in source['methods']]
    with store.db() as c:notes=[dict(r) for r in c.execute('SELECT page,visual_notes FROM pages WHERE paper_id=?',(args.paper,))]
    root=store.ROOT/'.local'/'validation'/('agent-native-'+store.uid());root.mkdir(parents=True)
    baseline={'source_paper':args.paper,'fresh_database':True,'reuse':'same PDF visual notes and existing scientific extraction only; NO prior attachments, data adapter, tool implementation, generated input or proof copied','human_supplied_urls':args.url,'root':str(root)}
    (root/'baseline.json').write_text(store.encode(baseline),encoding='utf-8')
    store.DATA=root/'workspace';catalog.init();p,_=catalog.import_paper('test-original.pdf',pdf);pid=p['id']
    def remap(refs):return [{**r,'paper_id':pid} for r in refs]
    card=copy.deepcopy(source['card'])
    for v in card.values():v['evidence']=remap(v.get('evidence',[]))
    extracted=[]
    for m in methods:
        spec=copy.deepcopy(m['spec']);spec['evidence']=remap(spec.get('evidence',[]))
        extracted.append({'existing_method_id':'','name':m['name'],'spec':spec,'tool_requirements':['Prepare actual data adaptation and tool from scratch']})
    with store.db() as c:
        for n in notes:c.execute('UPDATE pages SET visual_notes=? WHERE paper_id=? AND page=?',(n['visual_notes'],pid,n['page']))
    p=catalog.reconcile_extraction(pid,p['revision'],card,source['coverage'],extracted,{})
    p=catalog.update('paper',pid,{k:source[k] for k in ('title','authors','year','doi')},p['revision'],'validation harness','Reuse scientific reading only')
    engine.seed_memory(pid)
    (root/'case.json').write_text(store.encode({'paper_id':pid,'data':str(store.DATA),**baseline}),encoding='utf-8')
    print(store.encode({'case':str(root),'paper_id':pid}),flush=True)
    job=engine.submit('reproduction',pid,reproduction.complete,{'urls':args.url,'experiment_request':args.experiment})
    try:
        while True:
            j=catalog.job_get(job['id']);print(store.encode({k:j[k] for k in ('id','status','stage','error')}),flush=True)
            (root/'job.json').write_text(store.encode(j),encoding='utf-8')
            if j['status'] not in ('queued','running'):break
            time.sleep(10)
        task=execution_agent.latest(pid)
        (root/'task.json').write_text(store.encode(task),encoding='utf-8')
        (root/'paper.json').write_text(store.encode(catalog.get('paper',pid)),encoding='utf-8')
        print(store.encode({'finished':j['status'],'task_status':(task or {}).get('status'),'error':j['error']}),flush=True)
    finally:bridge.close()
    return 0 if j['status']=='completed' else 1

if __name__=='__main__':raise SystemExit(main())
