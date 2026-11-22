"""Explicit developer test review + unchanged host verification in an isolated case."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import store,catalog,engine,reproduction,execution_agent

def main():
    p=argparse.ArgumentParser();p.add_argument('case');p.add_argument('--review-reason',required=True);args=p.parse_args()
    root=Path(args.case).resolve();case=json.loads((root/'case.json').read_text(encoding='utf-8'))
    store.DATA=Path(case['data']);catalog.init();pid=case['paper_id'];task=execution_agent.latest(pid)
    tool=catalog.get('tool',task['state']['tool_id'])
    catalog.review('tool',tool['id'],'confirmed','Developer test review (not production human approval)',args.review_reason,tool['revision'])
    job=catalog.job('validation',pid);catalog.job_update(job['id'],status='running')
    result=reproduction.verify(job['id'],pid,tool['id']);catalog.job_update(job['id'],status='completed',progress={'result':result})
    (root/'verification.json').write_text(store.encode(result),encoding='utf-8')
    print(store.encode({'status':result['status'],'tool_id':tool['id'],'run_id':result['state'].get('run_id'),'gaps':result['state'].get('gaps')}),flush=True)
    if result['state'].get('run_id'):
        with store.db() as c:r=catalog._decode(catalog._row(c,'runs',result['state']['run_id']))
        (root/'verification-run.json').write_text(store.encode(r),encoding='utf-8');print(store.encode(r['result'].get('comparison') or {'error':r['result'].get('error')}),flush=True)
    return 0 if result['status']=='verified' else 1
if __name__=='__main__':raise SystemExit(main())
