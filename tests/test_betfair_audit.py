import copy
import unittest
from datetime import datetime,timezone
from scangoal.betfair_audit import evidence_candidates,exact_1x2,factual_settle,sensitivity,age_bucket,snapshot,disagreement_cases,sha
from scangoal.parser import settle

NOW=datetime(2026,11,17,18,tzinfo=timezone.utc)
TARGET={'g':{'teams':['Alpha','Beta'],'date_utc':'2026-11-16T17:00:00+00:00','kickoff_utc':'2026-11-16T18:00:00+00:00'}}

class BetfairAudit(unittest.TestCase):
    def ev(self,i=1,**changes):
        return dict(dict(id=i,home_team='Alpha',away_team='Beta',event_date='2026-11-16T18:00:00Z',status='finished',home_score=2,away_score=1),**changes)
    def envelope(self,events,**changes):
        return dict(dict(provider='bsd',observed_utc=NOW.isoformat(),payload={'results':events}),**changes)
    def test_exact_identity_and_provenance(self):
        e=self.envelope([self.ev()]);found,q=evidence_candidates(TARGET,[e],NOW)
        self.assertEqual(found['g']['ft'],[2,1]);self.assertFalse(q)
        self.assertEqual(found['g']['evidence'][0]['evidence_sha256'],sha(e))
        self.assertIn('PROPOSE_ONLY',found['g']['decision'])
    def test_names_and_dates_cannot_be_guessed(self):
        for ev in [self.ev(home_team='Alpha Town'),self.ev(event_date='2026-11-17T18:00:00Z'),self.ev(home_team='Beta',away_team='Alpha')]:
            found,q=evidence_candidates(TARGET,[self.envelope([ev])],NOW)
            self.assertFalse(found)
    def test_ambiguity_and_conflicting_sources_quarantined(self):
        found,q=evidence_candidates(TARGET,[self.envelope([self.ev(),self.ev(2,home_score=0)])],NOW)
        self.assertFalse(found);self.assertTrue(q)
        found,q=evidence_candidates(TARGET,[self.envelope([self.ev()]),self.envelope([self.ev(home_score=0)])],NOW)
        self.assertFalse(found);self.assertEqual(q[-1]['reason'],'sources disagree')
    def test_exact_duplicates_deduplicated(self):
        found,q=evidence_candidates(TARGET,[self.envelope([self.ev(),self.ev()])],NOW)
        self.assertIn('g',found)
    def test_bad_observation_times_and_unsupported_providers(self):
        for extra in [{'observed_utc':'unknown'},{'observed_utc':'2026-11-18T18:00:00Z'},
                      {'observed_utc':'2026-11-15T18:00:00Z'},{'observed_utc':'2026-11-16T17:30:00Z'},{'provider':'sportsdb'}]:
            found,q=evidence_candidates(TARGET,[self.envelope([self.ev()],**extra)],NOW)
            self.assertFalse(found);self.assertTrue(q)
    def test_late_or_awarded_scores_never_become_regular_ft(self):
        for status in ['awarded','after_extra_time','after_penalties','postponed']:
            found,q=evidence_candidates(TARGET,[self.envelope([self.ev(status=status)])],NOW)
            self.assertFalse(found);self.assertTrue(q)
    def test_highlightly_regular_vs_extra_time(self):
        ev={'id':11,'homeTeam':{'name':'Alpha'},'awayTeam':{'name':'Beta'},'date':'2026-11-16T18:00:00Z','state':{'description':'Finished','score':{'current':'2 - 1'}}}
        e={'provider':'hl','observed_utc':NOW.isoformat(),'payload':{'data':[ev]}}
        self.assertIn('g',evidence_candidates(TARGET,[e],NOW)[0])
        ev['state']['description']='Finished after penalties'
        self.assertFalse(evidence_candidates(TARGET,[e],NOW)[0])
    def test_team_name_1x2_bug_isolated_from_production(self):
        b={'market':'1x2','selection':'Beta','teams':['Alpha','Beta'],'phase':'pre'}
        self.assertEqual(settle('1x2','Beta',[0,2],b['teams']),-1)
        self.assertEqual(factual_settle(b,[0,2]),1)
        self.assertIsNone(exact_1x2('Bet',['Alpha','Beta']))
        self.assertIsNone(exact_1x2('Alpha',['Alpha','Alpha']))
    def test_live_handicap_requires_scope_evidence(self):
        self.assertIsNone(factual_settle({'market':'Asian Handicap','selection':'Alpha (-1)','teams':['Alpha','Beta'],'phase':'live'},[2,1]))
    def test_sensitivity_does_not_settle_pending(self):
        rows=[dict(w=1,odds=4,match_id='one'),dict(w=-1,odds=4,match_id='one'),dict(w=None,odds=5,match_id='two')]
        before=copy.deepcopy(rows);r=sensitivity(rows)
        self.assertEqual(rows,before);self.assertEqual(r['independent_games'],2)
        self.assertEqual(r['haircuts']['0']['all_pending_lost_profit'],1)
        self.assertEqual(r['pending'],1)
    def test_age_boundaries_and_not_started(self):
        for t,want in [('2026-11-18T18:00:00Z','not_started'),('2026-11-17T18:00:00Z','<24h'),('2026-11-16T18:00:00Z','24-48h'),('2026-11-15T18:00:00Z','48-72h'),('2026-11-14T18:00:00Z','>72h')]:
            self.assertEqual(age_bucket({'date_utc':t},NOW),want)
    def test_malformed_envelopes_fail_closed(self):
        for e in [None,{},self.envelope([],payload=[]),self.envelope([],payload={'results':'bad'})]:
            found,q=evidence_candidates(TARGET,[e],NOW);self.assertFalse(found);self.assertTrue(q)
