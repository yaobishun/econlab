"""Export current paper proofs; never run tools, grant approval, or change a library.

python econlab/tools/paper_acceptance_report.py --paper-id <id>
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import catalog
import experiments
import store
import workflow


def collect(pid):
    paper = catalog.get('paper', pid)
    state = experiments.summary(pid)
    cases = []
    with store.db() as connection:
        for case in state['experiments']:
            run = connection.execute('SELECT * FROM runs WHERE id=?', (case.get('run_id'),)).fetchone()
            result = store.decode(run['result'], {}) if run else {}
            metrics = result.get('comparison', {}).get('metrics', [])
            errors = [m['max_relative_error'] for m in metrics if m.get('max_relative_error') is not None]
            cases.append({
                'id': case['id'], 'name': case['name'], 'kind': case['kind'],
                'required': case['required'], 'status': case['status'],
                'method_id': case['method_id'], 'tool_id': case.get('tool_id'),
                'figures': case['figures'], 'evidence': case['evidence'],
                'input_asset': case.get('input_asset'), 'reference_asset': case.get('reference_asset'),
                'validation_fields': case['validation_fields'], 'units': case['units'],
                'dependencies': case['dependencies'], 'dependency_materials': case.get('dependency_materials', []),
                'gaps': case['gaps'], 'run_id': case.get('run_id'),
                'run_status': run['status'] if run else None,
                'signature_matches': bool(run and run['fingerprint'] == case['signature']),
                'max_relative_error': max(errors) if errors else None,
                'metrics': metrics, 'error': result.get('error'),
                'publication_precision': case.get('publication_precision'),
                'publication_precision_acceptance_count': result.get('comparison',{}).get('publication_precision_acceptance_count',0),
            })
        human_reviews = connection.execute('SELECT count(*) FROM reviews WHERE entity_id IN (SELECT id FROM papers WHERE id=? UNION SELECT method_id FROM method_sources WHERE paper_id=? UNION SELECT tool_id FROM tool_sources WHERE paper_id=?)', (pid, pid, pid)).fetchone()[0]
        approvals = connection.execute('SELECT count(*) FROM experiment_approvals WHERE paper_id=?', (pid,)).fetchone()[0]
    strict_complete = state['eligible'] and not state['counts'].get('excluded') and all(
        c['status'] == 'qualitative_checked' if c['kind'] == 'qualitative'
        else c['status'] == 'passed' and c['run_status'] == 'passed' and c['signature_matches']
        for c in cases if c['required']
    )
    return {
        'generated': store.now(), 'version': store.VERSION, 'paper_id': pid, 'title': paper['title'],
        'paper_sha256': catalog.asset(paper['original_asset'])['sha256'],
        'scope': 'All current required experiments, including appendices; qualitative checks are separate.',
        'numeric_policy': 'Default relative error strictly <10%; explicitly authorized per-element publication rounding intervals are recorded with raw errors; explicit absolute tolerance for zero references.',
        'complete': bool(strict_complete), 'eligible_for_solving': state['eligible'],
        'counts': state['counts'], 'plan_revision': state['revision'], 'plan_approved': state['approved'],
        'controller': {k: v for k, v in workflow.state(pid).items() if k in ('status', 'stage', 'experiment_id', 'updated')},
        'human_reviews': human_reviews, 'human_plan_approvals': approvals,
        'named_figures': sorted({f for c in cases for f in c['figures']}),
        'cases': cases,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--paper-id', required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = collect(args.paper_id)
    root = args.output or store.DATA / 'validation' / ('paper-' + args.paper_id)
    root.mkdir(parents=True, exist_ok=True)
    (root / 'acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    rows = [f"# {report['title']}", '', f"整篇验收：{'通过' if report['complete'] else '未通过'}", '',
            f"清单版本 {report['plan_revision']}；数值通过 {report['counts']['passed']}；必验 {report['counts']['required']}。", '',
            '| 实验 | 状态 | 最大相对误差 | 实际运行 |', '| --- | --- | --- | --- |']
    for case in report['cases']:
        error = f"{case['max_relative_error']:.4%}" if case['max_relative_error'] is not None else '—'
        status=case['status']+(f"（论文显示精度接受 {case['publication_precision_acceptance_count']} 项）" if case['publication_precision_acceptance_count'] else '')
        rows.append(f"| {case['name'].replace('|', '/')} | {status} | {error} | {case['run_id'] or '—'} |")
    (root / 'acceptance.md').write_text('\n'.join(rows) + '\n', encoding='utf-8')
    print(json.dumps({'complete': report['complete'], 'counts': report['counts'], 'report': str(root)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
