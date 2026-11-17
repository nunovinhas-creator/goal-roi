"""Isolated prospective shadow ledger. No production imports with side effects, network or notifications.

An actual capture timestamp, not reconstructed signal time, is the decision time.
The ledger must be published externally before outcomes to establish prospective provenance.
"""
import argparse
import copy
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from . import chronology, report
from .eligibility import eligible
from .parser import settle

UTC = timezone.utc
ALPHA = .05 / 6  # three contrasts, two directions; union bound, dependence allowed


def digest(x):
    return hashlib.sha256(json.dumps(x, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def protocol(main_sha, frozen):
    start = chronology.parse_utc(frozen)
    if start is None or len(main_sha) != 40:
        raise ValueError('invalid freeze')
    return {'version': 1, 'main_sha': main_sha, 'frozen_utc': start.isoformat(),
            'end_utc': (start + timedelta(days=30)).isoformat(), 'stake_units': 1,
            'rules': {'H1': 'OPEN_GAME; abs(home-away)>=2; minute in [46,90]',
                      'H2': 'OPEN_GAME; abs(home-away)>=2; any match period'},
            'h1_period_policy': '46..90 proves regular second half; 45 or >90 ambiguous: H2 only, flagged; never infer from FT',
            'eligibility': 'eligible(); original unedited first version; market Asian Total; valid quarter line; odds>1; '
                           'sent AND captured after freeze, within 120s of sending; no FT/mark/note known for game; '
                           'actual capture is decision; no retrospective backfill',
            'min_games': 100, 'alpha_each_direction': ALPHA, 'lambda': .25,
            'tests': ['H1 profit at odds*0.98', 'H2 profit at odds*0.98', 'H1 minus H2 policy per H2 game at odds*0.98'],
            'sequential': 'common nonoverlapping H2 game chain selected at H2 capture; admit next only after previous closes; '
                          'close at FT or H2 decision+72h; unresolved counted -1 for individual tests, -1 for paired test '
                          '(worst bound); factual settlements remain pending; fixed lambda e-products 1+lambda*x '
                          'for improvement, 1-lambda*x/B for deterioration; B fixed before outcome from max observed odds '
                          '(paired upper bound uses max offered odds before closure); paired test uses symmetric per-game '
                          'bound fixed at H2 entry: absolute profit capped at 1u ONLY for paired contrast; '
                          'success requires 100 chain games and e>=1/alpha and 30d elapsed; otherwise INCONCLUSIVO',
            'paired_estimand': 'profit(H1 first eligible, or 0 if none) minus profit(H2 first eligible); '
                               'same game once; paired confirmatory contrast clips each policy profit to [-1,1]; '
                               'uncapped difference descriptive; no causal attribution',
            'failure': 'negative-direction e>=1/alpha with 100 games after 30d; no crossing => INCONCLUSIVO; '
                       'one terminal decision; no extension without new protocol',
            'activation': 'not_installed; requires separately authorized integration; existing certification unchanged'}


def new_ledger(p):
    return {'protocol_sha256': digest(p), 'events': [], 'head': None}


def append(ledger, event):
    e = dict(event, prev=ledger['head'])
    e['hash'] = digest(e)
    ledger['events'].append(e)
    ledger['head'] = e['hash']


def verify(p, ledger):
    if ledger['protocol_sha256'] != digest(p):
        raise ValueError('protocol changed')
    prev = None
    last = None
    for event in ledger['events']:
        raw = {k: v for k, v in event.items() if k != 'hash'}
        t = chronology.parse_utc(event['at'])
        if event['prev'] != prev or event['hash'] != digest(raw) or t is None or (last and t < last):
            raise ValueError('ledger corruption or time reversal')
        prev, last = event['hash'], t
    if ledger['head'] != prev:
        raise ValueError('head mismatch')


def firsts(ledger):
    out = {'H1': {}, 'H2': {}}
    for e in ledger['events']:
        if e['kind'] == 'decision':
            for h in e['rules']:
                out[h].setdefault(e['bet']['match_id'], e)
    return out


def capture(p, ledger, signals, now, known_games=()):
    """Actual invocation only. Never use this function for a historical replay claim."""
    verify(p, ledger)
    start, end = map(chronology.parse_utc, (p['frozen_utc'], p['end_utc']))
    if not start <= now < end:
        return ledger
    selected = firsts(ledger)
    known = set(known_games)
    seen_ids = {e.get('message_id') for e in ledger['events'] if e['kind'] in ('decision', 'subsequent')}
    for b in sorted(signals, key=lambda x: (x['date_utc'], x['message_id'])):
        if b.get('type') != 'OPEN_GAME' or b['message_id'] in seen_ids:
            continue
        sent = chronology.parse_utc(b.get('date_utc'))
        score, minute = b.get('score_at_signal'), b.get('minute')
        if not eligible(b) or not sent or not start <= sent <= now or now-sent > timedelta(seconds=120):
            continue
        if not (isinstance(score, (list, tuple)) and len(score) == 2 and all(type(x) is int and x>=0 for x in score)
                and abs(score[0]-score[1]) >= 2 and type(minute) is int and minute>=0):
            continue
        if b.get('market') != 'Asian Total' or b.get('match_id') in known or b.get('ft') or b.get('had_ft_at_first_sight'):
            continue
        try:
            side, line = b['selection'].split()
            if side not in ('Over', 'Under') or not math.isfinite(float(line)) or float(line)<0 or float(line)*4 % 1:
                continue
        except (ValueError, KeyError):
            continue
        if not math.isfinite(b['odds']) or b['odds'] <= 1:
            continue
        mid = b['match_id']
        rules = [h for h, condition in [('H2', True), ('H1', 46<=minute<=90)] if condition and mid not in selected[h]]
        fields = ('match_id','message_id','date_utc','signal_seen_utc','market','selection','odds','minute','score_at_signal','teams','league')
        snap = {k: b.get(k) for k in fields}
        event = {'kind': 'decision' if rules else 'subsequent', 'at': now.isoformat(), 'message_id': b['message_id'],
                 'rules': rules, 'bet': snap, 'ambiguous_h1_period': minute==45 or minute>90}
        append(ledger, event)
        for h in rules:
            selected[h][mid] = event
        seen_ids.add(b['message_id'])
    return ledger


def record_result(p, ledger, mid, ft, source, observed, now):
    verify(p, ledger)
    if not source or not isinstance(ft, list) or len(ft)!=2 or any(type(x) is not int or x<0 for x in ft):
        raise ValueError('unproven result')
    t = chronology.parse_utc(observed)
    decisions = [e for e in ledger['events'] if e['kind']=='decision' and e['bet']['match_id']==mid]
    if not decisions or t is None or t>now or t<=max(chronology.parse_utc(e['at']) for e in decisions):
        raise ValueError('result chronology')
    prev = [e for e in ledger['events'] if e['kind']=='result' and e['match_id']==mid]
    if prev and prev[-1]['ft']==ft and prev[-1]['source']==source:
        return
    append(ledger, {'kind':'result','at':now.isoformat(),'match_id':mid,'ft':ft,'source':source,
                    'observed_utc':observed, 'correction':bool(prev and prev[-1]['ft']!=ft)})


def metrics(rows):
    done = [r for r in rows if r['w'] is not None]
    profits = [report.pl(r['w'], r['odds']) for r in done]
    n = len(done)
    total, peak, dd = 0., 0., 0.
    for x in profits:
        total += x; peak = max(peak,total); dd = max(dd,peak-total)
    # Distribution-free fixed-time Hoeffding interval; not used for sequential decisions.
    bound = max([r['odds'] for r in rows]+[1.])
    radius = bound*math.sqrt(math.log(40)/(2*n)) if n else None
    return {'games':len(rows),'settled':n,'pending':len(rows)-n,
            'wins':sum(r['w']==1 for r in done),'losses':sum(r['w']==-1 for r in done),
            'pushes':sum(r['w']==0 for r in done),'half_wins':sum(r['w']==.5 for r in done),
            'half_losses':sum(r['w']==-.5 for r in done), 'profit_units':total,'max_drawdown_units':dd,
            'roi':{str(h):sum(report.pl(r['w'],r['odds'],h) for r in done)/n if n else None for h in (0,.02,.05)},
            'fixed_time_ci95': [max(-1,total/n-radius),min(bound-1,total/n+radius)] if n else None,
            'ci_assumption':'independent games; descriptive only; dependence controlled separately by serial chain',
            'pending_sensitivity_profit': [total-(len(rows)-n),total+sum(r['odds']-1 for r in rows if r['w'] is None)],
            'all_settled_flipped_profit':sum(report.pl(-r['w'],r['odds']) for r in done),
            'by_day':groups(rows,lambda r:r['date_utc'][:10]),
            'by_competition':groups(rows,lambda r:r.get('league') or 'unknown'),
            'by_odds':groups(rows,lambda r:'1-2' if r['odds']<2 else '2-3' if r['odds']<=3 else '>3')}


def groups(rows, key):
    out={}
    for r in rows:
        g=out.setdefault(key(r),{'games':0,'settled':0,'profit_units':0.})
        g['games']+=1
        if r['w'] is not None:
            g['settled']+=1; g['profit_units']+=report.pl(r['w'],r['odds'])
    return out


def analyse(p, ledger, now, history_versions=None):
    verify(p, ledger)
    selected=firsts(ledger); results={}
    for e in ledger['events']:
        if e['kind']=='result' and chronology.parse_utc(e['at'])<=now:
            results[e['match_id']]=e
    rows={h:[] for h in selected}
    for h, games in selected.items():
        for mid,e in games.items():
            if chronology.parse_utc(e['at'])>now:
                continue
            r=results.get(mid); b=e['bet']
            rows[h].append(dict(b,decision_utc=e['at'],w=settle(b['market'],b['selection'],r['ft'],b['teams']) if r else None))
    pairs=[]
    for r2 in rows['H2']:
        r1=next((r for r in rows['H1'] if r['match_id']==r2['match_id']),None)
        pairs.append({'match_id':r2['match_id'],'h1':r1,'h2':r2,
                      'same_decision':bool(r1 and r1['message_id']==r2['message_id']),
                      'difference': (report.pl(r1['w'],r1['odds']) if r1 else 0)-report.pl(r2['w'],r2['odds'])
                                    if r2['w'] is not None and (r1 is None or r1['w'] is not None) else None})
    seq=sequential(p,ledger,now)
    problems=history_check(p,ledger,history_versions)
    if any(e.get('correction') for e in ledger['events']): problems.append('corrected outcomes: inference quarantined')
    statuses={k:'INCONCLUSIVO' for k in ('H1','H2','delta')}
    if not problems and now>=chronology.parse_utc(p['end_utc'])+timedelta(hours=72):
        for k,values in seq['evalues_positive_negative'].items():
            if seq['games'][k]>=p['min_games']:
                if values[0]>=1/ALPHA and values[1]<1/ALPHA: statuses[k]='CONFIRMADO'
                elif values[1]>=1/ALPHA and values[0]<1/ALPHA: statuses[k]='REJEITADO'
    seq['decision']=statuses['delta']; seq['statuses']=statuses;seq['history_problems']=problems
    return {'protocol_sha256':digest(p),'as_of':now.isoformat(),'scientific_state':seq['decision'],
            'H1':metrics(rows['H1']),'H2':metrics(rows['H2']), 'paired':pairs,
            'overlap':sum(x['h1'] is not None for x in pairs),'h2_exclusive':sum(x['h1'] is None for x in pairs),
            'sequential':seq, 'production_certification':'not_activated; untouched',
            'continuous_monitoring':False, 'external_history_verified':not problems}


def sequential(p, ledger, now):
    """Predictable serial admission; missing outcome worst case separate from factual data.

    Algebraic e-values only: never issue certification without external append-only proof.
    Conditional null required for each game given all information at its decision.
    """
    games=firsts(ledger); evalues={k:[1.,1.] for k in ('H1','H2','delta')}; counts={k:0 for k in evalues}
    previous_close=None; chain=[]
    for mid,e2 in games['H2'].items():
        entry=chronology.parse_utc(e2['at'])
        if entry>now or (previous_close and entry<=previous_close):
            continue
        deadline=entry+timedelta(hours=72)
        # Earliest actual result closes the chain; never use latest correction for admission.
        observed=[e for e in ledger['events'] if e['kind']=='result' and e['match_id']==mid]
        first=min(observed,key=lambda e:e['at']) if observed else None
        close=min(chronology.parse_utc(first['at']),deadline) if first else deadline
        previous_close=close
        if close>now:
            break
        r=first if first and chronology.parse_utc(first['at'])<=deadline else None
        e1=games['H1'].get(mid)
        if e1 and chronology.parse_utc(e1['at'])>=close:
            e1=None
        vals={}; raw={}
        for h,event in [('H1',e1),('H2',e2)]:
            if event is None:
                continue
            b=event['bet']; w=settle(b['market'],b['selection'],r['ft'],b['teams']) if r else None
            x=report.pl(w,b['odds'],.02) if w is not None else -1.
            raw[h]=x; vals[h]=(x,max(1.,b['odds']*.98-1))
        delta=max(-1.,min(1.,raw.get('H1',0)))-max(-1.,min(1.,raw['H2'])) if r else -2.
        vals['delta']=(delta/2,1.)
        for k,(x,bound) in vals.items():
            evalues[k][0]*=1+.25*x
            evalues[k][1]*=1-.25*(x if r else bound)/bound
            counts[k]+=1
        chain.append({'match_id':mid,'entry':entry.isoformat(),'close':close.isoformat(),'missing_conservative':r is None})
    return {'evalues_positive_negative':evalues,'games':counts,'threshold':1/ALPHA,'chain':chain,
            'algebraic_crossings':{k:('positive' if v[0]>=1/ALPHA else 'negative' if v[1]>=1/ALPHA else None) for k,v in evalues.items()},
            'decision':'INCONCLUSIVO', 'blocker':'external publication/history proof not installed; no scientific certificate permitted'}


def history_check(p, ledger, versions):
    """External git publication proof, under the same trusted-commit-time assumption as certification.

    Local hash integrity alone is never accepted. Every decision must be published before
    the first system observation of any result of its game. Fail closed on unpublished events.
    """
    if not versions: return ['no external publication proof']
    previous=[]; first_published={}; problems=[]
    for when,version in versions:
        try: verify(p,version)
        except (ValueError,KeyError,TypeError): return ['invalid published ledger']
        events=version['events']
        if events[:len(previous)]!=previous: return ['history rewritten']
        for e in events[len(previous):]:
            at=chronology.parse_utc(e['at'])
            # Git dates have one-second resolution. Operational v2 also requires exact remote receipts.
            coherent=(when is not None and at<=when<=at+timedelta(minutes=5))
            if p.get('version')==2 and when is not None:
                coherent=at<when+timedelta(seconds=1) and when<=at+timedelta(minutes=5)
            if not coherent: problems.append('out-of-time publication')
            first_published[e['hash']]=when
        previous=events
    if ledger['events']!=previous: problems.append('unpublished or changed events')
    result_times={}
    for e in ledger['events']:
        if e['kind']=='result':
            t=chronology.parse_utc(e['observed_utc'])
            if t is None: problems.append('unknown result time')
            else: result_times[e['match_id']]=min(t,result_times.get(e['match_id'],t))
    for e in ledger['events']:
        if e['kind']=='decision':
            published=first_published.get(e['hash']); result=result_times.get(e['bet']['match_id'])
            if result and (published is None or published>=result): problems.append('decision not published before result')
    return problems

def collect_project(p, ledger, now):
    """Read existing project observations. No API requests, production writes or retrospective decisions."""
    from .store import data_dir
    chron={}; msgs,ft,marks,notes,_=report.load(chron)
    ext={}; chron['external']={}; scores={}
    for mid,score in ft.items(): scores.setdefault(mid,set()).add(tuple(score))
    for name in ('hl','bsd'):
        path=data_dir()/('results_'+name+'.json')
        state=json.loads(path.read_text()) if path.exists() else {}
        for mid,r in state.get('results',{}).items():
            ext.setdefault(mid,dict(r,provider=name.upper()))
            chron['external'].setdefault(mid,[]).append(r.get('matched_at_utc'))
            scores.setdefault(mid,set()).add(tuple(r['ft']))
    signals=report.build_bets(msgs,ft,marks,notes,strict=False,bsd=ext,chronology=chron)
    known={b['match_id'] for b in signals if b.get('ft') or b.get('result_known_utc')}
    known.update(chron.get('ft_any',{})); known.update(k[0] for k in marks)
    for m in msgs:
        if m.get('bot_mark') is not None or m.get('had_ft_at_first_sight'): known.add(m.get('match_id'))
    # Use individual original messages, not per-market aggregation: later independent signals are retained.
    originals=[dict(m,in_roi=report.provenance_ok(m),signal_seen_utc=m.get('collected_at_utc')) for m in msgs]
    capture(p,ledger,originals,now,known)
    chosen=set(firsts(ledger)['H2'])
    for b in signals:
        mid=b['match_id']
        if mid not in chosen or b['ft'] is None or len(scores.get(mid,set()))>1:
            continue
        if chronology.parse_utc(b.get('result_known_utc')) is None:
            continue
        try:
            record_result(p,ledger,mid,list(b['ft']),b['result_known_source'],b['result_known_utc'],now)
        except ValueError:
            # An unknown/contradictory timestamp remains pending; never invent a result time.
            continue
    return ledger

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('action',choices=['init','analyse','capture','collect']); ap.add_argument('--protocol',required=True)
    ap.add_argument('--ledger',required=True); ap.add_argument('--output'); ap.add_argument('--input'); args=ap.parse_args()
    p=json.loads(Path(args.protocol).read_text()); path=Path(args.ledger)
    if args.action=='init':
        with path.open('x') as f: json.dump(new_ledger(p),f,indent=2)
    elif args.action=='collect':
        import fcntl
        with path.with_suffix('.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            ledger=collect_project(p,json.loads(path.read_text()),datetime.now(UTC))
            temp=path.with_suffix('.tmp'); temp.write_text(json.dumps(ledger,indent=2)+'\n'); temp.replace(path)
    elif args.action=='capture':
        if not args.input: ap.error('--input required (signals, known_games, results)')
        data=json.loads(Path(args.input).read_text()); ledger=json.loads(path.read_text()); now=datetime.now(UTC)
        capture(p,ledger,data['signals'],now,data['known_games'])
        for r in data.get('results',[]):
            record_result(p,ledger,r['match_id'],r['ft'],r['source'],r['observed_utc'],now)
        temp=path.with_suffix('.tmp'); temp.write_text(json.dumps(ledger,indent=2)+'\n'); temp.replace(path)
    else:
        from .certification import published_versions
        versions,problems=published_versions(path)
        result=analyse(p,json.loads(path.read_text()),datetime.now(UTC),versions if not problems else None)
        if args.output: Path(args.output).write_text(json.dumps(result,indent=2)+'\n')
        else: print(json.dumps(result,indent=2))

if __name__=='__main__': main()
