"""Reproduce parser impact at one immutable main snapshot and one report clock, offline."""
import argparse,collections,hashlib,json,subprocess,sys,types
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scangoal import report
from scangoal.store import data_dir

BASE='f03bda08878085a27f22735336012a7a4026d445'
CLOCK='2026-11-17T18:05:26+00:00'


def leaf_diff(a,b,path=''):
    if a==b:return []
    if isinstance(a,dict) and isinstance(b,dict):
        return [r for k in sorted(set(a)|set(b)) for r in leaf_diff(a.get(k),b.get(k),path+'/'+str(k))]
    if isinstance(a,list) and isinstance(b,list) and len(a)==len(b):
        return [r for i,(x,y) in enumerate(zip(a,b)) for r in leaf_diff(x,y,path+'/'+str(i))]
    return [{'path':path,'before':a,'after':b}]


def bet_rows():
    c={}; m,f,k,n,_=report.load(c);ext={}
    for name in ('hl','bsd'):
        for mid,r in json.loads((data_dir()/('results_'+name+'.json')).read_text()).get('results',{}).items():ext.setdefault(mid,dict(r,provider=name.upper()))
    return report.build_bets(m,f,k,n,strict=False,bsd=ext,chronology=c)


def run(out):
    # Load production code from the frozen commit into an isolated module: independent baseline, not a second new implementation.
    legacy=types.ModuleType('legacy_parser')
    exec(compile(subprocess.check_output(['git','show',BASE+':scangoal/parser.py'],text=True),'<frozen parser>','exec'),legacy.__dict__)
    old_report=types.ModuleType('scangoal._frozen_report');old_report.__package__='scangoal'
    exec(compile(subprocess.check_output(['git','show',BASE+':scangoal/report.py'],text=True),'<frozen report>','exec'),old_report.__dict__)
    # Capture all global report bindings before the corrected implementation runs.
    with patch.object(report,'settle',legacy.settle):
        before_bets=bet_rows()
    old_report.settle=legacy.settle
    before=old_report.build(now=datetime.fromisoformat(CLOCK))
    after=report.build(now=datetime.fromisoformat(CLOCK));after_bets=bet_rows()
    bykey={tuple(b['key']):b for b in before_bets};changed=[]
    for b in after_bets:
        old=bykey[tuple(b['key'])]
        if old['w']!=b['w']:
            changed.append({k:b.get(k) for k in ('message_id','match_id','type','phase','market','selection','odds','teams','ft','w','in_roi')})
            changed[-1].update(before_w=old['w'],delta_units=(report.pl(b['w'],b['odds'])-report.pl(old['w'],old['odds'])) if b['w'] is not None and old['w'] is not None else None)
    diffs=leaf_diff(before,after)
    groups={k:{'before':before['groups'][k],'after':after['groups'][k]} for k in before['groups'] if before['groups'][k]!=after['groups'][k]}
    result={'base_sha':BASE,'clock_utc':CLOCK,'changed_bets':changed,'changed_groups':groups,
        'production_data_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in data_dir().glob('*') if p.is_file()},
        'changed_top_level_fields':dict(collections.Counter(r['path'].split('/')[1] for r in diffs)),
        'changed_brain_sections':dict(collections.Counter(r['path'].split('/')[2] for r in diffs if r['path'].startswith('/brain/'))),
        'units_delta':sum(r['delta_units'] or 0 for r in changed),
        'before_counts':{k:before[k] for k in ('unique_bets','settled_bets','unsettled_market')},
        'after_counts':{k:after[k] for k in ('unique_bets','settled_bets','unsettled_market')},
        'certification_before':before['brain']['validation']['state'],'certification_after':after['brain']['validation']['state'],
        'full_leaf_differences':diffs}
    out.mkdir(parents=True,exist_ok=True)
    with (out/'impact.json').open('x') as f:json.dump(result,f,ensure_ascii=False,indent=2);f.write('\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('full_leaf_differences','production_data_sha256','changed_groups')},ensure_ascii=False,indent=2))

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--output-dir',required=True);args=ap.parse_args();run(Path(args.output_dir))
