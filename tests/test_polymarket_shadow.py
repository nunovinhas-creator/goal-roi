"""P1 — testes adversariais da observação sombra Polymarket (offline; nenhum pedido de rede)."""

import copy
import json
import os
import re
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from email.message import Message
from io import BytesIO
from urllib.request import HTTPSHandler, ProxyHandler
from urllib.response import addinfourl
from pathlib import Path
from unittest.mock import patch

from scangoal import polymarket_shadow as ps
from scripts import polymarket_shadow as cli

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / 'tests/fixtures/polymarket_p1/ceuta_almeria_20261123.json').read_text(encoding='utf-8'))
SOURCE = datetime.fromtimestamp(int(FIXTURE['book_over']['timestamp']) / 1000, timezone.utc)
RECEIVED = SOURCE + timedelta(seconds=1)
CONDITION = '0xcc8f08f954e303712669cdecc6382f7dede99a8f552474cf9786df8e54636b0e'
OVER = '26327110167391870994816581715576912324458716406795184584714962315551123010144'
UNDER = '22928013230054087688621356190323210936999045966899017945593315681279555668622'


def stamp(value):
    return value.isoformat()


def signal(**changes):
    base = {
        'type': 'POLYMARKET', 'phase': 'pre', 'market': 'Asian Total', 'selection': 'Over 2.5',
        'market_scope': 'match_goals', 'period': 'regulation', 'bet': True,
        'match_id': 'ad-ceuta-fc-vs-almeria-20261123', 'message_id': 32317,
        'teams': ['AD Ceuta FC', 'Almeria'], 'league': 'Spanish Segunda',
        'kickoff_utc': '2026-11-23T19:30+00:00', 'odds': 1.89, 'share': 100.0, 'total': 5300.0,
        'date_utc': stamp(RECEIVED - timedelta(seconds=90)),
        'collected_at_utc': stamp(RECEIVED - timedelta(seconds=60)),
    }
    base.update(changes)
    return base


class FakeRemote:
    """Simula Gamma e CLOB a partir das respostas reais; regista cada URL pedido."""

    def __init__(self, **overrides):
        self.data = copy.deepcopy(FIXTURE)
        self.data['search'] = {'events': [self.data['parent']], 'pagination': {}}
        self.data.update(overrides)
        self.urls = []
        self.headers = {'cf-cache-status': 'DYNAMIC'}
        self.received = RECEIVED
        self.fail = None

    def __call__(self, url):
        ps.guard_url(url)
        self.urls.append(url)
        if self.fail:
            raise ps.Refusal(self.fail)
        if '/public-search?' in url:
            payload = self.data['search']
        elif '/events/slug/' in url:
            payload = self.data['child']
        elif '/clob-markets/' in url:
            payload = self.data['clob_market']
        elif 'token_id=' + OVER in url:
            payload = self.data['book_over']
        elif 'token_id=' + UNDER in url:
            payload = self.data['book_under']
        else:
            raise AssertionError('unexpected url ' + url)
        return {'url': url, 'payload': payload, 'sha256': 'x', 'bytes': 1, 'headers': dict(self.headers),
                'requested_utc': self.received - timedelta(milliseconds=200), 'received_utc': self.received}


def contract_market(remote, slug='es2-ceu-alm-2026-11-23-total-2pt5'):
    return next(m for m in remote.data['child']['markets'] if m['slug'] == slug)


class PilotHappyPath(unittest.TestCase):
    def test_real_payloads_identify_contract_and_quote(self):
        remote = FakeRemote()
        obs = ps.observe(signal(), get=remote)
        self.assertEqual(obs['status'], 'observed', obs.get('reason'))
        self.assertEqual(obs['contract']['condition_id'], CONDITION)
        self.assertEqual(obs['contract']['token_over'], OVER)
        self.assertEqual(obs['contract']['slug'], 'es2-ceu-alm-2026-11-23-total-2pt5')
        self.assertTrue(obs['contract']['rules_regulation_90_plus_stoppage'])
        self.assertTrue(obs['contract']['rules_void_50_50'])
        self.assertEqual(obs['event']['teams'], ['AD Ceuta FC', 'UD Almería'])
        self.assertEqual(obs['orders_sent'], 0)
        self.assertFalse(obs['credentials_used'])
        self.assertEqual(obs['quote']['best_ask'], '0.53')
        self.assertTrue(obs['quote']['mirror_consistent'])
        self.assertEqual(obs['clob']['fee'], {'rate': '0.05', 'exponent': '1'})
        first = obs['quote']['fills'][0]
        self.assertTrue(first['filled'])
        self.assertEqual(first['net_decimal_odds'], '1.8435')
        self.assertEqual(obs['timing']['relation_to_signal'], 'contemporaneous')
        self.assertTrue(obs['timing']['evidence_for_signal'])

    def test_only_get_to_allowed_hosts(self):
        remote = FakeRemote()
        ps.observe(signal(), get=remote)
        hosts = {re.match(r'https://([^/]+)/', u).group(1) for u in remote.urls}
        self.assertEqual(hosts, {'gamma-api.polymarket.com', 'clob.polymarket.com'})
        self.assertFalse(any(k in u for u in remote.urls for k in ('order', 'auth', 'api-key')))

    def test_gamma_cached_prices_never_drive_the_quote(self):
        remote = FakeRemote()
        market = contract_market(remote)
        market.update(bestAsk=0.01, bestBid=0.009, lastTradePrice=0.99)
        obs = ps.observe(signal(), get=remote)
        self.assertEqual(obs['quote']['best_ask'], '0.53')
        self.assertEqual(obs['contract']['gamma_cached_quote_not_used']['bestAsk'], 0.01)


class CurrentVersusHistorical(unittest.TestCase):
    def relation(self, seen_offset, sent_offset=None):
        sent_offset = seen_offset - 30 if sent_offset is None else sent_offset
        sig = signal(collected_at_utc=stamp(RECEIVED + timedelta(seconds=seen_offset)),
                     date_utc=stamp(RECEIVED + timedelta(seconds=sent_offset)))
        return ps.observe(sig, get=FakeRemote())['timing']

    def test_late_quote_is_current_only(self):
        timing = self.relation(-121)
        self.assertEqual(timing['relation_to_signal'], 'current_only')
        self.assertFalse(timing['evidence_for_signal'])

    def test_five_hours_late_like_the_pilot(self):
        timing = self.relation(-5 * 3600)
        self.assertEqual(timing['relation_to_signal'], 'current_only')
        self.assertFalse(timing['evidence_for_signal'])

    def test_boundary_is_inclusive(self):
        self.assertTrue(self.relation(-120)['evidence_for_signal'])

    def test_quote_before_signal_is_not_evidence(self):
        timing = self.relation(5)
        self.assertEqual(timing['relation_to_signal'], 'before_signal_seen')
        self.assertFalse(timing['evidence_for_signal'])

    def test_seen_before_sent_is_unproven(self):
        timing = self.relation(-10, sent_offset=-5)
        self.assertEqual(timing['relation_to_signal'], 'signal_time_unproven')
        self.assertFalse(timing['evidence_for_signal'])

    def test_missing_signal_times_are_unproven(self):
        obs = ps.observe(signal(collected_at_utc=None), get=FakeRemote())
        self.assertFalse(obs['timing']['evidence_for_signal'])

    def test_after_kickoff_refused(self):
        remote = FakeRemote()
        remote.received = datetime(2026, 11, 23, 19, 30, tzinfo=timezone.utc)
        remote.data['book_over'] = dict(remote.data['book_over'], timestamp=str(int(remote.received.timestamp() * 1000)))
        obs = ps.observe(signal(collected_at_utc=stamp(remote.received)), get=remote)
        self.assertEqual((obs['status'], obs['reason']), ('refused', 'after_kickoff'))

    def test_source_timestamp_from_the_future_refused(self):
        remote = FakeRemote()
        remote.received = SOURCE - timedelta(seconds=3)
        obs = ps.observe(signal(), get=remote)
        self.assertEqual(obs['reason'], 'source_timestamp_in_future')

    def test_quiet_book_is_flagged_not_hidden(self):
        remote = FakeRemote()
        remote.received = SOURCE + timedelta(minutes=6)
        obs = ps.observe(signal(collected_at_utc=stamp(remote.received - timedelta(seconds=10))), get=remote)
        self.assertEqual(obs['status'], 'observed')
        self.assertTrue(obs['timing']['quiet_book'])

    def test_cached_book_refused(self):
        remote = FakeRemote()
        remote.headers = {'cf-cache-status': 'HIT'}
        self.assertEqual(ps.observe(signal(), get=remote)['reason'], 'book_served_from_cache')


class MatchIdentity(unittest.TestCase):
    def test_swapped_home_away_not_found(self):
        obs = ps.observe(signal(teams=['Almeria', 'AD Ceuta FC']), get=FakeRemote())
        self.assertEqual(obs['reason'], 'identity_not_found')

    def test_kickoff_one_minute_off_not_found(self):
        obs = ps.observe(signal(kickoff_utc='2026-11-23T19:31+00:00'), get=FakeRemote())
        self.assertEqual(obs['reason'], 'identity_not_found')

    def test_similar_names_are_not_matched(self):
        for teams in (['AD Ceuta FC', 'Almeria B'], ['Ceuta B', 'Almeria'], ['AD Ceuta FC', 'Almer']):
            with self.subTest(teams=teams):
                self.assertEqual(ps.observe(signal(teams=teams), get=FakeRemote())['reason'], 'identity_not_found')

    def test_polymarket_published_alias_is_exact(self):
        # A Gamma publica alias="Ceuta" para "AD Ceuta FC": igualdade exata, não semelhança.
        self.assertEqual(ps.observe(signal(teams=['Ceuta', 'Almeria']), get=FakeRemote())['status'], 'observed')

    def test_reviewed_alias_is_the_only_bridge(self):
        sig = signal(teams=['Ceuta Club', 'Almeria'])
        self.assertEqual(ps.observe(sig, get=FakeRemote())['reason'], 'identity_not_found')
        obs = ps.observe(sig, get=FakeRemote(), aliases={'Ceuta Club': ['AD Ceuta FC']})
        self.assertEqual(obs['status'], 'observed')

    def test_two_matching_events_are_ambiguous(self):
        remote = FakeRemote()
        twin = dict(remote.data['parent'], id=999, slug='duplicate')
        remote.data['search'] = {'events': [remote.data['parent'], twin]}
        self.assertEqual(ps.observe(signal(), get=remote)['reason'], 'identity_ambiguous')

    def test_null_search_result_is_not_found(self):
        remote = FakeRemote()
        remote.data['search'] = {'events': None, 'pagination': {}}
        self.assertEqual(ps.observe(signal(), get=remote)['reason'], 'identity_not_found')

    def test_non_object_search_is_invalid(self):
        remote = FakeRemote()
        remote.data['search'] = ['oops']
        self.assertEqual(ps.observe(signal(), get=remote)['reason'], 'search_invalid')

    def test_child_event_must_link_to_parent(self):
        remote = FakeRemote()
        remote.data['child'] = dict(remote.data['child'], parentEventId=1)
        self.assertEqual(ps.observe(signal(), get=remote)['reason'], 'child_event_not_linked')

    def test_child_event_with_other_game(self):
        remote = FakeRemote()
        remote.data['child'] = dict(remote.data['child'], gameId=1)
        self.assertEqual(ps.observe(signal(), get=remote)['reason'], 'child_event_identity_mismatch')

    def test_out_of_scope_signals_refused_before_any_request(self):
        for change in ({'selection': 'Under 2.5'}, {'phase': 'live'}, {'period': 'first_half'},
                       {'market_scope': 'team_goals'}, {'type': 'BETFAIR'}):
            with self.subTest(change=change):
                remote = FakeRemote()
                self.assertEqual(ps.observe(signal(**change), get=remote)['reason'], 'signal_not_in_scope')
                self.assertEqual(remote.urls, [])


class ContractRules(unittest.TestCase):
    def drop(self, remote, slug):
        remote.data['child']['markets'] = [m for m in remote.data['child']['markets'] if m['slug'] != slug]

    def test_first_half_and_team_totals_never_selected(self):
        remote = FakeRemote()
        self.drop(remote, 'es2-ceu-alm-2026-11-23-total-2pt5')
        self.assertEqual(ps.observe(signal(), get=remote)['reason'], 'contract_not_found')

    def test_duplicate_full_match_line_is_ambiguous(self):
        remote = FakeRemote()
        remote.data['child']['markets'].append(dict(contract_market(remote), slug='copy'))
        self.assertEqual(ps.observe(signal(), get=remote)['reason'], 'contract_ambiguous')

    def test_reversed_outcomes_choose_the_over_token(self):
        remote = FakeRemote()
        market = contract_market(remote)
        market['outcomes'] = '["Under", "Over"]'
        market['clobTokenIds'] = json.dumps([UNDER, OVER])
        obs = ps.observe(signal(), get=remote)
        self.assertEqual(obs['contract']['token_over'], OVER)
        self.assertEqual(obs['status'], 'observed')

    def test_mislabelled_tokens_caught_by_clob(self):
        remote = FakeRemote()
        contract_market(remote)['clobTokenIds'] = json.dumps([UNDER, OVER])
        self.assertEqual(ps.observe(signal(), get=remote)['reason'], 'clob_tokens_mismatch')

    def test_rules_without_regulation_clause_refused(self):
        remote = FakeRemote()
        market = contract_market(remote)
        market['description'] = market['description'].replace(ps.RULE_REGULATION, 'whole game including extra time')
        self.assertEqual(ps.observe(signal(), get=remote)['reason'], 'rules_not_regulation_over25')

    def test_rules_with_other_threshold_refused(self):
        remote = FakeRemote()
        market = contract_market(remote)
        market['description'] = market['description'].replace('3 or more goals', '4 or more goals')
        self.assertEqual(ps.observe(signal(), get=remote)['reason'], 'rules_not_regulation_over25')

    def test_neg_risk_and_bad_condition_refused(self):
        for change, reason in (({'negRisk': True}, 'neg_risk_market'),
                               ({'conditionId': '0xabc'}, 'condition_id_invalid'),
                               ({'outcomes': '["Yes", "No"]'}, 'outcomes_invalid'),
                               ({'clobTokenIds': '["1", "2"]'}, 'token_invalid'),
                               ({'gameStartTime': '2026-11-23 20:30:00+00'}, 'kickoff_mismatch'),
                               ({'line': 'NaN'}, 'contract_not_found'),
                               ({'line': True}, 'contract_not_found')):
            with self.subTest(change=change):
                remote = FakeRemote()
                contract_market(remote).update(change)
                self.assertEqual(ps.observe(signal(), get=remote)['reason'], reason)

    def test_market_not_accepting_orders(self):
        for target, change in (('market', {'acceptingOrders': False}), ('market', {'closed': True}),
                               ('clob', {'ao': False})):
            with self.subTest(change=change):
                remote = FakeRemote()
                (contract_market(remote) if target == 'market' else remote.data['clob_market']).update(change)
                self.assertEqual(ps.observe(signal(), get=remote)['reason'], 'market_not_accepting_orders')

    def test_clob_condition_and_kickoff(self):
        for change, reason in (({'c': '0x' + '0' * 64}, 'clob_condition_mismatch'),
                               ({'gst': '2026-11-24T19:30:00Z'}, 'clob_kickoff_mismatch'),
                               ({'mts': '0'}, 'clob_params_invalid')):
            with self.subTest(change=change):
                remote = FakeRemote()
                remote.data['clob_market'].update(change)
                self.assertEqual(ps.observe(signal(), get=remote)['reason'], reason)

    def test_unknown_fee_schedule_never_assumed_zero(self):
        remote = FakeRemote()
        remote.data['clob_market'].pop('fd')
        obs = ps.observe(signal(), get=remote)
        self.assertEqual(obs['status'], 'observed')
        for row in obs['quote']['fills']:
            self.assertIsNone(row['net_decimal_odds'])
            self.assertEqual(row['reason'], 'fee_schedule_unknown')


class BookIntegrity(unittest.TestCase):
    def reason(self, **changes):
        remote = FakeRemote()
        remote.data['book_over'] = dict(remote.data['book_over'], **changes)
        return ps.observe(signal(), get=remote).get('reason')

    def test_identity_of_book(self):
        self.assertEqual(self.reason(asset_id=UNDER), 'book_identity_mismatch')
        self.assertEqual(self.reason(market='0x' + '1' * 64), 'book_identity_mismatch')

    def test_crossed_and_locked(self):
        self.assertEqual(self.reason(bids=[{'price': '0.53', 'size': '10'}]), 'book_crossed_or_locked')
        self.assertEqual(self.reason(bids=[{'price': '0.60', 'size': '10'}]), 'book_crossed_or_locked')

    def test_hostile_levels(self):
        cases = {
            'book_off_tick': [{'price': '0.535', 'size': '10'}],
            'book_level_out_of_range': [{'price': '1', 'size': '10'}],
            'invalid_number': [{'price': 'NaN', 'size': '10'}],
            'book_duplicate_level': [{'price': '0.6', 'size': '1'}, {'price': '0.60', 'size': '2'}],
        }
        for reason, asks in cases.items():
            with self.subTest(reason=reason):
                self.assertEqual(self.reason(asks=asks), reason)
        self.assertEqual(self.reason(asks=[{'price': '0.6', 'size': '-1'}]), 'book_level_out_of_range')
        self.assertEqual(self.reason(asks=[{'price': True, 'size': '1'}]), 'invalid_number')
        self.assertEqual(self.reason(asks=[{'price': '0.6', 'size': 'Infinity'}]), 'invalid_number')
        self.assertEqual(self.reason(asks=[]), 'book_no_asks')
        self.assertEqual(self.reason(asks='0.5'), 'book_invalid')

    def test_timestamp_and_tick(self):
        self.assertEqual(self.reason(timestamp='-1'), 'book_timestamp_invalid')
        self.assertEqual(self.reason(timestamp=1795456287636), 'book_timestamp_invalid')
        self.assertEqual(self.reason(tick_size='0.001'), 'book_tick_mismatch')

    def test_unsorted_levels_still_take_cheapest_first(self):
        remote = FakeRemote()
        asks = remote.data['book_over']['asks']
        remote.data['book_over'] = dict(remote.data['book_over'], asks=list(reversed(asks)))
        self.assertEqual(ps.observe(signal(), get=remote)['quote']['best_ask'], '0.53')

    def test_mirror_mismatch_is_reported(self):
        remote = FakeRemote()
        remote.data['book_under'] = dict(remote.data['book_under'], bids=[{'price': '0.40', 'size': '10'}])
        self.assertFalse(ps.observe(signal(), get=remote)['quote']['mirror_consistent'])


class FillAndFees(unittest.TestCase):
    SCHEDULE = {'rate': Decimal('0.05'), 'exponent': Decimal('1')}

    def test_fee_matches_documented_table(self):
        # docs.polymarket.com/trading/fees: 100 acções a 0,50 → 1,25 USDC; a 0,90 → 0,45 USDC
        self.assertEqual(ps.level_fee(Decimal(100), Decimal('0.5'), self.SCHEDULE), Decimal('1.25'))
        self.assertEqual(ps.level_fee(Decimal(100), Decimal('0.9'), self.SCHEDULE).quantize(Decimal('0.01')),
                         Decimal('0.45'))

    def test_multi_level_vwap(self):
        asks = [(Decimal('0.50'), Decimal('10')), (Decimal('0.60'), Decimal('100'))]
        row = ps.fill(asks, Decimal('11'), self.SCHEDULE, Decimal('5'))
        # 5 USDC → 10 acções a 0,50; 6 USDC → 10 acções a 0,60; VWAP = 11/20
        self.assertEqual(row['shares'], '20.0000')
        self.assertEqual(row['vwap'], '0.550000')
        self.assertEqual(row['levels_used'], 2)
        self.assertEqual(row['worst_price'], '0.60')
        fee = Decimal('10') * Decimal('0.05') * Decimal('0.25') + Decimal('10') * Decimal('0.05') * Decimal('0.24')
        self.assertEqual(row['fee_usdc'], str(fee.quantize(Decimal('0.00001'))))
        self.assertLess(Decimal(row['net_decimal_odds']), Decimal(row['gross_decimal_odds']))

    def test_insufficient_depth_is_not_filled(self):
        row = ps.fill([(Decimal('0.5'), Decimal('10'))], Decimal('100'), self.SCHEDULE, Decimal('5'))
        self.assertFalse(row['filled'])
        self.assertEqual(row['reason'], 'insufficient_depth')
        self.assertEqual(row['depth_usdc'], '5.0')

    def test_below_minimum_order(self):
        row = ps.fill([(Decimal('0.5'), Decimal('100'))], Decimal('1'), self.SCHEDULE, Decimal('5'))
        self.assertEqual(row['reason'], 'below_min_order_size')

    def test_fee_rounds_up(self):
        row = ps.fill([(Decimal('0.5'), Decimal('100'))], Decimal('5.000001'), self.SCHEDULE, Decimal('5'))
        raw = Decimal('5.000001') / Decimal('0.5') * Decimal('0.05') * Decimal('0.25')
        self.assertGreaterEqual(Decimal(row['fee_usdc']), raw)


class Transport(unittest.TestCase):
    def test_guard_refuses_everything_else(self):
        for url in ('http://clob.polymarket.com/book', 'https://data-api.polymarket.com/v2/prices-history',
                    'https://polymarket.com/', 'https://user:pw@clob.polymarket.com/book',
                    'https://clob.polymarket.com:8443/book', 'https://clob.polymarket.com.evil.test/book',
                    'https://evil.test/?h=clob.polymarket.com', 'https://clob.polymarket.com/book#x'):
            with self.subTest(url=url):
                with self.assertRaises(ps.Refusal):
                    ps.guard_url(url)

    def test_http_get_only_get_without_credentials(self):
        seen = {}

        class Response:
            status = 200
            headers = {'Date': 'x', 'Set-Cookie': 'secret', 'cf-cache-status': 'DYNAMIC'}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, n):
                return b'{"ok": true}'

        def opener(request, timeout):
            seen['method'] = request.get_method()
            seen['headers'] = {k.lower() for k in request.headers}
            seen['data'] = request.data
            return Response()

        out = ps.http_get('https://clob.polymarket.com/time', opener=opener)
        self.assertEqual(seen['method'], 'GET')
        self.assertIsNone(seen['data'])
        self.assertEqual(seen['headers'], {'user-agent', 'accept'})
        self.assertNotIn('set-cookie', out['headers'])
        self.assertEqual(out['payload'], {'ok': True})

    def test_disallowed_host_never_reaches_opener(self):
        def opener(*a, **k):
            raise AssertionError('network touched')
        with self.assertRaises(ps.Refusal):
            ps.http_get('https://data-api.polymarket.com/x', opener=opener)

    def test_oversized_body_refused(self):
        class Big:
            status = 200
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, n):
                return b' ' * n

        with self.assertRaises(ps.Refusal) as ctx:
            ps.http_get('https://clob.polymarket.com/book', opener=lambda r, timeout: Big())
        self.assertEqual(ctx.exception.code, 'body_too_large')

    def test_network_errors_become_unavailable(self):
        remote = FakeRemote()
        remote.fail = 'network_error'
        obs = ps.observe(signal(), get=remote)
        self.assertEqual((obs['status'], obs['reason']), ('unavailable', 'network_error'))

    def test_module_has_no_write_or_order_paths(self):
        source = (ROOT / 'scangoal/polymarket_shadow.py').read_text(encoding='utf-8')
        for forbidden in ("method='POST'", 'method="POST"', 'write_text', "open(", 'Authorization', 'POLY_',
                          '/order', 'private_key', 'data_dir'):
            self.assertNotIn(forbidden, source)


class Redirects(unittest.TestCase):
    """O opener real (NoRedirect) com um transporte simulado: nenhum pedido sai do contentor."""

    def opener(self, code, location):
        requested = []

        class FakeHTTPS(HTTPSHandler):
            def https_open(self, req):
                requested.append(req.full_url)
                headers = Message()
                if req.full_url.startswith('https://clob.polymarket.com/book'):
                    headers['Location'] = location
                    status, body = code, b''
                else:                                       # o destino do redirecionamento
                    status, body = 200, b'{"hijacked": true}'
                response = addinfourl(BytesIO(body), headers, req.full_url, status)
                response.msg = 'redirect' if status != 200 else 'OK'
                return response

        return ps.make_opener(ProxyHandler({}), FakeHTTPS()), requested

    def test_external_redirects_are_never_followed(self):
        for code in (301, 302, 303, 307, 308):
            for location in ('https://evil.example/book', 'http://clob.polymarket.com/book',
                             'https://data-api.polymarket.com/v2/x', 'https://clob.polymarket.com.evil.test/',
                             'https://user:pw@clob.polymarket.com/book', '//evil.example/book'):
                with self.subTest(code=code, location=location):
                    opener, requested = self.opener(code, location)
                    with self.assertRaises(ps.Refusal) as ctx:
                        ps.http_get('https://clob.polymarket.com/book?token_id=1', opener=opener.open)
                    self.assertEqual(ctx.exception.code, 'redirect_host_not_allowed')
                    self.assertEqual(requested, ['https://clob.polymarket.com/book?token_id=1'])

    def test_redirects_inside_the_allowlist_are_also_refused(self):
        for code in (301, 302, 307, 308):
            with self.subTest(code=code):
                opener, requested = self.opener(code, 'https://gamma-api.polymarket.com/markets')
                with self.assertRaises(ps.Refusal) as ctx:
                    ps.http_get('https://clob.polymarket.com/book?token_id=1', opener=opener.open)
                self.assertEqual(ctx.exception.code, 'redirect_refused')
                self.assertEqual(len(requested), 1)

    def test_default_opener_refuses_redirects(self):
        self.assertTrue(any(isinstance(h, ps.NoRedirect) for h in ps.OPENER.handlers))
        self.assertFalse(any(type(h).__name__ == 'HTTPRedirectHandler' for h in ps.OPENER.handlers))

    def test_response_from_another_url_is_refused(self):
        class Moved:
            status = 200
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def geturl(self):
                return 'https://evil.example/book'

            def read(self, n):
                return b'{}'

        with self.assertRaises(ps.Refusal) as ctx:
            ps.http_get('https://clob.polymarket.com/book', opener=lambda r, timeout: Moved())
        self.assertEqual(ctx.exception.code, 'redirect_refused')

    def test_redirect_in_observe_is_a_refusal_not_a_crash(self):
        remote = FakeRemote()
        remote.fail = 'redirect_host_not_allowed'
        obs = ps.observe(signal(), get=remote)
        self.assertEqual((obs['status'], obs['reason']), ('refused', 'redirect_host_not_allowed'))
        self.assertNotIn('quote', obs)


class NoProductionCoupling(unittest.TestCase):
    def test_not_wired_into_workflows_alerts_or_reports(self):
        for path in list((ROOT / '.github/workflows').glob('*.yml')) + [
                ROOT / 'scangoal/notify.py', ROOT / 'scangoal/report.py', ROOT / 'scangoal/polymarket_alerts.py',
                ROOT / 'scangoal/certification.py', ROOT / 'scangoal/strategy_board.py', ROOT / 'scangoal/brain.py']:
            with self.subTest(path=path.name):
                content = path.read_text(encoding='utf-8')
                if path.name == 'polymarket-rehearsal.yml':
                    content = content.replace('from scangoal.polymarket_shadow import REQUEST_HEADERS', '', 1)
                self.assertNotIn('polymarket_shadow', content)


class Cli(unittest.TestCase):
    def test_refuses_every_path_inside_the_repository(self):
        tracked = subprocess.run(['git', '-C', str(ROOT), 'ls-files'], capture_output=True, text=True,
                                 check=True).stdout.split()
        self.assertIn('scangoal/polymarket_shadow.py', tracked)
        targets = ['.', 'README.md', 'scangoal/polymarket_shadow.py', 'scangoal/report.py', 'scripts/polymarket_shadow.py',
                   'tests/test_polymarket_shadow.py', '.github/workflows/collect.yml', 'data/messages.jsonl', 'data',
                   'docs/report.json', 'audits/strategy-lab/shortlist-v1.json', 'requirements.txt',
                   'new-untracked-file.json', 'scangoal/new_module.py', 'deep/new/dir/x.json', '.git/config',
                   'scangoal/../README.md'] + tracked[::25]
        for target in targets:
            with self.subTest(target=target):
                with self.assertRaises(SystemExit):
                    cli.safe_output(ROOT / target)
        with tempfile.TemporaryDirectory() as tmp:
            sneaky = Path(tmp) / '..' / os.path.relpath(ROOT / 'README.md', Path(tmp).parent)
            with self.assertRaises(SystemExit):
                cli.safe_output(sneaky)

    def test_relative_paths_resolve_against_cwd(self):
        cwd = os.getcwd()
        try:
            os.chdir(ROOT / 'scangoal')
            for target in ('report.py', '../README.md', 'x.json'):
                with self.subTest(target=target):
                    with self.assertRaises(SystemExit):
                        cli.safe_output(target)
        finally:
            os.chdir(cwd)

    def test_refuses_symlinks_into_the_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / 'out.json'
            link.symlink_to(ROOT / 'scangoal/report.py')
            with self.assertRaises(SystemExit):
                cli.safe_output(link)
            dir_link = Path(tmp) / 'repo'
            dir_link.symlink_to(ROOT)
            with self.assertRaises(SystemExit):
                cli.safe_output(dir_link / 'new.json')
            dangling = Path(tmp) / 'dangling.json'
            dangling.symlink_to(Path(tmp) / 'missing.json')
            with self.assertRaises(SystemExit):
                cli.safe_output(dangling)

    def test_refuses_hard_links_and_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp) / 'a.json'
            original.write_text('{}')
            os.link(original, Path(tmp) / 'b.json')
            with self.assertRaises(SystemExit):
                cli.safe_output(Path(tmp) / 'b.json')
            with self.assertRaises(SystemExit):
                cli.safe_output(tmp)

    def test_accepts_plain_path_outside(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(cli.safe_output(Path(tmp) / 'o.json'), (Path(tmp) / 'o.json').resolve())
            existing = Path(tmp) / 'e.json'
            existing.write_text('old')
            cli.write_output(cli.safe_output(existing), 'new')
            self.assertEqual(existing.read_text(), 'new')

    def test_writer_does_not_follow_a_late_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            victim = Path(tmp) / 'victim.txt'
            victim.write_text('keep')
            target = cli.safe_output(Path(tmp) / 'o.json')
            target.symlink_to(victim)                       # troca entre a verificação e a escrita
            with self.assertRaises(OSError):
                cli.write_output(target, 'overwrite')
            self.assertEqual(victim.read_text(), 'keep')

    def test_main_refuses_before_reading_data_or_network(self):
        with patch.object(cli.report, 'load', side_effect=AssertionError('data read')), \
                patch.object(cli.polymarket_shadow, 'http_get', side_effect=AssertionError('network')):
            for argv in (['--coverage', '--out', str(ROOT / 'scangoal/report.py')],
                         ['--upcoming', '--out', str(ROOT / 'out.json')]):
                with self.subTest(argv=argv):
                    with self.assertRaises(SystemExit):
                        cli.main(argv)

    def test_refuses_custom_data_dir(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'SCANGOAL_DATA_DIR': tmp}):
            for target in (Path(tmp) / 'o.json', Path(tmp) / 'sub' / 'o.json', Path(tmp)):
                with self.subTest(target=target):
                    with self.assertRaises(SystemExit):
                        cli.safe_output(target)
            with tempfile.TemporaryDirectory() as other:
                link = Path(other) / 'd'
                link.symlink_to(tmp)
                with self.assertRaises(SystemExit):
                    cli.safe_output(link / 'o.json')
                self.assertTrue(cli.safe_output(Path(other) / 'o.json'))

    def test_first_signal_per_future_match(self):
        now = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)
        rows = [
            signal(message_id=2, date_utc='2026-11-23T11:00:00+00:00'),
            signal(message_id=1, date_utc='2026-11-23T10:00:00+00:00'),
            signal(message_id=3, match_id='old', teams=['X', 'Y'], kickoff_utc='2026-11-23T11:00+00:00'),
            signal(message_id=4, match_id='live', teams=['P', 'Q'], phase='live'),
        ]
        picked = cli.first_signals(rows, now)
        self.assertEqual([s['message_id'] for s in picked], [1])
        self.assertEqual(cli.first_signals(rows, now, {'other'}), [])


class Coverage(unittest.TestCase):
    def test_coverage_reads_no_prices(self):
        remote = FakeRemote()
        row = ps.coverage(signal(), get=remote)
        self.assertEqual(row['status'], 'contract_verified')
        self.assertEqual(row['condition_id'], CONDITION)
        self.assertFalse(any('clob.polymarket.com' in u for u in remote.urls))

    def test_coverage_falls_back_to_closed_events(self):
        remote = FakeRemote()
        calls = []
        original = remote.__call__

        def get(url):
            calls.append(url)
            if 'events_status=active' in url:
                return dict(original(url), payload={'events': None})
            return original(url)

        row = ps.coverage(signal(), get=get)
        self.assertEqual(row['status'], 'contract_verified')
        self.assertTrue(any('events_status=closed' in u for u in calls))

    def test_coverage_reports_identity_failures(self):
        self.assertEqual(ps.coverage(signal(teams=['Tottenham', 'Ipswich']), get=FakeRemote())['status'],
                         'identity_not_found')
        remote = FakeRemote()
        remote.fail = 'network_error'
        self.assertEqual(ps.coverage(signal(), get=remote)['status'], 'unavailable')

    def test_coverage_includes_past_matches(self):
        now = datetime(2026, 11, 24, tzinfo=timezone.utc)
        self.assertEqual(cli.first_signals([signal()], now), [])
        self.assertEqual(len(cli.first_signals([signal()], now, upcoming_only=False)), 1)


if __name__ == '__main__':
    unittest.main()
