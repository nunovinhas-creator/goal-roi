"""Offline integration rehearsal with real Git push to a local bare remote and real wall clock. No production resources."""
import argparse,json,sys
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]));sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
import test_shadow_ops as fixture
from scangoal import shadow_ops as ops


def run(output):
    fixture.T=datetime.now(timezone.utc)
    case=fixture.OperationalShadow();case.setUp()
    try:
        case.pub.clock=lambda:datetime.now(timezone.utc)
        case.time=datetime.now(timezone.utc);case.message(age=0)
        result=ops.tick(case.pub,case.source,case.auth)
        ledger=case.ledger();receipt=ops.read(case.pub.state/'receipts.json')[0]
        remote_sha=ops.git(case.remote,'rev-parse','refs/heads/shadow/h1-h2-operational')
        remote_ledger=json.loads(ops.git(case.remote,'show',remote_sha+':'+str(ops.ROOT/'ledgers'/f'{case.ident}.json')))
        original_events=list(ledger['events']);ops.tick(case.pub,case.source,case.auth)
        analysis=ops.evaluate(case.pub)
        decision=next(e for e in ledger['events'] if e['kind']=='decision')
        proof={'offline_fixture_only':True,'synthetic_signal':True,'production_writes':0,'provider_requests':0,
               'remote_kind':'local bare Git repository','real_wall_clock':True,'result':result,
               'remote_head_sha':remote_sha,'publication_receipt':receipt,'decision':decision,
               'publication_delay_seconds':(ops.parse_utc(receipt['verified_utc'])-ops.parse_utc(decision['at'])).total_seconds(),
               'remote_ledger_identical':remote_ledger==ledger,'restart_preserved_events':case.ledger()['events']==original_events,
               'operational_publication_problems':analysis['operational_publication_problems'],
               'external_history_verified':analysis['external_history_verified'],'scientific_state':analysis['scientific_state']}
        if not proof['remote_ledger_identical'] or not proof['restart_preserved_events'] or proof['operational_publication_problems'] or not proof['external_history_verified']:
            raise RuntimeError('rehearsal integrity failure')
        with Path(output).open('x') as f:json.dump(proof,f,ensure_ascii=False,indent=2);f.write('\n')
        print(json.dumps({k:proof[k] for k in ('offline_fixture_only','publication_delay_seconds','remote_ledger_identical','restart_preserved_events','external_history_verified','scientific_state')}))
    finally:case.doCleanups()

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--output',required=True);a=ap.parse_args();run(a.output)
