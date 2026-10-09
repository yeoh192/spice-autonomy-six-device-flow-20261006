"""Persist output budgets by endpoint/model; never assume gateway thinking support."""
from .state import fingerprint, read, save

def profile_path(store, route):
    return store.folder / 'response_profiles' / (fingerprint({k: route.get(k) for k in ('provider','model','base_url')}) + '.json')

def settings(store, route):
    path = profile_path(store, route)
    return read(path) if path.exists() else {'max_tokens':4096,'truncations':0}

def truncated(store, route):
    value = settings(store, route)
    value['truncations'] += 1
    value['max_tokens'] = min(32768, max(8192, value['max_tokens'] * 2))
    save(profile_path(store, route), value)
    return value

def focus(context, profile):
    import copy
    c = copy.deepcopy(context)
    c['proposal_scope'] = 'Return exactly one concrete legal change addressing one failure. Keep rationale concise. Preserve all frozen targets; full regression is mandatory. Do not provide an essay or a multi-step plan instead of the required JSON.'
    if profile['truncations']:
        rows = c.get('shared_evidence',{}).get('tests',[])
        targets = [r for r in rows if r.get('result',{}).get('execution') == 'failed'] or [r for r in rows if r.get('result',{}).get('acceptance') == 'fail'] or rows
        if targets:
            chosen = targets[(profile['truncations']-1) % len(targets)]['test']
            c['focus_test_ids'] = [chosen]
            for row in rows:
                if row['test'] != chosen:
                    row.pop('actual_test_circuit',None)
    return c
