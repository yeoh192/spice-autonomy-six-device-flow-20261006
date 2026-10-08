"""Import locally reviewed, real-calibrated methods without claiming device delivery."""
import copy
from pathlib import Path
from .state import read,digest,fingerprint,Fault,artifacts_valid
from .spice import render,raw_data,measure,acceptance,load_reference

def verified_records(library):
    library=Path(library).resolve()
    for path in sorted((library/'qualified').glob('*.json')):
        if digest(path)!=path.with_suffix('.sha256').read_text().strip():raise Fault('cache_corrupt','Method receipt hash changed')
        record=read(path);case=record['case'];receipt=record['receipt']
        if record.get('qualification_backend')!='real_LTspice' or record.get('delivery_claim') or receipt.get('status')!='method_qualified_exact_conditions':raise Fault('coverage_execution','Not a real qualified method')
        if receipt['model_sha256']!=case['model']['sha256'] or receipt['protocol_sha256']!=fingerprint(case['protocol']):raise Fault('cache_corrupt','Method identity changed')
        if digest(case['model']['path'])!=case['model']['sha256']:raise Fault('cache_corrupt','Registered model changed')
        proof={str(path):digest(path),case['model']['path']:case['model']['sha256']}
        if len(receipt['calibrations'])!=2:raise Fault('calibration','Two independent calibration receipts required')
        for row in receipt['calibrations']+[receipt['trial']]:
            folder=Path(row['folder']);hashes=row['artifacts']
            if not {'model.lib','test.cir','test.raw','test.log','execution.json'}.issubset(hashes) or not artifacts_valid(folder,hashes):raise Fault('cache_corrupt','Method real evidence changed')
            ex=read(folder/'execution.json')
            if ex.get('returncode')!=0 or ex.get('test_backend'):raise Fault('coverage_execution','Failed or mocked calibration/trial')
            if digest(folder/'model.lib')!=case['model']['sha256']:raise Fault('cache_corrupt','Executed model differs')
            if not raw_data(folder/'test.raw')['complete']:raise Fault('coverage_execution','Incomplete evidence RAW')
            proof.update({str(folder/n):h for n,h in hashes.items()})
        from gpt_corrected_tests import oracles
        for cal,(protocol,expected,reference) in zip(receipt['calibrations'],oracles(case)):
            folder=Path(cal['folder'])
            if (folder/'test.cir').read_text()!=render(protocol,case['model']):raise Fault('coverage_protocol','Actual calibration differs from oracle')
            result=measure(protocol,raw_data(folder/'test.raw'),reference)
            error=result['metrics']['max_absolute_error'] if reference else abs(result['value']-expected)
            tolerance=1e-9 if reference else max(abs(expected)*1e-4,1e-10)
            if result!=cal['result'] or error!=cal['error'] or expected!=cal['expected'] or cal['tolerance']!=tolerance or error>tolerance:raise Fault('calibration','Replayed calibration did not match independent oracle')
        trial=receipt['trial'];folder=Path(trial['folder'])
        if (folder/'test.cir').read_text()!=render(case['protocol'],case['model']):raise Fault('coverage_protocol','Actual circuit differs from recipe')
        reference=None
        if case.get('reference'):
            ref=case['reference']
            if digest(ref['path'])!=ref['sha256']:raise Fault('cache_corrupt','Reference changed')
            reference=load_reference(ref['path'],case['expectation']['unit'],ref.get('condition'));proof[ref['path']]=ref['sha256']
        measured=measure(case['protocol'],raw_data(folder/'test.raw'),reference)
        if measured!=trial['result'] or acceptance(measured,case['expectation'])!=trial['acceptance']:raise Fault('coverage_result','Stored method result differs from real waveform')
        yield record,proof

def import_registered(root,packet,inventory,receipts,diagnostics,library):
    from .coverage_binding import source_identity,confirm_receipt,verify_proof
    records=list(verified_records(library));by_id={i['id']:i for i in inventory['items']}
    for record,proof in records:
        c=record['case']
        if c['device']!=packet['device']:continue
        if c.get('coverage_registration_allowed') is False:
            diagnostics.append({'test':c['id'],'status':'diagnostic_method_only','gap':c.get('definition_gap')});continue
        for rid in c['reference_ids']:
            item=by_id.get(rid)
            if not item or item['kind']!='test':raise Fault('coverage_binding','Registered method references unknown record')
            original=next((e for e in c['reference_evidence'] if e['id']==rid),None)
            if not original or original.get('reference_evidence')!=item.get('reference_evidence'):raise Fault('coverage_evidence','Library handbook conditions differ from input')
            if item.get('binding_complete'):continue
            old=receipts.get(rid,{})
            if old:verify_proof(old)
            proof.update({str(root/p):h for p,h in packet['assets'].items()})
            r={'status':'reference_method_qualified','source_identity':source_identity(root,packet),'test_ids':sorted(set(old.get('test_ids',[])+[c['id']])),
               'electrical_acceptance':'pending','proof_files':{**old.get('proof_files',{}),**proof},'test_backend':False,'all_declared_methods_qualified':False,
               'review_backend':'GPT/Codex local source review; not GLM approval','exact_conditions_only':True,
               'benchmark_results':{**old.get('benchmark_results',{}),c['id']:record['receipt']['trial']},
               'scope_note':'Method available at declared points; full handbook range/series coverage still pending'}
            confirm_receipt(item,r);receipts[rid]=r
            diagnostics.append({'reference_id':rid,'status':'reference_method_qualified','test':c['id'],'scope':'exact_conditions_only'})
