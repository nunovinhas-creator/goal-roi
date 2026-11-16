"""Operational blockers: real local Git transactions + injected HTTP bytes, no API."""
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
from scangoal import recovery as engine
from scangoal import recovery_trial as trial
from scangoal.recovery_trial_journal import Journal,OfflineGitStore,record_capture,REF,remote_cas_plan
from scangoal.recovery_trial_transport import PreparedBSD,OfflineWire,WireResponse,network_bridge

ROOT=Path(__file__).resolve().parents[1]
DAY='2026-11-14'
NOW=datetime.now(timezone.utc)


def evidence(now=NOW,remaining=105,sequence=0):
    raw=b'OFFLINE EXAMPLE of a reviewed provider capture, not real account evidence'
    meta=dict(account_id='fixture-account',scope='football',origin='dashboard',
              source_uri='https://sports.bzzoiro.com/',observed_utc=now.isoformat(),
              reset_utc=(now+timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0).isoformat(),
              review_ref='offline-review',account_binding_ref='offline-account-review',remaining=remaining,sequence=sequence)
    return record_capture(raw,meta,now)


class Operations(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.now=datetime.now(timezone.utc)
        self.proof=evidence(self.now)
        self.config=dict(trial_id=trial.TRIAL_ID,cap=5,account_id='fixture-account',reserve=100,
                         consumers=['trial','collector'],coverage_review_ref='OFFLINE fixture only',
                         approved_evidence=[self.proof['evidence_sha256']])
        self.store,self.anchor=OfflineGitStore.bootstrap_fixture(self.base/'server.git',self.config)
        self.journal=Journal(self.store,self.anchor)
        self.journal.append(dict(kind='quota',evidence=self.proof,now=self.now.isoformat()))
    def reserve(self,op='request-1',owner='runner-1',now=None,**kw):
        return self.journal.reserve(op,owner,DAY,0,now or self.now,**kw)
    def finish(self,op='request-1',owner='runner-1',outcome='complete'):
        return self.journal.append(dict(kind='complete',operation_id=op,owner=owner,outcome=outcome,
            response_sha256='a'*64,observed_utc=datetime.now(timezone.utc).isoformat()))
    def adapter(self,responses,token='fixture-credential'):
        wire=OfflineWire(responses)
        return PreparedBSD(wire,self.journal,'adapter-runner',token,self.base/'prepared-fixture-trial')
    def test_same_request_two_competing_git_transactions(self):
        barrier=threading.Barrier(2)
        original=self.store.propose
        def proposed(parent,event):
            result=original(parent,event)
            if event['kind']=='reserve':barrier.wait(timeout=10)
            return result
        def claim(owner):
            try:return self.reserve(owner=owner)['send_permitted']
            except trial.TrialBlocked:return False
        with patch.object(self.store,'propose',proposed),ThreadPoolExecutor(2) as pool:
            outcomes=list(pool.map(claim,['runner-1','runner-2']))
        self.assertEqual(sum(outcomes),1)
        self.assertEqual(len(self.journal.load()[1]['attempts']),1)
    def test_runner_crash_after_debit_new_process_cannot_resend(self):
        self.assertTrue(self.reserve()['send_permitted'])
        script='''from scangoal.recovery_trial_journal import Journal,OfflineGitStore
from datetime import datetime
import sys
j=Journal(OfflineGitStore(sys.argv[1]),sys.argv[2])
r=j.reserve('request-1','runner-1','2026-11-14',0,datetime.fromisoformat(sys.argv[3]))
assert not r['send_permitted'] and len(j.load()[1]['attempts'])==1
'''
        subprocess.run([sys.executable,'-c',script,str(self.store.path),self.anchor,self.now.isoformat()],
                       cwd=ROOT,check=True,capture_output=True)
        with self.assertRaises(trial.TrialBlocked):self.reserve(op='request-2',owner='runner-2')
    def test_utc_next_day_does_not_reset_budget(self):
        for n in range(5):self.reserve(op=str(n));self.finish(op=str(n))
        with self.assertRaisesRegex(trial.TrialBlocked,'budget_exhausted'):
            self.reserve(op='six',now=self.now+timedelta(days=1))
        self.assertEqual(len(self.journal.load()[1]['attempts']),5)
    def test_missing_ref_never_bootstraps_or_refunds(self):
        self.reserve();self.store.git('update-ref','-d',REF)
        with self.assertRaises(trial.TrialBlocked):self.reserve(op='other')
    def test_corrupt_or_missing_object_blocks(self):
        head=self.store.head();p=self.store.path/'objects'/head[:2]/head[2:]
        p.chmod(0o600);p.write_bytes(b'corrupt')
        with self.assertRaises(trial.TrialBlocked):self.reserve()
    def test_wrong_anchor_blocks(self):
        with self.assertRaises(trial.TrialBlocked):Journal(self.store,'0'*40).load()
    def test_uncoordinated_consumer_invalidates_trial(self):
        self.journal.append(dict(kind='uncoordinated-consumer'))
        with self.assertRaisesRegex(trial.TrialBlocked,'coverage_unproven'):self.reserve()
    def test_no_inventory_approval_is_not_global_exclusion(self):
        config=dict(self.config,coverage_review_ref=None)
        store,anchor=OfflineGitStore.bootstrap_fixture(self.base/'unknown.git',config)
        with self.assertRaisesRegex(trial.TrialBlocked,'coverage_unproven'):
            Journal(store,anchor).reserve('a','b',DAY,0,self.now)
    def test_collector_active_blocks_trial(self):
        self.reserve(op='collector-request',owner='collector-runner',consumer='collector')
        with self.assertRaisesRegex(trial.TrialBlocked,'active_pending'):self.reserve()
    def test_collector_pending_intent_has_priority(self):
        self.journal.append(dict(kind='collector-intent',owner='pending-collector'))
        with self.assertRaisesRegex(trial.TrialBlocked,'active_pending'):self.reserve()
        receipt=self.reserve(op='collector-request',owner='pending-collector',consumer='collector')
        self.assertTrue(receipt['send_permitted'])
    def test_stale_quota_blocks(self):
        with self.assertRaisesRegex(trial.TrialBlocked,'stale'):self.reserve(now=self.now+timedelta(seconds=61))
    def test_known_intermediate_consumption_subtracted(self):
        self.reserve(op='collector-request',owner='collector',consumer='collector')
        self.finish(op='collector-request',owner='collector')
        with self.assertRaisesRegex(trial.TrialBlocked,'reserve_insufficient'):self.reserve()
        self.assertEqual(self.journal.load()[1]['debits'],1)
    def test_forged_manual_number_does_not_match_approved_envelope(self):
        p=copy.deepcopy(self.proof);p['remaining']=7500
        with self.assertRaisesRegex(trial.TrialBlocked,'not_reviewed'):
            self.journal.append(dict(kind='quota',evidence=p,now=self.now.isoformat()))
    def test_evidence_account_sequence_and_reset_bound(self):
        for field,value in [('account_id','another'),('sequence',99),('reset_utc',self.now.isoformat()),('origin','manual-number')]:
            p=copy.deepcopy(self.proof);p[field]=value
            with self.subTest(field=field),self.assertRaises(trial.TrialBlocked):
                self.journal.append(dict(kind='quota',evidence=p,now=self.now.isoformat()))
    def test_no_key_no_reservation_no_wire_calls(self):
        a=self.adapter([],token='')
        with self.assertRaisesRegex(trial.TrialBlocked,'missing_bsd_key'):a('bsd',DAY,0,200)
        self.assertEqual(a.wire.calls,[]);self.assertEqual(self.journal.load()[1]['debits'],0)
    def test_wire_429_halts_no_retry_no_secret_leak(self):
        a=self.adapter([WireResponse(429,b'{"error":"rate limited"}')])
        with self.assertRaisesRegex(trial.TrialBlocked,'http_429'):a('bsd',DAY,0,200)
        with self.assertRaises(trial.TrialBlocked):a('bsd',DAY,200,200)
        self.assertEqual(len(a.wire.calls),1)
        self.assertTrue(self.journal.load()[1]['halted'])
        self.assertNotIn('fixture-credential',repr(a.wire.calls[0]))
        for p in (self.base/'prepared-fixture-trial').iterdir():
            self.assertNotIn(b'fixture-credential',p.read_bytes())
    def test_invalid_raw_response_halts(self):
        a=self.adapter([WireResponse(200,b'{"results": "wrong", "count": 1}')])
        with self.assertRaisesRegex(trial.TrialBlocked,'invalid_response'):a('bsd',DAY,0,200)
        self.assertTrue(self.journal.load()[1]['halted']);self.assertEqual(len(a.wire.calls),1)
    def test_sixth_global_attempt_blocked(self):
        for n in range(5):self.reserve(op=str(n));self.finish(op=str(n))
        with self.assertRaisesRegex(trial.TrialBlocked,'budget_exhausted'):self.reserve(op='six')
    def test_unauthorized_date_source_or_pagination_zero_wire(self):
        a=self.adapter([])
        for args in [('bsd','2026-11-12',0,200),('hl',DAY,0,200),('bsd',DAY,-1,200),('bsd',DAY,0,100)]:
            with self.subTest(args=args),self.assertRaises(trial.TrialBlocked):a(*args)
        self.assertEqual(a.wire.calls,[]);self.assertEqual(self.journal.load()[1]['debits'],0)
    def test_output_production_rejected_before_reservation(self):
        with self.assertRaises(trial.TrialBlocked):
            PreparedBSD(OfflineWire([]),self.journal,'a','fake',ROOT/'data'/'prepared-fixture-trial')
        self.assertEqual(self.journal.load()[1]['debits'],0)
    def test_network_bridge_and_arbitrary_injection_forbidden(self):
        with self.assertRaises(trial.TrialBlocked):network_bridge()
        calls=[]
        with self.assertRaises(trial.TrialBlocked):PreparedBSD(lambda *a:calls.append(a),self.journal,'a','fake',self.base)
        a=self.adapter([]);a.wire=lambda *a:calls.append(a)
        with self.assertRaises(trial.TrialBlocked):a('bsd',DAY,0,200)
        self.assertEqual(calls,[]);self.assertEqual(self.journal.load()[1]['debits'],0)
    def test_workflow_secretless_no_collector_group_actions(self):
        w=json.loads((ROOT/'.github/workflows/recovery-trial.yml').read_text());text=json.dumps(w)
        self.assertNotIn('secrets.',text);self.assertNotIn('scangoal-write',text)
        self.assertNotIn('concurrency',w);self.assertEqual(w['permissions'],{'contents':'read'})
        self.assertEqual(set(w['on']),{'workflow_dispatch'})
        self.assertNotIn('gh workflow',text)
    def test_response_wire_bytes_hash_observation_and_no_redirects(self):
        body=b'{ "count": 0, "results": [] }\n'
        a=self.adapter([WireResponse(200,body,{'RateLimit':'"football";r=104;t=60','Authorization':'never-save'})])
        before=datetime.now(timezone.utc);self.assertEqual(a('bsd',DAY,0,200),{'count':0,'results':[]})
        files=list((self.base/'prepared-fixture-trial').glob('*.json'));meta=json.loads(files[0].read_text())
        self.assertGreaterEqual(datetime.fromisoformat(meta['observed_utc']),before)
        self.assertEqual(meta['response_sha256'],hashlib.sha256(body).hexdigest())
        self.assertEqual(files[0].with_suffix('.bin').read_bytes(),body)
        self.assertNotIn('Authorization',meta['quota_headers'])
        spec=a.wire.calls[0];self.assertFalse(spec.follow_redirects);self.assertEqual(spec.retries,0)
        self.assertIn('status=finished',spec.url);self.assertIn('offset=0',spec.url)
    def test_http_redirect_never_followed(self):
        a=self.adapter([WireResponse(302,b'{}',{'Location':'https://elsewhere.invalid/'})])
        with self.assertRaises(trial.TrialBlocked):a('bsd',DAY,0,200)
        self.assertEqual(len(a.wire.calls),1);self.assertTrue(self.journal.load()[1]['halted'])
    def test_reflected_credential_never_written(self):
        a=self.adapter([WireResponse(200,b'{"token":"fixture-credential"}')])
        with self.assertRaisesRegex(trial.TrialBlocked,'unsafe_response'):a('bsd',DAY,0,200)
        self.assertFalse(a.output.exists());self.assertIsNotNone(self.journal.load()[1]['lease'])
    def test_response_loss_on_new_runner_blocks_before_new_debit(self):
        a=self.adapter([WireResponse(200,b'{"count":0,"results":[]}')]);a('bsd',DAY,0,200)
        for p in a.output.glob('*.bin'):p.unlink()
        with self.assertRaisesRegex(trial.TrialBlocked,'evidence_lost'):a('bsd',DAY,200,200)
        self.assertEqual(len(a.wire.calls),1);self.assertEqual(self.journal.load()[1]['debits'],1)
    def test_adapter_reuses_engine_identity_and_quarantine(self):
        # Prepared transport returns native BSD dictionaries to the unchanged engine.
        mid='horsham-vs-torquay-united-20261114'
        target={'variants':[dict(teams=['Horsham','Torquay United'],league='Fixture league',
                 date_utc=DAY+'T14:00:00+00:00',kickoff_utc=DAY+'T15:00:00+00:00')], 'days':[DAY],'invalid':False}
        ev=dict(id=9,status='finished',home_team='Horsham',away_team='Torquay United',league='Fixture league',
                event_date=DAY+'T15:00:00+00:00',home_score=2,away_score=1)
        a=self.adapter([WireResponse(200,json.dumps(dict(count=1,results=[ev])).encode())])
        state=engine.run_batch(engine.initial_state(),{mid:target},a,self.now,daily={'bsd':5,'hl':0},
            available={'bsd':105,'hl':0},max_requests=1)
        self.assertEqual(state['accepted'][mid]['ft'],[2,1])
        self.assertGreaterEqual(datetime.fromisoformat(state['accepted'][mid]['matched_at_utc']),self.now.replace(microsecond=0))

    def test_remote_cas_descriptor_exact_expected_oid_no_force(self):
        plan=remote_cas_plan('fixture-repo-id','a'*40,'b'*40)
        update=plan['variables']['input']['refUpdates'][0]
        self.assertEqual(update,dict(name=REF,beforeOid='a'*40,afterOid='b'*40,force=False))
        self.assertNotIn('token',json.dumps(plan))
    def test_snapshot_checkout_revision_and_existing_state_binding(self):
        # Data-only checkout; no import or execution from source revision.
        source=self.base/'source';subprocess.run(['git','init','--quiet',str(source)],check=True)
        (source/'data').mkdir();(source/'data'/'inputs.json').write_text('{}')
        subprocess.run(['git','-C',str(source),'add','data'],check=True)
        subprocess.run(['git','-C',str(source),'-c','user.name=Offline','-c','user.email=offline@example.invalid',
                        'commit','--quiet','-m','fixture snapshot'],check=True)
        sha=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
        out=self.base/'snapshot-staging'
        with patch.object(trial,'verify_engine'),patch.object(trial,'snapshot',return_value=([],{},{})), \
             patch.object(engine,'load_aliases',return_value={}):
            value=trial.prepare(ROOT,out,self.now,snapshot_checkout=source,snapshot_sha=sha)
            self.assertEqual(value['source_revision'],sha)
            self.assertEqual(trial.manifest(source/'data'),trial.manifest(out/'snapshot'))
            with self.assertRaises(trial.TrialBlocked):trial.prepare(ROOT,out,self.now,snapshot_checkout=source,snapshot_sha='0'*40)
            (source/'data'/'inputs.json').write_text('changed')
            with self.assertRaises(trial.TrialBlocked):trial.prepare(ROOT,out,self.now,snapshot_checkout=source,snapshot_sha=sha)
    def test_preflight_never_asks_for_key_and_remains_blocked(self):
        with patch.object(trial,'prepare',return_value={'targets':{},'excluded_ids':[]}), \
             patch.object(trial,'verify_engine'),patch.object(trial,'manifest',return_value={}), \
             patch.dict(os.environ,{},clear=True),patch.object(sys,'argv',['trial','--output',str(self.base/'preflight')]):
            self.assertEqual(trial.main(),2)
        report=json.loads((self.base/'preflight'/'preflight.json').read_text())
        self.assertIn('bsd_key_check_deferred_to_authorized_execution',report['blockers'])
        self.assertNotIn('missing_bsd_key',report['blockers']);self.assertEqual(report['bsd_requests'],0)

    def test_adapter_initial_offset_and_repeated_page_fail_closed(self):
        a=self.adapter([])
        with self.assertRaisesRegex(trial.TrialBlocked,'start_at_zero'):a('bsd',DAY,200,200)
        self.assertEqual(self.journal.load()[1]['debits'],0)
        rows=[dict(id=n) for n in range(200)]
        a=self.adapter([WireResponse(200,json.dumps(dict(count=201,results=rows)).encode()),
                        WireResponse(200,json.dumps(dict(count=201,results=[dict(id=0)])).encode())])
        a('bsd',DAY,0,200)
        with self.assertRaisesRegex(trial.TrialBlocked,'invalid_response'):a('bsd',DAY,200,200)
        self.assertTrue(self.journal.load()[1]['halted']);self.assertEqual(len(a.wire.calls),2)

    def test_json_escaped_credential_never_persisted(self):
        body=json.dumps({'reflected': 'fixture-credential'}).replace('f',r'\u0066').encode()
        a=self.adapter([WireResponse(200,body)])
        with self.assertRaisesRegex(trial.TrialBlocked,'unsafe_response'):a('bsd',DAY,0,200)
        self.assertFalse(a.output.exists());self.assertEqual(self.journal.load()[1]['debits'],1)

if __name__=='__main__':unittest.main()
