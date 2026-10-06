"""Frozen-fixture qualification with shared budgets, two reviews and verified receipts.
Reference benchmarks never become candidate-model or whole-manual delivery evidence.
"""
import copy
from pathlib import Path
from .state import (Fault, Store, digest, fingerprint, read, save, file_lock,
                    artifacts_valid)
from .agents import Agents
from .input_batch import validate_batch
from .ac_calibration_runner import fixtures, MODEL
from .spice import Simulator, measure, raw_data, render, acceptance, validate_protocol
from .workflow import calibration_protocols


def verified_calibration(path, runner):
    path, runner = Path(path).resolve(), Path(runner).resolve()
    report = read(path)
    if (report.get('schema') != 'ac-calibration-1' or report.get('status') != 'calibrated'
            or not report.get('current_calibration_passed') or report.get('test_backend')):
        raise Fault('calibration', '需要真实通过的交流校准记录')
    old_code = report.get('code', {})
    code_root = Path(__file__).parent
    if not old_code or any(not (code_root / n).is_file() or digest(code_root / n) != h
                           for n, h in old_code.items()):
        raise Fault('calibration', '原校准执行代码已变化；不得复用旧资格')
    rows = fixtures()
    identity = fingerprint({'code': old_code, 'runner_sha256': digest(runner),
                           'fixtures': rows, 'offline_transport': False})
    state = read(path.parent / 'state.json')
    if report.get('identity') != identity or state.get('identity') != identity:
        raise Fault('calibration', '校准运行器、代码或夹具身份不符')
    results = report.get('results', [])
    if [r.get('id') for r in results] != [r['id'] for r in rows]:
        raise Fault('calibration', '校准项目不完整或重复')
    for row, receipt in zip(rows, results):
        folder = Path(receipt['folder']).resolve()
        if path.parent not in folder.parents or not artifacts_valid(folder, receipt.get('artifact_hashes')):
            raise Fault('cache_corrupt', '校准输出已变化或越界')
        ledger = state.get('simulations', {}).get(folder.name, {})
        if ledger.get('hashes') != receipt['artifact_hashes'] or ledger.get('status') != 'completed':
            raise Fault('calibration', '校准仿真账本不一致')
        if receipt.get('protocol_sha256') != fingerprint(row['protocol']):
            raise Fault('calibration', '校准协议不一致')
        if (folder / 'test.cir').read_text() != render(row['protocol'], MODEL):
            raise Fault('calibration', '校准实际电路不一致')
        result = measure(row['protocol'], raw_data(folder / 'test.raw'), row.get('reference'))
        error = result['metrics']['max_absolute_error'] if 'metrics' in result else abs(result['value']-row['expected'])
        if receipt.get('status') != 'pass' or error > max(abs(row['expected'])*1e-4, 1e-12):
            raise Fault('calibration', '校准实测结果不合格')
    return {'path': str(path), 'sha256': digest(path), 'identity': identity,
            'runner_sha256': digest(runner), 'fixtures_verified': len(rows)}


def approved(response, evidence_ids, post=False):
    if response.get('decision') == 'defer':
        raise Fault('evidence_gap', str(response.get('reason', '条件不足')))
    if response.get('decision') != 'approve':
        raise Fault('review_revision', '审查要求修订', response)
    required = ['conditions', 'ports', 'measurement', 'scope'] + (['results'] if post else [])
    checks = response.get('checks', {})
    if not isinstance(checks, dict) or any(checks.get(k) is not True for k in required):
        raise Fault('review_revision', '审查未明确确认全部检查项', response)
    cites = response.get('evidence_ids')
    if not isinstance(cites, list) or not cites or any(c not in evidence_ids for c in cites):
        raise Fault('review_revision', '审查缺少有效证据引用', response)
    if response.get('unresolved') != []:
        raise Fault('evidence_gap', '审查仍有未解决条件', response)


def context_for(root, packet, case):
    inventory = read(root / packet['inventory'])
    items = [i for i in inventory['items'] if i['id'] in case['reference_ids']]
    if {i['id'] for i in items} != set(case['reference_ids']):
        raise Fault('input', '草案参考项目不存在')
    materials = packet['materials']
    pages = read(root / materials['manual_page_evidence']) if materials.get('manual_page_evidence') else []
    page_ids = {p for item in items for p in item.get('pdf_pages', [])}
    selected_pages = [p for p in pages if p.get('page') in page_ids]
    evidence = {'records': items, 'ports': read(root / materials['ports']),
                'manual_pages': selected_pages, 'manual_sha256': digest(root / materials['manual'])}
    if not selected_pages:
        raise Fault('evidence_gap', '草案缺少已提取的手册页证据')
    return {'device': packet['device'], 'case': case, 'evidence': evidence,
            'actual_circuit': render(case['protocol'], case['model']),
            'model_text': (root / case['model']['path']).read_text(),
            'acceptance_standard': packet['acceptance_standard'],
            'evidence_ids': case['reference_ids'] + ['ports', 'manual_pages', 'actual_circuit', 'calibration', 'benchmark_result'],
            'constraints': 'Frozen protocol, references, limits and ports. No Python/shell/model edits. '
            'Reference model is an interface benchmark only. Finite grid does not verify entire frequency range. '
            'Missing measurement definition/conditions must defer. Approval is not device delivery.'}


def run(batch, calibration, runner, output, rounds=2, resume=False, check_only=False,
        agent_transport=None, simulation_transport=None):
    if not 1 <= rounds <= 3:
        raise Fault('input', 'rounds必须在1至3之间')
    batch, output, runner = Path(batch).resolve(), Path(output).resolve(), Path(runner).resolve()
    validate_batch(batch)
    receipt = verified_calibration(calibration, runner)
    jobs = []
    for row in read(batch)['devices']:
        root = (batch.parent / row['input']).parent
        packet = read(root / 'device_input.json')
        for case in read(root / packet['fixture_drafts'])['cases']:
            jobs.append((root, packet, case))
    code = {p.name: digest(p) for p in Path(__file__).parent.glob('*.py')}
    limits = {'api_calls': len(jobs)*rounds*6, 'simulations': len(jobs)*3,
              'repairs': len(jobs)*max(0, rounds-1), 'seconds': 7200}
    identity = fingerprint({'batch': digest(batch), 'jobs': [(p, c) for _, p, c in jobs],
                            'calibration': receipt, 'code': code, 'limits': limits,
                            'test_backend': bool(agent_transport or simulation_transport)})
    if check_only:
        if output.exists(): raise Fault('input', '请使用新预检目录')
        report = {'status': 'prepared', 'drafts': len(jobs), 'calibration': receipt,
                  'budgets': limits, 'api_calls': 0, 'simulations': 0}
        save(output / 'summary.json', report)
        return report
    with file_lock(output / '.workflow.lock'):
        store = Store(output, identity, limits, resume)
        runner_spec = {'argv': [str(runner), '-b', '-ascii', '{circuit}'], 'timeout_seconds': 120}
        results, terminal = [], None
        shared_keys = {}
        for index, (root, packet, case) in enumerate(jobs, 1):
            key = fingerprint({'device': packet['device'], 'case': case})
            status = store.get('qualifications', key)
            if status:
                if not artifacts_valid(output, status.get('hashes')):
                    raise Fault('cache_corrupt', '资格记录已变化')
                cached = read(output / status['record'])
                for r in cached.get('calibrations', []) + ([cached['benchmark']] if 'benchmark' in cached else []):
                    folder = Path(r['folder']).resolve()
                    if output not in folder.parents or not artifacts_valid(folder, r.get('artifact_hashes')):
                        raise Fault('cache_corrupt', '已获资格的实测证据损坏')
                results.append(cached)
                store.event(case['id'], 'qualification_reused')
                continue
            case_folder = output / ('case_%02d' % index)
            row = {'device': packet['device'], 'id': case['id'], 'reference_ids': case['reference_ids'],
                   'status': 'qualification_pending', 'delivery_eligible': False,
                   'candidate_model_verified': False, 'full_manual_coverage': False}
            agents = Agents(store, packet['routes'], transport=agent_transport)
            agents.keys = shared_keys  # Credentials retained in memory only, once per provider.
            sim = Simulator(store, runner_spec, case['model'], transport=simulation_transport)
            feedback = None
            try:
                context = context_for(root, packet, case)
                context['calibration'] = receipt
                save(case_folder / 'input.json', context)
                for n in range(1, rounds+1):
                    if n > 1: store.reserve('repairs', key + ':' + str(n))
                    current = dict(context, round=n, feedback=feedback)
                    current['instruction'] = ('Return decision=approve/revise/defer, reason, evidence_ids, unresolved array, '
                        'checks with conditions,ports,measurement,scope booleans. Validate frozen actual circuit against manual. '
                        'On revision return corrected explanation, not changed conditions or limits.')
                    try:
                        design = agents.ask('test_designer', current)
                        if 'protocol' in design and design['protocol'] != case['protocol']:
                            raise Fault('proposal', '草案条件被冻结；不能用提案覆盖实际电路', design)
                        approved(design, context['evidence_ids'])
                        review = agents.ask('test_reviewer', dict(current, proposal=design))
                        approved(review, context['evidence_ids'])
                        calibrations = []
                        # AC: two physical oracles. DC resistance: independent R/I oracle using SAME measurement expression.
                        cps = calibration_protocols(case)
                        if case['protocol']['analysis']['kind'] == 'dc':
                            m = case['protocol']['measurement']
                            import re
                            nodes = re.fullmatch(r'v\(([^,]+),([^\)]+)\)', m['signal'], re.I)
                            if not nodes or m['mode'] != 'sample':
                                raise Fault('capability_gap', '缺少该直流测量表达式的独立校准')
                            a,b = nodes.groups(); source = case['protocol']['analysis']['source']
                            cps = []
                            for resistance in (1000, 2000):
                                p = copy.deepcopy(case['protocol']); p['device_nodes'] = {}; p['checks'] = []
                                p['components'] = [{'kind':'R','name':'RCAL','nodes':[a,b],'value':resistance},
                                    {'kind':'R','name':'RGROUND','nodes':[b,'0'],'value':1e-6},
                                    {'kind':'I','name':source,'nodes':[b,a],'value':{'dc':0}}]
                                cps.append((p, resistance*m['at']*m.get('scale',1)*m.get('sign',1)))
                        for p, expected in cps:
                            validate_protocol(p, case['model'])
                            data, folder = sim.run(p, root / case['model']['path'], 'calibration_' + case['id'])
                            result = measure(p, data)
                            if abs(result['value']-expected) > max(abs(expected)*1e-4, 1e-12):
                                raise Fault('calibration', '对应测量校准不合格', result)
                            calibrations.append({'expected':expected,'result':result,'folder':str(folder), 'artifact_hashes':store.get('simulations', folder.name)['hashes']})
                        data, folder = sim.run(case['protocol'], root / case['model']['path'], 'benchmark_' + case['id'])
                        measured = measure(case['protocol'], data)
                        benchmark = {'result': measured, 'acceptance': acceptance(measured, case['expectation']),
                                     'folder': str(folder), 'artifact_hashes':store.get('simulations', folder.name)['hashes'], 'model_role':'reference_interface_benchmark_only'}
                        post = agents.ask('test_reviewer', dict(current, proposal=design, pre_review=review,
                            calibrations=calibrations, benchmark_result=benchmark,
                            instruction=current['instruction'] + ' Post-trial review: include checks.results=true only if results '
                            'support exact measurement validity. Benchmark spec failure is not itself method failure.'))
                        approved(post, context['evidence_ids'], post=True)
                        row.update(status='method_qualified_reference_benchmark_only', benchmark=benchmark,
                                   calibrations=calibrations, protocol_sha256=fingerprint(case['protocol']),
                                   protocol=case['protocol'], model_sha256=case['model']['sha256'],
                                   qualification_backend='offline_test' if agent_transport or simulation_transport else 'real')
                        save(case_folder / 'qualification.json', row)
                        if not (agent_transport or simulation_transport):
                            save(output / 'capabilities' / (key + '.json'), row)
                        break
                    except Fault as e:
                        feedback = e.record()
                        save(case_folder / ('feedback_%02d.json' % n), feedback)
                        store.event(case['id'], 'revision_required', feedback)
                        if e.kind not in ('review_revision', 'proposal', 'response_format'):
                            raise
                else:
                    row.update(status='revision_budget', fault=feedback)
            except (OSError, KeyError, ValueError, TypeError) as e:
                row.update(status='local_fault', fault={'kind':'local_fault','message':str(e)})
            except Fault as e:
                row.update(status=e.kind, fault=e.record())
                if e.kind in ('budget','authentication','api_configuration','credentials','api_recovery_exhausted'):
                    terminal = e.record()
            save(case_folder / 'qualification.json', row)
            rel = str((case_folder / 'qualification.json').relative_to(output))
            if not terminal:
                hashes = {rel:digest(output/rel)}
                if (case_folder / 'input.json').exists():
                    name = str((case_folder/'input.json').relative_to(output)); hashes[name] = digest(output/name)
                for request_key, request_record in store.data['requests'].items():
                    if request_record.get('status') == 'completed':
                        for name, h in request_record['hashes'].items():
                            hashes['requests/' + request_key + '/' + name] = h
                store.put('qualifications', key, {'record':rel, 'hashes':hashes})
            results.append(row)
            save(output / 'progress.json', results)
            if terminal: break
        report = {'status': 'stopped_with_evidence' if terminal else 'qualification_completed_with_gaps',
                  'drafts':len(jobs), 'results': results, 'not_processed': len(jobs)-len(results),
                  'method_qualified':sum(r['status']=='method_qualified_reference_benchmark_only' for r in results),
                  'calibration': receipt, 'usage': store.data['usage'], 'budgets':limits, 'terminal':terminal,
                  'inventory_modified':False, 'models_modified':False, 'full_batch_delivery':False,
                  'candidate_model_verification_required':True,
                  'scope':'Frozen fixture qualification; protocol/limits unchanged; reference benchmark only',
                  'device_coverage':[{'device':read(batch.parent/d['input'])['device'],
                      'record_count':d['record_count'], 'configured_tests':d['configured_tests'],
                      'draft_tests':d['draft_tests'], 'full_manual_coverage':False}
                      for d in read(batch)['devices'] if 'record_count' in d]}
        lines = ['# 草案资格报告', '', '状态：' + report['status'],
                 '方法资格数：%d / %d' % (report['method_qualified'], len(jobs)),
                 '', '参考模型用于接口验证；没有修改输入清单或模型，没有完成器件全覆盖验收。', '']
        for r in results:
            lines.append('- %s / %s：%s' % (r['device'], r['id'], r['status']))
            if r.get('fault'): lines.append('  原因：' + r['fault']['message'])
        (output / 'report.md').write_text('\n'.join(lines)+'\n')
        save(output / 'summary.json', report); store.finish(report['status'])
        return report
