"""RETROSPECTIVE diagnostic of original signals. NEVER writes the prospective ledger."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scangoal import report
from scangoal.eligibility import eligible
from scangoal.shadow import metrics
from scangoal.store import data_dir


def run():
    msgs,ft,marks,notes,_=report.load(); ext={}
    for name in ('hl','bsd'):
        path=data_dir()/('results_'+name+'.json')
        for mid,r in json.loads(path.read_text()).get('results',{}).items():
            ext.setdefault(mid,dict(r,provider=name.upper()))
    selected={'H1':{},'H2':{}}
    for m in sorted(msgs,key=lambda x:(x['date_utc'],x['message_id'])):
        if m['type']!='OPEN_GAME':continue
        # One original at a time: no per-market aggregation can hide later H1 entry.
        for b in report.build_bets([m],ft,marks,notes,strict=False,bsd=ext):
            score=b.get('score_at_signal');minute=b.get('minute')
            if not eligible(b) or not score or abs(score[0]-score[1])<2:continue
            selected['H2'].setdefault(b['match_id'],b)
            if type(minute) is int and 46<=minute<=90:selected['H1'].setdefault(b['match_id'],b)
    paired=[]
    for mid,b2 in selected['H2'].items():
        b1=selected['H1'].get(mid)
        known=b2['w'] is not None and (not b1 or b1['w'] is not None)
        paired.append({'match_id':mid,'H1':{k:b1.get(k) for k in ('message_id','date_utc','minute','market','selection','odds','w')} if b1 else None,
                       'H2':{k:b2.get(k) for k in ('message_id','date_utc','minute','market','selection','odds','w')},
                       'same_entry':bool(b1 and b1['message_id']==b2['message_id']),
                       'uncapped_policy_difference_units':(report.pl(b1['w'],b1['odds']) if b1 else 0)-report.pl(b2['w'],b2['odds']) if known else None})
    return {'warning':'RETROSPECTIVE ONLY. Original signals and current final outcomes; not chronology-admitted, no scientific or causal validation; no prospective observations.',
            'H1':metrics(list(selected['H1'].values())),'H2':metrics(list(selected['H2'].values())),
            'overlap':len(selected['H1']),'h2_exclusive':len(selected['H2'])-len(selected['H1']),
            'same_entry':sum(r['same_entry'] for r in paired),'paired':paired}

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--output',required=True);args=ap.parse_args()
    out=Path(args.output)
    if out.name in ('ledger.json','protocol.json') or out.exists():ap.error('new descriptive file required; never overwrite protocol or ledger')
    out.write_text(json.dumps(run(),ensure_ascii=False,indent=2)+'\n')
