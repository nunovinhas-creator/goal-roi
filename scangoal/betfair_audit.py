"""Read-only Betfair reconciliation and empirical sensitivity. No network or production writes.

Evidence import accepts only already integrated Highlightly/BSD payloads. Results are proposals,
never automatic settlement changes. Existing parser/identity/time/quarter-line logic is reused.
"""
import argparse
import collections
import csv
import hashlib
import json
import math
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from . import report
from .chronology import parse_utc
from .eligibility import eligible
from .parser import parse, settle
from .results_bsd import load_json, match_events
from .results_hl import to_event
from .store import data_dir

UTC=timezone.utc


def snapshot():
    chron={}; msgs,ft,marks,notes,audit=report.load(chron)
    ext={}; states={}; chron['external']={}
    for name in ('hl','bsd'):
        state=load_json(data_dir()/('results_'+name+'.json'),{})
        states[name]=state
        for mid,r in state.get('results',{}).items():
            ext.setdefault(mid,dict(r,provider=name.upper()))
            chron['external'].setdefault(mid,[]).append(r.get('matched_at_utc'))
    details=load_json(data_dir()/'details_hl.json',{}).get('details',{})
    chron['details']={m:r.get('fetched_at_utc') for m,r in details.items()}
    manual=load_json(data_dir()/'manual_results.json',{})
    for mid,r in manual.items():
        if not mid.startswith('_') and isinstance(r,dict) and isinstance(r.get('ft'),list):
            ext.setdefault(mid,dict(r,provider='manual',known_utc=r.get('observed_utc')))
            chron['external'].setdefault(mid,[]).append(r.get('observed_utc'))
    # Same regular-time reconstruction rule as production; raw late score is never used.
    import re
    for mid,d in details.items():
        if mid not in ext and d.get('consistent') and d.get('score_90') and re.search(r'extra time|penalt',d.get('status') or '',re.I):
            ext[mid]={'ft':d['score_90'],'provider':'HL aos 90 min','known_utc':d.get('fetched_at_utc')}
    bets=report.build_bets(msgs,ft,marks,notes,strict=False,bsd=ext,details=details,chronology=chron)
    return bets,states,audit


def exact_1x2(selection,teams):
    """A team name is accepted ONLY as an exact unique team name, never a substring/alias."""
    if selection in ('1','X','2'): return selection
    if not isinstance(selection,str): return None
    if selection.strip().casefold()=='draw': return 'X'
    hits=[str(i+1) for i,t in enumerate(teams or []) if t.strip().casefold()==selection.strip().casefold()]
    return hits[0] if len(hits)==1 else None


def factual_settle(b,ft):
    if b['market'].casefold()=='1x2':
        canonical=exact_1x2(b['selection'],b.get('teams'))
        return settle('1X2',canonical,ft,b.get('teams')) if canonical else None
    # Existing production policy: live AH depends on settlement scope, not just FT.
    if b['market'].casefold()=='asian handicap' and b['phase']=='live': return None
    return settle(b['market'],b['selection'],ft,b.get('teams'))


def evidence_candidates(targets,envelopes,now):
    found=collections.defaultdict(list); quarantine=[]
    for envelope in envelopes:
        if not isinstance(envelope,dict):
            quarantine.append({'reason':'invalid envelope','evidence_sha256':sha(envelope)});continue
        provider=envelope.get('provider'); observed=parse_utc(envelope.get('observed_utc'))
        if provider not in ('hl','bsd') or observed is None or observed>now:
            quarantine.append({'reason':'unsupported provider or invalid/future observation','evidence_sha256':sha(envelope)})
            continue
        payload=envelope.get('payload',{})
        if not isinstance(payload,dict):
            quarantine.append({'reason':'invalid payload shape','evidence_sha256':sha(envelope)});continue
        raw=payload.get('data',[]) if provider=='hl' else payload.get('results',[])
        if not isinstance(raw,list):
            quarantine.append({'reason':'invalid payload shape','evidence_sha256':sha(envelope)}); continue
        try:
            events=[to_event(x) for x in raw if isinstance(x,dict)] if provider=='hl' else raw
        except (TypeError,AttributeError,ValueError):
            quarantine.append({'reason':'malformed provider event','evidence_sha256':sha(envelope)});continue
        # Exact duplicate payload entries do not create false ambiguity. Conflicting duplicates do.
        unique={sha(x):x for x in events if isinstance(x,dict)}
        unfinished={}
        try:
            matches,near=match_events(targets,list(unique.values()),{},unfinished)
        except (TypeError,ValueError,KeyError,AttributeError):
            quarantine.append({'reason':'malformed provider event','evidence_sha256':sha(envelope)});continue
        for mid,r in matches.items():
            if observed<parse_utc(targets[mid]['date_utc']) or observed<parse_utc(r['event_date']):
                quarantine.append({'match_id':mid,'reason':'result observed before signal or kickoff'});continue
            found[mid].append(dict(r,provider=provider,observed_utc=observed.isoformat(),evidence_sha256=sha(envelope)))
        for mid,r in near.items(): quarantine.append({'match_id':mid,'reason':'name mismatch; no alias introduced','detail':r})
        for mid,r in unfinished.items(): quarantine.append({'match_id':mid,'reason':'not regular-time finished','detail':r})
        # match_events deliberately returns no result for multiple candidates; expose that state explicitly.
        from .results_bsd import norm_team,time_ok,kickoff_of,name_of
        for mid,t in targets.items():
            candidates=[e for e in unique.values() if norm_team(name_of(e,'home_team'))==norm_team(t['teams'][0])
                        and norm_team(name_of(e,'away_team'))==norm_team(t['teams'][1]) and time_ok(t,kickoff_of(e))]
            if len(candidates)>1: quarantine.append({'match_id':mid,'reason':'multiple exact candidates','evidence_sha256':sha(envelope)})
    proposals={}
    blocked={q['match_id'] for q in quarantine if q.get('match_id') and q['reason'] in ('multiple exact candidates','not regular-time finished')}
    for mid,rs in found.items():
        if len({tuple(r['ft']) for r in rs})>1:
            quarantine.append({'match_id':mid,'reason':'sources disagree','evidence':rs}); continue
        if mid in blocked: continue
        proposals[mid]={'ft':rs[0]['ft'],'confidence':'exact teams + compatible kickoff + unique regular-time final',
                        'evidence':rs,'decision':'PROPOSE_ONLY; production unchanged'}
    return proposals,quarantine


def sha(x): return hashlib.sha256(json.dumps(x,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def sensitivity(rows):
    done=[b for b in rows if b['w'] is not None]; pending=[b for b in rows if b['w'] is None]
    out={'signals':len(rows),'independent_games':len({r['match_id'] for r in rows}),
         'settled':len(done),'settled_independent_games':len({r['match_id'] for r in done}),
         'pending':len(pending),'pending_independent_games':len({r['match_id'] for r in pending}),'wins':sum(b['w']==1 for b in done),
         'losses':sum(b['w']==-1 for b in done),'stats_clustered_by_game':report.stats(done) if done else None}
    out['haircuts']={}
    for hc in (0,.02,.05):
        profit=sum(report.pl(b['w'],b['odds'],hc) for b in done)
        out['haircuts'][str(hc)]={'observed_profit_units':profit,'observed_roi':profit/len(done) if done else None,
            'all_pending_lost_profit':profit-len(pending),
            'all_pending_lost_roi':(profit-len(pending))/len(rows) if rows else None,
            'all_pending_won_roi':(profit+sum(b['odds']*(1-hc)-1 for b in pending))/len(rows) if rows else None,
            'additional_losses_to_nonpositive':max(0,math.ceil(profit)),
            'k_additional_losses':[{'k':k,'hypothetical_profit':profit-k,'hypothetical_roi':(profit-k)/(len(done)+k) if len(done)+k else None,
                                    'still_unknown':len(pending)-k} for k in sorted({0,min(5,len(pending)),min(10,len(pending)),len(pending)})]}
    out['warning']='Scenarios only, not factual losses. Multiple selections in one match are dependent; no prospective claim.'
    return out


def disagreement_cases():
    out=[]
    for line_no,line in enumerate((data_dir()/'messages.jsonl').read_text().splitlines(),1):
        if not line.strip():continue
        row=json.loads(line); p=parse(row['text'])
        if not (p.get('ft') and p.get('bet') and p.get('bot_mark') is not None):continue
        own=settle(p['market'],p['selection'],p['ft'],p.get('teams'))
        if own in (None,0) or (own>0)==p['bot_mark']:continue
        canonical=exact_1x2(p['selection'],p.get('teams')) if p['market'].casefold()=='1x2' else None
        corrected=settle('1X2',canonical,p['ft'],p.get('teams')) if canonical else None
        out.append({'line':line_no,'message_id':row['message_id'],'match_id':p.get('match_id'),'type':p['type'],
                    'market':p['market'],'selection':p['selection'],'teams':p.get('teams'),'ft':p['ft'],
                    'bot_mark':p['bot_mark'],'production_w':own,'canonical_1x2':canonical,'audit_w':corrected,
                    'source':'messages.jsonl:'+str(line_no),'observed_utc':row.get('collected_at_utc'),
                    'evidence_sha256':sha(row),'decision':'parser 1X2 team-name mismatch; proposed fix only' if corrected is not None else 'quarantine; manual review'})
    return out


def age_bucket(b,now):
    t=parse_utc(b.get('kickoff_utc') or b['date_utc'])
    if t is None:return 'unknown'
    h=(now-t).total_seconds()/3600
    if h<0:return 'not_started'
    return '<24h' if h<24 else '24-48h' if h<48 else '48-72h' if h<72 else '>72h'


def make_audit(now,envelopes=()):
    bets,states,production_audit=snapshot()
    bf=[b for b in bets if b['type']=='BETFAIR']
    targets={}
    for b in bf:
        if b['w'] is None: targets.setdefault(b['match_id'],b)
    proposals,quarantine=evidence_candidates(targets,envelopes,now)
    # A proposal conflicting with ANY previously stored final result is never accepted.
    for mid in list(proposals):
        known=[r['ft'] for st in states.values() for m,r in st.get('results',{}).items() if m==mid]
        known += [b['ft'] for b in bf if b['match_id']==mid and b['ft'] is not None]
        if any(tuple(ft)!=tuple(proposals[mid]['ft']) for ft in known):
            quarantine.append({'match_id':mid,'reason':'proposal conflicts with stored final','proposal':proposals.pop(mid)})
    # Re-index every saved provider result against pending identities, without new requests/aliases.
    cache_hits={}; cache_scanned=0
    for name,st in states.items():
        events=[]; originals={}
        for old_mid,r in st.get('results',{}).items():
            if not all(k in r for k in ('bsd_id','home','away','event_date','ft')): continue
            event={'id':r['bsd_id'],'home_team':r['home'],'away_team':r['away'],'event_date':r['event_date'],
                   'status':'finished','home_score':r['ft'][0],'away_score':r['ft'][1]}
            events.append(event); originals[r['bsd_id']]=(old_mid,r)
        cache_scanned+=len(events)
        matched,_=match_events(targets,events,{})
        for mid,r in matched.items():
            old_mid,original=originals[r['bsd_id']]
            cache_hits.setdefault(mid,[]).append({'provider':name,'original_match_id':old_mid,'record':original})
    inventory=[]
    for b in bf:
        pending=b['w'] is None; mid=b['match_id']; reason=None
        if pending:
            if b['ft']:reason='FT known; market scope/selection not settleable'
            elif any(q.get('match_id')==mid for q in quarantine):reason='quarantine: evidence ambiguous or conflicting'
            elif any(mid in st.get('near_misses',{}) for st in states.values()):reason='name mismatch in existing source cache; no alias accepted'
            elif any(mid in st.get('not_regular_time',{}) for st in states.values()):reason='extra time/penalties; no proven 90-minute score'
            elif any(mid in st.get('not_covered',{}) for st in states.values()):reason='source lists checked; no exact covered result'
            else:reason='no reliable final result in integrated observations'
        inventory.append({**{k:b.get(k) for k in ('message_id','match_id','teams','date_utc','kickoff_utc','phase','market','selection','odds','ft','w','result_known_utc','result_known_source')},
            'bet_id':sha(list(b['key'])),'age':age_bucket(b,now),'age_reference':'kickoff when known, otherwise signal; not time since final',
            'odds_gt_3':b['odds']>3,'eligible_roi':eligible(b),'state':'pending' if pending else 'settled',
            'reason':reason,'proposal':proposals.get(mid),
            'saved_provider_evidence':{name:st.get('results',{}).get(mid) for name,st in states.items() if mid in st.get('results',{})},
            'confidence':'existing project settlement' if not pending else ('proposal requires review' if mid in proposals else 'insufficient'),
            'source_checks':{name:{'last_run_utc':st.get('last_run_utc'),'near_miss':st.get('near_misses',{}).get(mid),
                                   'not_covered':st.get('not_covered',{}).get(mid)} for name,st in states.items()}})
    pending=[r for r in inventory if r['state']=='pending']
    cross=collections.Counter((r['phase'],r['age'],'1X2' if r['market'].casefold()=='1x2' else 'other','>3' if r['odds_gt_3'] else '<=3') for r in pending)
    filtered=[b for b in bf if eligible(b) and b['phase']=='live' and b['odds']>3]
    proposed_rows=[]
    for b in filtered:
        new=dict(b)
        if b['w'] is None and b['match_id'] in proposals: new['w']=factual_settle(b,proposals[b['match_id']]['ft'])
        proposed_rows.append(new)
    files=['messages.jsonl','results_hl.json','results_bsd.json','details_hl.json','team_aliases.json']
    return {'as_of_utc':now.isoformat(),'main_snapshot_sha':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
            'source_file_sha256':{f:hashlib.sha256((data_dir()/f).read_bytes()).hexdigest() for f in files if (data_dir()/f).exists()},
            'production_audit':production_audit,'inventory':inventory,'pending':pending,
            'pending_counts':{'all':len(pending),'eligible_roi':sum(r['eligible_roi'] for r in pending),
                'phase':dict(collections.Counter(r['phase'] for r in pending)),
                'age':dict(collections.Counter(r['age'] for r in pending)),
                'market':dict(collections.Counter(r['market'] for r in pending)),
                'odds_gt_3':sum(r['odds_gt_3'] for r in pending),'known_ft_unsettled':sum(r['ft'] is not None for r in pending),
                'cross_tab':[dict(phase=k[0],age=k[1],market=k[2],odds=k[3],n=n) for k,n in sorted(cross.items())]},
            'bf_gt3_live_eligible':sensitivity(filtered),'bf_gt3_live_all':sensitivity([b for b in bf if b['phase']=='live' and b['odds']>3]),
            'bf_gt3_live_proposed_only':sensitivity(proposed_rows),'disagreements':disagreement_cases(),
            'proposals':proposals,'quarantine':quarantine,'network_requests':0,
            'cache_reindex':{'pending_games':len(targets),'saved_records_scanned':cache_scanned,'exact_hits':cache_hits},
            'provider_quota_evidence':{name:{'last_run_utc':st.get('last_run_utc'),'stats':st.get('stats')} for name,st in states.items()},
            'limitations':['TheSportsDB is not integrated; BSD means Bzzoiro.',
                'No local provider credentials; no secrets read or changed; no additional API request performed.',
                'Existing result caches are trusted under their integrated matcher policy; no raw API response is preserved for historical matches.',
                'No production mutation; proposed settlements require reviewed evidence; absence is never a factual loss.']}


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output-dir',required=True);ap.add_argument('--evidence-jsonl');ap.add_argument('--as-of');ap.add_argument('--snapshot-sha')
    a=ap.parse_args();now=parse_utc(a.as_of) if a.as_of else datetime.now(UTC)
    if now is None:ap.error('timezone-aware --as-of required')
    envelopes=[json.loads(x) for x in Path(a.evidence_jsonl).read_text().splitlines() if x.strip()] if a.evidence_jsonl else []
    result=make_audit(now,envelopes)
    if a.snapshot_sha:
        if len(a.snapshot_sha)!=40:ap.error('full snapshot SHA required')
        for name,expected in result['source_file_sha256'].items():
            original=subprocess.check_output(['git','show',a.snapshot_sha+':data/'+name])
            if hashlib.sha256(original).hexdigest()!=expected:ap.error('snapshot data do not match the declared Git commit')
        result['main_snapshot_sha']=a.snapshot_sha
    result['auditor_modules_sha256']={name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in ('betfair_audit.py','parser.py','report.py')}
    out=Path(a.output_dir).resolve()
    # A read-only auditor must never overwrite production data/report/notification state.
    if out==data_dir().resolve() or out==Path('docs').resolve() or data_dir().resolve() in out.parents or Path('docs').resolve() in out.parents:
        ap.error('output must be outside production directories')
    out.mkdir(parents=True,exist_ok=True)
    if any((out/name).exists() for name in ('audit.json','all_betfair.csv','pending_betfair.csv','evidence_import.jsonl')):
        ap.error('snapshot already exists; choose a new output directory (no silent replacement)')
    if envelopes:
        (out/'evidence_import.jsonl').write_text(''.join(json.dumps(e,ensure_ascii=False,sort_keys=True)+'\n' for e in envelopes))
    (out/'audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    fields=['bet_id','message_id','match_id','teams','date_utc','phase','market','selection','odds','age','eligible_roi','state','ft','w','result_known_source','result_known_utc','confidence','reason']
    for name,rows in [('all_betfair',result['inventory']),('pending_betfair',result['pending'])]:
        with (out/(name+'.csv')).open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(rows)
    print(json.dumps({'pending':result['pending_counts'],'hypothesis':result['bf_gt3_live_eligible'],'proposals':len(result['proposals'])},indent=2))

if __name__=='__main__':main()
