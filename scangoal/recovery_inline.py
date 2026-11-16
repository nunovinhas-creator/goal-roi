"""Collector-tail proposal: five irreversible slots, no account-wide expiring lease.

Preparation only. Reuses PR30's data-only HTTP adapter; no live bridge or CLI.
Git store is injected (only local Git server tested); no remote ref is created.
"""
import base64
import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime,timedelta,timezone
from pathlib import Path
from .recovery_trial import CAP,DAYS,TRIAL_ID,TrialBlocked,isolated
from .recovery_trial_journal import canonical,utc
from . import recovery as engine

UTC=timezone.utc


def structured_subset(value,required):
    """Accept only the documented single football item + nonnegative integer params.

    Deliberately not a general SFV parser. Unsupported legal syntax fails closed;
    no permissive regex, multiple scopes, duplicates or unknown params accepted.
    """
    if not isinstance(value,str):raise TrialBlocked('quota_header_missing')
    parts=value.split(';')
    if parts[0].strip()!='"football"':raise TrialBlocked('quota_scope_unproven')
    out={}
    for part in parts[1:]:
        pair=part.strip().split('=')
        if len(pair)!=2:raise TrialBlocked('quota_header_unsupported')
        key,number=(x.strip() for x in pair)
        if key not in required or key in out or not number.isascii() or not number.isdigit() or len(number)>15:
            raise TrialBlocked('quota_header_unsupported')
        out[key]=int(number)
    if set(out)!=set(required):raise TrialBlocked('quota_header_incomplete')
    return out


@dataclass(frozen=True)
class QuotaReceipt:
    remaining:int
    received_utc:datetime
    run_owner:str
    normal_response_sha256:str
    header_sha256:str
    # Opaque local sequence number in the serial BSD step, not a manual balance.
    normal_sequence:int
    slot_debits:int

    @classmethod
    def from_normal_response(cls,headers,body,owner,sequence,received,slot_debits):
        if type(sequence) is not int or sequence<0 or type(slot_debits) is not int or not 0<=slot_debits<=CAP:
            raise TrialBlocked('quota_sequence_invalid')
        h={k.lower():v for k,v in headers.items()}
        policy=structured_subset(h.get('ratelimit-policy'),('q','w'))
        rate=structured_subset(h.get('ratelimit'),('r','t'))
        if policy!={'q':7500,'w':86400} or not 0<=rate['r']<=7500:
            raise TrialBlocked('free_football_policy_unproven')
        if received.tzinfo is None:raise TrialBlocked('invalid_utc')
        received=received.astimezone(UTC)
        midnight=(received+timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0)
        if abs((midnight-received).total_seconds()-rate['t'])>60:
            raise TrialBlocked('quota_reset_mismatch')
        return cls(rate['r'],received,owner,hashlib.sha256(body).hexdigest(),
                   hashlib.sha256(canonical({k:h[k] for k in ('ratelimit','ratelimit-policy')})).hexdigest(),sequence,slot_debits)


class InlineSlots:
    """Five slots in one append-only Git head. No budget reset or timed lease steal.

    Owner is run_id:run_attempt of the existing collector execution. A reservation
    is a one-shot send permit. A lost permit is burned, not reused. Run-terminal
    proof can retire an abandoned slot; never refunds or reissues the same query.
    """
    def __init__(self,store,anchor,output,receipt=None,normal_sequence=None):
        self.store,self.anchor=store,anchor
        self.output=isolated(Path(__file__).resolve().parents[1],output)
        if self.output.name!='prepared-fixture-trial':raise TrialBlocked('fixture_namespace_required')
        self.receipt,self.normal_sequence=receipt,normal_sequence
        # Latest UTC instant a confirmed reservation may still authorize a send.
        self.send_not_after=None
    def load(self):
        head=self.store.head();cursor=head;events=[];seen=set()
        while cursor:
            if cursor in seen:raise TrialBlocked('inline_history_cycle')
            seen.add(cursor);parent,event=self.store.read(cursor)
            if cursor==self.anchor:
                config=event.get('config',{})
                if (parent is not None or event.get('kind')!='genesis'
                    or config.get('mode')!='inline-five-slots' or config.get('trial_id')!=TRIAL_ID
                    or config.get('cap')!=CAP or not config.get('snapshot_sha')
                    or type(config.get('prior_debits')) is not int or not 0<=config['prior_debits']<=CAP
                    or not config.get('predecessor_budget_review_ref')):
                    raise TrialBlocked('inline_anchor_invalid')
                state={'config':config,'attempts':{},'lease':None,'halted':False};break
            events.append(event);cursor=parent
        else:raise TrialBlocked('inline_anchor_missing')
        for e in reversed(events):self.apply(state,e)
        return head,state
    @staticmethod
    def apply(s,e):
        op=e.get('operation_id');kind=e['kind']
        if kind=='reserve':
            if s['halted'] or s['lease'] or len(s['attempts'])+s['config']['prior_debits']>=CAP or op in s['attempts']:
                raise TrialBlocked('inline_reservation_invalid')
            if any(a['owner']==e['owner'] or (a['day'],a['offset'])==(e['day'],e['offset']) for a in s['attempts'].values()):
                raise TrialBlocked('one_page_per_run_no_query_retry')
            if e['day'] not in DAYS or type(e['offset']) is not int or e['offset']<0:
                raise TrialBlocked('query_outside_authorization')
            q=e['quota'];used=len(s['attempts'])+s['config']['prior_debits']
            if not 0<=q['slot_debits']<=used:
                raise TrialBlocked('quota_debit_sequence_invalid')
            available=q['remaining']-(used-q['slot_debits'])
            if available<s['config'].get('reserve',100)+CAP-used:
                raise TrialBlocked('quota_reserve_insufficient')
            s['attempts'][op]=dict(e,consumer='trial',outcome='reserved')
            s['lease']={'owner':e['owner'],'operation_id':op}
        elif kind=='complete':
            if s['lease']!={'owner':e['owner'],'operation_id':op}:raise TrialBlocked('inline_owner_mismatch')
            raw=base64.b64decode(e['raw_b64'],validate=True)
            if hashlib.sha256(raw).hexdigest()!=e['response_sha256']:raise TrialBlocked('inline_evidence_corrupt')
            s['attempts'][op].update(copy.deepcopy(e));s['lease']=None
            if e['outcome']!='complete':s['halted']=True
        elif kind=='abandon':
            prior=s['attempts'].get(op)
            proof=e['terminal_run']
            proof_raw=base64.b64decode(e['proof_raw_b64'],validate=True)
            if hashlib.sha256(proof_raw).hexdigest()!=e['proof_sha256'] or json.loads(proof_raw)!=proof:
                raise TrialBlocked('terminal_proof_corrupt')
            owner=str(proof.get('id'))+':'+str(proof.get('run_attempt'))
            if (not prior or prior['outcome']!='reserved' or owner!=prior['owner']
                or proof.get('status')!='completed' or proof.get('path')!='.github/workflows/collect.yml'
                or proof.get('head_branch')!='main' or not e.get('proof_sha256')):
                raise TrialBlocked('terminal_run_proof_insufficient')
            prior.update(outcome='abandoned',proof_sha256=e['proof_sha256']);s['lease']=None
        else:raise TrialBlocked('inline_unknown_event')
    def write(self,event):
        head,state=self.load();self.apply(state,event)
        candidate=self.store.propose(head,event)
        if not self.store.cas(head,candidate):raise TrialBlocked('inline_atomic_conflict')
        return candidate
    def reserve(self,operation_id,owner,day,offset,now):
        head,state=self.load()
        if operation_id in state['attempts']:
            return {'send_permitted':False,'head':head,'operation_id':operation_id}
        config=state['config'];r=self.receipt
        # This reviewed inventory is still an OPERATIONAL prerequisite, not proved by a fixture.
        if not config.get('exclusive_account_review_ref'):raise TrialBlocked('external_consumers_unproven')
        if (type(r) is not QuotaReceipt or r.run_owner!=owner or r.normal_sequence!=self.normal_sequence
            or r.received_utc.date()!=now.astimezone(UTC).date()
            or not timedelta(0)<=now-r.received_utc<=timedelta(seconds=60)):
            raise TrialBlocked('normal_quota_witness_stale_or_interleaved')
        reserve=config.get('reserve',100)
        if type(reserve) is not int or reserve<100 or r.remaining-(len(state['attempts'])+config['prior_debits']-r.slot_debits)<reserve+CAP-len(state['attempts'])-config['prior_debits']:
            raise TrialBlocked('quota_reserve_insufficient')
        # No normal request is allowed between this witness and the collector-tail send.
        # That ordering is supplied by the future single-process integration, not a boolean input.
        event=dict(kind='reserve',operation_id=operation_id,owner=owner,day=day,offset=offset,
                   started_utc=now.isoformat(),quota=copy.deepcopy(r.__dict__))
        event['quota']['received_utc']=r.received_utc.isoformat()
        candidate=self.write(event)
        # Metadata persistence may outlive the quota witness. Burn the reserved
        # slot rather than send using stale proof; never refund it.
        confirmed=datetime.now(UTC)
        valid=(r.received_utc.date()==confirmed.date()
               and timedelta(0)<=confirmed-r.received_utc<=timedelta(seconds=60)
               and (self.send_not_after is None or confirmed<=self.send_not_after))
        return {'send_permitted':valid,'head':candidate,'operation_id':operation_id}
    def append(self,event):
        if event['kind']!='complete':raise TrialBlocked('inline_operation_forbidden')
        # PR30 prepared transport writes the raw bytes before completing its reservation.
        op=event['operation_id'];raw=(self.output/(op+'.bin')).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=event['response_sha256']:
            raise TrialBlocked('inline_response_not_persisted')
        meta=json.loads((self.output/(op+'.json')).read_text())
        return self.write(dict(event,raw_b64=base64.b64encode(raw).decode(),metadata=meta))
    def abandon(self,operation_id,raw_proof):
        # Future caller must fetch this exact attempt from authenticated GitHub API.
        # A pasted JSON blob does NOT independently authenticate GitHub.
        proof=json.loads(raw_proof)
        return self.write(dict(kind='abandon',operation_id=operation_id,terminal_run=proof,
                               proof_sha256=hashlib.sha256(raw_proof).hexdigest(),
                               proof_raw_b64=base64.b64encode(raw_proof).decode()))
    def restore_evidence(self):
        _,state=self.load();self.output.mkdir(parents=True,exist_ok=True)
        for op,a in state['attempts'].items():
            if 'raw_b64' not in a:continue
            raw=base64.b64decode(a['raw_b64'],validate=True)
            path=self.output/(op+'.bin')
            if path.exists() and path.read_bytes()!=raw:raise TrialBlocked('local_evidence_conflict')
            if not path.exists():
                with path.open('xb') as f:
                    f.write(raw);f.flush()
                    import os
                    os.fsync(f.fileno())
            engine.write_json(self.output/(op+'.json'),a['metadata'])
        return state


class NormalQuotaTracker:
    """Capture only the LAST successful normal reply, in the existing serial step.

    An error or a subsequent request invalidates the earlier witness. The wrapper
    uses the same key already passed to the normal collector; never stores it.
    """
    def __init__(self,owner,slot_debits):
        self.owner,self.slot_debits=owner,slot_debits
        self.sequence=0;self.receipt=None;self.sealed=False
        self.observed=[];self.uncontrolled_consumption=False
    def observe(self,headers,body,received):
        self.receipt=None
        try:
            self.receipt=QuotaReceipt.from_normal_response(headers,body,self.owner,self.sequence,received,self.slot_debits)
            self.observed.append((self.sequence,self.receipt.remaining,self.receipt.received_utc.date()))
        except Exception:
            # Quota capture cannot fail the normal collector or retain old proof.
            pass
    def wrap(self,normal_fetch):
        def fetch(url,token):
            if self.sealed:raise TrialBlocked('normal_phase_already_closed')
            self.sequence+=1;self.receipt=None
            return normal_fetch(url,token,observe=self.observe)
        return fetch
    def finish_normals(self):
        self.sealed=True
        # Within the serial step each normal request costs exactly one unit. Any
        # other drop (or a reset) between two of our own replies means a consumer
        # we do not control, or a counter we do not model: never reserve on it.
        for (s1,r1,d1),(s2,r2,d2) in zip(self.observed,self.observed[1:]):
            if d1!=d2 or r1-r2!=s2-s1:
                self.uncontrolled_consumption=True;self.receipt=None
        return self.receipt,self.sequence
