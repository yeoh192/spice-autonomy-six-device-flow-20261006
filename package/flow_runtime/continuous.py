"""Master owns termination; local attempt limits are scheduling quanta only."""
import time
from .state import Fault, read, save

API_FAULTS = {'authentication','credentials','api_configuration','transport','api_recovery_exhausted'}

def wait(store, stage, fault, attempt):
    delay = min(30, 2 ** min(attempt, 5))
    store.event(stage, 'waiting_for_recovery', {'fault': fault, 'retry_after_seconds': delay})
    # Bounded sleep remains interruptible by Ctrl+C; no worker waits forever.
    time.sleep(delay)

def missing_information(w):
    blockers=[]
    for gap in w.gaps:
        if gap.get('kind') in {'missing_structured_contract','missing_reference','missing_reference_curve'}:
            blockers.append(gap)
    rows={r['test']:r for r in w.results}
    for c in w.cases:
        if rows.get(c['id'],{}).get('acceptance')=='pending':
            blockers.append({'test':c['id'],'kind':'missing_acceptance_evidence',
                             'reason':'实测已有，但参考/判定定义不足；禁止自动放宽冻结标准'})
    return blockers

def accepted(w):
    rows={r['test']:r for r in w.results}
    electrical=bool(w.cases) and set(rows)=={c['id'] for c in w.cases} and all(
        r.get('execution')=='completed' and r.get('acceptance')=='pass' for r in rows.values())
    coverage=w.inventory['review_status']=='reviewed_complete' and all(
        i['kind']=='informational' or (i['kind']=='test' and i.get('binding_complete') and i.get('bindings') and all(
            rows.get(b,{}).get('acceptance')=='pass' for b in i['bindings'])) for i in w.inventory['items'])
    p=w.task['model'].get('provenance',{})
    provenance=not w.policy['require_non_vendor'] or (p.get('kind')=='non_vendor' and bool(p.get('evidence')))
    return electrical and coverage and provenance

def run(w):
    state=w.store.get('checkpoints','master') or {'phase':'bootstrap','cycle':0,'retry':0}
    try:
        while True:
            w.store.put('checkpoints','master',state)
            w.store.put('checkpoints','master_cycle',state['cycle'])
            try:
                if state['phase']=='bootstrap':
                    w.select_template()
                    w.results=w.evaluate_all(w.model_path,'initial_known_tests')
                    w.develop_missing()
                    w.results=w.evaluate_all(w.model_path,'baseline')
                    w.checkpoint()
                    state.update(phase='cycle',retry=0)
                    w.store.put('checkpoints','master',state)
                if accepted(w):
                    return w.audit('finished')
                if state['phase']=='cycle':
                    # Starting a new scheduling batch, not resetting physical API usage.
                    w.optimization_index=0
                    old=w.store.get('checkpoints','model_diagnostics') or {}
                    w.store.put('checkpoints','model_diagnostics', {'index':0,'history':old.get('history',[]),
                        'physical_trials':0,'analyses':0,'consecutive_revisions':0,'terminal':False})
                    state['phase']='optimize'
                    w.checkpoint()
                    w.store.put('checkpoints','master',state)
                if state['phase']=='optimize':
                    failed=[{'test':r['test'],'fault':r.get('fault')} for r in w.results if r.get('execution')!='completed']
                    w.store.event('master','routing_repair',{'cycle':state['cycle'],'execution_failures':failed,
                        'route':'frozen_fixture_or_model_diagnosis_then_full_regression'})
                    w.optimize()
                    state['phase']='diagnose'
                    w.store.put('checkpoints','master',state)
                if state['phase']=='diagnose':
                    if w.policy.get('model_diagnosis_enabled'):
                        from .model_diagnostics import repair
                        repair(w)
                    state['phase']='assess'
                    w.store.put('checkpoints','master',state)
                if accepted(w):
                    return w.audit('finished')
                blockers=missing_information(w)
                if blockers:
                    state.update(phase='cycle',cycle=state['cycle']+1,blockers=blockers)
                    w.store.put('checkpoints','master',state)
                    save(w.store.folder/'missing_information.json',blockers)
                    w.checkpoint()
                    return w.audit('paused_missing_information')
                # Stop/defer/revision exhaustion/rollback are local outcomes, never terminal.
                state.update(phase='cycle',cycle=state['cycle']+1,retry=0)
                w.checkpoint()
                w.store.put('checkpoints','master',state)
                w.store.event('master','continuing',{'cycle':state['cycle']})
            except Fault as ex:
                state['retry']+=1
                state['last_fault']=ex.record()
                w.store.put('checkpoints','master',state)
                w.checkpoint()
                if ex.kind in API_FAULTS:
                    wait(w.store,'master_api',ex.record(),state['retry'])
                else:
                    # Evidence stays intact. Framework/corruption faults require repair,
                    # never interpreted as a passing model or a missing document.
                    wait(w.store,'master_fault',ex.record(),state['retry'])
                    if ex.kind in {'proposal','review','interface_contract','response_format','response_incomplete'}:
                        state.update(phase='cycle',cycle=state['cycle']+1)
            except Exception as ex:
                state['retry']+=1
                state['last_fault']={'kind':'framework','message':str(ex)[:1000],'type':type(ex).__name__}
                w.store.put('checkpoints','master',state)
                w.checkpoint()
                wait(w.store,'master_framework',state['last_fault'],state['retry'])
    except KeyboardInterrupt:
        w.store.put('checkpoints','master',state)
        w.checkpoint()
        return w.audit('interrupted')
