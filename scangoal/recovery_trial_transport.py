"""Prepared BSD request/response adapter. No sockets or live bridge in this revision.

Only data-only OfflineWire can be injected. A future network bridge requires
separate review; no CLI or workflow input can enable it here.
"""
import hashlib
import json
from dataclasses import dataclass,field
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlencode
from . import recovery as engine
from .results_bsd import ROOT
from .recovery_trial import CAP,DAYS,TrialBlocked,isolated

UTC=timezone.utc


@dataclass(frozen=True)
class RequestSpec:
    url: str
    headers: dict=field(repr=False)
    timeout: int=20
    follow_redirects: bool=False
    retries: int=0


@dataclass(frozen=True)
class WireResponse:
    status: int
    body: bytes=field(repr=False)
    headers: dict=field(default_factory=dict,repr=False)
    final_url: str=''


class OfflineWire:
    """Static response bytes only. No arbitrary callback can reach this adapter."""
    def __init__(self,responses):
        self.responses=list(responses)
        self.calls=[]
    def send(self,spec):
        self.calls.append(spec)
        if not self.responses: raise TrialBlocked('fixture_response_missing')
        value=self.responses.pop(0)
        if type(value) is not WireResponse: raise TrialBlocked('fixture_response_invalid')
        return value


def network_bridge(*args,**kwargs):
    raise TrialBlocked('network_disabled_pending_new_audit_and_authorization')


class PreparedBSD:
    # Engine timestamps the first valid final after effective response receipt.
    live=True
    def __init__(self,wire,journal,owner,token,output):
        if type(wire) is not OfflineWire: raise TrialBlocked('network_transport_forbidden')
        self.wire,self.journal,self.owner,self._token=wire,journal,owner,token
        self.output=isolated(Path(__file__).resolve().parents[1],output)
        if self.output.name!='prepared-fixture-trial': raise TrialBlocked('fixture_namespace_required')
    def __call__(self,source,day,offset,limit):
        if type(self.wire) is not OfflineWire: raise TrialBlocked('network_transport_forbidden')
        if not isinstance(self._token,str) or not self._token.strip(): raise TrialBlocked('missing_bsd_key')
        if source!='bsd' or day not in DAYS or type(offset) is not int or offset<0 or limit!=200:
            raise TrialBlocked('query_outside_authorization')
        _, prior=self.journal.load()
        for op,attempt in prior['attempts'].items():
            if attempt.get('outcome')=='complete' and attempt['consumer']=='trial':
                p=self.output/(op+'.bin')
                if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=attempt['response_sha256']:
                    raise TrialBlocked('previous_response_evidence_lost')
        prior_pages=[(op,a) for op,a in prior['attempts'].items()
                     if a['consumer']=='trial' and a['day']==day and a['outcome']=='complete']
        previous_events=[]; previous_total=None
        if not prior_pages and offset!=0:
            raise TrialBlocked('pagination_must_start_at_zero')
        for op,attempt in prior_pages:
            try:
                page=json.loads((self.output/(op+'.bin')).read_bytes())
                events,total,_=engine.parse_page('bsd',page,attempt['offset'])
            except (OSError,ValueError,TypeError,KeyError):
                raise TrialBlocked('previous_response_evidence_invalid') from None
            previous_events.extend(events);previous_total=total
        if prior_pages:
            expected=prior_pages[-1][1]['offset']+len(events)
            if offset!=expected or expected>=previous_total:
                raise TrialBlocked('pagination_cursor_invalid_or_complete')
        params=dict(date_from=day,date_to=day,status='finished',limit=200,offset=offset)
        operation=hashlib.sha256(json.dumps(params,sort_keys=True).encode()).hexdigest()
        now=datetime.now(UTC)
        receipt=self.journal.reserve(operation,self.owner,day,offset,now)
        if not receipt['send_permitted']: raise TrialBlocked('reserved_request_never_resent')
        # The receipt is durably committed before constructing/sending the request.
        spec=RequestSpec(ROOT+'?'+urlencode(params),{'Authorization':'Token '+self._token,
                         'Accept':'application/json','User-Agent':'goal-roi/1.0'})
        try:
            response=self.wire.send(spec)
        except Exception:
            # Leave the reservation/lease unresolved, never refund or retry.
            raise TrialBlocked('transport_failed_reservation_retained') from None
        received=datetime.now(UTC)
        if (type(response.body) is not bytes or len(response.body)>3000000
                or self._token.encode() in response.body):
            # Do not persist a response that reflects the credential.
            raise TrialBlocked('unsafe_response_reservation_retained')
        # Reject JSON-escaped credential reflection as well as literal bytes.
        try:
            decoded_for_safety=json.loads(response.body)
        except (ValueError,TypeError):
            decoded_for_safety=None
        if decoded_for_safety is not None and self._token in str(decoded_for_safety):
            raise TrialBlocked('unsafe_response_reservation_retained')
        digest=hashlib.sha256(response.body).hexdigest()
        self.output.mkdir(parents=True,exist_ok=True)
        raw_path=self.output/(operation+'.bin')
        # Exclusive creation; never silently overwrite evidence after a restart.
        try:
            with raw_path.open('xb') as f:
                f.write(response.body); f.flush()
                import os
                os.fsync(f.fileno())
        except OSError:
            raise TrialBlocked('response_persistence_failed_reservation_retained') from None
        meta=dict(evidence_kind='synthetic_wire',source='bsd',params=params,
                  response_sha256=digest,observed_utc=received.isoformat(),status=response.status,
                  reservation_head=receipt['head'],operation_id=operation,
                  # Whitelist only quota fields; never Authorization/cookies/location.
                  quota_headers={k:v for k,v in response.headers.items()
                                 if k.lower() in ('ratelimit','ratelimit-policy') and self._token not in str(v)})
        engine.write_json(self.output/(operation+'.json'),meta)
        outcome='complete'; payload=None
        if response.status==429: outcome='http_429'
        elif response.status!=200: outcome='http_error_or_redirect'
        elif response.final_url and response.final_url!=spec.url: outcome='redirect_forbidden'
        else:
            try:
                payload=json.loads(response.body)
                current,total,_=engine.parse_page('bsd',payload,offset)
                if (previous_total is not None and total!=previous_total
                        or not current and offset<total
                        or {e['id'] for e in current}&{e['id'] for e in previous_events}
                        or len({e['id'] for e in current})!=len(current)):
                    raise ValueError('invalid_response')
            except (ValueError,TypeError,KeyError): outcome='invalid_response'
        self.journal.append(dict(kind='complete',operation_id=operation,owner=self.owner,
                                 outcome=outcome,response_sha256=digest,observed_utc=received.isoformat()))
        if outcome!='complete':
            raise TrialBlocked(outcome)
        return payload
