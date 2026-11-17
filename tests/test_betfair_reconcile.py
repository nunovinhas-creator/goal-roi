import copy,unittest
from unittest.mock import patch
from scangoal.betfair_reconcile import reconcile

class Reconciliation(unittest.TestCase):
    def case(self,key,state='pending',**kw):
        return dict(dict(bet_id=key,message_id=1,match_id=key,teams=['A','B'],date_utc='2026-11-17T12:00:00Z',
                         kickoff_utc=None,phase='live',selection='1',odds=4.,state=state,ft=[2,0] if state=='settled' else None,
                         w=1 if state=='settled' else None,result_known_source='bot_message',result_known_utc='2026-11-17T15:00:00Z'),**kw)
    def audit(self,rows):
        return {'as_of_utc':'2026-11-17T18:00:00Z','inventory':rows,'pending':[r for r in rows if r['state']=='pending'],
                'bf_gt3_live_eligible':{},'provider_quota_evidence':{}}
    def test_prior_cohort_recovered_vs_new_intervention_are_separate(self):
        old=self.audit([self.case('one')]);new=self.audit([self.case('one','settled'),self.case('two')])
        before=copy.deepcopy((old,new))
        with patch('scangoal.betfair_reconcile.proofs',return_value=[{'source':'fixture only'}]):r=reconcile(old,new,{}, {})
        self.assertEqual(r['reference_pending_now_settled_with_evidence'],1)
        self.assertEqual(r['new_pending_since_reference'],1)
        self.assertEqual(r['additional_factual_results_recovered_by_this_run'],0)
        self.assertEqual(r['current_pending_before_this_intervention'],r['current_pending_after_this_intervention'])
        self.assertEqual(before,(old,new))
    def test_missing_individual_proof_is_not_a_recovery(self):
        with patch('scangoal.betfair_reconcile.proofs',return_value=[]):r=reconcile(self.audit([self.case('g')]),self.audit([self.case('g','settled')]),{}, {})
        self.assertEqual(r['reference_pending_now_settled_with_evidence'],0)
        self.assertEqual(len(r['insufficient_recovery_evidence']),1)
    def test_penalty_details_inconsistent_are_never_repaired_by_dropping_goals(self):
        a=self.audit([self.case('g')]);detail={'consistent':False,'final':[1,1],'goals':[[49,0,'home'],[72,0,'away'],[120,2,'home']], 'status':'Finished after penalties'}
        r=reconcile(a,a,{'hl':{'not_regular_time':{'g':{'id':1,'status':detail['status']}}}},{'g':detail})
        self.assertEqual(r['pending_classifications'][0]['decision'],'KEEP_PENDING')
        self.assertIn('inconsistent_goal_details',r['pending_classifications'][0]['causes'])
        self.assertEqual(r['additional_factual_results_recovered_by_this_run'],0)
    def test_alias_candidates_do_not_change_identity(self):
        a=self.audit([self.case('g')]);st={'hl':{'near_misses':{'g':{'bot':['A','B'],'bsd_candidates':[['A Town','B']]}}}}
        r=reconcile(a,a,st,{})
        self.assertEqual(len(r['unverified_alias_candidates']),1)
        self.assertIn('different_names',r['pending_classifications'][0]['causes'])
        self.assertEqual(a['inventory'][0]['teams'],['A','B'])
    def test_inconsistent_prematch_time_flagged(self):
        a=self.audit([self.case('g',phase='pre',kickoff_utc='2026-11-17T10:00:00Z')])
        r=reconcile(a,a,{},{});self.assertIn('prematch_timestamp_not_before_announced_kickoff',r['pending_classifications'][0]['causes'])
    def test_absence_of_coverage_is_not_a_loss(self):
        a=self.audit([self.case('g')]);r=reconcile(a,a,{'hl':{'not_covered':{'g':'2026-11-17T15:00:00Z'}}},{})
        self.assertIn('no_exact_coverage_after_complete_checks',r['pending_classifications'][0]['causes'])
        self.assertIsNone(a['inventory'][0]['w'])
