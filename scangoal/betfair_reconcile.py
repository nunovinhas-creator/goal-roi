"""Individual evidence reconciliation against an immutable previous audit. No external requests or writes to production."""
import argparse,collections,hashlib,json,re,subprocess
from pathlib import Path
from . import betfair_audit
from .chronology import parse_utc
from .parser import parse
from .results_bsd import time_ok
from .store import data_dir


def proofs(case, as_of=None):
    out=[]
    for line_no,line in enumerate((data_dir()/'messages.jsonl').read_text().splitlines(),1):
        if not line.strip():continue
        raw=json.loads(line);p=parse(raw['text'])
        if p.get('match_id')!=case['match_id'] or p.get('ft')!=case['ft']:continue
        if [t.casefold().strip() for t in p.get('teams',[])]!=[t.casefold().strip() for t in case['teams']]:continue
        observed=parse_utc(raw.get('collected_at_utc'));signal=parse_utc(case['date_utc'])
        kickoff = parse_utc(case.get('kickoff_utc'))
        if observed is None or signal is None or observed <= signal or (kickoff and observed <= kickoff):
            continue
        if as_of is not None and observed > as_of:
            continue
        if re.search(r'after extra time|after penalties|awarded|abandoned',raw['text'],re.I):continue
        out.append({'source':'messages.jsonl','line':line_no,'message_id':raw['message_id'],
                    'collected_at_utc':raw['collected_at_utc'],'sha256':hashlib.sha256(line.encode()).hexdigest(),
                    'parsed_teams':p['teams'],'parsed_ft':p['ft'],'raw_version':raw})
    return out


def snapshot_sha(audit, *, legacy=False):
    if legacy and 'data_snapshot_sha' not in audit:
        return audit.get('main_snapshot_sha')
    return audit.get('data_snapshot_sha')


def validate_inventory(audit, manifest, *, legacy=False):
    # Historical artifacts predate explicit optional absence. Accept the old format only
    # for the reference and rebuild the factual rows from the immutable reference commit.
    expected = ({name: digest for name, digest in manifest.items() if digest is not None}
                if legacy and 'data_snapshot_sha' not in audit else manifest)
    if audit.get('source_file_sha256') != expected:
        raise ValueError('inventory manifest differs from the declared commit')
    as_of = parse_utc(audit.get('as_of_utc'))
    if as_of is None:
        raise ValueError('inventory requires a timezone-aware as_of_utc')
    rebuilt = betfair_audit.make_audit(as_of)

    def factual(rows):
        return [{key: value for key, value in row.items()
                 if key not in ('reason', 'proposal', 'confidence')} for row in rows]

    for key in ('inventory', 'pending'):
        if factual(audit[key]) != factual(rebuilt[key]):
            raise ValueError(f'{key} differs from the factual rows of the declared commit')
    for key in ('bf_gt3_live_eligible', 'provider_quota_evidence'):
        if audit[key] != rebuilt[key]:
            raise ValueError(f'{key} differs from the declared commit')
    return as_of


def provider_proofs(case, states, as_of):
    evidence = []
    signal = parse_utc(case['date_utc'])
    for name, state in states.items():
        record = state.get('results', {}).get(case['match_id'])
        if not record or record.get('ft') != case['ft'] or not case['ft']:
            continue
        if [str(record.get(key, '')).strip().casefold() for key in ('home', 'away')] != [
                t.strip().casefold() for t in case['teams']]:
            continue
        start = parse_utc(record.get('event_date'))
        observed = parse_utc(record.get('matched_at_utc'))
        if signal and start and observed and max(start, signal) < observed <= as_of and time_ok(case, start):
            evidence.append({'source': f'results_{name}.json', 'record': record,
                             'sha256': betfair_audit.sha(record)})
    return evidence


def reconcile(reference,current,states,details):
    as_of = parse_utc(current['as_of_utc'])
    old={b['bet_id']:b for b in reference['inventory']};new={b['bet_id']:b for b in current['inventory']}
    recovered=[];ambiguous=[]
    for key,before in old.items():
        after=new.get(key)
        if before['state']=='pending' and after and after['state']=='settled':
            identity = ('match_id', 'teams', 'date_utc', 'phase', 'selection', 'odds')
            if any(before.get(field) != after.get(field) for field in identity):
                raise ValueError(f'bet identity changed between inventories: {key}')
            evidence = proofs(after, as_of) + provider_proofs(after, states, as_of)
            item={'bet_id':key,'message_id':after['message_id'],'match_id':after['match_id'],'teams':after['teams'],
                  'selection':after['selection'],'odds':after['odds'],'ft':after['ft'],'w':after['w'],
                  'result_known_utc':after['result_known_utc'],'source':after['result_known_source'],'evidence':evidence,
                  'decision':'reconciled against previous audit; already settled by existing collector before intervention; no production write'}
            if evidence:recovered.append(item)
            else:ambiguous.append(dict(item,decision='insufficient preserved individual evidence'))
    classified=[];aliases=[]
    for b in current['pending']:
        mid=b['match_id'];causes=[];candidates=[]
        for name,st in states.items():
            near=st.get('near_misses',{}).get(mid)
            if near:
                aliases.append({'match_id':mid,'bot_teams':b['teams'],'provider':name,'candidates':near,
                                'decision':'candidate only; not verified; do not apply aliases'})
                causes.append('different_names')
            if mid in st.get('not_covered',{}):causes.append('no_exact_coverage_after_complete_checks')
            if mid in st.get('not_regular_time',{}):causes.append('regular_time_score_not_proven')
        ko=parse_utc(b.get('kickoff_utc'));sent=parse_utc(b['date_utc'])
        if ko and sent and b['phase']=='pre' and sent>=ko:causes.append('prematch_timestamp_not_before_announced_kickoff')
        det=details.get(mid)
        if det and not det.get('consistent'):
            causes.append('inconsistent_goal_details')
            # Explicit diagnostic only: never repair a regular-time score by silently dropping shootout events.
            candidates.append({'source':'details_hl.json','record':det,'decision':'QUARANTINE; requires explicit regulation-period evidence; no factual settlement'})
        if not causes:causes=['no_reliable_result_in_integrated_sources']
        classified.append({'bet_id':b['bet_id'],'message_id':b['message_id'],'match_id':mid,'teams':b['teams'],
                           'causes':sorted(set(causes)),'evidence_candidates':candidates,'decision':'KEEP_PENDING'})
    unique_aliases={betfair_audit.sha(x):x for x in aliases}
    return {'reference_as_of':reference['as_of_utc'],'current_as_of':current['as_of_utc'],
            'reference_pending':len(reference['pending']),'reference_pending_now_settled_with_evidence':len(recovered),
            'reference_pending_still_pending':sum(b['state']=='pending' and new.get(k,{}).get('state')=='pending' for k,b in old.items()),
            'new_pending_since_reference':sum(k not in old and b['state']=='pending' for k,b in new.items()),
            'current_pending_before_this_intervention':len(current['pending']),'current_pending_after_this_intervention':len(current['pending']),
            'additional_factual_results_recovered_by_this_run':0,'new_provider_requests':0,'production_writes':0,
            'reconciled_reference_cases':recovered,'insufficient_recovery_evidence':ambiguous,
            'pending_classifications':classified,'cause_counts_overlapping':dict(collections.Counter(c for b in classified for c in b['causes'])),
            'unverified_alias_candidates':list(unique_aliases.values()),'current_hypothesis':current['bf_gt3_live_eligible'],
            'reference_hypothesis':reference['bf_gt3_live_eligible'],
            'quota_evidence':current['provider_quota_evidence'],
            'limits':'No TheSportsDB, new workflow, API call, alias application or production result mutation. Current unknowns remain factual pending.'}


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--reference',required=True);ap.add_argument('--current',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    out=Path(a.output).resolve()
    if out==data_dir().resolve() or data_dir().resolve() in out.parents or Path('docs').resolve() in out.parents:ap.error('not a production destination')
    reference=json.loads(Path(a.reference).read_text());current=json.loads(Path(a.current).read_text())
    ref_sha = snapshot_sha(reference, legacy=True)
    data_sha = snapshot_sha(current)
    try:
        with betfair_audit.frozen_inputs(ref_sha) as manifest:
            reference_time = validate_inventory(reference, manifest, legacy=True)
        with betfair_audit.frozen_inputs(data_sha, data_dir()) as manifest:
            current_time = validate_inventory(current, manifest)
            if current_time < reference_time:
                raise ValueError('current snapshot predates reference')
            betfair_audit.git('merge-base', '--is-ancestor', ref_sha, data_sha)
            _, states, _ = betfair_audit.snapshot()
            details = betfair_audit.load_json(data_dir()/'details_hl.json', {}).get('details', {})
            result = reconcile(reference, current, states, details)
            result['data_snapshot_sha'] = data_sha
            result['reference_data_snapshot_sha'] = ref_sha
            result['source_file_sha256'] = manifest
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        ap.error(str(exc))
    out.parent.mkdir(parents=True,exist_ok=True)
    with out.open('x') as f:json.dump(result,f,ensure_ascii=False,indent=2);f.write('\n')
    print(json.dumps({k:v for k,v in result.items() if k.endswith('_pending') or k.endswith('_intervention') or k.endswith('_evidence') and not isinstance(v,(dict,list)) or k in ('additional_factual_results_recovered_by_this_run','cause_counts_overlapping')},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
