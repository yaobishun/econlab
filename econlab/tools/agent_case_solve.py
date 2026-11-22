"""Exercise actual Codex method selection and host computation in an isolated case."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import store,catalog,engine
from provider_bridge import bridge

def main():
    p=argparse.ArgumentParser();p.add_argument('case');p.add_argument('--question',required=True);p.add_argument('--expect-blocked',action='store_true');args=p.parse_args()
    root=Path(args.case).resolve();case=json.loads((root/'case.json').read_text(encoding='utf-8'));store.DATA=Path(case['data']);catalog.init()
    job=catalog.job('solve');catalog.job_update(job['id'],status='running');active_job=job;suffix='-negative' if args.expect_blocked else ''
    try:
        plan=engine.solve_plan(job['id'],None,args.question,{'data':'Use registered verified inputs when applicable; do not assume a new shock is supported.'})
        (root/('solve-plan'+suffix+'.json')).write_text(store.encode(plan),encoding='utf-8')
        catalog.job_update(job['id'],status='completed',progress={'result':plan})
        print(store.encode({'task_id':plan['id'],'tool_id':plan.get('selected_tool_id'),'missing':plan.get('missing_information')}),flush=True)
        blocked=bool(plan.get('missing_information') or not plan.get('selected_tool_id'))
        if args.expect_blocked:return 0 if blocked else 1
        if blocked:return 1
        run=catalog.job('solve_execute');catalog.job_update(run['id'],status='running');active_job=run
        result=engine.solve_execute(run['id'],None,plan['id'],json.loads(plan['parameters_json']),True)
        catalog.job_update(run['id'],status='completed',progress={'result':result})
        (root/'solve-result.json').write_text(store.encode(result),encoding='utf-8')
        print(store.encode({'status':result['status'],'run_id':result.get('run_id'),'proof':result.get('validation_proof',{}).get('run_id'),'explanation':result.get('explanation','')[:1000]}),flush=True)
        return 0 if result['status']=='passed' and not result.get('explanation_warning') else 1
    except Exception as exc:
        catalog.job_update(active_job['id'],status='failed',error=str(exc))
        (root/('solve-error'+suffix+'.json')).write_text(store.encode({'error':str(exc),'time':store.now()}),encoding='utf-8');raise
    finally:bridge.close()
if __name__=='__main__':raise SystemExit(main())
