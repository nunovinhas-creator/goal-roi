import copy,json,subprocess,tempfile,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
from scangoal import shadow,shadow_ops as ops

ROOT=Path(__file__).resolve().parents[1]
T=datetime(2026,11,18,12,tzinfo=timezone.utc)
OPEN="""🔥 Open game — heading for over
Friendly · 60'
Alpha 2
Beta 0
Rhythm 87/100 · Over 100%
💰 O 2.25: 2.00
https://scangoal.com/match/alpha-vs-beta-20261118?lang=en"""

class OperationalShadow(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.source=self.root/'source';self.source.mkdir();ops.git(self.source,'init','-b','main')
        for path in ops.CODE:
            target=self.source/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((ROOT/path).read_bytes())
        (self.source/'seed').write_text('fixture only')
        ops.git(self.source,'add','.');ops.git(self.source,'-c','user.name=fixture','-c','user.email=fixture@example.test','commit','-m','fixture source')
        self.approved=ops.git(self.source,'rev-parse','HEAD')
        self.data=self.source/'data';self.data.mkdir();(self.data/'messages.jsonl').write_text('')
        for file in ('results_hl.json','results_bsd.json'):(self.data/file).write_text('{"results":{}}')
        self.remote=self.root/'remote.git';self.remote.mkdir();ops.git(self.remote,'init','--bare')
        self.repo=self.root/'publisher';ops.git(self.root,'clone',str(self.remote),str(self.repo))
        ops.git(self.repo,'checkout','--orphan','shadow/h1-h2-operational');(self.repo/'seed').write_text('fixture only')
        ops.git(self.repo,'add','seed');ops.git(self.repo,'-c','user.name=fixture','-c','user.email=fixture@example.test','commit','-m','fixture publisher')
        self.time=T
        self.pub=ops.Publisher(self.repo,clock=lambda:self.time)
        self.auth=self.root/'authorization.json';self.auth.write_text(json.dumps({'authorized':True,'installed':True,
            'authorization_reference':'OFFLINE_FIXTURE_ONLY','installed_runner_reference':'local test process','approved_code_sha':self.approved,'authorized_utc':(T-timedelta(minutes=1)).isoformat()}))
        self.historical=ROOT/'audits/open-game-shadow/protocol.json'
        self.ident=ops.activate(self.pub,self.source,self.auth,self.historical)
    def message(self,age=30,i=1,text=OPEN):
        row={'message_id':i,'date_utc':(self.time-timedelta(seconds=age)).isoformat(),'collected_at_utc':self.time.isoformat(),'edit_date_utc':None,'text':text}
        with (self.data/'messages.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
    def step(self,seconds=60):self.time+=timedelta(seconds=seconds)
    def ledger(self):return ops.read(self.pub.state/'ledgers'/f'{self.ident}.json')
    def test_actual_remote_receipt_and_restart_no_duplicates(self):
        self.step();self.message();r=ops.tick(self.pub,self.source,self.auth)
        self.assertEqual(r['state'],'published');self.assertEqual(r['observations'],1)
        remote=ops.git(self.remote,'rev-parse','refs/heads/shadow/h1-h2-operational')
        self.assertEqual(remote,ops.git(self.repo,'rev-parse','HEAD'))
        receipt=ops.read(self.pub.state/'receipts.json')[0]
        self.assertEqual(receipt['commit_sha'],receipt['verified_remote_sha']);self.assertTrue(receipt['timely'])
        events=copy.deepcopy(self.ledger()['events'])
        self.pub=ops.Publisher(self.repo,clock=lambda:self.time);ops.tick(self.pub,self.source,self.auth)
        self.assertEqual(self.ledger()['events'],events)
    def test_outside_120s_and_unmonitored_interval_never_backfill(self):
        self.step(300);self.message(age=121)
        self.message(age=500,i=2)
        r=ops.tick(self.pub,self.source,self.auth);self.assertEqual(r['observations'],0)
        self.assertEqual([e['reason'] for e in self.ledger()['events'] if e['kind']=='missed'],['outside_120s'])
    def test_push_failure_preserved_and_late_retry_quarantined(self):
        self.step();self.message()
        original=self.pub.publish;calls=[0]
        def fail():
            calls[0]+=1
            if calls[0]==2:raise ops.OpsError('simulated push outage')
            return original()
        with patch.object(self.pub,'publish',fail):
            with self.assertRaises(ops.OpsError):ops.tick(self.pub,self.source,self.auth)
        first=copy.deepcopy(self.ledger()['events']);self.assertTrue(first)
        self.step(301);r=ops.tick(self.pub,self.source,self.auth)
        self.assertEqual(r['state'],'quarantined');self.assertTrue(r['problems'])
        self.assertEqual(self.ledger()['events'][:len(first)],first)
        self.assertFalse(ops.read(self.pub.state/'receipts.json')[0]['timely'])
    def test_result_known_before_push_invalidates_even_a_timely_receipt(self):
        self.step();self.message();ops.tick(self.pub,self.source,self.auth)
        receipts=ops.read(self.pub.state/'receipts.json');ledger=self.ledger();p=ops.read(self.pub.state/'protocols'/f'{self.ident}.json')
        shadow.append(ledger,{'kind':'knowledge','at':self.time.isoformat(),'match_id':OPEN.split('/match/')[1].split('?')[0],
                              'observed_utc':(self.time-timedelta(seconds=1)).isoformat(),'unknown_timestamp':False})
        self.assertTrue(any('after result' in x for x in ops.check_receipts(p,ledger,receipts)))
    def test_result_known_at_capture_and_correction_append_only(self):
        self.step();self.message(text=OPEN+'\n\n🏁 FT 3:0 · Asian Total → Over 2.25 ✅')
        self.assertEqual(ops.tick(self.pub,self.source,self.auth)['observations'],0)
    def test_lock_excludes_concurrent_processes(self):
        other=ops.Publisher(self.repo,clock=lambda:self.time)
        with self.pub.locked():
            with self.assertRaises(ops.OpsError):
                with other.locked():pass
    def test_ref_conflict_does_not_force_push_or_rewrite(self):
        other=self.root/'other';ops.git(self.root,'clone','--branch','shadow/h1-h2-operational',str(self.remote),str(other))
        (other/'other.txt').write_text('concurrent writer');ops.git(other,'add','other.txt');ops.git(other,'-c','user.name=fixture','-c','user.email=fixture@example.test','commit','-m','other writer')
        ops.git(other,'push','origin','HEAD:shadow/h1-h2-operational')
        original=ops.git(self.remote,'rev-parse','refs/heads/shadow/h1-h2-operational')
        self.step();self.message()
        with self.assertRaises(ops.OpsError):ops.tick(self.pub,self.source,self.auth)
        self.assertEqual(original,ops.git(self.remote,'rev-parse','refs/heads/shadow/h1-h2-operational'))
        self.assertEqual(self.ledger()['events'],[])
    def test_corruption_stops_before_publication(self):
        path=self.pub.state/'ledgers'/f'{self.ident}.json';l=ops.read(path);l['head']='bad';ops.write(path,l)
        with self.assertRaises(ValueError):ops.tick(self.pub,self.source,self.auth)
    def test_code_change_or_missing_authorization_blocks_activation(self):
        p=self.source/'scangoal/shadow_ops.py';p.write_text(p.read_text()+'\n# unapproved change\n')
        with self.assertRaises(ops.OpsError):ops.tick(self.pub,self.source,self.auth)
    def test_production_branch_forbidden_and_protocol_immutable(self):
        with self.assertRaises(ops.OpsError):ops.Publisher(self.repo,branch='main')
        before=self.historical.read_bytes()
        with self.assertRaises(ops.OpsError):ops.activate(self.pub,self.source,self.auth,self.historical)
        self.assertEqual(before,self.historical.read_bytes())
        p=ops.read(self.pub.state/'protocols'/f'{self.ident}.json');old=json.loads(before)
        for k in ('rules','min_games','alpha_each_direction','lambda','tests','sequential','paired_estimand','failure'):
            self.assertEqual(p[k],old[k])
    def test_exact_120s_capture_and_publication_after_299s(self):
        self.step(180);self.message(age=120)
        original=self.pub.publish;calls=[0]
        def delayed():
            calls[0]+=1
            if calls[0]==2:self.step(299)
            return original()
        with patch.object(self.pub,'publish',delayed):r=ops.tick(self.pub,self.source,self.auth)
        self.assertEqual(r['observations'],1);self.assertFalse(r['problems'])
        self.assertTrue(ops.read(self.pub.state/'receipts.json')[0]['timely'])
    def test_restart_after_fsync_before_commit_loses_no_decision(self):
        self.step();self.message();original=self.pub.commit;calls=[0]
        def crash(message):
            calls[0]+=1
            if calls[0]==2:raise ops.OpsError('crash after durable ledger write')
            return original(message)
        with patch.object(self.pub,'commit',crash):
            with self.assertRaises(ops.OpsError):ops.tick(self.pub,self.source,self.auth)
        events=copy.deepcopy(self.ledger()['events']);self.step(30)
        self.pub=ops.Publisher(self.repo,clock=lambda:self.time);r=ops.tick(self.pub,self.source,self.auth)
        self.assertFalse(r['problems']);self.assertEqual(events,self.ledger()['events'])
    def test_corrections_are_appended_and_first_decision_unchanged(self):
        self.step();self.message();ops.tick(self.pub,self.source,self.auth)
        first=copy.deepcopy(self.ledger()['events'][0]);self.step(45 * 60)
        self.message(i=1,text=OPEN+'\n\n🏁 FT 3:0 · Asian Total → Over 2.25 ✅');ops.tick(self.pub,self.source,self.auth)
        self.step();self.message(i=1,text=OPEN+'\n\n🏁 FT 4:0 · Asian Total → Over 2.25 ✅');ops.tick(self.pub,self.source,self.auth)
        events=self.ledger()['events'];self.assertEqual(first,events[0])
        results=[e for e in events if e['kind']=='result'];self.assertEqual([r['ft'] for r in results],[[3,0],[4,0]])
        self.assertTrue(results[-1]['correction'])
    def test_different_h1_h2_entries_preserve_prices(self):
        self.step();self.message(text=OPEN.replace("60'","20'"));ops.tick(self.pub,self.source,self.auth)
        self.step();self.message(i=2,text=OPEN.replace('O 2.25: 2.00','O 3.25: 2.50'));ops.tick(self.pub,self.source,self.auth)
        selected=shadow.firsts(self.ledger());mid=next(iter(selected['H2']))
        self.assertEqual(selected['H2'][mid]['bet']['message_id'],1);self.assertEqual(selected['H2'][mid]['bet']['odds'],2)
        self.assertEqual(selected['H1'][mid]['bet']['message_id'],2);self.assertEqual(selected['H1'][mid]['bet']['odds'],2.5)
    def test_bounded_sidecar_makes_zero_provider_calls(self):
        self.assertFalse(any(x in ops.__dict__ for x in ('request_json','urlopen','Telethon','notify')))
        with self.assertRaises(ops.OpsError):ops.supervise(self.pub,self.source,self.auth,seconds=1081)
        with self.assertRaises(ops.OpsError):ops.supervise(self.pub,self.source,self.auth,poll=1)

    def test_ephemeral_runner_restores_failed_push_from_incremental_outbox(self):
        self.step();self.message();original=self.pub.publish;calls=[0]
        def fail():
            calls[0]+=1
            if calls[0]==2:raise ops.OpsError('push outage')
            return original()
        with patch.object(self.pub,'publish',fail):
            with self.assertRaises(ops.OpsError):ops.tick(self.pub,self.source,self.auth)
        events=copy.deepcopy(self.ledger()['events']);archive=self.root/'outbox.zip'
        ops.export_outbox(self.pub,archive)
        fresh=self.root/'fresh';ops.git(self.root,'clone','--branch','shadow/h1-h2-operational',str(self.remote),str(fresh))
        restored=ops.Publisher(fresh,clock=lambda:self.time)
        self.assertEqual(ops.restore_outbox(restored,archive)['state'],'restored')
        self.pub=restored
        self.assertEqual(self.ledger()['events'],events)
        r=ops.tick(self.pub,self.source,self.auth);self.assertFalse(r['problems'])
        self.assertLess(archive.stat().st_size,5_000_000)
    def test_outbox_free_storage_budget_and_traversal_protection(self):
        with self.assertRaises(ops.OpsError):ops.export_outbox(self.pub,self.root/'too-small.zip',max_bytes=1)
        self.assertFalse((self.root/'too-small.zip').exists())

    def test_outbox_path_traversal_is_rejected_without_write(self):
        import zipfile,hashlib
        path=self.root/'bad.zip';name='research/shadow-operational/../../escape.json';body=b'{}'
        manifest={'branch':self.pub.branch,'files_sha256':{name:hashlib.sha256(body).hexdigest()}}
        with zipfile.ZipFile(path,'w') as z:z.writestr('manifest.json',json.dumps(manifest));z.writestr(name,body)
        with self.assertRaises(ops.OpsError):ops.restore_outbox(self.pub,path)
        self.assertFalse((self.root/'escape.json').exists())

    def assert_result_excluded_on_remote(self):
        self.message()
        result = ops.tick(self.pub, self.source, self.auth)
        self.assertEqual(result['observations'], 0)
        self.assertEqual(self.ledger()['events'][0]['reason'], 'result_already_present')
        remote_ledger = json.loads(ops.git(
            self.remote, 'show',
            f'refs/heads/{self.pub.branch}:{ops.ROOT}/ledgers/{self.ident}.json',
        ))
        self.assertEqual(remote_ledger, self.ledger())
        self.assertFalse(any(event['kind'] == 'decision' for event in remote_ledger['events']))
        self.assertEqual(ops.read(self.pub.state / 'receipts.json'), [])

    def test_manual_result_already_known_never_reaches_remote_as_decision(self):
        self.step()
        mid = 'alpha-vs-beta-20261118'
        (self.data / 'manual_results.json').write_text(json.dumps({
            mid: {'ft': [3, 0], 'observed_utc': (self.time - timedelta(seconds=10)).isoformat()},
        }))
        self.assert_result_excluded_on_remote()

    def test_existing_highlightly_ft90_is_checked_before_admission(self):
        self.step()
        mid = 'alpha-vs-beta-20261118'
        (self.data / 'details_hl.json').write_text(json.dumps({'details': {
            mid: {'score_90': [3, 0], 'status': 'Finished', 'consistent': True,
                  'fetched_at_utc': (self.time - timedelta(seconds=10)).isoformat()},
        }}))
        self.assert_result_excluded_on_remote()

    def test_unproven_manual_result_and_its_later_removal_cannot_enable_capture(self):
        self.step()
        path = self.data / 'manual_results.json'
        path.write_text('{"alpha-vs-beta-20261118": {"ft": [3, 0]}}')
        self.assert_result_excluded_on_remote()
        path.unlink()
        self.step()
        self.message(i=2)
        self.assertEqual(ops.tick(self.pub, self.source, self.auth)['observations'], 0)
        self.assertEqual([e['reason'] for e in self.ledger()['events']],
                         ['result_already_present', 'result_already_present'])

    def test_deadline_kills_git_group_and_reserves_time_for_recoverable_outbox(self):
        import time

        self.step()
        self.message()
        self.pub.publish()  # first push is verified, so the slow hook runs on the decision push
        hook = Path(ops.git(self.repo, 'rev-parse', '--absolute-git-dir')) / 'hooks' / 'pre-push'
        hook.write_text('#!/bin/sh\nsleep 25\nexit 1\n')
        hook.chmod(0o700)
        archive = self.root / 'deadline-outbox.zip'
        source_before = {p.name: p.read_bytes() for p in self.data.iterdir()}
        started = time.monotonic()
        # Scale the reserve for the local fixture while exercising a real slow Git child.
        with patch.object(ops, 'OUTBOX_RESERVE', 0.5):
            with self.assertRaises(ops.DeadlineExceeded):
                ops.supervise(self.pub, self.source, self.auth, seconds=2, outbox=archive)
        self.assertLess(time.monotonic() - started, 2.5)
        self.assertTrue(archive.is_file())
        self.assertEqual({p.name: p.read_bytes() for p in self.data.iterdir()}, source_before)
        events = self.ledger()['events']
        self.assertEqual(sum(e['kind'] == 'decision' for e in events), 1)
        remote = self.root / 'deadline-fresh'
        ops.git(self.root, 'clone', '--branch', self.pub.branch, str(self.remote), str(remote))
        self.pub = ops.Publisher(remote, clock=lambda: self.time)
        self.assertEqual(ops.restore_outbox(self.pub, archive)['state'], 'restored')
        self.assertEqual(self.ledger()['events'], events)
        self.assertFalse(ops.tick(self.pub, self.source, self.auth)['problems'])
        self.assertEqual(self.ledger()['events'], events)
        self.assertEqual(len(ops.read(self.pub.state / 'receipts.json')), 1)

    def test_deadline_applies_to_python_work_and_keeps_export_available(self):
        import time

        archive = self.root / 'python-timeout.zip'
        started = time.monotonic()
        with patch.object(ops, 'OUTBOX_RESERVE', 0.5), patch.object(
                ops, 'tick_locked', side_effect=lambda *args: time.sleep(10)):
            with self.assertRaises(ops.DeadlineExceeded):
                ops.supervise(self.pub, self.source, self.auth, seconds=1, outbox=archive)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertTrue(archive.is_file())
        self.assertEqual(self.ledger()['events'], [])
