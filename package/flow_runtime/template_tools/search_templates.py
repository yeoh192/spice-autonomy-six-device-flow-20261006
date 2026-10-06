#!/usr/bin/env python3
"""Bounded, source-neutral SQLite template retrieval; never executes model text."""
import argparse
import json
from pathlib import Path
import sqlite3
from .classify_templates import CATEGORIES, classify

FAMILIES = {'D':'diode','VDMOS':'mosfet','NMOS':'mosfet','PMOS':'mosfet','NPN':'bjt','PNP':'bjt','NJF':'jfet','PJF':'jfet'}

def search(index, category=None, name=None, ports=None, limit=20, include_review=False, subcategory=None, tag=None, capability=None, confidence=None):
    if not 1 <= limit <= 100: raise ValueError('limit must be 1..100')
    db=sqlite3.connect('file:'+str(Path(index).resolve())+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    try:
        policy=db.execute("select value from metadata where key='source_policy'").fetchone()
        policy=policy[0] if policy else 'strict'
        has_taxonomy=bool(db.execute("select 1 from sqlite_master where type='table' and name='taxonomy'").fetchone())
        taxonomy={r['entry_id']:json.loads(r['details_json']) for r in db.execute('select entry_id,details_json from taxonomy')} if has_taxonomy else {}
        entries=[dict(r) for r in db.execute('select e.*,f.sha256 from entries e join files f on e.path=f.path where e.top_level=1 order by e.name COLLATE NOCASE,e.path,e.line')]
        internal={}
        for r in db.execute('select path,scope,model_type from entries where top_level=0 and kind="model"'):
            internal.setdefault(r['path'],[]).append(dict(r))
        result=[];seen={}
        for e in entries:
            if e['selection_status'] not in ('searchable','selectable') and not include_review:continue
            if name and name.casefold() not in e['name'].casefold():continue
            pins=json.loads(e['ports_json'])
            if ports is not None and len(pins)!=ports:continue
            details=taxonomy.get(e['id'])
            if details is None:
                # Legacy indexes lack header evidence; use only declared types or external port semantics.
                synthetic=['']*(e['line']-1)+[e['declaration'],'.ends']
                details=classify(e,synthetic,internal.get(e['path'],[]))
            family=details['category'];basis=details['confidence']
            if category and family!=category:continue
            if subcategory and details['subcategory']!=subcategory:continue
            if tag and tag not in {t['tag'] for t in details['tags']}:continue
            if capability and capability not in {c['capability'] for c in details['capabilities']}:continue
            if confidence and details['confidence']!=confidence:continue
            key=(e['sha256'],e['name'].casefold(),e['kind'],e['scope'])
            if key in seen:
                seen[key]['duplicate_locations'].append({'path':e['path'],'line':e['line']});continue
            evidence=[dict(r) for r in db.execute('select line,kind,text from source_evidence where path=? limit 5',(e['path'],))]
            item={'path':e['path'],'line':e['line'],'entry':e['name'],'kind':e['kind'],'model_type':e['model_type'],'ports':pins,'category_hint':family,'category_basis':basis,'classification':details,'sha256':e['sha256'],'selection_status':e['selection_status'],'dependency_status':e['dependency_status'],'parser_status':e['parser_status'],'source_status':e['source_status'],'source_evidence':evidence,'duplicate_locations':[],'electrical_acceptance':'not_evaluated'}
            seen[key]=item;result.append(item)
        return {'source_policy':policy,'matched_distinct_candidates':len(result),'returned':min(limit,len(result)),'candidates':result[:limit],'note':'Categories are static hints. Match ports, dependencies, datasheet conditions and real SPICE regression before retention. No source-based ranking or fit claim.'}
    finally:db.close()

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--index',type=Path,required=True);ap.add_argument('--category',choices=CATEGORIES);ap.add_argument('--subcategory');ap.add_argument('--tag');ap.add_argument('--capability');ap.add_argument('--confidence',choices=['declared','documented_hint','topology_hint','identity_and_ports_hint','unknown']);ap.add_argument('--name');ap.add_argument('--ports',type=int);ap.add_argument('--limit',type=int,default=20);ap.add_argument('--include-review',action='store_true');ap.add_argument('--output',type=Path)
    a=ap.parse_args();data=search(a.index,a.category,a.name,a.ports,a.limit,a.include_review,a.subcategory,a.tag,a.capability,a.confidence);text=json.dumps(data,ensure_ascii=False,indent=2)
    if a.output:
        with a.output.open('x',encoding='utf-8') as f:f.write(text+'\n')
    print(text)
if __name__=='__main__':main()
