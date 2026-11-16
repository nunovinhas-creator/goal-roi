"""PR30 safety tests. All provider responses are synthetic; no network required."""
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from scangoal import recovery as engine
from scangoal import recovery_trial as trial

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026,11,16,17,tzinfo=timezone.utc)
MID = 'horsham-vs-torquay-united-20261114'
DAY = '2026-11-14'
TARGET = {'variants':[{'teams':['Horsham','Torquay United'],'league':'Test league',
    'date_utc':DAY+'T14:00:00+00:00','kickoff_utc':DAY+'T15:00:00+00:00'}],
    'days':[DAY],'invalid':False}
EVENT = {'id':100,'home_team':'Horsham','away_team':'Torquay United','league':'Test league',
    'event_date':DAY+'T15:00:00+00:00','status':'finished','home_score':2,'away_score':1}

def payload(event=None,total=1):
    return {'results':[copy.deepcopy(event or EVENT)],'count':total}

def bet(mid=MID):
    return dict(TARGET['variants'][0],match_id=mid,w=None,ft=None)

class TrialSafety(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.output=Path(self.tmp.name)/'fixture-trial'
        self.transport=trial.FixtureTransport({(DAY,0):payload()})
    def run_trial(self,**kw):
        return trial.simulate(self.output,{MID:copy.deepcopy(TARGET)},self.transport,NOW,**kw)
    def test_workflow_manual_only(self):
        w=json.loads((ROOT/'.github/workflows/recovery-trial.yml').read_text())
        self.assertEqual(set(w['on']),{'workflow_dispatch'})
        self.assertEqual(w['permissions'],{'contents':'read'}); self.assertNotIn('concurrency',w)
        steps=w['jobs']['preflight']['steps']
        self.assertEqual([s['run'] for s in steps if 'run' in s],
            ['python -m scangoal.recovery_trial --output "$RUNNER_TEMP/recovery-trial"'])
        self.assertEqual(steps[2]['env']['BSD_API_KEY'],'${{ secrets.BSD_API_KEY }}')
        self.assertEqual(steps[-1]['with']['path'],'${{ runner.temp }}/recovery-trial/preflight.json')
        self.assertFalse(steps[0]['with']['persist-credentials'])
        for s in steps:
            if 'uses' in s: self.assertRegex(s['uses'],r'@[a-f0-9]{40}$')
    def test_no_key_zero_requests(self):
        with self.assertRaises(trial.TrialBlocked): self.run_trial(key_present=False)
        self.assertEqual(self.transport.calls,[])
    def test_unverified_quota_zero_requests(self):
        with self.assertRaises(trial.TrialBlocked): self.run_trial(quota_verified=False)
        self.assertEqual(self.transport.calls,[])
    def test_balance_below_105_zero_requests(self):
        for value in (None,104,0,-1,True,'7500'):
            with self.subTest(value=value),self.assertRaises(trial.TrialBlocked): self.run_trial(balance=value)
        self.assertEqual(self.transport.calls,[])
    def test_collector_active_or_uncertain_zero_requests(self):
        for active,exclusion in ((True,True),(None,True),(False,False)):
            with self.subTest(active=active),self.assertRaises(trial.TrialBlocked):
                self.run_trial(collector_active=active,exclusion_verified=exclusion)
        self.assertEqual(self.transport.calls,[])
    def test_assertions_cannot_enable_live(self):
        self.assertEqual(trial.blockers(key_present=True,balance=7500,quota_verified=True,
            collector_active=False,exclusion_verified=True,durable_verified=True),
            ['live_transport_not_enabled_pending_review'])
    def test_data_only_fixture_transport(self):
        calls=[]
        with self.assertRaises(trial.TrialBlocked):
            trial.simulate(self.output,{MID:TARGET},lambda *a:calls.append(a),NOW)
        self.assertEqual(calls,[])
    def test_exhausted_budget_survives_midnight_and_new_process(self):
        pages={(DAY,off):{'count':1200,'results':[dict(EVENT,id=i+1) for i in range(off,off+200)]}
               for off in range(0,1200,200)}
        transport=trial.FixtureTransport(pages)
        state=trial.simulate(self.output,{MID:TARGET},transport,NOW)
        self.assertEqual(len(transport.calls),5); self.assertEqual(len(state['attempts']),5)
        script='''from pathlib import Path
from datetime import datetime
from scangoal.recovery_trial import simulate,FixtureTransport
import json,sys
transport=FixtureTransport({})
state=simulate(Path(sys.argv[1]),json.loads(sys.argv[2]),transport,datetime.fromisoformat(sys.argv[3]))
assert len(state['attempts'])==5 and transport.calls==[]
'''
        subprocess.run([sys.executable,'-c',script,str(self.output),json.dumps({MID:TARGET}),
            (NOW+timedelta(days=1)).isoformat()],cwd=ROOT,check=True,capture_output=True)
    def test_429_stops_trial_and_resume(self):
        self.transport=trial.FixtureTransport({(DAY,0):429})
        state=self.run_trial(); self.assertTrue(state['halted'])
        self.assertEqual(state['attempts'][0]['outcome'],'http_429')
        self.run_trial(); self.assertEqual(len(self.transport.calls),1)
    def test_invalid_response_stops_trial(self):
        self.transport=trial.FixtureTransport({(DAY,0):{'bad':[]}})
        state=self.run_trial(); self.assertTrue(state['halted'])
        self.run_trial(); self.assertEqual(len(self.transport.calls),1)
    def test_reservation_precedes_failure_no_secret_error(self):
        calls=[]
        def fail(*args):
            calls.append(len(json.loads((self.output/'state.json').read_text())['attempts']))
            raise RuntimeError('secret text must not persist')
        with patch.object(trial.FixtureTransport,'__call__',fail): state=self.run_trial()
        self.assertEqual(calls,[1]); self.assertTrue(state['halted'])
        self.assertNotIn('secret text',(self.output/'state.json').read_text())
    def test_crash_reservation_not_refunded(self):
        with patch.object(trial.FixtureTransport,'__call__',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt): self.run_trial()
        state=json.loads((self.output/'state.json').read_text())
        self.assertEqual(len(state['attempts']),1)
        self.assertEqual(state['attempts'][0]['outcome'],'reserved')
        self.run_trial()
        self.assertEqual(len(json.loads((self.output/'state.json').read_text())['attempts']),2)
    def test_selection_ten_ids_three_days_only(self):
        self.assertEqual(len(trial.MATCH_IDS),10)
        self.assertEqual(trial.DAYS,{'2026-11-13','2026-11-14','2026-11-15'})
        self.assertEqual(set(trial.select_targets([bet(),bet('other')],NOW)),{MID})
        outside=bet(); outside.update(date_utc='2026-11-13T00:20:00Z',kickoff_utc=None)
        self.assertEqual(trial.select_targets([outside],NOW),{})
        for targets in ({'other':TARGET},{MID:dict(TARGET,days=['2026-11-12'])}):
            with self.assertRaises(trial.TrialBlocked): trial.simulate(self.output,targets,self.transport,NOW)
        self.assertEqual(self.transport.calls,[])
    def test_already_final_or_settled_excluded(self):
        b=bet(); b['ft']=[2,1]; self.assertEqual(trial.select_targets([b],NOW),{})
        b=bet(); b['w']=True; self.assertEqual(trial.select_targets([b],NOW),{})
    def test_staging_integrity_no_api_telegram_certification(self):
        before={p:trial.manifest(ROOT/p) for p in ('data','docs','.github','scangoal')}
        with patch.object(engine,'bsd_request',side_effect=AssertionError('BSD forbidden')), \
             patch.object(engine,'hl_request',side_effect=AssertionError('HL forbidden')):
            state=self.run_trial()
        self.assertIn(MID,state['accepted'])
        self.assertEqual(before,{p:trial.manifest(ROOT/p) for p in before})
        script="import sys,socket; from unittest.mock import patch; from scangoal import recovery_trial; assert 'scangoal.notify' not in sys.modules"
        subprocess.run([sys.executable,'-c',script],cwd=ROOT,check=True,capture_output=True)
        self.assertEqual(state['evidence_kind'],'synthetic_fixture')
        self.assertTrue((self.output/'state.json').exists())
    def test_production_output_rejected(self):
        for out in (ROOT/'data'/'fixture-trial',ROOT/'docs'/'fixture-trial',ROOT.parent):
            with self.assertRaises(trial.TrialBlocked): trial.isolated(ROOT,out)
        with self.assertRaises(trial.TrialBlocked):
            trial.simulate(ROOT/'data'/'fixture-trial',{MID:TARGET},self.transport,NOW)
        self.assertEqual(self.transport.calls,[])
    def test_provenance_parameters_hash_and_offset_zero(self):
        state=self.run_trial(); response=json.loads((self.output/'response-00.json').read_text())
        self.assertEqual(self.transport.calls,[('bsd',DAY,0,200)])
        self.assertEqual(response['params'],dict(date_from=DAY,date_to=DAY,status='finished',limit=200,offset=0))
        raw=json.dumps(response['payload'],sort_keys=True,ensure_ascii=False).encode()
        self.assertEqual(response['sha256'],hashlib.sha256(raw).hexdigest())
        self.assertEqual(state['accepted'][MID]['matched_at_utc'],engine.stamp(NOW))
        self.assertNotIn('BSD_API_KEY',json.dumps(state))
    def test_t1_t2_t3_final_chronology_and_quarantine(self):
        versions=[{'event':dict(EVENT,status='live',home_score=None,away_score=None),'observed_utc':engine.stamp(NOW)},
            {'event':EVENT,'observed_utc':engine.stamp(NOW+timedelta(minutes=1))},
            {'event':dict(EVENT,home_score=3),'observed_utc':engine.stamp(NOW+timedelta(minutes=2))}]
        self.assertIsNone(engine.final_observation(versions[:1],EVENT))
        self.assertEqual(engine.final_observation(versions,EVENT),engine.stamp(NOW+timedelta(minutes=1)))
        state=engine.initial_state()
        state['pages']['bsd|'+DAY]={'events':{'100':versions[-1]['event']},'status':'complete'}
        state['observations']['bsd|100']=copy.deepcopy(versions)
        engine.reconcile(state,{MID:TARGET},{},{})
        self.assertNotIn(MID,state['accepted']); self.assertIn('provider_result_changed',state['conflicts'][MID])
        self.assertEqual(state['observations']['bsd|100'],versions)
    def test_resume_binding_cannot_change_aliases(self):
        self.run_trial()
        with self.assertRaises(trial.TrialBlocked): self.run_trial(aliases={'horsham':['another']})
        self.assertEqual(len(self.transport.calls),1)
    def test_prepare_copy_resume_and_tamper_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'checkout'; (root/'data').mkdir(parents=True)
            (root/'data'/'original.json').write_text('{"unchanged":true}\n'); out=Path(tmp)/'staging'
            with patch.object(trial,'verify_engine'),patch.object(trial,'snapshot',return_value=([bet()],{},{})), \
                 patch.object(engine,'load_aliases',return_value={}):
                first=trial.prepare(root,out,NOW)
                self.assertEqual(first,trial.prepare(root,out,NOW+timedelta(hours=1)))
                self.assertEqual(trial.manifest(root/'data'),trial.manifest(out/'snapshot'))
                (out/'snapshot'/'original.json').write_text('tampered')
                with self.assertRaises(trial.TrialBlocked): trial.prepare(root,out,NOW)
    def test_symlink_input_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp)/'link').symlink_to(ROOT/'data',target_is_directory=True)
            with self.assertRaises(trial.TrialBlocked): trial.manifest(Path(tmp))
    def test_missing_cross_run_ledger_blocks_live(self):
        self.assertIn('cross_run_budget_not_proven',trial.blockers(key_present=True,balance=105))
    def test_cli_has_no_live_override(self):
        result=subprocess.run([sys.executable,'-m','scangoal.recovery_trial','--live','--output',str(self.output)],
            cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(result.returncode,2); self.assertFalse(self.output.exists())
    def test_engine_verifier_rejects_changed_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'scangoal').mkdir(); (root/'scangoal/store.py').write_text('changed')
            with patch.object(trial.subprocess,'check_output',side_effect=['scangoal/store.py\n',b'original']):
                with self.assertRaises(trial.TrialBlocked): trial.verify_engine(root)
    def test_local_lock_excludes_second_process(self):
        self.output.mkdir()
        with (self.output/'state.lock').open('a') as lock:
            trial.fcntl.flock(lock,trial.fcntl.LOCK_EX|trial.fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError): self.run_trial()
        self.assertEqual(self.transport.calls,[])

if __name__=='__main__': unittest.main()
