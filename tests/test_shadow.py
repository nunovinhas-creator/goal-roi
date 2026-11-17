import copy
import unittest
from datetime import datetime, timedelta, timezone
from scangoal.shadow import protocol,new_ledger,capture,record_result,analyse,verify,metrics,history_check

T=datetime(2026,11,17,18,tzinfo=timezone.utc)

class Shadow(unittest.TestCase):
    def setUp(self):
        self.p=protocol('a'*40,T.isoformat()); self.l=new_ledger(self.p)
    def bet(self,mid='game',i=1,minute=30,when=None,**kw):
        t=when or T+timedelta(seconds=1)
        return dict(match_id=mid,message_id=i,type='OPEN_GAME',date_utc=t.isoformat(),signal_seen_utc=t.isoformat(),
                    in_roi=True,market='Asian Total',selection='Over 2.25',odds=2.,minute=minute,
                    score_at_signal=[2,0],teams=['A','B'],**kw)
    def test_overlap_and_immutable_first_odds(self):
        t=T+timedelta(seconds=2)
        capture(self.p,self.l,[self.bet()],t)
        capture(self.p,self.l,[self.bet(i=2,minute=60,when=t)],t)
        capture(self.p,self.l,[self.bet(i=3,minute=70,when=t)],t)
        self.assertEqual([x['kind'] for x in self.l['events']],['decision','decision','subsequent'])
        record_result(self.p,self.l,'game',[2,0],'fixture', (t+timedelta(hours=2)).isoformat(),t+timedelta(hours=2))
        r=analyse(self.p,self.l,t+timedelta(hours=3))
        self.assertEqual((r['overlap'],r['H2']['games'],r['H1']['games']),(1,1,1))
        self.assertEqual(r['H1']['half_losses'],1)
        self.assertEqual(r['paired'][0]['difference'],0)
    def test_no_backfill_future_result_and_late_capture(self):
        for bet in [self.bet(when=T-timedelta(seconds=1)),self.bet(when=T+timedelta(hours=2)),self.bet(ft=[3,0]),self.bet(edited_at_first_sight=True)]:
            capture(self.p,self.l,[bet],T+timedelta(seconds=5))
        capture(self.p,self.l,[self.bet()],T+timedelta(minutes=3))
        capture(self.p,self.l,[self.bet()],T+timedelta(seconds=5),{'game'})
        self.assertEqual(self.l['events'],[])
    def test_tamper_clock_and_protocol_rejected(self):
        capture(self.p,self.l,[self.bet()],T+timedelta(seconds=5))
        self.l['events'][0]['bet']['odds']=4
        with self.assertRaises(ValueError):verify(self.p,self.l)
    def test_same_game_serial_chain_and_unresolved(self):
        t=T+timedelta(seconds=5)
        capture(self.p,self.l,[self.bet(minute=60),self.bet('overlap',2,60)],t)
        r=analyse(self.p,self.l,T+timedelta(days=4))
        self.assertEqual(r['H1']['pending'],2)
        self.assertEqual(r['sequential']['games']['H2'],1)
        self.assertTrue(r['sequential']['chain'][0]['missing_conservative'])
        self.assertEqual(r['scientific_state'],'INCONCLUSIVO')
    def test_corrections_append_no_rewrite(self):
        capture(self.p,self.l,[self.bet()],T+timedelta(seconds=2))
        with self.assertRaises(ValueError):record_result(self.p,self.l,'game',[2,0],'fixture',T.isoformat(),T)
        record_result(self.p,self.l,'game',[2,0],'fixture',(T+timedelta(hours=1)).isoformat(),T+timedelta(hours=1))
        before=copy.deepcopy(self.l['events'])
        record_result(self.p,self.l,'game',[3,0],'fixture',(T+timedelta(hours=2)).isoformat(),T+timedelta(hours=2))
        self.assertEqual(self.l['events'][:-1],before)
        self.assertTrue(self.l['events'][-1]['correction'])
    def test_asian_haircuts_and_drawdown(self):
        r=metrics([dict(w=.5,odds=2,date_utc=T.isoformat()),dict(w=-.5,odds=2,date_utc=T.isoformat()),dict(w=None,odds=4,date_utc=T.isoformat())])
        self.assertEqual(r['profit_units'],0)
        self.assertAlmostEqual(r['roi']['0.02'],-.01)
        self.assertEqual(r['max_drawdown_units'],.5)
        self.assertEqual(r['pending_sensitivity_profit'],[-1,3])
    def test_ambiguous_period_and_no_odds(self):
        capture(self.p,self.l,[self.bet(minute=91)],T+timedelta(seconds=2))
        self.assertEqual(self.l['events'][0]['rules'],['H2'])
        b=self.bet('other',2);b['odds']=float('nan')
        capture(self.p,self.l,[b],T+timedelta(seconds=2))
        self.assertEqual(len(self.l['events']),1)

    def test_external_publication_before_result(self):
        capture(self.p,self.l,[self.bet()],T+timedelta(seconds=2))
        published=copy.deepcopy(self.l)
        record_result(self.p,self.l,'game',[3,0],'fixture',(T+timedelta(hours=1)).isoformat(),T+timedelta(hours=1))
        versions=[(T+timedelta(seconds=3),published),(T+timedelta(hours=1,seconds=1),copy.deepcopy(self.l))]
        self.assertEqual(history_check(self.p,self.l,versions),[])
        versions[0]=(T+timedelta(hours=2),published)
        self.assertIn('decision not published before result',history_check(self.p,self.l,versions))
    def test_missing_cannot_create_negative_evidence(self):
        capture(self.p,self.l,[self.bet(minute=60)],T+timedelta(seconds=2))
        seq=analyse(self.p,self.l,T+timedelta(days=4))['sequential']
        for vals in seq['evalues_positive_negative'].values():
            self.assertLessEqual(vals[0],1)
            self.assertLessEqual(vals[1],1)
    def test_unknown_timestamp_and_future_results_fail_closed(self):
        capture(self.p,self.l,[self.bet()],T+timedelta(seconds=2))
        for when in ('invalid',(T+timedelta(hours=2)).isoformat()):
            with self.assertRaises(ValueError): record_result(self.p,self.l,'game',[3,0],'fixture',when,T+timedelta(hours=1))

    def test_positive_roi_alone_never_confirms_second_half_advantage(self):
        versions=[]
        for i in range(100):
            t=T+timedelta(hours=2*i,seconds=2)
            capture(self.p,self.l,[self.bet(str(i),i+1,60,when=t)],t)
            versions.append((t+timedelta(seconds=1),copy.deepcopy(self.l)))
            record_result(self.p,self.l,str(i),[3,0],'fixture',(t+timedelta(hours=1)).isoformat(),t+timedelta(hours=1))
            versions.append((t+timedelta(hours=1,seconds=1),copy.deepcopy(self.l)))
        early=analyse(self.p,self.l,T+timedelta(days=15),versions)
        self.assertEqual(early['scientific_state'],'INCONCLUSIVO')
        final=analyse(self.p,self.l,T+timedelta(days=34),versions)
        self.assertEqual(final['sequential']['statuses']['H1'],'CONFIRMADO')
        self.assertEqual(final['sequential']['statuses']['H2'],'CONFIRMADO')
        self.assertEqual(final['sequential']['statuses']['delta'],'INCONCLUSIVO')
        self.assertEqual(final['sequential']['games']['delta'],100)
    def test_same_score_new_source_is_corroboration_not_correction(self):
        capture(self.p,self.l,[self.bet()],T+timedelta(seconds=2))
        record_result(self.p,self.l,'game',[3,0],'one',(T+timedelta(hours=1)).isoformat(),T+timedelta(hours=1))
        record_result(self.p,self.l,'game',[3,0],'two',(T+timedelta(hours=2)).isoformat(),T+timedelta(hours=2))
        self.assertFalse(self.l['events'][-1]['correction'])
