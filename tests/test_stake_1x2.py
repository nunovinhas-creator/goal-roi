import unittest
from scangoal.parser import settle,selection_1x2

class Stake1X2(unittest.TestCase):
    def test_canonical_selections_and_market_case(self):
        for market in ('1X2','1x2'):
            for score,side in [([2,1],'1'),([1,2],'2'),([1,1],'X')]:
                for selection in ('1','X','2'):
                    self.assertEqual(settle(market,selection,score),1 if selection==side else -1)
    def test_exact_home_away_and_draw(self):
        for selection,score in [('Alpha',[3,1]),('Beta',[0,2]),('X',[1,1]),('Draw',[1,1])]:
            self.assertEqual(settle('1x2',selection,score,['Alpha','Beta']),1)
        self.assertEqual(settle('1X2',' beta ',[0,2],['Alpha','Beta']),1)
    def test_unknown_partial_similar_and_ambiguous_stay_pending(self):
        for selection in ('Al','Alpha FC','Alph','unknown','',None,1,[],{}):
            self.assertIsNone(settle('1X2',selection,[3,1],['Alpha','Alpha United']))
        self.assertIsNone(settle('1X2','Alpha',[3,1],['Alpha','Alpha']))
        self.assertIsNone(settle('1X2','Alpha',[3,1],['Alpha','alpha']))
        for teams in (None,[],['Alpha'],['Alpha',None],'Alpha'):
            self.assertIsNone(selection_1x2('Alpha',teams))
    def test_complete_team_name_does_not_match_other_team_prefix(self):
        self.assertEqual(settle('1x2','Alpha',[3,1],['Alpha','Alpha United']),1)
        self.assertEqual(settle('1x2','Alpha United',[1,3],['Alpha','Alpha United']),1)
    def test_seven_historical_bets_and_1470_units(self):
        fixtures=[(['Norway','Wales'],'Norway',[3,1],1.40),(['Angola','South Sudan'],'Angola',[2,0],1.75),
                  (['Spain','England'],'England',[2,3],4.10),(['Germany','Netherlands'],'Germany',[5,0],1.85),
                  (['Granada','Las Palmas'],'Granada',[1,0],1.98),(['Belarus','Albania'],'Albania',[0,2],1.91),
                  (['Switzerland','Scotland'],'Switzerland',[4,0],1.71)]
        uplift=0
        for teams,selection,ft,odds in fixtures:
            self.assertEqual(settle('1x2',selection,ft,teams),1)
            uplift+=(odds-1)-(-1)
        self.assertAlmostEqual(uplift,14.70)
    def test_other_markets_non_regression(self):
        cases=[('Asian Total','Over 2.25',[2,0],-.5),('Asian Total','Under 2.25',[2,0],.5),
               ('Asian Handicap','Alpha (-0.25)',[1,1],-.5),('Double Chance','Alpha or Draw',[1,1],1),
               ('Both Teams To Score','Yes',[2,1],1),('Both Teams To Score','No',[2,0],1)]
        for market,selection,ft,want in cases:
            self.assertEqual(settle(market,selection,ft,['Alpha','Beta']),want)

    def test_unknown_1x2_never_falls_back_to_bot_mark(self):
        from scangoal import report
        m=dict(type='STAKE_LOAD',phase='pre',bet=True,odds=2.,market='1X2',selection='Unknown',match_id='g',
               message_id=1,date_utc='2026-11-17T12:00:00+00:00',kickoff_utc='2026-11-17T13:00:00+00:00',collected_at_utc='2026-11-17T12:00:01+00:00',teams=['Alpha','Beta'])
        for mark in (True,False):
            bet=report.build_bets([m],{'g':[2,0]},{report.key_of(m):mark},strict=False,
                                  chronology={'ft': {'g': '2026-11-17T16:00:00+00:00'}})[0]
            self.assertIsNone(bet['w'])
            self.assertEqual(bet['ft'],[2,0])
