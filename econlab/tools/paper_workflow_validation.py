"""Isolated synthetic acceptance; --online uses real logged-in Codex, never daily data.

This is an engineering fixture, not evidence of reproducing a published paper.
No solver or adapter is supplied by this script in online mode.
"""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import store


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--online',action='store_true');parser.add_argument('--resume',type=Path);parser.add_argument('--mode',choices=['automatic','manual'],default='automatic');args=parser.parse_args()
    if not args.online:parser.error('Use --online to explicitly run the real Codex acceptance fixture')
    resume=bool(args.resume)
    if resume:
        target=args.resume.resolve();base=(store.ROOT/'.local'/'validation').resolve()
        if target.parent!=base or not target.name.startswith('paper-controller-'):raise ValueError('Resume only an isolated paper-controller fixture')
        store.DATA=target
    else:store.DATA=store.ROOT/'.local'/'validation'/('paper-controller-'+store.uid())
    store.DATA.mkdir(parents=True,exist_ok=True)
    import fitz,catalog,engine,experiments,workflow,environments
    from provider_bridge import bridge
    catalog.init();store.set_setting('reproduction_mode',args.mode)
    if resume:
        p=catalog.listing('paper')[0];pid=p['id']
    else:
        pdf=fitz.open();page=pdf.new_page()
        lines=['Synthetic Linear Production Model: Engineering Acceptance Fixture',
            'EconLab test fixture, 2026. This is not a published scientific paper.',
            'Core model: output y = a * x. Input x is fixed at 100 units.',
            'Assumptions: static deterministic production, constant scale parameter a.',
            'The same core model and solver are used in both experiments below.',
            'Table 1. Baseline experiment: a = 1. Input x = 100 units. Output y = 100 units.',
            'Table 2. Counterfactual experiment: a = 2. Input x = 100 units. Output y = 200 units.',
            'These two tables are the complete inventory of quantitative experiments.',
            'There are no other experiments, figures, appendices, calibrations or external datasets.',
            'The attached input.json contains the raw input x. Separate reference JSON files',
            'contain the reported y values from Table 1 and Table 2, respectively.',
            'No original source program is supplied. The equation above completely defines the model.']
        page.insert_text((35,45),'\n\n'.join(lines),fontsize=9);content=pdf.tobytes();pdf.close()
        p=catalog.import_paper('synthetic-linear-production.pdf',content)[0];pid=p['id']
        catalog.add_asset(pid,'input.json',b'{"x":100}','data')
        catalog.add_asset(pid,'table1-reference.json',b'{"y":100}','reference');catalog.add_asset(pid,'table2-reference.json',b'{"y":200}','reference')
    job=catalog.job('live_paper_controller_'+store.uid(),pid)
    catalog.job_update(job['id'],status='running')
    report={'kind':'synthetic_engineering_fixture','reproduction_mode':args.mode,'paper_id':pid,'data_path':str(store.DATA),'started':store.now()}
    print(json.dumps(report),flush=True)
    try:
        report['preparation']=workflow.run(job['id'],pid) if resume else engine.import_workflow(job['id'],pid,False,True)
        s=experiments.summary(pid)
        report['inventory_before_review']=s
        if len(s['experiments'])!=2 or len(s['coverage'])!=2:raise ValueError('Expected both separately identified table experiments')
        toolids={e['tool_id'] for e in s['experiments']}
        if len(toolids)!=1:raise ValueError('The two fixture experiments did not reuse one shared solver')
        if any(e['gaps'] for e in s['experiments']):raise ValueError('Fixture still has preparation gaps')
        if args.mode=='manual':
            # Explicit engineering test review only. Production never receives this approval.
            for mid in {e['method_id'] for e in s['experiments']}:
                m=catalog.get('method',mid);catalog.review('method',mid,'confirmed','Engineering fixture reviewer','Checked the complete synthetic equation and original table evidence',m['revision'])
            for tid in toolids:
                t=catalog.get('tool',tid)
                source=t['code']
                if not source or any(word in source for word in ('subprocess','os.system','requests.','reference')):raise ValueError('Fixture solver requires additional manual code inspection')
                compile(source,'fixture-solver','exec')
                catalog.review('tool',tid,'confirmed','Engineering fixture reviewer','Synthetic test only: inspected actual linear solver, scalar inputs and separate references',t['revision'])
                env=environments.prepare(tid)
                if env['status']!='ready':raise ValueError(env.get('error'))
            experiments.review(pid,s['revision'],'Engineering fixture reviewer','Synthetic test only: both tables cover the entire one-page fixture; checked code, data, units, references, parameters and dependencies')
            report['batch']=workflow.run(job['id'],pid)
        else:
            with store.db() as c:
                report['human_reviews']=c.execute('SELECT count(*) FROM reviews').fetchone()[0]
                report['human_plan_approvals']=c.execute('SELECT count(*) FROM experiment_approvals').fetchone()[0]
                report['automatic_checks']=c.execute("SELECT count(*) FROM automatic_checks WHERE decision='accept'").fetchone()[0]
            if report['human_reviews'] or report['human_plan_approvals']:raise ValueError('Automatic fixture must not contain human approvals')
            report['batch']=report['preparation']
        report['final_inventory']=experiments.summary(pid)
        if not report['final_inventory']['eligible']:raise ValueError('Whole fixture did not pass')
        report['solve_plan']=engine.solve_plan(job['id'],None,'In the Synthetic Linear Production Model with input x=100, compute output y when a=3. Use the same static deterministic model.',{'data':'Use the registered x=100 input.','outputs':'y in units; no empirical causal inference.'})
        plan=report['solve_plan']
        if plan['missing_information'] or not plan['selected_tool_id']:raise ValueError('Actual AI solve planner could not select the completed fixture')
        values=json.loads(plan['parameters_json'])
        report['solve_result']=engine.solve_execute(job['id'],None,plan['id'],values,True)
        result=report['solve_result']
        if result['status']!='passed' or result.get('explanation_warning'):raise ValueError('Solve calculation or actual AI explanation did not finish')
        import numpy as np
        if not np.allclose(result['outputs']['y'],300):raise ValueError('New parameter calculation should be 300, not a copied reference')
        report['passed']=True
        catalog.job_update(job['id'],status='completed',stage='Synthetic fixture passed')
    except Exception as exc:
        report['passed']=False;report['error']=str(exc)
        catalog.job_update(job['id'],status='failed',stage='Synthetic fixture incomplete',error=str(exc))
        raise
    finally:
        report['finished']=store.now();(store.DATA/'acceptance.json').write_text(store.encode(report),encoding='utf-8')
        print(json.dumps({'passed':report.get('passed'),'error':report.get('error'),'report':str(store.DATA/'acceptance.json')}),flush=True)
        bridge.close()


if __name__=='__main__':main()
