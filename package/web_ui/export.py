"""Portable analysis exports; candidate status is never inferred from its filename."""
import csv, hashlib, json, re, shutil, zipfile
from pathlib import Path
from flow_runtime.spice import raw_data

def inside(path, root):
    p=Path(path).resolve();p.relative_to(Path(root).resolve());return p

def export(runtime, task):
    runtime=Path(runtime);out=runtime.parent/'exports';out.mkdir(exist_ok=True)
    report=json.loads((runtime/'summary.json').read_text())
    cases={c['id']:c for c in task.get('cases',[])}
    fields=['test','execution','acceptance','value','unit','min','typical','max','typical_error_percent','metrics_json','fault_json']
    with (out/'measurements.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for r in report.get('results',[]):
            e=cases.get(r['test'],{}).get('expectation',{});lim=e.get('limits',{});typ=e.get('typical');val=r.get('value')
            w.writerow({**{k:r.get(k,'') for k in fields[:5]},'min':lim.get('min',''),'max':lim.get('max',''),'typical':typ if typ is not None else '',
                'typical_error_percent':100*(val-typ)/abs(typ) if isinstance(val,(int,float)) and isinstance(typ,(int,float)) and typ else '',
                'metrics_json':json.dumps(r.get('metrics',{}),ensure_ascii=False),'fault_json':json.dumps(r.get('fault',{}),ensure_ascii=False)})
    notes=[]
    for r in report.get('results',[]):
        if not r.get('artifacts'):continue
        try:
            folder=inside(r['artifacts'],runtime)
            name=re.sub(r'[^A-Za-z0-9_.-]','_',r['test']);dest=out/'circuits'/name;dest.mkdir(parents=True,exist_ok=True)
            # Preserve relative includes beside their generated circuit.
            for src in folder.iterdir():
                if src.is_file() and src.suffix.lower() in ('.cir','.lib','.inc'):shutil.copy2(src,dest/src.name)
            raw=next(folder.glob('*.raw'));data=raw_data(raw)
            signals=data['signals'];columns=[];values=[]
            for key,series in signals.items():
                if any(isinstance(v,complex) for v in series):
                    columns.extend([key+'_real',key+'_imag']);values.extend([[complex(v).real for v in series],[complex(v).imag for v in series]])
                else:columns.append(key);values.append(series)
            wave=out/'waveforms';wave.mkdir(exist_ok=True)
            with (wave/(name+'.csv')).open('w',newline='',encoding='utf-8-sig') as f:
                w=csv.writer(f);w.writerow(columns);w.writerows(zip(*values))
        except Exception as e:notes.append({'test':r['test'],'export_error':str(e)[:300]})
    model=report.get('model');model_info={'available':False}
    if model:
        source=inside(model,runtime)
        if source.is_file():
            shutil.copy2(source,out/'candidate.lib');initial=runtime/'models/input_model.lib'
            model_info={'available':True,'sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                'changed_from_input':initial.exists() and initial.read_bytes()!=source.read_bytes()}
    plots=[]
    try:
        from .plots import generate
        plots=generate(report,out)
    except Exception as e:notes.append({'stage':'plots','export_error':str(e)[:300]})
    meta={'plots':plots,'workflow_status':report.get('workflow_status'),'model':model_info,
          'notice':'candidate.lib is the retained candidate, not necessarily accepted. See summary.json for full acceptance and gaps.', 'export_warnings':notes}
    (out/'export_manifest.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2))
    shutil.copy2(runtime/'summary.json',out/'summary.json')
    bundle=out/'results.zip'
    with zipfile.ZipFile(bundle,'w',zipfile.ZIP_DEFLATED) as z:
        for f in out.rglob('*'):
            if f.is_file() and f!=bundle:z.write(f,f.relative_to(out))
    return meta
