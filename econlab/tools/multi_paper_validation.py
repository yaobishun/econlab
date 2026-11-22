"""Drive the real import API for a preselected, isolated multi-paper test cohort.

Selection and all private artifacts live under .local/validation, never in Git.
The production library is not changed by this controller.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import time
import httpx

APP = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path)
    parser.add_argument('--concurrency', type=int, default=2, choices=(1, 2))
    args = parser.parse_args()
    run = args.run or Path(json.loads((APP/'.local/validation/latest-multi-paper.json').read_text(encoding='utf-8'))['run_directory'])
    selection = json.loads((run/'selection.json').read_text(encoding='utf-8'))
    expected_data = Path(selection['data_directory']).resolve()
    if not expected_data.is_relative_to((APP/'.local/validation').resolve()) or expected_data == (APP/'.local').resolve():
        raise SystemExit('Refusing a non-isolated test database')
    base = 'http://127.0.0.1:' + str(selection['server_port'])
    headers = {'X-EconLab':'1'}
    state_path = run/'import_state.json'
    state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {'cases':[{**p,'status':'waiting'} for p in selection['selected']]}

    def save():
        state['updated_utc'] = time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
        state_path.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')

    with httpx.Client(timeout=120) as client:
        boot = client.get(base+'/api/bootstrap');boot.raise_for_status()
        if Path(boot.json()['data_path']).resolve()!=expected_data:raise SystemExit('Test server uses an unexpected library')
        connection=client.get(base+'/api/connection');connection.raise_for_status()
        if not connection.json().get('connected'):raise SystemExit('Codex login is required for live PDF tests')
        deadline=time.monotonic()+7200
        while time.monotonic()<deadline:
            active=[p for p in state['cases'] if p['status'] in ('queued','running')]
            for p in state['cases']:
                if len(active)>=args.concurrency:break
                if p['status']!='waiting':continue
                source=Path(p['path'])
                if hashlib.sha256(source.read_bytes()).hexdigest()!=p['sha256']:raise ValueError('Source PDF changed: '+p['key'])
                with source.open('rb') as stream:
                    response=client.post(base+'/api/papers',headers=headers,data={'analyze':'true'},files={'file':(source.name,stream,'application/pdf')})
                response.raise_for_status();data=response.json();p['paper_id']=data['paper']['id']
                if data['duplicate'] and data['paper']['pipeline_status']=='ready':
                    p.update(status='completed',duplicate=True);save();continue
                job=data.get('job')
                if not job:
                    response=client.post(base+'/api/papers/'+p['paper_id']+'/analyze',headers=headers,json={'resume':True});response.raise_for_status();job=response.json()
                p.update(status=job['status'],job_id=job['id']);active.append(p);save()
                print(json.dumps({'event':'import_started','key':p['key'],'pages':p['pages'],'paper_id':p['paper_id'],'job_id':p['job_id']},ensure_ascii=False),flush=True)
            for p in list(active):
                response=client.get(base+'/api/jobs/'+p['job_id']);response.raise_for_status();job=response.json()
                update=(job['status'],job['stage'],job['progress'].get('pages_read'))
                if update!=tuple(p.get('last_update',[])):
                    p.update(status=job['status'],last_update=list(update),progress=job['progress'],error=job.get('error',''));save()
                    print(json.dumps({'event':'progress','key':p['key'],'status':job['status'],'stage':job['stage'],'progress':job['progress'],'error':job.get('error','')},ensure_ascii=False),flush=True)
                if job['status']=='completed':
                    detail=client.get(base+'/api/records/paper/'+p['paper_id']);detail.raise_for_status();detail=detail.json()
                    p.update(method_ids=[m['id'] for m in detail['methods']],tool_ids=[t['id'] for t in detail['tools']],coverage=detail['coverage'],review_status=detail['review_status'])
                    (run/(p['key']+'-extraction.json')).write_text(json.dumps(detail,ensure_ascii=False,indent=2),encoding='utf-8');save()
                    if detail['coverage'].get('pages')!=list(range(1,p['pages']+1)):raise ValueError('Incomplete page coverage: '+p['key'])
            if all(p['status'] not in ('waiting','queued','running') for p in state['cases']):break
            time.sleep(4)
        else:raise SystemExit('Test deadline reached; checkpoints retained for inspection')
        save();print(json.dumps({'event':'import_tests_finished','cases':[{k:p.get(k) for k in ('key','status','paper_id','method_ids','tool_ids','error')} for p in state['cases']]},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
