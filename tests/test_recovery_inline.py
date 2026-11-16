"""Collector-tail specific safety checks. No live HTTP or deployed refs."""
import json
import tempfile
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from scangoal import recovery_inline as inline
from scangoal.recovery_trial import TrialBlocked,TRIAL_ID
from scangoal.recovery_trial_journal import OfflineGitStore,REF
from scangoal.recovery_trial_transport import PreparedBSD,OfflineWire,WireResponse

ROOT=Path(__file__).resolve().parents[1]
DAY='2026-11-14'

class InlineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.now=datetime.now(timezone.utc)
        reset=(self.now+timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0)
        self.headers={'RateLimit-Policy':'"football";q=7500;w=86400',
                      'RateLimit':'"football";r=105;t='+str(int((reset-self.now).total_seconds()))}
        self.receipt=inline.QuotaReceipt.from_normal_response(self.headers,b'{}','100:1',4,self.now,0)
        self.config=dict(mode='inline-five-slots',trial_id=TRIAL_ID,cap=5,snapshot_sha='a'*40,reserve=100,
            exclusive_account_review_ref='OFFLINE fixture inventory',prior_debits=0,
            predecessor_budget_review_ref='OFFLINE fixture predecessor review')
        self.store,self.anchor=OfflineGitStore.bootstrap_fixture(self.root/'server.git',self.config)
        self.out=self.root/'prepared-fixture-trial'
        self.slots=inline.InlineSlots(self.store,self.anchor,self.out,self.receipt,4)
    def reserve(self,op='one',owner='100:1',day=DAY,offset=0):
        return self.slots.reserve(op,owner,day,offset,self.now)
    def terminal(self,run_id=100,attempt=1,status='completed'):
        return json.dumps(dict(id=run_id,run_attempt=attempt,status=status,
            path='.github/workflows/collect.yml',head_branch='main')).encode()
    def test_quota_headers_only_free_scope_and_integer_subset(self):
        self.assertEqual(self.receipt.remaining,105)
        for value in ('"tennis";r=105;t=1','"football";r=105;r=999;t=1',
                      '"football";r=105.5;t=1','"football";r=105;t=1, "tennis";r=9'):
            with self.subTest(value=value),self.assertRaises(TrialBlocked):
                inline.QuotaReceipt.from_normal_response(dict(self.headers,RateLimit=value),b'{}','100:1',4,self.now,0)
    def test_missing_headers_never_probe_or_reserve(self):
        with self.assertRaises(TrialBlocked):inline.QuotaReceipt.from_normal_response({},b'{}','100:1',4,self.now,0)
        self.assertEqual(self.slots.load()[1]['attempts'],{})
    def test_stale_or_interleaved_normal_request_blocks(self):
        self.slots.normal_sequence=5
        with self.assertRaises(TrialBlocked):self.reserve()
        self.slots.normal_sequence=4
        with self.assertRaises(TrialBlocked):self.slots.reserve('x','100:1',DAY,0,self.now+timedelta(seconds=61))
    def test_104_remaining_blocks_initial_budget(self):
        from dataclasses import replace
        self.slots.receipt=replace(self.receipt,remaining=104)
        with self.assertRaises(TrialBlocked):self.reserve()
    def test_two_atomic_claims_one_winner(self):
        barrier=threading.Barrier(2);original=self.store.propose
        def propose(parent,event):
            result=original(parent,event);barrier.wait(timeout=10);return result
        def attempt(op):
            try:return self.reserve(op)['send_permitted']
            except TrialBlocked:return False
        with patch.object(self.store,'propose',propose),ThreadPoolExecutor(2) as executor:
            self.assertEqual(sum(executor.map(attempt,['one','two'])),1)
        self.assertEqual(len(self.slots.load()[1]['attempts']),1)
    def test_abandoned_slot_burned_no_retry_next_run_can_use_other_day(self):
        self.reserve();self.slots.abandon('one',self.terminal())
        self.assertFalse(self.reserve()['send_permitted'])
        self.assertEqual(self.slots.load()[1]['attempts']['one']['outcome'],'abandoned')
        from dataclasses import replace
        self.slots.receipt=replace(self.receipt,run_owner='101:1',slot_debits=1,remaining=104)
        self.assertTrue(self.reserve('two','101:1','2026-11-15')['send_permitted'])
        self.assertEqual(len(self.slots.load()[1]['attempts']),2)
    def test_running_or_wrong_attempt_cannot_retire(self):
        self.reserve()
        for proof in (self.terminal(status='in_progress'),self.terminal(attempt=2),self.terminal(run_id=999)):
            with self.assertRaises(TrialBlocked):self.slots.abandon('one',proof)
        self.assertIsNotNone(self.slots.load()[1]['lease'])
    def test_sixth_after_crashes_and_utc_day_change_blocked(self):
        from dataclasses import replace
        for n in range(5):
            owner=str(100+n)+':1';self.slots.receipt=replace(self.receipt,run_owner=owner,slot_debits=n,remaining=105-n)
            self.reserve(str(n),owner,DAY,n*200)
            self.slots.abandon(str(n),self.terminal(100+n))
        self.slots.receipt=replace(self.receipt,run_owner='106:1',slot_debits=5,remaining=100,
            received_utc=self.now+timedelta(days=1))
        with self.assertRaises(TrialBlocked):self.slots.reserve('six','106:1',DAY,1000,self.now+timedelta(days=1))
        self.assertEqual(len(self.slots.load()[1]['attempts']),5)
    def test_no_timeout_based_lease_steal(self):
        self.reserve()
        from dataclasses import replace
        later=self.now+timedelta(hours=1)
        self.slots.receipt=replace(self.receipt,received_utc=later,run_owner='101:1')
        with self.assertRaises(TrialBlocked):self.slots.reserve('other','101:1','2026-11-15',0,later)
    def test_no_externally_unproven_inventory(self):
        config=dict(self.config,exclusive_account_review_ref=None)
        store,anchor=OfflineGitStore.bootstrap_fixture(self.root/'other.git',config)
        s=inline.InlineSlots(store,anchor,self.out,self.receipt,4)
        with self.assertRaisesRegex(TrialBlocked,'external_consumers'):s.reserve('x','100:1',DAY,0,self.now)
    def test_one_page_per_existing_run(self):
        self.reserve();self.slots.abandon('one',self.terminal())
        with self.assertRaises(TrialBlocked):self.reserve('two',day='2026-11-15')
    def test_same_query_never_resent_after_unknown_send(self):
        self.reserve();self.slots.abandon('one',self.terminal())
        from dataclasses import replace
        self.slots.receipt=replace(self.receipt,run_owner='101:1',slot_debits=1,remaining=104)
        with self.assertRaises(TrialBlocked):self.reserve('different-id','101:1')
    def test_actual_wire_adapter_reused_and_durable_evidence_restored(self):
        body=b'{ "results": [], "count": 0 }\n'
        adapter=PreparedBSD(OfflineWire([WireResponse(200,body)]),self.slots,'100:1','fixture-token',self.out)
        self.assertEqual(adapter('bsd',DAY,0,200),{'results':[],'count':0})
        state=self.slots.load()[1];self.assertEqual(len(state['attempts']),1)
        for p in self.out.iterdir():p.unlink()
        script="from scangoal.recovery_inline import InlineSlots; from scangoal.recovery_trial_journal import OfflineGitStore; import sys; InlineSlots(OfflineGitStore(sys.argv[1]),sys.argv[2],sys.argv[3]).restore_evidence()"
        subprocess.run([sys.executable,'-c',script,str(self.store.path),self.anchor,str(self.out)],
                       cwd=ROOT,check=True,capture_output=True)
        self.assertEqual(next(self.out.glob('*.bin')).read_bytes(),body)
    def test_429_halts_only_trial(self):
        adapter=PreparedBSD(OfflineWire([WireResponse(429,b'{}')]),self.slots,'100:1','fixture-token',self.out)
        with self.assertRaises(TrialBlocked):adapter('bsd',DAY,0,200)
        self.assertTrue(self.slots.load()[1]['halted'])
    def test_store_loss_blocks_no_reinitialize(self):
        self.reserve();self.store.git('update-ref','-d',REF)
        with self.assertRaises(TrialBlocked):self.slots.load()
    def test_prior_debits_carried_not_five_extra_requests(self):
        config=dict(self.config,prior_debits=5)
        store,anchor=OfflineGitStore.bootstrap_fixture(self.root/'spent.git',config)
        from dataclasses import replace
        s=inline.InlineSlots(store,anchor,self.out,replace(self.receipt,slot_debits=5),4)
        with self.assertRaises(TrialBlocked):s.reserve('six','100:1',DAY,0,self.now)
    def test_patch_runs_tail_after_normal_save_and_contains_failure(self):
        patch_path=ROOT/'audits/bsd-inline-review/collector-tail-proposal.patch'
        checkout=self.root/'proposal';(checkout/'scangoal').mkdir(parents=True)
        (checkout/'scangoal/results_bsd.py').write_bytes((ROOT/'scangoal/results_bsd.py').read_bytes())
        subprocess.run(['git','apply','--unsafe-paths','--directory='+str(checkout),str(patch_path)],check=True)
        text=(checkout/'scangoal/results_bsd.py').read_text();namespace={'__package__':'scangoal','__name__':'scangoal.inline_patch_test'}
        exec(compile(text,'inline-patch','exec'),namespace)
        events=[];normal_data=self.root/'normal-data';normal_data.mkdir()
        namespace['data_dir']=lambda:normal_data
        namespace['load_aliases']=lambda:{}
        namespace['pending_targets']=lambda now:{'x':{'days':[DAY],'teams':['A','B'],'kickoff_utc':DAY+'T12:00:00+00:00','date_utc':DAY+'T11:00:00+00:00'}}
        def normal(*args):events.append('normal');return {'results':[],'count':0,'next':None}
        def tail():
            self.assertTrue((normal_data/'results_bsd.json').exists());events.append('tail');raise RuntimeError('never log secret')
        result=namespace['run'](fetch=normal,now=self.now,token='fake',after_bsd=tail)
        self.assertEqual(events,['normal','tail']);self.assertEqual(result['stats']['requests'],1)
    def test_no_new_workflow_no_modified_collector(self):
        original=subprocess.check_output(['git','show','247deee40e5e6ebdb107808b66adb96622074238:.github/workflows/collect.yml'],cwd=ROOT)
        self.assertEqual(original,(ROOT/'.github/workflows/collect.yml').read_bytes())
        self.assertNotIn('gh workflow run',(ROOT/'scangoal/recovery_inline.py').read_text())
        self.assertNotIn('urlopen',(ROOT/'scangoal/recovery_inline.py').read_text())

    def test_last_normal_response_only_no_fallback_after_failed_request(self):
        tracker=inline.NormalQuotaTracker('100:1',0)
        def good(url,token,observe):
            observe(self.headers,b'{}',self.now);return {}
        tracker.wrap(good)('url','fake')
        self.assertEqual(tracker.receipt.remaining,105)
        def failed(*args,**kw):raise RuntimeError('normal unavailable')
        with self.assertRaises(RuntimeError):tracker.wrap(failed)('url','fake')
        receipt,seq=tracker.finish_normals();self.assertIsNone(receipt);self.assertEqual(seq,2)
        with self.assertRaises(TrialBlocked):tracker.wrap(good)('url','fake')
    def test_unsupported_quota_cannot_interrupt_normal_collection(self):
        tracker=inline.NormalQuotaTracker('100:1',0)
        def normal(url,token,observe):observe({},b'{}',self.now);return {'normal':'ok'}
        self.assertEqual(tracker.wrap(normal)('url','fake'),{'normal':'ok'})
        self.assertIsNone(tracker.finish_normals()[0])

if __name__=='__main__':unittest.main()
