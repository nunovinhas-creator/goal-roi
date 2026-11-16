"""Recovery safety and failure-mode tests. Every provider response is synthetic."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from scangoal.recovery import (digest, identity, initial_state, names_equal, offline_trial,
                              parse_page, reconcile, run_batch, targets_from, write_json)
from scangoal.recovery_audit import audit
from scangoal.results_hl import to_event

NOW = datetime.fromisoformat('2026-11-16T15:40:00+00:00')
DAY = '2026-11-12'
EV = {'id': 42, 'status': 'finished', 'home_team': 'AlbinoLeffe', 'away_team': 'Brescia',
      'home_score': 2, 'away_score': 1, 'event_date': DAY+'T18:00:00+00:00', 'league': 'Serie C'}
VAR = {'teams': ['Albinoleffe','Brescia'], 'kickoff_utc': DAY+'T18:00:00+00:00',
       'date_utc': DAY+'T17:30:00+00:00', 'league': 'Serie C'}
TARGET = {'variants': [VAR], 'days': [DAY], 'invalid': False}


def payload(ev=EV, total=1):
    return {'results': [copy.deepcopy(ev)] if ev else [], 'count': total, 'next': None}


def run(state=None, fetch=None, **kw):
    opts = {'daily': {'hl':0,'bsd':20}, 'available':{'hl':0,'bsd':200}, 'max_requests':1}
    opts.update(kw)
    return run_batch(state or initial_state(), {'match': TARGET}, fetch or (lambda *a:payload()), NOW, **opts)


class RecoveryIdentity(unittest.TestCase):
    def test_exact_unicode_and_designators(self):
        self.assertTrue(names_equal('FC Trenčín Women', 'Trencin Women', {}))
        self.assertTrue(identity(TARGET,[EV],{})[0])

    def test_reviewed_alias(self):
        ev = dict(EV, away_team='Union Brescia')
        self.assertIsNone(identity(TARGET,[ev],{})[0])
        self.assertTrue(identity(TARGET,[ev],{'brescia':['Union Brescia']})[0])

    def test_distinct_similar_teams(self):
        for name in ('Brescia II','Brescia B','Brescia Women','Brescia U19','Brescia U21','Bresciano'):
            with self.subTest(name=name):
                self.assertIsNone(identity(TARGET,[dict(EV,away_team=name)],{})[0])

    def test_legacy_alias_cannot_remove_gender(self):
        self.assertFalse(names_equal('Brondby Women','Brøndby',{'brondby women':['Brøndby']}))

    def test_two_dates(self):
        self.assertEqual(identity(TARGET,[EV,dict(EV,id=43,event_date='2026-11-13T18:00:00+00:00')],{})[0]['id'],42)

    def test_incompatible_hours(self):
        self.assertIsNone(identity(TARGET,[dict(EV,event_date=DAY+'T23:00:00+00:00')],{})[0])

    def test_multiple_candidates(self):
        self.assertEqual(identity(TARGET,[EV,dict(EV,id=43)],{})[1],'multiple_candidates')

    def test_competition_conflict_or_unknown(self):
        for league in (None,'Other League'):
            self.assertIsNone(identity(TARGET,[dict(EV,league=league)],{})[0])

    def test_reviewed_competition_mapping(self):
        ev=dict(EV,league='Italian Serie C')
        self.assertIsNone(identity(TARGET,[ev],{})[0])
        self.assertIsNotNone(identity(TARGET,[ev],{'_competitions':{'serie c':['Italian Serie C']}})[0])

    def test_nested_provider_league(self):
        self.assertIsNotNone(identity(TARGET,[dict(EV,league={'id':8,'name':'Serie C'})],{})[0])

    def test_multiple_signals_require_all_times(self):
        target=copy.deepcopy(TARGET)
        target['variants'].append(dict(VAR,kickoff_utc=DAY+'T23:00:00+00:00'))
        self.assertIsNone(identity(target,[EV],{})[0])


class RecoveryTransport(unittest.TestCase):
    def test_success_stages_provenance_at_observation(self):
        state=run()
        res=state['accepted']['match']
        self.assertEqual(res['ft'],[2,1])
        self.assertEqual(res['matched_at_utc'],NOW.isoformat())
        self.assertEqual(res['evidence'][0]['event_id'],42)
        self.assertEqual(res['evidence'][0]['raw_sha256'],digest(EV))

    def test_incomplete_not_accepted_then_resume_offset(self):
        first=run(fetch=lambda *a:payload(total=2))
        self.assertFalse(first['accepted'])
        self.assertEqual(first['pages']['bsd|'+DAY]['status'],'incomplete')
        calls=[]
        def fetch(s,d,o,l):
            calls.append(o)
            return payload(dict(EV,id=43,home_team='Other'),total=2)
        second=run(state=first,fetch=fetch)
        self.assertEqual(calls,[1])
        self.assertIn('match',second['accepted'])

    def test_pagination_candidate_in_later_page_blocks_ambiguity(self):
        first=run(fetch=lambda *a:payload(total=2))
        second=run(state=first,fetch=lambda *a:payload(dict(EV,id=43),total=2))
        self.assertFalse(second['accepted'])
        self.assertIn('multiple_candidates',second['conflicts']['match'])

    def test_daily_quota_and_reserve(self):
        for kwargs in ({'available':{'hl':0,'bsd':100}}, {'daily':{'hl':0,'bsd':0}}):
            calls=[]
            run(fetch=lambda *a:calls.append(a),**kwargs)
            self.assertEqual(calls,[])

    def test_http_429_cooldown_and_sanitization(self):
        def fail(*a):raise RuntimeError('HTTP 429 token=secret')
        state=run(fetch=fail)
        self.assertEqual(state['attempts'][0]['outcome'],'http_429')
        self.assertNotIn('secret',json.dumps(state))
        calls=[]
        run(state=state,fetch=lambda *a:calls.append(a))
        self.assertEqual(calls,[])

    def test_invalid_response(self):
        state=run(fetch=lambda *a:{'bad':'response'})
        self.assertEqual(state['attempts'][0]['outcome'],'invalid_response')
        self.assertFalse(state['accepted'])

    def test_unavailable_not_coverage(self):
        def fail(*a):raise OSError('private URL')
        state=run(fetch=fail)
        self.assertEqual(state['attempts'][0]['outcome'],'transport_unavailable')
        self.assertNotIn('not_covered',json.dumps(state))

    def test_empty_truncated_page_not_complete(self):
        state=run(fetch=lambda *a:payload(None,total=2))
        self.assertEqual(state['attempts'][0]['outcome'],'truncated_page')

    def test_repeated_page_cannot_loop(self):
        first=run(fetch=lambda *a:payload(total=2))
        second=run(state=first,fetch=lambda *a:payload(total=2))
        self.assertEqual(second['attempts'][-1]['outcome'],'repeated_page')
        self.assertFalse(second['accepted'])

    def test_total_drift_stops(self):
        first=run(fetch=lambda *a:payload(total=2))
        second=run(state=first,fetch=lambda *a:payload(dict(EV,id=43),total=3))
        self.assertEqual(second['attempts'][-1]['outcome'],'pagination_total_changed_requires_review')

    def test_postponed_abandoned_cancelled(self):
        for status in ('postponed','abandoned','cancelled','upcoming','live'):
            with self.subTest(status=status):
                self.assertFalse(run(fetch=lambda *a:payload(dict(EV,status=status)))['accepted'])

    def test_extra_time_and_penalties_not_ft(self):
        for status in ('Finished after extra time','Finished after penalties','Awarded'):
            event=to_event({'id':42,'state':{'description':status,'score':{'current':'4 - 3'}}})
            self.assertNotEqual(event['status'],'finished')
            self.assertIsNone(event['home_score'])

    def test_corrected_score_quarantined_no_history_rewrite(self):
        state=run()
        saved=copy.deepcopy(state['observations'])
        # Next day's scheduled re-observation sees corrected score.
        later=NOW+timedelta(days=1)
        state=run_batch(state,{'match':TARGET},lambda *a:payload(dict(EV,home_score=3)),later,
                        daily={'hl':0,'bsd':20},available={'hl':0,'bsd':200},max_requests=1)
        self.assertFalse(state['accepted'])
        self.assertEqual(state['observations']['bsd|42'][0],saved['bsd|42'][0])
        self.assertEqual(len(state['observations']['bsd|42']),2)

    def test_conflicting_providers(self):
        state=run()
        state['pages']['hl|'+DAY]={'status':'complete','events':{'42':dict(EV,home_score=0)}}
        state['observations']['hl|42']=[{'event':dict(EV,home_score=0),'observed_utc':NOW.isoformat()}]
        reconcile(state,{'match':TARGET},{},{})
        self.assertFalse(state['accepted'])
        self.assertIn('provider_conflict',state['conflicts']['match'])

    def test_partial_provider_conflict_still_blocks(self):
        state=run()
        state['pages']['hl|'+DAY]={'status':'incomplete','events':{'42':dict(EV,home_score=0)}}
        state['observations']['hl|42']=[{'event':dict(EV,home_score=0),'observed_utc':NOW.isoformat()}]
        reconcile(state,{'match':TARGET},{},{})
        self.assertFalse(state['accepted'])
        self.assertIn('provider_conflict',state['conflicts']['match'])

    def test_repeated_page_requires_review_no_endless_retries(self):
        state=run(fetch=lambda *a:payload(total=2))
        state=run(state=state,fetch=lambda *a:payload(total=2))
        calls=[]
        run_batch(state,{'match':TARGET},lambda *a:calls.append(a),NOW+timedelta(days=1),
                  daily={'hl':0,'bsd':20},available={'hl':0,'bsd':200},max_requests=2)
        self.assertFalse(calls)

    def test_existing_confirmed_result_never_overwritten(self):
        state=run(existing={'match':{'ft':[0,0],'matched_at_utc':'2020-01-01T00:00:00+00:00'}})
        self.assertFalse(state['accepted'])
        self.assertIn('previously_confirmed_conflict',state['conflicts']['match'])

    def test_resume_after_crash_reservation_not_refunded(self):
        checkpoints=[]
        class Crash(BaseException):pass
        def fail(*a):raise Crash()
        with self.assertRaises(Crash):
            run(fetch=fail,save=lambda s:checkpoints.append(copy.deepcopy(s)))
        state=checkpoints[-1]
        self.assertEqual(state['usage']['bsd|2026-11-16']['reserved_requests'],1)
        resumed=run(state=state)
        self.assertEqual(resumed['usage']['bsd|2026-11-16']['reserved_requests'],2)
        self.assertIn('match',resumed['accepted'])

    def test_rerun_no_duplicate_observation_or_requests(self):
        state=run()
        again=run(state=state)
        self.assertEqual(again,state)

    def test_old_day_not_starved_by_recent_day(self):
        target2=copy.deepcopy(TARGET); target2['days']=['2026-11-13']
        calls=[]
        state=run_batch(initial_state(),{'old':TARGET,'recent':target2},
                        lambda s,d,o,l:(calls.append(d) or payload(total=50)),NOW,
                        daily={'hl':0,'bsd':20},available={'hl':0,'bsd':200},max_requests=2)
        self.assertEqual(calls,[DAY,'2026-11-13'])

    def test_quota_plan_validation(self):
        with self.assertRaises(ValueError):run(available={'hl':101,'bsd':200})

    def test_header_reserve_stops_before_next_request(self):
        calls=[]
        def fetch(*a):
            calls.append(a)
            return ({'data':[], 'pagination':{'totalCount':0}},20)
        run_batch(initial_state(),{'match':TARGET},fetch,NOW,max_requests=3,
                  available={'hl':100,'bsd':0},daily={'hl':5,'bsd':0})
        self.assertEqual(len(calls),1)


class RecoveryRealData(unittest.TestCase):
    def test_offline_trial_preserves_all_data_and_scientific_files(self):
        paths=[p for root in ('data','scangoal') for p in Path(root).glob('*') if p.is_file()]
        before={str(p):p.read_bytes() for p in paths}
        result=offline_trial(NOW)
        self.assertEqual(result['api_requests_made'],0)
        self.assertEqual(result['before'],result['after'])
        self.assertEqual(before,{str(p):p.read_bytes() for p in paths})

    def test_real_audit_matches_report_universe(self):
        r=audit(NOW)
        production=json.loads(Path('data/report.json').read_text())
        self.assertEqual(r['unique_bets'],production['unique_bets'])
        self.assertEqual(r['settled_bets'],production['settled_bets'])
        self.assertEqual(sum(r['by_category'].values()),r['pending_bets'])
        self.assertTrue(all(x['reason'] for x in r['rows']))
        self.assertTrue(all(x['category'] in 'ABCDEFGHIJ' for x in r['rows']))

    def test_final_score_insufficient_market_excluded_from_queries(self):
        b=dict(VAR,match_id='m',w=None,ft=[2,1],market='1st Half - Asian Total')
        self.assertEqual(targets_from([b],NOW),{})

    def test_old_pending_included_configurable_window(self):
        b=dict(VAR,match_id='m',w=None,ft=None)
        self.assertIn('m',targets_from([b],NOW,30))
        self.assertNotIn('m',targets_from([b],NOW,2))

    def test_atomic_persistence(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'state.json'
            write_json(p,{'x':1}); write_json(p,{'x':2})
            self.assertEqual(json.loads(p.read_text()),{'x':2})
            self.assertFalse(p.with_suffix('.json.tmp').exists())

    def test_live_opt_in_and_production_output_guard(self):
        for args in (['--allow-live'],['--backfill','--allow-live','--now',NOW.isoformat()],['--audit','--output','data/recovery']):
            cmd=[sys.executable,'-m','scangoal.recovery','--output','/tmp/recovery-test']+args
            self.assertNotEqual(subprocess.run(cmd,capture_output=True).returncode,0)


if __name__=='__main__':unittest.main()
