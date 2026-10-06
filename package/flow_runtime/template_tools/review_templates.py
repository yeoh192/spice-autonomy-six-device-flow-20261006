#!/usr/bin/env python3
"""Audit indexed library against local provenance catalog; never fabricate approval."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
from .index_templates import decode, logical_lines, write_csv

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def provenance_matches(root):
    catalog=root/'library/manufacturer_model_index.csv'
    matches={}
    if not catalog.is_file():return matches
    with catalog.open(encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            path=(root/row['model_or_symbol_path']).resolve()
            if root.resolve() not in path.parents or not path.is_file():continue
            digest=sha(path)
            evidence={'catalog':str(catalog),'matched_path':str(path),'sha256':digest,'manufacturer':row['manufacturer'],'source_id':row['source_id'],'product_url':row.get('product_url',''),'download_url':row.get('download_url','')}
            if evidence not in matches.setdefault(digest,[]):matches[digest].append(evidence)
    catalog2=root/'library/template_library.csv'
    if catalog2.is_file():
        with catalog2.open(encoding='utf-8-sig') as f:
            for row in csv.DictReader(f):
                path=(root/row['file_path']).resolve()
                if root.resolve() not in path.parents or not path.is_file():continue
                digest=sha(path)
                if digest!=row['file_sha256']:continue
                evidence={'catalog':str(catalog2),'matched_path':str(path),'sha256':digest,'manufacturer':row['manufacturer'],'source_id':row['template_id'],'product_url':row.get('product_url',''),'download_url':row.get('download_url','')}
                if evidence not in matches.setdefault(digest,[]):matches[digest].append(evidence)
    return matches

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--index',type=Path,required=True);ap.add_argument('--root',type=Path,required=True);ap.add_argument('--provenance-root',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);ap.add_argument('--source-policy',choices=['all','strict'],default='all')
    args=ap.parse_args();out=args.output.resolve();root=args.root.resolve()
    if out==root or root in out.parents:ap.error('不得在原模板库内输出')
    if out.exists() and any(out.iterdir()):ap.error('输出目录必须为空')
    catalog=provenance_matches(args.provenance_root)
    out.mkdir(parents=True,exist_ok=True);shutil.copy2(args.index,out/'reviewed.sqlite')
    db=sqlite3.connect(out/'reviewed.sqlite');db.row_factory=sqlite3.Row
    files=[dict(r) for r in db.execute('select * from files')]
    for f in files:
        if sha(root/f['path'])!=f['sha256']:raise ValueError('原模板发生变化：'+f['path'])
    reviewed=[];structural=[]
    db.execute('CREATE TABLE provenance_review(path TEXT PRIMARY KEY,decision TEXT,evidence_json TEXT,reason TEXT)')
    for f in files:
        evidence=catalog.get(f['sha256'],[])
        markers=[dict(r) for r in db.execute('select line,kind,text from source_evidence where path=?',(f['path'],))]
        if evidence:
            decision='excluded_catalog_match' if args.source_policy=='strict' else 'recorded_catalog_match';reason='Exact bytes match locally recorded manufacturer/official-distribution catalog; online origin not independently re-downloaded.'
        elif markers:
            decision='excluded_marked_source' if args.source_policy=='strict' else 'recorded_source_marker';reason='Organization/rights markers retained; exclusion only under strict policy, not an authorship/legal determination.'
        else:
            decision='pending_provenance';reason='No independently evidenced author or derivation history; filename and missing copyright do not establish eligibility.'
        item={'path':f['path'],'sha256':f['sha256'],'decision':decision,'reason':reason,'evidence':evidence or markers}
        reviewed.append(item);db.execute('INSERT INTO provenance_review VALUES(?,?,?,?)',(f['path'],decision,json.dumps(evidence or markers,ensure_ascii=False),reason))
        if evidence:db.execute('UPDATE entries SET source_status="local_catalog_hash_match" WHERE path=?',(f['path'],))
        if evidence and args.source_policy=='strict':db.execute('UPDATE entries SET selection_status="excluded",selection_reason=?,source_status="local_catalog_hash_match" WHERE path=?',(reason,f['path']))
    for row in db.execute('select * from dependencies where status<>"resolved"'):
        d=dict(row);text,_enc,_fallback=decode((root/d['path']).read_bytes())
        if d['status']=='parameterized_call':
            key=d['target'].strip('{}');import re
            assignments=[{'line':line,'assignment':body[:1200]} for line,body in logical_lines(text) if re.search(r'(?i)\b'+re.escape(key)+r'\s*=',body)]
            structural.append({'path':d['path'],'line':d['line'],'issue':'parameterized_call','decision':'requires_parameter_expansion','detail':d['target'],'evidence':assignments[:20]})
        else:structural.append({'path':d['path'],'line':d['line'],'issue':d['status'],'decision':'needs_review','detail':d['target'],'evidence':d['resolved_json']})
    for row in db.execute('select * from parse_issues'):
        d=dict(row);text,_enc,_fallback=decode((root/d['path']).read_bytes())
        typo=[{'line':i+1,'text':s} for i,s in enumerate(text.splitlines()) if s.strip().upper().startswith('.ENDSS')]
        structural.append({'path':d['path'],'line':d['line'],'issue':'unclosed_subckt','decision':'blocked_original_unchanged','detail':d['detail'],'evidence':typo})
    if args.source_policy=='strict':
        db.execute("UPDATE entries SET selection_status=CASE WHEN source_status<>'unverified' THEN 'excluded' ELSE 'pending_review' END,selection_reason='strict source policy: independent origin not confirmed'")
    if args.source_policy=='all':
        db.execute("UPDATE entries SET selection_status=CASE WHEN top_level=0 THEN 'internal' WHEN parser_status='parsed_not_simulated' AND dependency_status='declared_dependencies_resolved' THEN 'searchable' ELSE 'needs_review' END,selection_reason='source-neutral retrieval; electrical acceptance not evaluated'")
    db.execute("INSERT OR REPLACE INTO metadata VALUES('source_policy',?)",(args.source_policy,))
    db.commit()
    eligible=[dict(r) for r in db.execute('select path,line,name,kind,model_type from entries where top_level=1 and selection_status IN ("selectable","searchable") and parser_status="parsed_not_simulated" and dependency_status="declared_dependencies_resolved"')]
    pending=[dict(r) for r in db.execute('select path,line,name,kind,model_type from entries where top_level=1 and selection_status="pending_review"')]
    summary={'source_policy':args.source_policy,'files_audited':len(files),'catalog_exact_match_files':sum(r['decision'] in ('excluded_catalog_match','recorded_catalog_match') for r in reviewed),'marked_source_files':sum(r['decision'] in ('excluded_marked_source','recorded_source_marker') for r in reviewed),'pending_provenance_files':sum(r['decision']=='pending_provenance' for r in reviewed),'top_level_pending_entries':len(pending),'eligible_candidates':len(eligible),'structural_review_items':len(structural),'integrity_check':db.execute('pragma integrity_check').fetchone()[0]}
    db.close()
    write_csv(out/'provenance_review.csv',['path','sha256','decision','reason','evidence'],reviewed)
    write_csv(out/'structural_review.csv',['path','line','issue','decision','detail','evidence'],structural)
    write_csv(out/'pending_entries.csv',['path','line','name','kind','model_type'],pending)
    (out/'candidate_pool.json').write_text(json.dumps({'status':'ready' if eligible else 'empty_no_verified_independent_provenance','entries':eligible,'note':'Source evidence retained; manufacturer models allowed under all policy. Retrieval is not electrical acceptance.'},ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'report.md').write_text('# 模板来源与结构核验\n\n'+ '\n'.join(f'- {k}: {v}' for k,v in summary.items())+'\n\n## 判定限制\n\n本地来源目录中记录的下载地址作为证据保留，本次未重新联网下载确认。哈希相同表示文件内容相同，不证明模型精度。来源审计结论与候选选择策略分开。默认all策略不因厂家或未知来源排除，strict策略保留此前行为。可检索不表示已通过手册拟合验收。参数化调用不是缺文件，仍需按入口展开参数才可证明可运行。原模板未修改，候选池为空也如实保留。\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2));print(out)
if __name__=='__main__':main()
