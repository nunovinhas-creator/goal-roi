"""Prepared collector tail. Only OfflineWire accepted; no live entry point.

The engine state is a deterministic projection of durable original responses.
Rebuilding it uses their original receipt time, never the replay wall clock.
"""
import copy
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from . import recovery as engine
from .recovery_inline import NormalQuotaTracker
from .recovery_trial import MATCH_IDS, DAYS, TrialBlocked, isolated
from .recovery_trial_journal import utc
from .recovery_trial_transport import PreparedBSD, OfflineWire

# collect.yml: timeout-minutes 15. The start stamp is written by the first step,
# after runner set-up; 30 s covers set-up already counted by GitHub.
JOB_TIMEOUT_S, JOB_SETUP_SLACK_S = 900, 30
# Hard wall clock of the whole tail (every Git op is clipped to it).
TAIL_BUDGET_S = 150
# After the send: BSD timeout 20 + completion load/check/push/confirm 4x10 +
# final replay fetch 10 + local persistence 10. No send if less remains.
POST_SEND_RESERVE_S = 80
# Fast reads alone are 540+10 s; the other post-BSD steps have no reviewed bound.
MIN_DOWNSTREAM_S = 550


def job_deadline_from_env(environ=None):
    """Deadline from the real job start, or None (=> skip). Never the step start."""
    raw = (os.environ if environ is None else environ).get('GOAL_ROI_JOB_STARTED_UTC')
    try:
        started = utc(raw)
    except TrialBlocked:
        return None
    if not timedelta(0) <= datetime.now(timezone.utc)-started <= timedelta(seconds=JOB_TIMEOUT_S):
        return None
    return started+timedelta(seconds=JOB_TIMEOUT_S-JOB_SETUP_SLACK_S)


def validate_targets(targets):
    if not targets or any(mid not in MATCH_IDS or not t['days'] or not set(t['days']) <= DAYS
                          for mid, t in targets.items()):
        raise TrialBlocked('target_outside_authorization')


def project_page(state, targets, day, offset, payload, observed, aliases, existing):
    # Initialize all required searches before reconciliation. Temporarily prevent
    # scheduling another day; do not narrow identity's required date coverage.
    opts = dict(aliases=aliases, existing=existing, daily={'hl':0,'bsd':5},
                available={'hl':0,'bsd':7500}, reserve={'hl':0,'bsd':100}, max_passes=1)
    state = engine.run_batch(state, targets, lambda *a: None, observed, max_requests=0, **opts)
    key = 'bsd|'+day
    if key not in state['pages'] or state['pages'][key]['offset'] != offset:
        raise TrialBlocked('replay_cursor_mismatch')
    hidden = {}
    for k, p in state['pages'].items():
        if k != key:
            hidden[k] = p.get('terminal_error')
            p['terminal_error'] = 'replay_other_page'
    calls = []
    def fetch(source, requested_day, requested_offset, limit):
        if (source, requested_day, requested_offset, limit) != ('bsd',day,offset,200):
            raise TrialBlocked('replay_query_mismatch')
        calls.append(1)
        return payload
    # fetch.live is absent: unchanged engine uses observed, not datetime.now().
    state = engine.run_batch(state, targets, fetch, observed, max_requests=1, **opts)
    if len(calls) != 1:
        raise TrialBlocked('replay_not_applied')
    for k, old in hidden.items():
        if old is None:
            state['pages'][k].pop('terminal_error', None)
        else:
            state['pages'][k]['terminal_error'] = old
    engine.reconcile(state, targets, aliases, existing)
    return state


def replay(slots, targets, *, aliases=None, existing=None):
    validate_targets(targets)
    aliases, existing = aliases or {}, existing or {}
    ledger = slots.restore_evidence()
    state = engine.initial_state()
    for op, a in ledger['attempts'].items():
        if a['outcome'] != 'complete':
            # A lost page cannot prove complete search/uniqueness. Never invent
            # events, a cursor, or a new observation to fill the missing page.
            continue
        meta = a['metadata']
        if (meta.get('evidence_kind') != 'synthetic_wire'
            or meta.get('observed_utc') != a['observed_utc']
            or meta.get('response_sha256') != a['response_sha256']
            or meta.get('params', {}).get('status') != 'finished'):
            raise TrialBlocked('replay_provenance_mismatch')
        payload = json.loads((slots.output/(op+'.bin')).read_bytes())
        state = project_page(state, targets, a['day'], a['offset'], payload,
                             utc(a['observed_utc']), aliases, existing)
    state['evidence_kind'] = 'synthetic_wire'
    engine.write_json(slots.output/'engine-state.json', state)
    return state


class PreparedTail:
    """Single callback after normal state save. Never fail the normal collector.

    job_deadline must originate at actual job start, not BSD step start.
    downstream_bound must be a reviewed worst-case bound, not a mean/boolean.
    Neither is currently wired in production; absent evidence always skips.
    """
    def __init__(self, slots, owner, wire, token, targets, *, aliases=None, existing=None,
                 job_deadline=None, downstream_bound=None):
        if type(wire) is not OfflineWire:
            raise TrialBlocked('network_transport_forbidden')
        self.slots, self.owner, self.wire, self.token = slots, owner, wire, token
        self.targets, self.aliases, self.existing = targets, aliases or {}, existing or {}
        self.job_deadline, self.downstream_bound = job_deadline, downstream_bound
        self.tracker = NormalQuotaTracker(owner, 0)
        self.outcome = 'not_called'

    def attach(self, run, normal_fetch, **normal_args):
        # No journal access before normal collection. Starting the receipt debit
        # at zero conservatively subtracts all previous trial attempts again;
        # this can skip a marginal balance, never overstate available quota.
        if self.tracker.sealed:
            return run(fetch=normal_fetch, **normal_args)
        return run(fetch=self.tracker.wrap(normal_fetch), after_bsd=self, **normal_args)

    def __call__(self):
        self.outcome = 'skipped'
        try:
            self.slots.receipt, self.slots.normal_sequence = self.tracker.finish_normals()
            now = datetime.now(timezone.utc)
            if (self.slots.receipt is None or not self.token or self.job_deadline is None
                or type(self.downstream_bound) is not int or self.downstream_bound < MIN_DOWNSTREAM_S
                or (self.job_deadline-now).total_seconds() < self.downstream_bound+TAIL_BUDGET_S):
                return
            # Enforce the budget instead of estimating it once: Git operations are
            # clipped to tail_end and no send is authorized after send_end.
            tail_end = now+timedelta(seconds=TAIL_BUDGET_S)
            self.slots.store.not_after = tail_end
            self.slots.send_not_after = tail_end-timedelta(seconds=POST_SEND_RESERVE_S)
            validate_targets(self.targets)
            # Rebuild from the authoritative journal, not a runner's state cache.
            state = replay(self.slots, self.targets, aliases=self.aliases, existing=self.existing)
            _, ledger = self.slots.load()
            if ledger['halted'] or ledger['lease']:
                return
            used_queries = {(a['day'],a['offset']) for a in ledger['attempts'].values()}
            jobs = []
            for day in sorted({d for t in self.targets.values() for d in t['days']}):
                page = state['pages'].get('bsd|'+day, {})
                if page.get('status') == 'complete' or page.get('terminal_error'):
                    continue
                offset = page.get('offset', 0)
                if (day,offset) not in used_queries:
                    jobs.append((day, offset))
            if not jobs:
                return
            day, offset = jobs[0]
            PreparedBSD(self.wire,self.slots,self.owner,self.token,self.slots.output)('bsd',day,offset,200)
            replay(self.slots,self.targets,aliases=self.aliases,existing=self.existing)
            self.outcome = 'synthetic_page_staged'
        except Exception:
            # No provider/body/credential/exception text exposed.
            self.outcome = 'trial_failed_normal_preserved'
