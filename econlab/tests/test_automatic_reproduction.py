"""Default automation, version checks and the separate human-review boundary."""
import json,os,subprocess,unittest
from unittest.mock import patch
import test_paper_workflow as fixtures
import automation,catalog,store,workflow,experiments,sandbox_runtime,environments,engine

ACCEPT={'decision':'accept','summary':'Checked synthetic model, code and both experiments','checks':['Original evidence','Code does not read reference','Both required tables covered'],'issues':[]}

class AutomaticReproduction(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.PaperWorkflow();self.f.setUp();self.addCleanup(self.f.doCleanups)
        store.set_setting('reproduction_mode','automatic')
        with store.db() as c:
            c.execute("UPDATE methods SET review_status='draft'");c.execute("UPDATE tools SET review_status='draft'");c.execute('DELETE FROM reviews')
        self.f.save()
        import execution_agent
        for e in self.f.doc['experiments']:
            task=execution_agent.Task(self.f.j['id'],self.f.pid,{'experiment_id':e['id'],'experiment_request':e['name']})
            e['preparation_task_id']=task.id
        self.f.save()

    def real_local_process(self,command,argv,env,timeout):
        # Unit-test adapter only. Live command/exec isolation has a separate probe.
        result=subprocess.run(argv,cwd=command.root,env=env,capture_output=True,text=True,timeout=timeout)
        return {'exitCode':result.returncode,'stdout':result.stdout,'stderr':result.stderr}

    def complete(self):
        with patch.object(engine,'ask',return_value=ACCEPT),patch.object(sandbox_runtime.Command,'execute',autospec=True,side_effect=self.real_local_process):
            return workflow.run(self.f.j['id'],self.f.pid)

    def test_auto_configures_environment_and_runs_all_without_human_review(self):
        result=self.complete();self.assertEqual(result['status'],'complete',result)
        state=experiments.summary(self.f.pid);self.assertTrue(state['eligible']);self.assertEqual(state['counts']['passed'],2)
        self.assertEqual(environments.status(catalog.get('tool',self.f.t['id']))['status'],'ready')
        self.assertEqual(catalog.get('tool',self.f.t['id'])['review_status'],'draft','Never forge a human confirmation')
        with store.db() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM experiment_approvals').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT count(*) FROM reviews').fetchone()[0],0)
            self.assertGreaterEqual(c.execute("SELECT count(*) FROM automatic_checks WHERE decision='accept'").fetchone()[0],3)

    def test_manual_mode_remains_an_opt_in_gate(self):
        store.set_setting('reproduction_mode','manual')
        with patch.object(engine,'ask') as ask:result=workflow.run(self.f.j['id'],self.f.pid)
        ask.assert_not_called();self.assertEqual(result['status'],'awaiting_review')

    def test_code_change_invalidates_auto_checks_and_numeric_proofs(self):
        self.complete();tool=catalog.get('tool',self.f.t['id'])
        changed=catalog.update('tool',tool['id'],{'code':tool['code']+'\n# change'},tool['revision'],'Editor','Changed code')
        self.assertFalse(automation.allowed('tool',changed));self.assertFalse(experiments.summary(self.f.pid)['eligible'])

    def test_human_dispute_is_not_overridden(self):
        m=catalog.get('method',self.f.m['id']);catalog.review('method',m['id'],'disputed','Reviewer','Model issue',m['revision'])
        with patch.object(engine,'ask') as ask:result=workflow.run(self.f.j['id'],self.f.pid)
        ask.assert_not_called();self.assertEqual(result['status'],'blocked')

    def test_switch_to_manual_does_not_treat_auto_checks_as_human_review(self):
        self.complete();store.set_setting('reproduction_mode','manual')
        self.assertFalse(experiments.summary(self.f.pid)['eligible'])
        self.assertFalse(automation.allowed('tool',catalog.get('tool',self.f.t['id'])))

    def test_checker_cannot_accept_unresolved_issues(self):
        with patch.object(engine,'ask',return_value={**ACCEPT,'issues':['Unresolved input mapping']}):
            result=automation.entity(self.f.j['id'],self.f.pid,'tool',self.f.t['id'])
        self.assertEqual(result['decision'],'revise');self.assertEqual(result['reported_decision'],'accept')
        self.assertEqual(result['issues'],['Unresolved input mapping'])
        self.assertFalse(automation.allowed('tool',catalog.get('tool',self.f.t['id'])))

    def test_sandbox_policy_is_explicit_and_cancellable(self):
        root=store.DATA/'runs'/'sandbox-probe';root.mkdir(parents=True)
        cmd=sandbox_runtime.Command(root)
        with patch.object(cmd.client,'rpc',return_value={'exitCode':0,'stdout':'ok','stderr':''}) as rpc:
            cmd.execute(['python','-c','print(1)'],{},10)
            args=rpc.call_args.args[1];self.assertEqual(args['sandboxPolicy']['writableRoots'],[str(root)])
            self.assertFalse(args['sandboxPolicy']['networkAccess']);self.assertEqual(args['cwd'],str(root))
            cmd.cancel();self.assertEqual(rpc.call_args.args[0],'command/exec/terminate')


    def test_ready_calibration_runs_before_later_case_is_prepared(self):
        import execution_agent
        doc=json.loads(store.encode(experiments.raw(self.f.pid)['document']))
        original=dict(doc['experiments'][1]);doc['experiments'][1]['input_asset']=''
        experiments.save(self.f.pid,doc,experiments.raw(self.f.pid)['revision'],'Test','Later input is pending')
        def prepare(jid,pid,options):
            current=experiments.summary(pid)
            self.assertEqual(current['experiments'][0]['status'],'passed','Calibration must run before preparing its dependent experiment')
            self.first_run=current['experiments'][0]['run_id']
            updated=experiments.raw(pid);updated['document']['experiments'][1]=original
            experiments.save(pid,updated['document'],updated['revision'],'Test','Prepare dependent input without altering calibration')
            return {'status':'prepared'}
        with patch.object(engine,'ask',return_value=ACCEPT),patch.object(execution_agent,'prepare',side_effect=prepare),patch.object(sandbox_runtime.Command,'execute',autospec=True,side_effect=self.real_local_process):
            result=workflow.run(self.f.j['id'],self.f.pid)
        self.assertEqual(result['status'],'complete',result)
        self.assertEqual(experiments.summary(self.f.pid)['experiments'][0]['run_id'],self.first_run)
        self.assertEqual(len(experiments.rows(self.f.pid)),2,'Unrelated preparation must not invalidate a numeric proof')

    def test_missing_input_does_not_block_an_independent_ready_case(self):
        import execution_agent
        plan=experiments.raw(self.f.pid);doc=plan['document'];doc['experiments'][0]['input_asset']=''
        doc['experiments'][1].update(dependencies=[],dependency_bindings=[])
        experiments.save(self.f.pid,doc,plan['revision'],'Test','One input missing; independent case remains ready')
        with patch.object(engine,'ask',return_value=ACCEPT),patch.object(execution_agent,'prepare',return_value={'status':'blocked','state':{'gaps':['Actual source missing']}}),patch.object(sandbox_runtime.Command,'execute',autospec=True,side_effect=self.real_local_process):
            result=workflow.run(self.f.j['id'],self.f.pid)
        self.assertEqual(result['status'],'blocked')
        state=experiments.summary(self.f.pid);self.assertFalse(state['eligible']);self.assertEqual(state['counts']['passed'],1)
        self.assertEqual(state['experiments'][1]['status'],'passed')

    def test_supplied_snapshot_satisfies_input_but_never_passes_upstream_test(self):
        import execution_agent
        plan=experiments.raw(self.f.pid);doc=plan['document'];doc['experiments'][0]['input_asset']=''
        doc['experiments'][1].update(dependency_bindings=[],dependency_materials=[{'experiment_id':'baseline','asset_ids':[self.f.input['id']],'reason':'Fixture supplies original base directly, instead of generated calibration output'}])
        experiments.save(self.f.pid,doc,plan['revision'],'Test','Use actual supplied snapshot for the dependent computation')
        with patch.object(engine,'ask',return_value=ACCEPT),patch.object(execution_agent,'prepare',return_value={'status':'blocked'}),patch.object(sandbox_runtime.Command,'execute',autospec=True,side_effect=self.real_local_process):
            result=workflow.run(self.f.j['id'],self.f.pid)
        state=experiments.summary(self.f.pid)
        self.assertEqual(state['experiments'][1]['status'],'passed',result)
        self.assertEqual(state['experiments'][0]['status'],'missing_materials')
        self.assertFalse(state['eligible'])

    def test_new_appendix_invalidates_proofs_without_breaking_status_polling(self):
        import fitz
        self.complete();pdf=fitz.open();pdf.new_page().insert_text((40,50),'An additional experiment.');catalog.add_asset(self.f.pid,'additional.pdf',pdf.tobytes(),'appendix');pdf.close()
        result=experiments.public_summary(self.f.pid)
        self.assertFalse(result['eligible']);self.assertFalse(result['approved']);self.assertGreaterEqual(result['counts']['stale'],1);self.assertTrue(all(e['status']!='passed' for e in result['experiments']))

    def test_qualitative_report_is_prepared_then_checked_without_numeric_proof(self):
        plan=experiments.raw(self.f.pid);doc=plan['document']
        doc['experiments'].append({'id':'analytic','name':'Analytic identity','kind':'qualitative','method_id':self.f.m['id'],'evidence':self.f.refs,'figures':[],'dependencies':[],'gaps':['Optional future simulation is not supplied']})
        experiments.save(self.f.pid,doc,plan['revision'],'Test','Add an actual analytic check')
        def ask(*args,**kw):
            if kw.get('kind')=='qualitative_prepare':return {'status':'prepared','mode':'analytic','report_markdown':'Substitute baseline inputs into y = base * factor: 100 * 1 = 100. This is analytic, not numerical validation.','gaps':[]}
            return ACCEPT
        with patch.object(engine,'ask',side_effect=ask),patch.object(sandbox_runtime.Command,'execute',autospec=True,side_effect=self.real_local_process):
            result=workflow.run(self.f.j['id'],self.f.pid)
        self.assertEqual(result['status'],'complete',result)
        state=experiments.summary(self.f.pid);self.assertEqual(state['counts']['passed'],2);self.assertEqual(state['counts']['qualitative_checked'],1)
        case=next(e for e in state['experiments'] if e['id']=='analytic')
        self.assertEqual(catalog.asset(case['qualitative_report_asset'])['role'],'derivation')
        self.assertEqual(case['gaps'],['Optional future simulation is not supplied'])
        self.assertEqual(len(experiments.rows(self.f.pid)),2)

    def test_code_auditor_receives_actual_preparation_report_content(self):
        asset=catalog.add_asset(self.f.pid,'input-audit.json',b'{"source_index":[2,3],"mapped_index":[3,2]}','derivation')
        tool=catalog.get('tool',self.f.t['id']);spec=tool['spec'];spec['execution_artifacts']=[{'asset_id':asset['id'],'role':'derivation'}]
        catalog.update('tool',tool['id'],{'spec':spec},tool['revision'],'Test','Attach actual input audit')
        with patch.object(engine,'ask',return_value=ACCEPT) as ask:
            automation.entity(self.f.j['id'],self.f.pid,'tool',tool['id'])
        prompt=ask.call_args.args[2]
        self.assertIn('source_index',prompt);self.assertIn(asset['sha256'],prompt)
        self.assertIn('preparation_reports',prompt)

    def test_tool_audit_receives_active_case_and_host_user_policy(self):
        plan=experiments.raw(self.f.pid);e=plan['document']['experiments'][0]
        e['publication_precision']={'rules':[{'field':'y','index':0,'decimals':0}],
            'reason':'User explicitly accepts publication precision','authorized_by':'User instruction','evidence':self.f.refs}
        self.f.save(plan['document'])
        with patch.object(workflow,'state',return_value={'experiment_id':'baseline'}),patch.object(engine,'ask',return_value=ACCEPT) as ask:
            automation.entity(self.f.j['id'],self.f.pid,'tool',self.f.t['id'])
        prompt=ask.call_args.args[2]
        packet=json.JSONDecoder().raw_decode(prompt.split('检查对象及完整相关资料：\n',1)[1])[0]
        self.assertEqual(packet['active_experiment_id'],'baseline')
        self.assertEqual([v['id'] for v in packet['prepared_experiments']],['baseline'])
        self.assertEqual([v['id'] for v in packet['remaining_scope']],['counterfactual'])
        self.assertEqual(packet['prepared_experiments'][0]['publication_precision']['authorized_by'],'User instruction')

    def test_scope_revision_is_repaired_automatically_without_dropping_cases(self):
        asked=[];first_scope=True
        def ask(*args,**kw):
            nonlocal first_scope
            asked.append(kw.get('kind'))
            if kw.get('kind')=='scope_correction':return {'updates':[{'id':'baseline','description':'Baseline calculation with explicit source location','design_json':'{}','additional_validation_fields':[],
                'dependencies':[],'related_experiments':[],'dependency_notes':[],'observations':[],'evidence':self.f.refs}],
                'new_experiments':[],'coverage_updates':[],'notes':'Preserved both original tables'}
            if kw.get('kind')=='automatic_audit' and '"kind": "scope"' in args[2] and first_scope:
                first_scope=False;return {**ACCEPT,'decision':'revise','issues':['Clarify baseline description before running']}
            return ACCEPT
        with patch.object(engine,'ask',side_effect=ask),patch.object(sandbox_runtime.Command,'execute',autospec=True,side_effect=self.real_local_process):
            result=workflow.run(self.f.j['id'],self.f.pid)
        self.assertEqual(result['status'],'complete',result);self.assertEqual(asked.count('scope_correction'),1)
        state=experiments.summary(self.f.pid);self.assertEqual(state['counts']['passed'],2);self.assertEqual(len(state['experiments']),2)

    def test_expanding_scope_keeps_unchanged_proofs_but_blocks_paper_eligibility(self):
        self.complete();before=experiments.summary(self.f.pid);run_ids=[e['run_id'] for e in before['experiments']]
        plan=experiments.raw(self.f.pid);new=dict(plan['document']['experiments'][0]);new.update(id='new_case',name='New required case',figures=[],parameters={'factor':3},preparation_task_id=None)
        plan['document']['experiments'].append(new)
        experiments.save(self.f.pid,plan['document'],plan['revision'],'Test','Expand required scope while retaining original results')
        after=experiments.summary(self.f.pid);self.assertFalse(after['eligible']);self.assertEqual(after['counts']['passed'],2)
        self.assertEqual([e['run_id'] for e in after['experiments'][:2]],run_ids)

    def test_case_checker_receives_frozen_input_not_only_common_tool_default(self):
        asset=catalog.add_asset(self.f.pid,'actual-case.json',b'{"base":50,"marker":"actual_case_input_marker"}','data')
        p=experiments.raw(self.f.pid);p['document']['experiments'][1].update(input_asset=asset['id'],parameters={'factor':4},dependencies=[],dependency_bindings=[])
        experiments.save(self.f.pid,p['document'],p['revision'],'Test','Bind actual case input different from tool default')
        with patch.object(experiments,'preflight'),patch.object(engine,'ask',return_value=ACCEPT) as ask:
            automation.experiment(self.f.j['id'],self.f.pid,'counterfactual')
        prompt=ask.call_args.args[2]
        self.assertIn('actual_case_input_marker',prompt);self.assertIn(asset['sha256'],prompt)
        self.assertIn('actual_case_input_materials',prompt)

if __name__=='__main__':unittest.main()
