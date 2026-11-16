"""Append-only trial protocol with atomic Git compare-and-append.

Only an OFFLINE Git object-store adapter ships in this revision. It exercises
real Git CAS/durability across processes, not a claim of a deployed global lock.
A remote service/anchor/consumer rollout is NOT bootstrapped by this module.
"""
import copy
import hashlib
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from .recovery_trial import CAP, DAYS, TRIAL_ID, TrialBlocked, isolated

UTC = timezone.utc
REF = 'refs/heads/bsd-trial-journal'


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':')).encode()


def utc(text):
    try:
        value=datetime.fromisoformat(text.replace('Z','+00:00'))
        if value.tzinfo is None:
            raise ValueError()
        return value.astimezone(UTC)
    except (AttributeError,ValueError,TypeError):
        raise TrialBlocked('invalid_utc') from None


class OfflineGitStore:
    """Local bare Git server for adversarial protocol tests; NO remote/network API."""
    def __init__(self,path):
        self.path=isolated(Path(__file__).resolve().parents[1],path)
    def git(self,*args,input=None):
        env=dict(os.environ,GIT_AUTHOR_NAME='Offline trial test',GIT_AUTHOR_EMAIL='offline@example.invalid',
                 GIT_COMMITTER_NAME='Offline trial test',GIT_COMMITTER_EMAIL='offline@example.invalid')
        try:
            return subprocess.check_output(['git','--git-dir='+str(self.path),*args],input=input,
                                           env=env,stderr=subprocess.DEVNULL)
        except (OSError,subprocess.CalledProcessError):
            raise TrialBlocked('journal_unavailable_or_corrupt') from None
    def head(self):
        return self.git('rev-parse','--verify',REF).decode().strip()
    def read(self,sha):
        raw=self.git('cat-file','commit',sha)
        # Check the object ID too, not merely parse JSON returned by a store.
        actual=hashlib.sha1(b'commit '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        if actual != sha:
            raise TrialBlocked('journal_hash_mismatch')
        headers,body=raw.split(b'\n\n',1)
        parents=[line[7:].decode() for line in headers.splitlines() if line.startswith(b'parent ')]
        if len(parents)>1:
            raise TrialBlocked('journal_merge_forbidden')
        try:
            return (parents[0] if parents else None),json.loads(body)
        except (ValueError,TypeError):
            raise TrialBlocked('journal_invalid_record') from None
    def propose(self,parent,event):
        tree=self.git('mktree',input=b'').decode().strip()
        args=['commit-tree',tree]
        if parent: args+=['-p',parent]
        return self.git(*args,input=canonical(event)+b'\n').decode().strip()
    def cas(self,previous,candidate):
        # The exact expected old object ID is checked atomically by Git.
        try:
            self.git('update-ref',REF,candidate,previous or '0'*40)
            return True
        except TrialBlocked:
            if previous is not None and self.head()!=previous:
                return False
            raise
    @classmethod
    def bootstrap_fixture(cls,path,config):
        """Explicit fixture creation ONLY. Normal ledger reads never recreate state."""
        path=isolated(Path(__file__).resolve().parents[1],path)
        subprocess.run(['git','init','--bare','--quiet',str(path)],check=True)
        store=cls(path)
        anchor=store.propose(None,{'kind':'genesis','config':config})
        if not store.cas(None,anchor): raise TrialBlocked('fixture_initialization_race')
        return store,anchor


def initial(config):
    if config.get('trial_id')!=TRIAL_ID or config.get('cap')!=CAP or not config.get('account_id'):
        raise TrialBlocked('invalid_anchor_config')
    return {'config':copy.deepcopy(config),'attempts':{},'lease':None,'halted':False,
            'debits':0,'quota':None,'collector_intents':[], 'unknown_consumption':False}


def transition(old,event):
    """Pure replayable state machine. All account consumers must use this journal."""
    s=copy.deepcopy(old); kind=event['kind']
    if kind=='quota':
        e=event['evidence']; c=s['config']
        # Hash approval is external review, not provider authentication by itself.
        proof_hash=hashlib.sha256(canonical({k:v for k,v in e.items() if k!='evidence_sha256'})).hexdigest()
        if e.get('evidence_sha256')!=proof_hash or proof_hash not in c.get('approved_evidence',[]):
            raise TrialBlocked('quota_capture_not_reviewed')
        if (e.get('account_id')!=c['account_id'] or e.get('scope')!='football'
                or e.get('origin') not in ('dashboard','existing-response-header')
                or not e.get('source_uri','').startswith('https://sports.bzzoiro.com/')
                or not e.get('review_ref') or not e.get('account_binding_ref')
                or e.get('sequence')!=s['debits'] or type(e.get('remaining')) is not int
                or not 0<=e['remaining']<=7500):
            raise TrialBlocked('quota_evidence_invalid')
        seen=utc(e['observed_utc']); reset=utc(e['reset_utc'])
        if reset != datetime.combine(seen.date()+timedelta(days=1),datetime.min.time(),UTC):
            raise TrialBlocked('quota_window_invalid')
        if utc(event['now'])<seen or utc(event['now'])-seen>timedelta(seconds=60):
            raise TrialBlocked('quota_stale')
        s['quota']=copy.deepcopy(e)
    elif kind=='collector-intent':
        if event['owner'] not in s['collector_intents']: s['collector_intents'].append(event['owner'])
    elif kind=='collector-done':
        if event['owner'] not in s['collector_intents']: raise TrialBlocked('unknown_collector_intent')
        if s['lease']: raise TrialBlocked('active_account_request')
        s['collector_intents'].remove(event['owner'])
    elif kind=='uncoordinated-consumer':
        s['unknown_consumption']=True
    elif kind=='reserve':
        c=s['config']; op=event['operation_id']
        if op in s['attempts']: raise TrialBlocked('duplicate_reservation')
        is_trial=event['consumer']=='trial'
        if s['lease'] or (is_trial and (s['halted'] or s['collector_intents'])):
            raise TrialBlocked('active_pending_or_uncertain_consumer')
        if is_trial and (s['unknown_consumption'] or not c.get('coverage_review_ref')):
            raise TrialBlocked('global_consumer_coverage_unproven')
        if event['consumer'] not in c.get('consumers',[]): raise TrialBlocked('unknown_consumer')
        if event['consumer']=='trial' and sum(a['consumer']=='trial' for a in s['attempts'].values())>=CAP:
            raise TrialBlocked('trial_budget_exhausted')
        if event['consumer']=='trial' and (event['day'] not in DAYS or type(event['offset']) is not int or event['offset']<0):
            raise TrialBlocked('query_outside_authorization')
        if is_trial:
            q=s['quota']; now=utc(event['now'])
            if not q or not utc(q['observed_utc'])<=now<utc(q['reset_utc']) or now-utc(q['observed_utc'])>timedelta(seconds=60):
                raise TrialBlocked('quota_stale_or_absent')
            available=q['remaining']-(s['debits']-q['sequence'])
            used=sum(a['consumer']=='trial' for a in s['attempts'].values())
            reserve=c.get('reserve',100)
            if type(reserve) is not int or reserve<100 or available<reserve+(CAP-used):
                raise TrialBlocked('quota_reserve_insufficient')
        s['attempts'][op]=dict(event,outcome='reserved')
        s['lease']={'owner':event['owner'],'operation_id':op}
        s['debits']+=1
    elif kind=='complete':
        lease=s['lease']; op=event['operation_id']
        if lease!={'owner':event['owner'],'operation_id':op}:
            raise TrialBlocked('lease_owner_mismatch')
        if (len(event.get('response_sha256',''))!=64
                or any(c not in '0123456789abcdef' for c in event['response_sha256'])
                or utc(event['observed_utc'])<utc(s['attempts'][op]['now'])
                or event['outcome'] not in ('complete','http_429','http_error_or_redirect','redirect_forbidden','invalid_response')):
            raise TrialBlocked('response_evidence_missing')
        s['attempts'][op].update(outcome=event['outcome'],response_sha256=event['response_sha256'],
                                 observed_utc=event['observed_utc'])
        s['lease']=None
        if event['outcome']!='complete': s['halted']=True
    else: raise TrialBlocked('unknown_journal_operation')
    return s


class Journal:
    def __init__(self,store,anchor):
        self.store,self.anchor=store,anchor
    def load(self):
        head=self.store.head(); cursor=head; events=[]; seen=set()
        while cursor:
            if cursor in seen or len(seen)>10000: raise TrialBlocked('journal_chain_invalid')
            seen.add(cursor); parent,event=self.store.read(cursor)
            if cursor==self.anchor:
                if parent is not None or event.get('kind')!='genesis': raise TrialBlocked('anchor_invalid')
                s=initial(event['config']); break
            events.append(event); cursor=parent
        else: raise TrialBlocked('anchor_missing_or_history_lost')
        for e in reversed(events): s=transition(s,e)
        return head,s
    def append(self,event):
        head,s=self.load()
        op=event.get('operation_id')
        if event['kind']=='reserve' and op in s['attempts']:
            prior=s['attempts'][op]
            for key in ('owner','consumer','day','offset'):
                if prior[key]!=event[key]: raise TrialBlocked('idempotency_key_reused')
            return {'send_permitted':False,'head':head,'operation_id':op}
        transition(s,event) # Validate BEFORE preparing a remote write.
        candidate=self.store.propose(head,event)
        if not self.store.cas(head,candidate):
            # Do not retry a losing claim as a new request. The caller must re-read.
            raise TrialBlocked('journal_compare_append_conflict')
        return {'send_permitted':event['kind']=='reserve','head':candidate,'operation_id':op}
    def reserve(self,operation_id,owner,day,offset,now,consumer='trial'):
        return self.append(dict(kind='reserve',operation_id=operation_id,owner=owner,consumer=consumer,
                                day=day,offset=offset,now=now.isoformat()))


def record_capture(raw,metadata,now):
    """Create an auditable envelope. It is NOT accepted until hash/account review.

    Raw bytes must be a sanitized excerpt/capture, not cookies, token or full HTML.
    No network request is made and no manual integer becomes trusted automatically.
    """
    if not isinstance(raw,bytes) or not raw or len(raw)>1000000:
        raise TrialBlocked('quota_capture_invalid')
    evidence=copy.deepcopy(metadata)
    evidence['capture_sha256']=hashlib.sha256(raw).hexdigest()
    if utc(evidence['observed_utc'])>now: raise TrialBlocked('future_quota_observation')
    evidence['evidence_sha256']=hashlib.sha256(canonical(evidence)).hexdigest()
    return evidence


def remote_cas_plan(repository_id,expected,candidate):
    """Prepared GitHub GraphQL CAS descriptor; never executes HTTP/GraphQL.

    updateRefs/beforeOid supplies server-side atomic expected-head checking,
    force=false prohibits non-fast-forward updates. Only the dedicated journal ref.
    """
    if not repository_id or any(len(v)!=40 or any(c not in '0123456789abcdef' for c in v)
                                for v in (expected,candidate)):
        raise TrialBlocked('invalid_remote_cas_descriptor')
    return {'query': 'mutation($input: UpdateRefsInput!) { updateRefs(input: $input) { clientMutationId } }',
            'variables':{'input':{'repositoryId':repository_id,'refUpdates':[
                {'name':REF,'beforeOid':expected,'afterOid':candidate,'force':False}]}}}
