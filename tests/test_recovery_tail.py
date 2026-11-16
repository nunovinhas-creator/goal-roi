"""Remote receive-pack, exact attempt authentication and full chronological replay."""
import copy
import json
import signal
import subprocess
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import test_recovery_inline as baseline
ROOT, DAY = baseline.ROOT, baseline.DAY
from scangoal import recovery_remote as remote
from scangoal import recovery_tail as tail
from scangoal.recovery_inline import InlineSlots
from scangoal.recovery_trial import MATCH_IDS, TrialBlocked
from scangoal.recovery_trial_journal import OfflineGitStore, REF
from scangoal.recovery_trial_transport import OfflineWire, WireResponse, PreparedBSD


class TailTests(unittest.TestCase):
    setUp = baseline.InlineTests.setUp
    reserve = baseline.InlineTests.reserve
    terminal = baseline.InlineTests.terminal

    def remote_store(self, name):
        cache = self.root/name
        subprocess.run(['git','init','--bare','--quiet',str(cache)],check=True)
        return remote.RemoteGitStore(cache, self.store.path.as_uri(), self.anchor)

    def protect_server(self):
        self.store.git('config','receive.denyNonFastForwards','true')
        self.store.git('config','receive.denyDeletes','true')

    def targets(self):
        mid = sorted(MATCH_IDS)[0]
        variant = dict(teams=['Home','Away'],kickoff_utc=DAY+'T12:00:00+00:00',
                       date_utc=DAY+'T11:00:00+00:00',league='Fixture league')
        return {mid:dict(variants=[variant],days=[DAY],invalid=False)}

    def body(self, conflict=False):
        ev = dict(id=1,status='finished',home_team='Home',away_team='Away',
                  home_score=2,away_score=1,event_date=DAY+'T12:00:00+00:00',league='Fixture league')
        events = [ev, dict(ev,id=2,home_score=3)] if conflict else [ev]
        return json.dumps(dict(results=events,count=len(events))).encode()

    def test_two_remote_runners_last_slot(self):
        config = dict(self.config,prior_debits=4)
        self.store,self.anchor = OfflineGitStore.bootstrap_fixture(self.root/'last.git',config)
        self.protect_server()
        from dataclasses import replace
        receipt = replace(self.receipt,slot_debits=4)
        stores = [self.remote_store('client'+str(i)) for i in range(2)]
        barrier = threading.Barrier(2)
        def runner(i):
            store = stores[i]; original = store.propose
            def propose(parent,event):
                candidate = original(parent,event);barrier.wait(timeout=10);return candidate
            store.propose = propose
            slots = InlineSlots(store,self.anchor,self.out,receipt,4)
            try:return slots.reserve('op'+str(i),'100:1',DAY,0,self.now)['send_permitted']
            except TrialBlocked:return False
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sum(pool.map(runner,range(2))),1)
        self.assertEqual(len(self.slots.load()[1]['attempts']),0) # original ledger unrelated
        state = InlineSlots(stores[0],self.anchor,self.out).load()[1]
        self.assertEqual(len(state['attempts'])+state['config']['prior_debits'],5)

    def test_remote_delete_and_reset_rejected(self):
        self.protect_server();store = self.remote_store('client')
        self.assertEqual(store.head(),self.anchor)
        candidate = store.propose(self.anchor,dict(kind='irrelevant-test-record'))
        self.assertTrue(store.cas(self.anchor,candidate))
        with self.assertRaises(TrialBlocked):store.transfer('push',self.store.path.as_uri(),':'+REF)
        with self.assertRaises(TrialBlocked):store.transfer('push',self.store.path.as_uri(),self.anchor+':'+REF)
        with self.assertRaises(TrialBlocked):store.propose(None,{})
        self.assertEqual(store.head(),candidate)

    def test_write_timeout_committed_is_confirmed_but_never_send(self):
        self.protect_server();store = self.remote_store('client');original = store.transfer
        def uncertain(*args):
            result = original(*args)
            if args[0]=='push':raise TrialBlocked('timeout')
            return result
        slots = InlineSlots(store,self.anchor,self.out,self.receipt,4)
        with patch.object(store,'transfer',uncertain),self.assertRaisesRegex(TrialBlocked,'confirmed_slot_burned'):
            slots.reserve('lost','100:1',DAY,0,self.now)
        self.assertEqual(slots.load()[1]['attempts']['lost']['outcome'],'reserved')
        self.assertFalse(slots.reserve('lost','100:1',DAY,0,self.now)['send_permitted'])

    def test_write_timeout_confirmation_unavailable_blocks(self):
        store = self.remote_store('client');original = store.transfer; failed = []
        def uncertain(*args):
            if args[0]=='push':failed.append(1);raise TrialBlocked('timeout')
            if failed:raise TrialBlocked('read_timeout')
            return original(*args)
        with patch.object(store,'transfer',uncertain),self.assertRaisesRegex(TrialBlocked,'confirmation_uncertain'):
            InlineSlots(store,self.anchor,self.out,self.receipt,4).reserve('lost','100:1',DAY,0,self.now)

    def test_remote_loss_never_uses_cached_head(self):
        store=self.remote_store('client');store.head()
        self.store.git('update-ref','-d',REF)
        with self.assertRaises(TrialBlocked):store.head()

    def test_response_before_git_completion_crash_burns_slot(self):
        wire=OfflineWire([WireResponse(200,self.body())])
        with patch.object(self.slots,'append',side_effect=TrialBlocked('disk failure')):
            with self.assertRaises(TrialBlocked):
                PreparedBSD(wire,self.slots,'100:1','fixture-token',self.out)('bsd',DAY,0,200)
        self.assertEqual(len(wire.calls),1)
        self.assertEqual(self.slots.load()[1]['attempts'][next(iter(self.slots.load()[1]['attempts']))]['outcome'],'reserved')
        self.assertFalse(tail.replay(self.slots,self.targets())['accepted'])
        with self.assertRaises(TrialBlocked):
            PreparedBSD(wire,self.slots,'100:1','fixture-token',self.out)('bsd',DAY,0,200)
        self.assertEqual(len(wire.calls),1)

    def test_full_nonempty_replay_new_process_preserves_chronology(self):
        PreparedBSD(OfflineWire([WireResponse(200,self.body())]),self.slots,'100:1','fixture-token',self.out)('bsd',DAY,0,200)
        before=tail.replay(self.slots,self.targets())
        self.assertEqual(next(iter(before['accepted'].values()))['ft'],[2,1])
        original_files={p.name:p.read_bytes() for p in self.out.iterdir()}
        targets_path=self.root/'targets.json';targets_path.write_text(json.dumps(self.targets()))
        for p in self.out.iterdir():p.unlink()
        script='''import json,sys
from scangoal.recovery_inline import InlineSlots
from scangoal.recovery_trial_journal import OfflineGitStore
from scangoal.recovery_tail import replay
s=InlineSlots(OfflineGitStore(sys.argv[1]),sys.argv[2],sys.argv[3])
replay(s,json.load(open(sys.argv[4])))
'''
        subprocess.run([sys.executable,'-c',script,str(self.store.path),self.anchor,str(self.out),str(targets_path)],cwd=ROOT,check=True)
        self.assertEqual(original_files,{p.name:p.read_bytes() for p in self.out.iterdir()})
        self.assertEqual(before,tail.replay(self.slots,self.targets()))
        self.assertEqual(len(self.slots.load()[1]['attempts']),1)

    def test_quarantine_consistent_on_replay(self):
        PreparedBSD(OfflineWire([WireResponse(200,self.body(True))]),self.slots,'100:1','fixture-token',self.out)('bsd',DAY,0,200)
        before=tail.replay(self.slots,self.targets())
        self.assertFalse(before['accepted']);self.assertTrue(before['conflicts'])
        self.assertEqual(before,tail.replay(self.slots,self.targets()))

    def authenticated(self, altered=None):
        proof=json.loads(self.terminal());proof.update(conclusion='success',repository={'full_name':remote.REPOSITORY},head_repository={'full_name':remote.REPOSITORY})
        proof.update(altered or {})
        class Response:
            status=200
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def geturl(self):return 'https://api.github.com/repos/'+remote.REPOSITORY+'/actions/runs/100/attempts/1'
            def read(self,n):return json.dumps(proof).encode()
        return Response()

    def test_authenticated_exact_attempt_abandon_no_refund(self):
        self.reserve()
        with patch.object(remote,'build_opener') as opener:
            opener.return_value.open.return_value=self.authenticated()
            remote.GitHubAttempts('fake-github-token').abandon(self.slots,'one')
            request=opener.return_value.open.call_args.args[0]
            self.assertTrue(request.full_url.endswith('/runs/100/attempts/1'))
        self.assertEqual(self.slots.load()[1]['attempts']['one']['outcome'],'abandoned')
        self.assertFalse(self.reserve()['send_permitted'])

    def test_forged_run_wrong_repo_attempt_or_active_rejected(self):
        self.reserve()
        for change in ({'run_attempt':2},{'status':'in_progress'},{'head_branch':'other'},
                       {'repository':{'full_name':'attacker/repo'}},{'path':'other.yml'}):
            with patch.object(remote,'build_opener') as opener:
                opener.return_value.open.return_value=self.authenticated(change)
                with self.assertRaises(TrialBlocked):remote.GitHubAttempts('fake-github-token').abandon(self.slots,'one')
        self.assertEqual(self.slots.load()[1]['attempts']['one']['outcome'],'reserved')

    def collector(self, name, patched=True):
        checkout=self.root/name;(checkout/'scangoal').mkdir(parents=True)
        (checkout/'scangoal/results_bsd.py').write_bytes((ROOT/'scangoal/results_bsd.py').read_bytes())
        if patched:
            subprocess.run(['git','apply','--unsafe-paths','--directory='+str(checkout),str(ROOT/'audits/bsd-inline-review/collector-tail-proposal.patch')],check=True)
        ns={'__package__':'scangoal','__name__':'scangoal.patch_test'}
        exec(compile((checkout/'scangoal/results_bsd.py').read_text(),'patch','exec'),ns)
        data=self.root/(name+'-data');data.mkdir();ns['data_dir']=lambda:data;ns['load_aliases']=lambda:{}
        ns['pending_targets']=lambda now:{'normal':{'days':[DAY],'teams':['Home','Away'],'kickoff_utc':DAY+'T12:00:00+00:00','date_utc':DAY+'T11:00:00+00:00'}}
        return ns,data

    def test_executor_patch_normal_first_failure_contained(self):
        ns,data=self.collector('patch')
        def normal(url,token,observe=None):
            if observe:observe(self.headers,b'{}',self.now)
            return {'results':[],'count':0}
        wire=OfflineWire([WireResponse(200,self.body())])
        executor=tail.PreparedTail(self.slots,'100:1',wire,'fixture-token',self.targets(),
            job_deadline=self.now+timedelta(seconds=800),downstream_bound=550)
        original=self.slots.reserve
        def reserve(*args):
            self.assertTrue((data/'results_bsd.json').is_file());return original(*args)
        with patch.object(self.slots,'reserve',reserve):
            result=executor.attach(ns['run'],normal,token='fixture-token',now=self.now)
        self.assertEqual(result['stats']['requests'],1);self.assertEqual(len(wire.calls),1)
        self.assertEqual(executor.outcome,'synthetic_page_staged')
        # Later trial failure does not change the normal run return value.
        with patch.object(self.slots,'load',side_effect=TrialBlocked('lost')):
            result=executor.attach(ns['run'],normal,token='fixture-token',now=self.now+timedelta(hours=1))
        self.assertEqual(result['stats']['requests'],1)

    def test_unproven_time_budget_skips_without_reservation(self):
        executor=tail.PreparedTail(self.slots,'100:1',OfflineWire([]),'fixture-token',self.targets())
        executor.tracker.observe(self.headers,b'{}',self.now);executor()
        self.assertEqual(self.slots.load()[1]['attempts'],{})

    def test_hosted_remote_requires_reviewed_anchor_and_token(self):
        cache=self.root/'hosted'
        with self.assertRaisesRegex(TrialBlocked,'anchor_not_provisioned'):
            remote.RemoteGitStore(cache,remote.GITHUB_REMOTE,self.anchor,token='fake-github-token')
        with patch.object(remote,'PROVISIONED_ANCHOR',self.anchor):
            with self.assertRaisesRegex(TrialBlocked,'git_auth_missing'):
                remote.RemoteGitStore(cache,remote.GITHUB_REMOTE,self.anchor)
            with self.assertRaisesRegex(TrialBlocked,'anchor_not_provisioned'):
                remote.RemoteGitStore(cache,remote.GITHUB_REMOTE,'b'*40,token='fake-github-token')

    def test_git_token_only_in_environment_never_argv(self):
        store=self.remote_store('client');store._token='fake-github-token'
        with patch.object(remote.subprocess,'check_output',return_value=b'') as run:
            store.transfer('fetch','--no-tags',store.remote,REF)
        argv,env=run.call_args.args[0],run.call_args.kwargs['env']
        self.assertNotIn('fake-github-token',' '.join(argv))
        self.assertNotIn('fake-github-token',env['GIT_CONFIG_VALUE_0'])  # base64 header only
        self.assertEqual(env['GIT_CONFIG_KEY_0'],'http.https://github.com/.extraheader')

    def test_tail_budget_clips_git_and_forbids_late_send(self):
        store=self.remote_store('client');store.not_after=self.now-timedelta(seconds=1)
        with patch.object(remote.subprocess,'check_output') as run:
            with self.assertRaisesRegex(TrialBlocked,'budget_exhausted'):store.head()
            run.assert_not_called()
        # Reservation confirmed after the last safe send instant: burned, never sent.
        wire=OfflineWire([WireResponse(200,self.body())])
        self.slots.send_not_after=self.now-timedelta(seconds=1)
        with self.assertRaisesRegex(TrialBlocked,'never_resent'):
            PreparedBSD(wire,self.slots,'100:1','fixture-token',self.out)('bsd',DAY,0,200)
        self.assertEqual(wire.calls,[])
        self.assertEqual(len(self.slots.load()[1]['attempts']),1)

    def test_margin_below_full_tail_budget_skips(self):
        # 690 s left passed the former +100 s rule but not the enforced 150 s tail.
        executor=tail.PreparedTail(self.slots,'100:1',OfflineWire([WireResponse(200,self.body())]),'fixture-token',
            self.targets(),job_deadline=self.now+timedelta(seconds=690),downstream_bound=550)
        executor.tracker.observe(self.headers,b'{}',self.now);executor()
        self.assertEqual(executor.outcome,'skipped');self.assertEqual(self.slots.load()[1]['attempts'],{})

    def test_unexplained_quota_drop_between_normal_replies_blocks(self):
        from scangoal.recovery_inline import NormalQuotaTracker
        def run(drops):
            tracker=NormalQuotaTracker('100:1',0);remaining=[120]
            def normal(url,token,observe):
                h=dict(self.headers,RateLimit=self.headers['RateLimit'].replace('r=105','r=%d'%remaining[0]))
                observe(h,b'{}',self.now);remaining[0]-=drops;return {}
            for _ in range(3):tracker.wrap(normal)('url','fake')
            return tracker.finish_normals()[0]
        self.assertEqual(run(1).remaining,118)
        self.assertIsNone(run(2))

    def test_job_deadline_from_real_job_start_only(self):
        self.assertIsNone(tail.job_deadline_from_env({}))
        self.assertIsNone(tail.job_deadline_from_env({'GOAL_ROI_JOB_STARTED_UTC':'not-a-time'}))
        old=(self.now-timedelta(seconds=901)).isoformat()
        self.assertIsNone(tail.job_deadline_from_env({'GOAL_ROI_JOB_STARTED_UTC':old}))
        start=self.now-timedelta(seconds=60)
        self.assertEqual(tail.job_deadline_from_env({'GOAL_ROI_JOB_STARTED_UTC':start.isoformat()}),
                         start+timedelta(seconds=870))

    def test_installed_patch_without_callback_is_behaviour_neutral(self):
        results=[]
        for patched in (False,True):
            ns,data=self.collector('neutral'+str(patched),patched)
            def normal(url,token):return json.loads(self.body())
            state=ns['run'](fetch=normal,token='fixture-token',now=self.now)
            results.append((state,(data/'results_bsd.json').read_bytes()))
        self.assertEqual(results[0],results[1])
        self.assertTrue(results[1][0]['results'])

    def watchdog_executor(self, wire):
        executor=tail.PreparedTail(self.slots,'100:1',wire,'fixture-token',self.targets(),
            job_deadline=self.now+timedelta(seconds=900),downstream_bound=550)
        executor.tracker.observe(self.headers,b'{}',self.now)
        return executor

    def assert_watchdog_cleared(self, handler):
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL),(0.0,0.0))
        self.assertIs(signal.getsignal(signal.SIGALRM),handler)

    def test_watchdog_bounds_blocked_work_before_reservation(self):
        handler=signal.getsignal(signal.SIGALRM)
        ns,data=self.collector('blocked')
        def normal(url,token,observe=None):
            if observe:observe(self.headers,b'{}',self.now)
            return {'results':[],'count':0}
        executor=tail.PreparedTail(self.slots,'100:1',OfflineWire([]),'fixture-token',self.targets(),
            job_deadline=datetime.now(timezone.utc)+timedelta(seconds=900),downstream_bound=550)
        started=time.monotonic()
        with patch.object(tail,'TAIL_BUDGET_S',1),patch.object(tail,'replay',lambda *a,**k:time.sleep(30)):
            result=executor.attach(ns['run'],normal,token='fixture-token',now=self.now)
        self.assertLess(time.monotonic()-started,5)
        self.assertEqual(executor.outcome,'trial_deadline_normal_preserved')
        self.assertEqual(result['stats']['requests'],1);self.assertTrue((data/'results_bsd.json').is_file())
        self.assertEqual(self.slots.load()[1]['attempts'],{})
        self.assert_watchdog_cleared(handler)

    def test_watchdog_bounds_blocked_send_reservation_burned_never_resent(self):
        handler=signal.getsignal(signal.SIGALRM)
        wire=OfflineWire([WireResponse(200,self.body())]);sent=[]
        def stuck(spec):sent.append(spec);time.sleep(30)
        wire.send=stuck
        executor=self.watchdog_executor(wire);started=time.monotonic()
        with patch.object(tail,'TAIL_BUDGET_S',1),patch.object(tail,'POST_SEND_RESERVE_S',0):
            executor()
        self.assertLess(time.monotonic()-started,5)
        self.assertEqual(executor.outcome,'trial_deadline_normal_preserved')
        ledger=self.slots.load()[1]
        self.assertEqual([a['outcome'] for a in ledger['attempts'].values()],['reserved'])
        self.assertIsNotNone(ledger['lease']);self.assertEqual(len(sent),1)
        self.assert_watchdog_cleared(handler)
        self.watchdog_executor(wire)();self.assertEqual(len(sent),1)

    def test_watchdog_unavailable_outside_main_thread_skips(self):
        executor=self.watchdog_executor(OfflineWire([WireResponse(200,self.body())]))
        worker=threading.Thread(target=executor);worker.start();worker.join(10)
        self.assertEqual(executor.outcome,'trial_failed_normal_preserved')
        self.assertEqual(self.slots.load()[1]['attempts'],{})

    def test_network_callback_cannot_be_injected(self):
        with self.assertRaises(TrialBlocked):tail.PreparedTail(self.slots,'100:1',lambda *a:None,'fixture-token',self.targets())
