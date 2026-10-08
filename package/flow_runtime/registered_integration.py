"""Bind independently verified method tasks into the paid batch without changing scope."""
import copy
from pathlib import Path
from .state import read,digest,fingerprint,Fault
from .task import load_task
from .registered_library import verified_records
from .project_standard import expectation_with_standard


def load_registered(manifest, library):
    if not library:raise Fault('input','Registered tasks require their qualification library')
    verified=list(verified_records(library))
    lookup={(r['case']['device'],r['case']['id']):(r,proof) for r,proof in verified}
    rows=copy.deepcopy(read(manifest)['tasks']);seen=set()
    for row in rows:
        path=Path(row['task']).resolve();task,assets=load_task(path)
        if task['device']!=row['device'] or len(task['cases'])!=row['tests']:
            raise Fault('cache_corrupt','Registered manifest differs from task')
        for case in task['cases']:
            key=(task['device'],case['id'])
            if key in seen or key not in lookup:raise Fault('cache_corrupt','Unknown or repeated registered test')
            seen.add(key);record,proof=lookup[key];original=record['case']
            expectation=expectation_with_standard(original['expectation'],task['acceptance_standard'],bool(original.get('reference')))
            if any(fingerprint(case.get(k))!=fingerprint(original.get(k)) for k in ('protocol','reference','reference_ids','coverage_registration_allowed')) or case['expectation']!=expectation:
                raise Fault('cache_corrupt','Registered conditions or acceptance changed')
            if any(task['model'].get(k)!=original['model'].get(k) for k in ('sha256','entry','ports','declared_ports')):
                raise Fault('cache_corrupt','Registered model interface changed')
            assets.update(proof)
        row.update(task=str(path),task_sha256=digest(path),proof_identity=fingerprint(assets))
    if seen!=set(lookup):raise Fault('input','Registered batch omits qualified library tests')
    return rows


def augment_candidate(task, rows):
    task=copy.deepcopy(task);assets={};merged={c['id']:c for c in task['cases']};added=[];skipped=[]
    for row in rows:
        if row['device']!=task['device']:continue
        registered,proof=load_task(row['task'])
        if digest(row['task'])!=row['task_sha256']:raise Fault('cache_corrupt','Registered task changed after validation')
        if any(registered['model'].get(k)!=task['model'].get(k) for k in ('sha256','entry','ports','declared_ports')):
            skipped.extend({'id':c['id'],'reason':'different_model_or_interface'} for c in registered['cases']);continue
        if registered.get('acceptance_standard')!=task.get('acceptance_standard'):
            raise Fault('input','Registered and candidate acceptance standards differ')
        assets.update(proof);assets[row['task']]=row['task_sha256']
        for case in registered['cases']:
            if case.get('coverage_registration_allowed') is False:
                skipped.append({'id':case['id'],'reason':'diagnostic_only'});continue
            old=merged.get(case['id'])
            def reference_identity(c):
                return {k:v for k,v in (c.get('reference') or {}).items() if k!='path'}
            if old and (any(fingerprint(old.get(k))!=fingerprint(case.get(k)) for k in ('protocol','expectation')) or reference_identity(old)!=reference_identity(case)):
                raise Fault('input','Same test ID has conflicting circuit or acceptance: '+case['id'])
            if not old:merged[case['id']]=copy.deepcopy(case);added.append(case['id'])
    task['cases']=list(merged.values())
    for item in task['inventory']['items']:
        bindings=set(item.get('bindings',[]))
        bindings.update(c['id'] for c in task['cases'] if item['id'] in c.get('reference_ids',[]))
        item['bindings']=sorted(bindings)
    task['budgets']['simulations']=max(task['budgets']['simulations'],len(task['cases'])*8)
    task['registered_integration']={'added':added,'excluded':skipped,'active_tests':len(task['cases']),'full_manual_coverage':False}
    return task,assets,task['registered_integration']
