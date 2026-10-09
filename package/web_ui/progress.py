"""Read-only stage artifacts and event timeline, usable before final summary."""
import json
from pathlib import Path
GROUPS=['器件资料载入','模型评估筛选','SPICE模型文件','测试电路构建','真实仿真执行','结果对比校验','模型参数与结构修正','修正结果复核','输出规定格式','Agent请求与回复']
EXT={'.diff','.json','.jsonl','.lib','.cir','.inc','.log','.txt','.csv','.md','.raw','.png','.svg','.pdf'}
def group(path):
    p=str(path)
    if p.startswith('input/') or p.startswith('preflight/') or path.name=='input_snapshot.json':return GROUPS[0]
    if p.startswith('exports/') or path.name in ('summary.json','report.md','engineering_candidate.lib','final_model.lib'):return GROUPS[8]
    if 'template_selection/' in p:return GROUPS[1]
    if '/models/' in p:return GROUPS[2]
    if '/requests/' in p or '/request_contexts/' in p:return GROUPS[9]
    if path.suffix in ('.cir','.inc'):return GROUPS[3]
    if '/simulations/' in p:return GROUPS[4]
    if 'diagnostic' in p or 'patch' in p:return GROUPS[6]
    if 'regression' in p:return GROUPS[7]
    return GROUPS[5]
def snapshot(folder):
    folder=Path(folder);files=[];labels={}
    try:
        state_data=json.loads((folder/'runtime/state.json').read_text())
        for key,record in state_data.get('simulations',{}).items():
            if record.get('label'):labels[key]=record['label']
        report=json.loads((folder/'runtime/summary.json').read_text())
        for row in report.get('results',[]):
            if row.get('artifacts'):labels[Path(row['artifacts']).name]=row['test']
    except (OSError,ValueError,KeyError):pass
    for p in folder.rglob('*'):
        if not p.is_file() or p.is_symlink() or p.suffix not in EXT:continue
        try:p.resolve().relative_to(folder.resolve())
        except ValueError:continue
        try:stat=p.stat()
        except OSError:continue
        rel=p.relative_to(folder)
        label=labels.get(p.parent.name)
        files.append({'label': (label+' / '+p.name+' ['+p.parent.name[:8]+']') if label else str(rel), 'path':str(rel),'stage':group(rel),'size':stat.st_size,'mtime':stat.st_mtime_ns,'previewable':p.suffix in {'.diff','.json','.jsonl','.lib','.cir','.inc','.log','.txt','.csv','.md'}})
    files.sort(key=lambda f:f['mtime'],reverse=True)
    events=[]
    state=folder/'runtime/state.json'
    if state.exists():
        try:
            from flow_runtime.stage_labels import message
            for e in json.loads(state.read_text()).get('events',[])[-60:]:
                human=message(e['stage'],e['status'],e.get('detail'))
                if human:events.append({'time':e['time'],'text':human})
        except (OSError,ValueError,KeyError):pass
    return {'groups':GROUPS,'files':files[:3000],'truncated':len(files)>3000,'events':events}
