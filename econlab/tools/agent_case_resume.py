"""Resume an isolated execution case through the application's real Agent workflow."""
import argparse,json,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import store,catalog,engine,reproduction,execution_agent
from provider_bridge import bridge

def main():
    parser=argparse.ArgumentParser();parser.add_argument('case');parser.add_argument('--repair',action='store_true');args=parser.parse_args()
    root=Path(args.case).resolve();case=json.loads((root/'case.json').read_text(encoding='utf-8'))
    store.DATA=Path(case['data']).resolve();catalog.init();pid=case['paper_id']
    if store.DATA!=root/'workspace':raise ValueError('This command only resumes an isolated case workspace')
    task=execution_agent.latest(pid)
    if not task:raise ValueError('No saved execution task to resume')
    options={'experiment_request':task['state'].get('request','')}
    if args.repair:
        if not task['state'].get('tool_id'):raise ValueError('No prepared tool to repair')
        options.update(repair=True,tool_id=task['state']['tool_id'])
    (root/('resume-baseline-'+store.uid()+'.json')).write_text(store.encode({'task_id':task['id'],'status':task['status'],'asset_count':len(catalog.get('paper',pid)['assets']),'options':options,'created':store.now()}),encoding='utf-8')
    job=engine.submit('reproduction',pid,reproduction.complete,options)
    previous=None
    try:
        while True:
            j=catalog.job_get(job['id']);status={k:j[k] for k in ('id','status','stage','error')}
            if status!=previous:print(store.encode(status),flush=True);previous=status
            (root/'resumed-job.json').write_text(store.encode(j),encoding='utf-8')
            if j['status'] not in ('queued','running'):break
            time.sleep(5)
        task=execution_agent.latest(pid)
        (root/'resumed-task.json').write_text(store.encode(task),encoding='utf-8')
        (root/'resumed-paper.json').write_text(store.encode(catalog.get('paper',pid)),encoding='utf-8')
        print(store.encode({'finished':j['status'],'task_status':task['status'],'task_id':task['id'],'error':j['error']}),flush=True)
        return 0 if j['status']=='completed' else 1
    finally:bridge.close()

if __name__=='__main__':raise SystemExit(main())
