"""Independent offline checks. Every message, result, account and send is synthetic."""
import asyncio
import contextlib
import io
import json
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scangoal import (brain, chronology, details_hl, equivalence, freshness, insights, llm, llm_eval,
                     notify, opportunity_forward, opportunity_results, parser, polymarket_alerts,
                     reader, report, results_bsd, results_hl, stake_alerts, store, watch)

NOW = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
TEAMS = ['Equipa Alfa', 'Equipa Beta']


def at(minutes=0, *, days=0):
    return NOW + timedelta(minutes=minutes, days=days)


def iso(minutes=0, *, days=0):
    return at(minutes, days=days).isoformat()


def telegram(mid='example-1', *, label='1', odds='2.00', result='', teams=TEAMS):
    return (
        '💷 Betfair · 20 nov 2026 15:00\n' + ' — '.join(teams) + ' · Example League\n'
        f'{label} · Seleção @{odds} · $1K\nmarket share 60% · whole 1X2 market $8K\n'
        f'https://scangoal.com/match/{mid}?lang=en' + (f'\n🏁 FT {result}' if result else '')
    )


def row(number, text, *, sent=-45, read=None, edited=None):
    return dict(message_id=number, date_utc=iso(sent), text=text, buttons=None,
                collected_at_utc=iso(sent if read is None else read),
                edit_date_utc=iso(edited) if edited is not None else None)


def bet(mid='example-1', **overrides):
    return dict(dict(match_id=mid, canonical_match_id=mid, key=('test', mid), type='BETFAIR',
                     phase='pre', group='BETFAIR/pre/mkt<10K', league='Example League',
                     teams=TEAMS, date_utc=iso(-30), signal_seen_utc=iso(-29), kickoff_utc=iso(),
                     market='1X2', selection='1', odds=2.0, odds_last=2.0, in_roi=True,
                     max_mult=1, n_signals=1, mkt_volume=8000, share=60, minute=None,
                     lead_min=30, no_source=False, w=None, ft=None), **overrides)


def state():
    return {'sent_total': 0, 'watch_sent': [], 'statuses': {}, 'hello': True,
            'migrations': {notify.STATUS_MIGRATION: {'at_utc': iso(-60)},
                           notify.FORMAT_MIGRATION: {'at_utc': iso(-60), 'sent_total': 0},
                           polymarket_alerts.ACTIVATION: {'at_utc': iso(-30)}}}


def dispatch_report(*, selection='Over 2.5', minute=-10, odds=1.9):
    signal = {'match_id': 'audit-fixture', 'match': 'Alfa — Beta', 'type': 'POLYMARKET',
              'phase': 'pre', 'market': 'Asian Total', 'market_scope': 'match_goals',
              'selection': selection, 'period': 'regulation', 'date_utc': iso(minute),
              'collected_at_utc': iso(minute + 1), 'signal_seen_utc': iso(minute + 1),
              'kickoff_utc': iso(30), 'odds': odds, 'share': 50, 'total': 100,
              'bet': True, 'had_ft_at_first_sight': False, 'edited_at_first_sight': False,
              'burst': False}
    return {'generated_at_utc': NOW.isoformat(), 'brain': {'watch': []},
            polymarket_alerts.REPORT_KEY: polymarket_alerts.candidates([signal], set(), NOW)}


def git(path, *args):
    result = subprocess.run(['git', '-C', str(path), *args], timeout=15,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise RuntimeError('Git local: ' + ' '.join(args[:2]))
    return result.stdout.decode().strip()


class Sandbox(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory(prefix='goal-roi-audit-'))
        self.directory = Path(directory)
        self.enterContext(patch.dict(os.environ, {'SCANGOAL_DATA_DIR': directory}))

    def write(self, name, value):
        (self.directory / name).write_text(json.dumps(value), encoding='utf-8')

    def remotes(self):
        remote, a, b = (self.directory / p for p in ('bare', 'runner-a', 'runner-b'))
        git(self.directory, 'init', '--bare', '--initial-branch=main', str(remote))
        git(self.directory, 'clone', str(remote), str(a))
        output = a / 'data'
        output.mkdir()
        s = notify.FileStore(output)
        s.save_state(state())
        s.log_path.touch()
        git(a, 'add', 'data')
        git(a, '-c', 'user.name=Audit', '-c', 'user.email=audit@example.invalid', 'commit', '-qm', 'seed')
        git(a, 'push', 'origin', 'main')
        git(self.directory, 'clone', str(remote), str(b))
        return remote, a, b

    def publisher(self, directory, index, observer=None, attempts=1):
        stamp = iso(index)
        def runner(args, **kwargs):
            result = subprocess.run(args, **kwargs,
                                    env=dict(os.environ, GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp))
            return observer(args, result) if observer is not None else result

        return notify.GitPublisher(directory, 'main',
                                   ['data/notify_state.json', 'data/notify_log.jsonl'],
                                   runner=runner, attempts=attempts)

    def test_A01_two_concurrent_runners_one_claim(self):
        """Both runners plan the same notice. Git rebases the second equivalent patch."""
        remote, a, b = self.remotes()
        barrier, first = threading.Barrier(2), threading.Event()
        attempts = []

        def run(index, checkout):
            local = notify.FileStore(checkout / 'data')
            publisher = self.publisher(checkout, index)

            def publish(key):
                barrier.wait(timeout=12)
                if index == 2 and not first.wait(timeout=12):
                    raise RuntimeError('Barrier timed out')
                try:
                    return publisher(key)
                finally:
                    if index == 1:
                        first.set()

            return notify.deliver(local, dispatch_report(), local.load_state(), NOW,
                                  lambda *_: attempts.append(index), 'synthetic', 'synthetic',
                                  publish=publish, clock=lambda: NOW)[1]

        with ThreadPoolExecutor(max_workers=2) as executor:
            tasks = [executor.submit(run, i, repo) for i, repo in enumerate((a, b), 1)]
            result = [task.result(timeout=35) for task in tasks]
        log, errors = notify.parse_log(git(remote, 'show', 'main:data/notify_log.jsonl').encode())
        intents = [item for item in log if item['event'] == 'intent']
        heads_equal = git(a, 'rev-parse', 'HEAD') == git(b, 'rev-parse', 'HEAD')
        self.assertEqual((len(attempts), len(intents), errors), (1, 1, 0),
                         f'tentativas por runner={result}, intenções no remoto={len(intents)}, HEAD igual={heads_equal}')

    def test_A02_git_lost_ack_is_not_retried_after_restart(self):
        remote, a, b = self.remotes()
        local, restarted = notify.FileStore(a / 'data'), notify.FileStore(b / 'data')
        sends = []

        def lose_ack(args, result):
            if 'push' in args:
                self.assertEqual(result.returncode, 0)
                return subprocess.CompletedProcess(args, 1, b'', b'ack lost after acceptance')
            return result

        notify.deliver(local, dispatch_report(), local.load_state(), NOW,
                       lambda *_: sends.append(True), 'synthetic', 'synthetic',
                       publish=self.publisher(a, 1, lose_ack), clock=lambda: NOW)
        git(b, 'pull', '--ff-only', 'origin', 'main')
        recovered, _ = notify.deliver(restarted, dispatch_report(), restarted.load_state(), NOW,
                                      lambda *_: sends.append(True), 'synthetic', 'synthetic',
                                      publish=self.publisher(b, 2), clock=lambda: NOW)
        self.assertEqual((len(sends), recovered['delivery_counts']['uncertain']), (0, 1))
        self.assertTrue(git(remote, 'log', '-1', '--format=%s').startswith('notify: intent '))

    def test_A03_telegram_lost_ack_does_not_repeat(self):
        memory, attempts = notify.MemoryStore(state()), []

        def telegram_accepted(*_):
            attempts.append(True)
            raise notify.DeliveryUncertain('synthetic lost ack')

        for _ in range(2):
            notify.deliver(memory, dispatch_report(), memory.load_state(), NOW, telegram_accepted,
                           'synthetic', 'synthetic', publish=lambda _: True, clock=lambda: NOW)
        self.assertEqual((len(attempts), memory.load_state()['delivery_counts']['uncertain']), (1, 1))

    def test_A04_same_selection_update_is_deduplicated(self):
        memory, attempts = notify.MemoryStore(state()), []
        for update in [dispatch_report(), dispatch_report(minute=-5, odds=2.1)]:
            notify.deliver(memory, update, memory.load_state(), NOW, lambda *_: attempts.append(True),
                           'synthetic', 'synthetic', publish=lambda _: True, clock=lambda: NOW)
        self.assertEqual(len(attempts), 1)

    def test_A05_new_selection_is_separate(self):
        one = dispatch_report()
        item = one[polymarket_alerts.REPORT_KEY][0]
        memory, texts = notify.MemoryStore(state()), []
        two = {'brain': {'watch': [dict(item, selection='Under 2.5',
                                      reasons=[{'id': 'audit', 'status': 'promissora'}])]}}
        for rep in [one, two]:
            notify.deliver(memory, rep, memory.load_state(), NOW, lambda *a: texts.append(a[-1]),
                           'synthetic', 'synthetic', publish=lambda _: True, clock=lambda: NOW)
        self.assertEqual(len(texts), 2)
        self.assertIn('NOVA SELEÇÃO', texts[-1])
        self.assertIn('Under 2,5', texts[-1])

    def test_A06_generic_notice_checks_expiry_after_publish(self):
        memory, sent = notify.MemoryStore(state()), []
        times = iter((NOW, at(31)))
        notify.deliver(memory, dispatch_report(), memory.load_state(), NOW, lambda *_: sent.append(True),
                       'synthetic', 'synthetic', publish=lambda _: True, clock=lambda: next(times))
        self.assertEqual((len(sent), memory.load_state()['delivery_counts']['expired']), (0, 1))

    def test_A07_stake_also_checks_expiry_after_publish(self):
        candidate = bet(type='STAKE_LOAD', phase='live', date_utc=iso(-29),
                        kickoff_utc=iso(-60), after_kickoff_min=31, total=500, odds=1.8)
        self.write(stake_alerts.STATE_NAME, {'format': stake_alerts.STATE_FORMAT, 'sent': {}, 'sent_total': 0})
        self.write(stake_alerts.LOG_NAME, {'format': stake_alerts.LOG_FORMAT})
        observed = []

        def accepted(_):
            observed.append(at(2))
            return True

        stake_alerts.deliver([candidate], NOW, {'TG_BOT_TOKEN': 'synthetic', 'TG_CHAT_ID': 'synthetic'},
                             sender=lambda *_: observed.append('sent'), publisher=accepted)
        self.assertNotIn('sent', observed, 'o aviso foi enviado depois de a sua idade atingir 31 min')

    def test_A08_reverting_an_edit_is_saved_as_a_new_version(self):
        original = row(1, telegram())
        modified = row(1, telegram(result='0:1'), read=120, edited=119)
        reverted = row(1, telegram(), read=122, edited=121)
        counts = [store.append_versions([event]) for event in (original, modified, reverted)]
        self.assertEqual(counts, [1, 1, 1], f'versões registadas por observação={counts}')

    def test_A09_button_only_change_is_versioned(self):
        original = row(1, telegram())
        edited = dict(original, buttons=[['Novo botão', None]], collected_at_utc=iso(-40),
                      edit_date_utc=iso(-41))
        self.assertEqual((store.append_versions([original]), store.append_versions([edited])), (1, 1))

    def test_A10_torn_append_keeps_previous_complete_rows_readable(self):
        store.append_versions([row(1, telegram())])
        with (self.directory / 'messages.jsonl').open('a', encoding='utf-8') as log:
            log.write('{"message_id":2,')
        try:
            found = store.load_seen(self.directory / 'messages.jsonl')[1]
            messages = len(report.load()[0])
        except ValueError as exc:
            self.fail(f'o coletor/relatório parou no registo parcial: {type(exc).__name__}')
        self.assertEqual((found, messages), (1, 1))

    def test_A11_later_siblings_do_not_change_historical_burst(self):
        store.append_versions([row(i, telegram(f'example-{i}')) for i in range(1, 5)])
        before = report.build(now=at(-40))['brain']['eligibility']['eligible']
        store.append_versions([row(5, telegram('example-5'), read=-35)])
        after = report.build(now=at(-40))['brain']['eligibility']['eligible']
        self.assertEqual((before, after), (4, 4), f'coorte lida até ao mesmo corte: {before} -> {after}')

    def test_A12_legacy_result_never_crosses_a_reused_match_id(self):
        first = row(1, telegram(), read=-44)
        later = row(2, telegram(teams=['Equipa Gama', 'Equipa Delta'], label='2'), sent=40, read=41)
        result = row(2, telegram(teams=['Equipa Gama', 'Equipa Delta'], label='2', result='0:1'),
                     sent=40, read=200, edited=199)
        store.append_versions([first, later, result])
        rep = report.build(now=at(220))
        earlier = next(item for item in rep['recent_bets'] if item['date_utc'] == first['date_utc'])
        self.assertIsNone(earlier['w'], f'o resultado do outro jogo liquidou a primeira aposta como {earlier["w"]}')

    def test_A13_legacy_full_time_note_checks_fixture_time(self):
        first = row(1, telegram(), read=-44)
        note = row(2, '🏁 Full time: Equipa Alfa 1:0 Equipa Beta', sent=-30, read=-29)
        store.append_versions([first, note])
        rep = report.build(now=at(10))
        self.assertEqual(rep['settled_bets'], 0, 'uma nota publicada 30 min antes do início liquidou a aposta')

    def test_A14_first_result_is_not_frozen_as_operational_truth(self):
        info = dict(ft=[1, 0], bsd_id=4, event_date=iso())
        archive = {'results': {}}
        opportunity_results.remember_provider_results(archive, {'example-1': info}, at(130))
        opportunity_results.remember_provider_results(archive, {'example-1': dict(info, ft=[0, 1])}, at(160))
        self.assertEqual(archive['results']['example-1']['ft'], [0, 1],
                         'a segunda observação só entra em ft_versions; o snapshot principal continua 1–0')

    def test_A15_conflict_resolution_is_not_backdated(self):
        evidence = [opportunity_results.collected('a', 1, [2, 1], iso(120), kickoff=iso()),
                    opportunity_results.collected('b', 1, [0, 0], iso(130), kickoff=iso()),
                    opportunity_results.collected('b', 1, [2, 1], iso(150), kickoff=iso())]
        self.assertTrue(opportunity_results.resolve(evidence, kickoff=NOW, as_of=at(140)).conflict)
        messages = [bet(canonical_match_id='example-1', collected_at_utc=iso(-29), bet=True)]
        snapshot = next(report.research_bets(messages, {'example-1': evidence}, at(160)))
        historical = chronology.as_of_view([snapshot], at(140))[0]
        self.assertIsNone(historical['w'],
                          f'o intervalo em conflito passou a mostrar w={historical["w"]} com disponibilidade {snapshot["result_available_utc"]}')

    def test_A16_late_ft_correction_keeps_forward_payoff_frozen(self):
        two_ft = [opportunity_results.collected('telegram', 1, [2, 1], iso(120), published=iso(119)),
                  opportunity_results.collected('telegram', 1, [0, 0], iso(1, days=4), published=iso(0, days=4))]
        signal = {'kickoff_utc': iso(), 'odds': 2.0}
        before = opportunity_forward.payoff(signal, two_ft, at(0, days=3))
        after = opportunity_forward.payoff(signal, two_ft, at(30, days=4))
        self.assertEqual(before[:3], after[:3])
        self.assertEqual((after[0], after[-1]), ('win', 'ft_confirmed'))

    def test_A17_quarantine_does_not_rewrite_certification_admissions(self):
        first = bet('first', w=-1.0, result_available_utc=iso(130),
                    match_result_first_seen_utc=iso(130))
        second = bet('second', w=1.0, date_utc=iso(20), signal_seen_utc=iso(21), kickoff_utc=iso(40),
                     result_available_utc=iso(160), match_result_first_seen_utc=iso(160))
        control = brain.confirmation([first, second], at(-60), 0.025, at(200))
        changed = brain.confirmation([dict(first, w=None, result_quarantined=True), second],
                                     at(-60), 0.025, at(200))
        self.assertEqual((control['skipped']['overlap'], changed['skipped']['overlap']), (1, 1),
                         f'o resultado futuro mudou a decisão de sobreposição; e={control["e_value"]} -> {changed["e_value"]}')

    def test_A18_unknown_result_is_not_in_a_past_view(self):
        later = bet(w=1.0, ft=[1, 0], result_known_utc=None, result_known_source='manual')
        [historical] = chronology.as_of_view([later], at(-20))
        self.assertIsNone(historical['w'], 'uma aposta com resultado sem hora permaneceu liquidada antes do início')

    def test_A19_provider_must_match_competition_as_well_as_names_and_time(self):
        target = {'teams': TEAMS, 'date_utc': iso(-40), 'kickoff_utc': iso(), 'league': 'League A'}
        event = {'id': 4, 'home_team': TEAMS[0], 'away_team': TEAMS[1], 'status': 'finished',
                 'event_date': iso(25), 'league': 'League B', 'home_score': 0, 'away_score': 1}
        found, _ = results_bsd.match_events({'example-1': target}, [event], {})
        self.assertFalse(found, 'o encontro da outra competição foi atribuído e liquidado')

    def test_A20_invalid_asian_and_btts_selections_are_not_wins_or_losses(self):
        results = [parser.settle('Asian Total', 'Other 2.5', [1, 0]),
                   parser.settle('Both Teams To Score', 'Unknown', [1, 0]),
                   parser.settle('Asian Handicap', 'Equipa Alfa (+0.30)', [0, 0], TEAMS)]
        self.assertEqual(results, [None, None, None], f'liquidações inválidas={results}')

    def test_A21_valid_market_payout_reference_table(self):
        cases = [('1X2', 'Equipa Alfa', [1, 0], 1), ('1X2', 'Equipa', [1, 0], None),
                 ('1X2', 'X', [0, 0], 1), ('Both Teams To Score', 'yes', [1, 1], 1),
                 ('Asian Total', 'Over 2.25', [1, 1], -0.5),
                 ('Asian Total', 'Over 2.75', [2, 1], 0.5),
                 ('Asian Handicap', 'Equipa Alfa (-0.25)', [1, 1], -0.5),
                 ('Asian Handicap', 'Equipa Alfa (+0.25)', [1, 1], 0.5),
                 ('Asian Handicap', 'Equipa Alfa (-1)', [2, 1], 0)]
        for market, selection, score, expected in cases:
            with self.subTest(market=market, selection=selection):
                self.assertEqual(parser.settle(market, selection, score, TEAMS), expected)

    def test_A22_fulltime_status_is_fail_closed_for_extra_time_and_abandonment(self):
        for status in ('Finished after extra time', 'Finished after penalties', 'Abandoned', 'Postponed', 'Awarded'):
            event = results_hl.to_event({'id': 7, 'state': {'description': status, 'score': {'current': '1 - 0'}}})
            self.assertIsNone(results_bsd.score_of(event))
        self.assertIsNone(results_bsd.score_of({'id': 7, 'status': 'finished', 'abandoned': True,
                                                'home_score': 0, 'away_score': 1}))

    def test_A23_inferred_90_score_checks_goal_timeline(self):
        def goal(minute, name):
            return {'type': 'goal', 'time': minute, 'team': {'name': name}}
        game = {'homeTeam': {'name': TEAMS[0]}, 'awayTeam': {'name': TEAMS[1]},
                'state': {'description': 'Finished after extra time', 'score': {'current': '2 - 1'}},
                'events': [goal('120+2', TEAMS[0]), goal('70', TEAMS[1]), goal('90+4', TEAMS[0])]}
        parsed = details_hl.to_details(game)
        self.assertTrue(parsed['consistent'])
        self.assertEqual(parsed['score_90'], [1, 1])

    def test_A24_roi_and_hit_rate_diverge_at_heterogeneous_prices(self):
        rows = [bet('high', odds=20.0, w=1.0)] + [bet(f'low-{i}', odds=1.1, w=-1.0) for i in range(9)]
        pnl = report.block(rows)['roi']['odd-0%']['roi']
        hits, misses, threshold, _ = insights.clustered(rows)
        self.assertEqual(pnl, 1.0)
        self.assertLess(hits / (hits + misses), threshold)
        self.assertAlmostEqual(threshold, (1 / 20 + 9 / 1.1) / 10)

    def test_A25_payout_denominators_count_pushes_and_split_stakes(self):
        unit = [bet('one', odds=2.0, w=0.5), bet('two', odds=2.0, w=-0.5),
                bet('three', odds=2.0, w=0.0), bet('four')]
        block = report.block(unit)
        self.assertEqual((block['bets'], block['settled'], block['pushes'], block['units']), (4, 3, 1, 0))
        self.assertEqual(insights.effective(unit[:3]), (0.5, 0.5))
        self.assertEqual(block['roi']['odd-2%']['roi'], -0.0067)

    def test_A26_llm_is_blocked_without_authorization_or_durable_budget(self):
        env = {'CLOUDFLARE_ACCOUNT_ID': 'a' * 32, 'MASTER_BRAIN_LLM_API_KEY': 'synthetic'}
        with patch.object(llm, 'urlopen') as remote:
            self.assertIsNone(llm.run(env=env, now=NOW))
            self.assertIsNone(llm.run(env=dict(env, GOAL_ROI_LLM_FREE_CONFIRMED='true'), now=NOW))
            remote.assert_not_called()

    def test_A27_llm_note_cannot_assert_unsupported_certainty(self):
        raw = json.dumps({'notes': [{'id': 'audit', 'note': 'Este padrão garante ganhos futuros.'}]})
        accepted = llm.accept_notes(raw, {'audit'})
        self.assertFalse(accepted, 'uma conclusão categórica sem evidência passou o filtro de linguagem')

    def test_A28_conflicting_results_are_quarantined_in_forward_study(self):
        evidence = [opportunity_results.collected('a', 1, [2, 1], iso(120), kickoff=iso()),
                    opportunity_results.collected('b', 1, [0, 0], iso(130), kickoff=iso())]
        self.assertEqual(opportunity_forward.payoff({'kickoff_utc': iso(), 'odds': 2.0}, evidence, at(145)),
                         ('quarantined', None, None, None))

    def test_A29_equal_timestamps_do_not_depend_on_provider_order(self):
        score = {'id': 4, 'home_team': TEAMS[0], 'away_team': TEAMS[1], 'status': 'finished',
                 'event_date': iso(), 'home_score': 1, 'away_score': 0}
        contradiction = dict(score, home_score=0, away_score=1)
        a, b = {'results': {}}, {'results': {}}
        for archive, scores in ((a, [score, contradiction]), (b, [contradiction, score])):
            single = dict(ft=[scores[0]['home_score'], scores[0]['away_score']],
                          bsd_id=4, event_date=NOW.isoformat(timespec='minutes'),
                          home=TEAMS[0], away=TEAMS[1])
            results_bsd.capture_result_versions(archive, {'example-1': single}, scores, at(120))
        res = [opportunity_results.resolve(opportunity_results.provider_observations(archive, 'bsd')['example-1'],
                                           kickoff=NOW, as_of=at(130)) for archive in (a, b)]
        self.assertEqual([entry.conflict for entry in res], [True, True])
        self.assertEqual(res[0], res[1])

    def test_A30_infinite_odd_is_never_a_bet(self):
        parsed = parser.parse(telegram(odds='9' * 320))
        self.assertFalse(parsed['bet'], 'registo de preço infinito entrou no conjunto de apostas')

    def test_A31_valid_stake_team_name_does_not_break_full_report(self):
        text = (
            '💸 Stake $1K\nExample League · 20 nov 2026 15:00\nEquipa Alfa — Equipa Beta\n'
            '📊 1X2 → Equipa Alfa @ 1.80\n🧾 2 bets · biggest $1K\n'
            'https://scangoal.com/match/example-1?lang=en'
        )
        store.append_versions([row(1, text)])
        try:
            rep = report.build(now=NOW)
        except KeyError:
            self.fail('a seleção 1X2 pelo nome completo é aceite no parser, mas quebra report.label')
        self.assertEqual(rep['unique_bets'], 1)

    def test_A32_untrusted_result_state_cannot_be_silently_discarded(self):
        (self.directory / 'results_bsd.json').write_text('{"results":', encoding='utf-8')
        with self.assertRaises(ValueError):
            results_bsd.load_result_state(self.directory / 'results_bsd.json')

    def test_A33_fully_qualified_live_alert_can_expire(self):
        signal = bet(phase='live', date_utc=iso(-29), kickoff_utc=iso(-60))
        self.assertTrue(freshness.current(signal, NOW))
        self.assertFalse(freshness.current(signal, at(2)))

    def test_A34_future_registry_cannot_influence_earlier_brain(self):
        signal = bet()
        conditions = [['Seleção', 'equipa da casa']]
        hid = brain.hyp_id('Betfair', map(tuple, conditions))
        registry = {hid: {'conditions': conditions, 'family': 'Betfair',
                          'source': 'gerador', 'born_utc': iso(60)}}
        output = brain.build([signal], registry, 300, as_of=NOW)
        self.assertEqual(output['hypotheses'], 0, 'a vista histórica inclui hipóteses que ainda não tinham sido registadas')

    def test_A35_decision_cannot_happen_after_known_kickoff_for_prematch(self):
        late = bet(date_utc=iso(-10), signal_seen_utc=iso(1))
        self.assertFalse(chronology.decidable(late), 'um sinal PRE lido após o início é aceite pela regra de 30 min')

    def test_A36_two_distinct_markets_do_not_equate_identical_scores(self):
        r = [{'family': 'Betfair', 'n': 9, 'hits': 7, 'id': 'first', 'game_set': 'first'},
             {'family': 'Betfair', 'n': 9, 'hits': 7, 'id': 'second', 'game_set': 'second'}]
        rep = {'brain': {'presentation': {'hypotheses': {h['id']: h for h in r}}}}
        self.assertEqual(len(notify.groups_of(rep, ['first', 'second'])), 2)

    def test_A37_two_different_intent_commits_do_not_both_send_on_conflict(self):
        _, a, b = self.remotes()
        sends = []
        for index, directory in enumerate((a, b), 1):
            local = notify.FileStore(directory / 'data')
            notify.deliver(local, dispatch_report(), local.load_state(), at(index), lambda *_: sends.append(index),
                           'synthetic', 'synthetic', publish=self.publisher(directory, index),
                           clock=lambda: at(index))
        self.assertEqual(sends, [1])

    def test_A38_health_notice_has_a_real_one_hour_limit(self):
        emissions = []
        env = {'TG_BOT_TOKEN': 'synthetic', 'TG_CHAT_ID': 'synthetic'}
        for when in (NOW, at(2)):
            notify.failure(lambda *_: emissions.append(True), when, env)
        self.assertEqual(len(emissions), 1, 'dois erros dentro de uma hora originaram dois avisos')

    def test_A39_identical_selection_names_in_different_markets_are_distinct(self):
        original = dispatch_report()
        item = original[polymarket_alerts.REPORT_KEY][0]
        alternate = {'brain': {'watch': [dict(item, market='1st Half - Asian Total',
                                            reasons=[{'id': 'audit', 'status': 'promissora'}])]}}
        memory, sends = notify.MemoryStore(state()), []
        for rep in (original, alternate):
            notify.deliver(memory, rep, memory.load_state(), NOW, lambda *_: sends.append(True),
                           'synthetic', 'synthetic', publish=lambda _: True, clock=lambda: NOW)
        self.assertEqual(len(sends), 2, 'a chave legada descartou uma aposta num mercado diferente')

    def test_A40_redundancy_rejects_two_proposals_with_equal_support(self):
        matches = [bet(f'home-{i}', league='League A') for i in range(5)]
        other = bet('other', league='League B', selection='2')
        one = {'family': 'Betfair', 'conditions': [['Liga', 'League A']]}
        two = {'family': 'Betfair', 'conditions': [['Seleção', 'equipa da casa']]}
        signals = matches + [other]
        accepted, rejected = llm.accept(json.dumps({'hypotheses': [one, two]}), llm.vocabulary(signals), {},
                                       support=lambda *_: 5, redundancy=equivalence.Redundancy(signals, {}))
        self.assertEqual((len(accepted), rejected), (1, 1))

    def test_A41_recovery_archives_an_old_page_without_live_replay(self):
        original = row(90, telegram('recent'))
        store.append_versions([original])
        live = self.directory / 'messages.jsonl'
        before = live.read_bytes()
        page_state = {'intervals': [{'after_id': 1, 'before_id': 90, 'cursor': 90, 'status': 'pending'}]}

        async def found(_, __, limit, stamp, **bounds):
            self.assertEqual((limit, bounds), (100, {'min_id': 1, 'offset_id': 90}))
            return [row(40, telegram('missed'), read=-20)], 40, {40}

        with patch.object(reader, 'fetch', found):
            asyncio.run(reader.recover_page(None, None, self.directory, page_state, iso(-20)))
        self.assertEqual(live.read_bytes(), before)
        self.assertEqual(page_state['intervals'][0]['status'], 'available_history_checked')
        self.assertEqual(len(list((self.directory / 'collection_backfill').glob('*.json'))), 1)

    def test_A42_an_outage_does_not_permanently_mark_confirmed_rejection_as_sent(self):
        memory, attempts = notify.MemoryStore(state()), []

        def unavailable(*_):
            attempts.append('offline')
            raise notify.DeliveryFailed('synthetic 429')

        for sender, when in ((unavailable, NOW), (lambda *_: attempts.append('recovered'), at(2))):
            notify.deliver(memory, dispatch_report(), memory.load_state(), when, sender,
                           'synthetic', 'synthetic', publish=lambda _: True, clock=lambda: when)
        self.assertEqual(attempts, ['offline', 'recovered'],
                         'o Telegram voltou enquanto a aposta ainda estava válida; o erro confirmado não teve nova tentativa')

    def test_A43_fast_reads_refresh_after_no_new_versions(self):
        called = []

        async def read():
            return 0

        async def skip_sleep(_):
            pass

        asyncio.run(watch.rounds(read, lambda: called.append(True), sleep=skip_sleep,
                                 total=120, every=120))
        self.assertEqual(len(called), 1, 'uma leitura sem mudanças não atualizou a frescura do relatório')

    def test_A44_quarantine_does_not_remove_precommitted_llm_trial(self):
        candidate = bet(w=None, result_quarantined=True, match_result_first_seen_utc=iso(130))
        trial = llm_eval.admitted([candidate], at(-60))
        self.assertIn(candidate['match_id'], trial,
                      'conflito conhecido após a decisão retirou a unidade de avaliação, antes da imputação')

    def test_A45_bets_without_fixture_ids_are_not_merged(self):
        raw = telegram().split('\nhttps:')[0]
        other = telegram(teams=['Equipa Gama', 'Equipa Delta']).split('\nhttps:')[0]
        store.append_versions([row(1, raw), row(2, other, sent=-42)])
        rep = report.build(now=NOW)
        self.assertEqual(rep['unique_bets'], 0,
                         'dois jogos sem identidade produziram uma aposta agregada com match_id=None')

    def test_A46_goal_before_signal_cannot_win_next_goal_window(self):
        msg = dict(bet(type='GOAL_BREWING', market=None, selection=None, odds=None, window=5,
                       minute=70, score_at_signal=[0, 0], message_id=1, bot_prob=80, usual_prob=25),
                   bet=False, collected_at_utc=iso(-29), had_ft_at_first_sight=False,
                   edited_at_first_sight=False, burst=False)
        info = {'consistent': True, 'goals': [[45, 0, 'home']], 'final': [1, 0], 'fetched_at_utc': iso(120)}
        result = report.build_bets([msg], {}, strict=False, details={'example-1': info})[0]
        self.assertIsNone(result['w'], 'um golo no minuto 45 ganhou a janela de 5 min a partir do minuto 70')


class Summary(unittest.TestResult):
    def __init__(self):
        super().__init__()
        self.results = {}

    def addSuccess(self, test):
        super().addSuccess(test)
        self.results[test.id().rsplit('.', 1)[-1]] = ('PASSOU', '')

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.results[test.id().rsplit('.', 1)[-1]] = ('FALHOU', str(err[1]))

    def addError(self, test, err):
        super().addError(test, err)
        self.results[test.id().rsplit('.', 1)[-1]] = ('NÃO VERIFICADO', f'{type(err[1]).__name__}: {err[1]}')
        # Preserve a traceback locally when developing the audit.
        if '--trace' in sys.argv:
            print(self.errors[-1][1], file=sys.stderr)


if __name__ == '__main__':
    result = Summary()
    tests = unittest.defaultTestLoader.loadTestsFromTestCase(Sandbox)
    with contextlib.redirect_stdout(io.StringIO()):
        tests.run(result)
    for name, (status, evidence) in sorted(result.results.items()):
        message = ' '.join(evidence.split())
        print(f'{name.split("_")[1]} {status} {name.split("_", 2)[-1]}' + (f' — {message}' if message else ''))
    print('Totais', dict(sorted(Counter(status for status, _ in result.results.values()).items())))
    sys.exit(not result.wasSuccessful())
