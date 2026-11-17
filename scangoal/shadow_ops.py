"""Operational SHADOW sidecar, explicitly dormant until approved installation.

No Telegram client, result API, ROI writer or certification activation. A dedicated Git branch
is the publication barrier. Failed/late decisions remain in the ledger and never become valid
merely because a retry later succeeds. An exclusive process lock protects restartable outboxes.
"""
import argparse
import copy
import fcntl
import hashlib
import json
import os
import subprocess
import tempfile
import time
import zipfile
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
from pathlib import Path
from . import shadow,report
from .chronology import parse_utc

UTC=timezone.utc
ROOT=Path('research/shadow-operational')
FILES=('messages.jsonl','results_hl.json','results_bsd.json','details_hl.json','manual_results.json')
CODE=('scangoal/shadow.py','scangoal/shadow_ops.py','scangoal/parser.py','scangoal/report.py','scangoal/eligibility.py','scangoal/chronology.py')

class OpsError(RuntimeError):pass


def git(repo,*args):
    try:
        return subprocess.run(['git','-C',str(repo),*map(str,args)],capture_output=True,text=True,check=True,timeout=40).stdout.strip()
    except (subprocess.SubprocessError,OSError):
        raise OpsError('git operation failed: '+str(args[0])) from None  # never disclose credentials/URLs


def write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp')
    with temp.open('w') as f:
        json.dump(data,f,ensure_ascii=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    temp.replace(path)


def read(path,default=None):
    return json.loads(path.read_text()) if path.exists() else copy.deepcopy(default)


class Publisher:
    def __init__(self,repo,branch='shadow/h1-h2-operational',remote='origin',clock=None):
        self.repo=Path(repo).resolve();self.branch=branch;self.remote=remote
        self.clock=clock or (lambda:datetime.now(UTC))
        if branch in ('main','master') or not branch.startswith('shadow/') or remote!='origin':
            raise OpsError('dedicated shadow/ branch and origin required')
        if git(self.repo,'branch','--show-current')!=branch:raise OpsError('wrong checked-out branch')
        if git(self.repo,'rev-parse','--is-shallow-repository')!='false':raise OpsError('complete Git history required')
        self.last_verified=None
        self.state=self.repo/ROOT
        self.journal=Path(git(self.repo,'rev-parse','--absolute-git-dir'))/'shadow-runtime.jsonl'
    def log(self,kind,**detail):
        event={'kind':kind,'at':self.clock().isoformat(),**detail}
        with self.journal.open('a') as f:
            f.write(json.dumps(event,sort_keys=True)+'\n');f.flush();os.fsync(f.fileno())
    @contextmanager
    def locked(self):
        path=self.journal.with_suffix('.lock')
        with path.open('a') as f:
            try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise OpsError('concurrent SHADOW process') from None
            yield
    def commit(self,message):
        # Never stage the source collector's data or unrelated dirty files.
        git(self.repo,'add','--',str(ROOT))
        if git(self.repo,'diff','--cached','--name-only'):
            staged=git(self.repo,'diff','--cached','--name-only').splitlines()
            if any(not p.startswith(str(ROOT)+'/') for p in staged):raise OpsError('unrelated staged files')
            git(self.repo,'-c','user.name=goal-roi-shadow','-c','user.email=shadow@users.noreply.github.com','commit','-m',message)
        return git(self.repo,'rev-parse','HEAD')
    def publish(self):
        sha=git(self.repo,'rev-parse','HEAD')
        if self.last_verified and self.last_verified[0]==sha and self.clock()-self.last_verified[1]<timedelta(seconds=60):
            return self.last_verified
        # No force-push, merge, reset or rebase. A conflict is preserved for explicit reconciliation.
        git(self.repo,'push',self.remote,'HEAD:refs/heads/'+self.branch)
        line=git(self.repo,'ls-remote',self.remote,'refs/heads/'+self.branch)
        if not line or line.split()[0]!=sha:raise OpsError('remote publication unconfirmed')
        at=self.clock()
        self.log('remote_verified',commit_sha=sha,verified_utc=at.isoformat())
        self.last_verified=(sha,at)
        return sha,at


def approved(source,authorization):
    a=read(Path(authorization),{})
    if a.get('authorized') is not True or a.get('installed') is not True or not a.get('authorization_reference') or not a.get('installed_runner_reference'):
        raise OpsError('explicit authorization and installed runner proof required')
    stamp=parse_utc(a.get('authorized_utc'))
    if stamp is None:raise OpsError('authorization timestamp missing')
    sha=a.get('approved_code_sha','')
    if len(sha)!=40:raise OpsError('approved code SHA required')
    # Data commits may advance; the authorized implementation must remain exactly the reviewed one.
    if any(not (Path(source)/p).is_file() for p in CODE):raise OpsError('reviewed implementation not installed')
    if git(source,'diff',sha,'--',*CODE):raise OpsError('source code differs from authorized SHA')
    return a


def activate(pub,source,authorization,historical):
    with pub.locked():
        a=approved(source,authorization)
        previous=read(pub.state/'current.json',{})
        if previous.get('activation_state')=='active':raise OpsError('already initialized; never replace an active operational protocol')
        # Confirm that the installed dedicated publisher actually works before starting any new clock.
        pub.publish()
        if previous:pub.log('superseded_failed_activation',protocol=previous['protocol_sha256'])
        now=pub.clock()
        if parse_utc(a['authorized_utc'])>now:raise OpsError('future authorization')
        p=read(Path(historical));old_hash=shadow.digest(p)
        p.update(version=2,main_sha=git(source,'rev-parse','HEAD'),frozen_utc=now.isoformat(),end_utc=(now+timedelta(days=30)).isoformat(),
                 activation='operational; explicit approval; collection only after remote genesis verification',
                 historical_protocol_sha256=old_hash,authorization_reference=a['authorization_reference'],
                 git_timestamp_resolution_seconds=1,exact_remote_receipts_required=True)
        ident=shadow.digest(p)
        write(pub.state/'protocols'/f'{ident}.json',p)
        write(pub.state/'ledgers'/f'{ident}.json',shadow.new_ledger(p))
        write(pub.state/'current.json',{'protocol_sha256':ident,'authorized_code_sha':a['approved_code_sha'],'activation_state':'pending'})
        write(pub.state/'receipts.json',[])
        pub.commit('shadow: operational genesis; no observations')
        try:sha,observed=pub.publish()
        except OpsError:
            pub.log('activation_pending');raise
        if observed>now+timedelta(minutes=5):raise OpsError('activation publication late; create a fresh version')
        current=read(pub.state/'current.json');current.update(activation_state='active',genesis_receipt={'commit_sha':sha,'verified_utc':observed.isoformat()})
        write(pub.state/'current.json',current);pub.commit('shadow: activation publication confirmed')
        try:pub.publish()
        except OpsError:
            current['activation_state']='pending';write(pub.state/'current.json',current)
            pub.log('activation_pending');raise
        pub.log('activation_published',protocol=ident,commit_sha=sha)
        return ident


@contextmanager
def frozen_inputs(source):
    """Coherent local data copy: retry rather than ingest a partially appended message file."""
    source=Path(source)
    with tempfile.TemporaryDirectory(prefix='goal-shadow-input-') as name:
        dst=Path(name)
        for attempt in range(3):
            before={p:(source/p).read_bytes() for p in FILES if (source/p).exists()}
            after={p:(source/p).read_bytes() for p in FILES if (source/p).exists()}
            if before==after:break
        else:raise OpsError('collector snapshot changed during copy')
        for p,body in before.items():(dst/p).write_bytes(body)
        previous=os.environ.get('SCANGOAL_DATA_DIR');os.environ['SCANGOAL_DATA_DIR']=str(dst)
        try:yield dst,{p:hashlib.sha256(v).hexdigest() for p,v in before.items()}
        finally:
            if previous is None:os.environ.pop('SCANGOAL_DATA_DIR',None)
            else:os.environ['SCANGOAL_DATA_DIR']=previous


def knowledge(ledger,now):
    """First result/mark observed anywhere in the input snapshot, not only the final source used."""
    from .store import data_dir
    c={};msgs,ft,marks,notes,_=report.load(c);times={}
    def add(mid,stamp):times.setdefault(mid,[]).append(parse_utc(stamp))
    for mid,stamp in c.get('ft_any',{}).items():add(mid,stamp)
    for key,stamp in c.get('marks',{}).items():add(key[0],stamp)
    for name in ('hl','bsd'):
        for mid,r in read(data_dir()/('results_'+name+'.json'),{}).get('results',{}).items():add(mid,r.get('matched_at_utc'))
    for mid,r in read(data_dir()/'details_hl.json',{}).get('details',{}).items():add(mid,r.get('fetched_at_utc'))
    for m in msgs:
        hit=report.note_match(m,notes) if m.get('teams') else None
        if hit:add(m.get('match_id'),c.get('notes',{}).get((hit[0],tuple(hit[1]),tuple(hit[2]))))
    chosen=shadow.firsts(ledger)['H2']
    prior={e['match_id']:e for e in ledger['events'] if e['kind']=='knowledge'}
    for mid,ts in times.items():
        if mid not in chosen:continue
        good=[t for t in ts if t];unknown=any(t is None for t in ts)
        stamp=min(good).isoformat() if good else None
        if prior.get(mid,{}).get('observed_utc')==stamp and prior.get(mid,{}).get('unknown_timestamp')==unknown:continue
        shadow.append(ledger,{'kind':'knowledge','at':now.isoformat(),'match_id':mid,'observed_utc':stamp,'unknown_timestamp':unknown})


def check_receipts(p,ledger,receipts):
    shadow.verify(p,ledger);problems=[]
    index={}
    for r in receipts:
        for h in r['decision_hashes']:index[h]=r
    known={}
    for e in ledger['events']:
        if e['kind']=='knowledge':
            if e['unknown_timestamp']:problems.append('unknown result chronology: '+str(e['match_id']))
            if e['observed_utc']:
                t=parse_utc(e['observed_utc']);known[e['match_id']]=min(t,known.get(e['match_id'],t))
    for e in ledger['events']:
        if e['kind']!='decision':continue
        r=index.get(e['hash']);at=parse_utc(e['at']);published=parse_utc(r.get('verified_utc')) if r else None
        if not r or not r.get('timely') or published is None or not at<=published<=at+timedelta(minutes=5):
            problems.append('unpublished or late decision: '+e['hash']);continue
        first=known.get(e['bet']['match_id'])
        if first is not None and published>=first:problems.append('publication after result: '+e['hash'])
    return problems


def tick(pub,source,authorization):
    """Resume pending local commits first. Never observe fresh inputs before the push barrier succeeds."""
    with pub.locked():
        approved(source,authorization)
        current=read(pub.state/'current.json')
        if not current or current.get('activation_state')!='active':raise OpsError('operational protocol not remotely activated')
        ident=current['protocol_sha256'];p=read(pub.state/'protocols'/f'{ident}.json')
        path=pub.state/'ledgers'/f'{ident}.json';ledger=read(path);shadow.verify(p,ledger)
        receipts=read(pub.state/'receipts.json',[])
        # Recover a crash between fsync/rename and git commit before issuing any receipt.
        pub.commit('shadow: resume durable outbox before observing new inputs')
        # Publishing a prior failed commit must happen before reading the collector again.
        sha,published=pub.publish()
        receipted={h for r in receipts for h in r['decision_hashes']}
        pending=[e for e in ledger['events'] if e['kind']=='decision' and e['hash'] not in receipted]
        if pending:
            receipts.append({'commit_sha':sha,'verified_remote_sha':sha,'verified_utc':published.isoformat(),
                             'decision_hashes':[e['hash'] for e in pending],
                             'timely':all(published<=parse_utc(e['at'])+timedelta(minutes=5) for e in pending)})
            write(pub.state/'receipts.json',receipts);pub.commit('shadow: durable remote publication receipt');pub.publish()
        before=copy.deepcopy(ledger)
        with frozen_inputs(Path(source)/'data') as (snapshot,hashes):
            now=pub.clock();shadow.collect_project(p,ledger,now);knowledge(ledger,now)
            msgs,_,_,_,_=report.load()
            emitted={e.get('message_id') for e in ledger['events'] if e['kind'] in ('decision','subsequent')}
            seen_missed={e.get('message_id') for e in ledger['events'] if e['kind']=='missed'}
            for m in msgs:
                sent=parse_utc(m.get('date_utc'))
                if m.get('type')=='OPEN_GAME' and sent and parse_utc(p['frozen_utc'])<=sent<=now and m['message_id'] not in emitted|seen_missed:
                    shadow.append(ledger,{'kind':'missed','at':now.isoformat(),'message_id':m['message_id'],
                                         'reason':'outside_120s' if now-sent>timedelta(seconds=120) else 'ineligible_or_result_already_known',
                                         'source_sha256':hashes.get('messages.jsonl')})
        if ledger==before:
            problems=check_receipts(p,ledger,receipts)
            return {'state':'quarantined' if problems else 'idle','observations':len(shadow.firsts(ledger)['H2']),'problems':problems}
        write(path,ledger);pub.commit('shadow: decisions and observations; immutable input snapshots')
        sha,published=pub.publish()
        new=[e for e in ledger['events'][len(before['events']):] if e['kind']=='decision']
        if new:
            receipts.append({'commit_sha':sha,'verified_remote_sha':sha,'verified_utc':published.isoformat(),
                             'decision_hashes':[e['hash'] for e in new],
                             'timely':all(published<=parse_utc(e['at'])+timedelta(minutes=5) for e in new)})
            write(pub.state/'receipts.json',receipts);pub.commit('shadow: durable remote publication receipt');pub.publish()
        problems=check_receipts(p,ledger,receipts)
        pub.log('cycle_complete',protocol=ident,problems=problems)
        return {'state':'published' if not problems else 'quarantined','observations':len(shadow.firsts(ledger)['H2']),'problems':problems}


def evaluate(pub):
    """Scientific analysis also requires the actual remote receipts, not just old Git commit dates."""
    from .certification import published_versions
    with pub.locked():
        pub.publish()
        current=read(pub.state/'current.json');ident=current['protocol_sha256']
        path=pub.state/'ledgers'/f'{ident}.json';p=read(pub.state/'protocols'/f'{ident}.json');ledger=read(path)
        receipts=read(pub.state/'receipts.json',[]);problems=check_receipts(p,ledger,receipts)
        for r in receipts:
            try:
                git(pub.repo,'merge-base','--is-ancestor',r['commit_sha'],'HEAD')
                committed=json.loads(git(pub.repo,'show',r['commit_sha']+':'+str(ROOT/'ledgers'/f'{ident}.json')))
                hashes={e['hash'] for e in committed['events'] if e['kind']=='decision'}
                if not set(r['decision_hashes'])<=hashes or r['verified_remote_sha']!=r['commit_sha']:
                    problems.append('receipt not anchored to its published decisions')
            except (OpsError,ValueError):problems.append('receipt Git proof unavailable')
        versions,history_problems=published_versions(path)
        result=shadow.analyse(p,ledger,pub.clock(),versions if not history_problems else None)
        result['operational_publication_problems']=problems+history_problems
        result['continuous_monitoring']=False  # no scheduler liveness assertion from a single process execution
        if problems or history_problems:
            result['external_history_verified']=False
            result['scientific_state']='INCONCLUSIVO'
            result['sequential']['statuses']={k:'INCONCLUSIVO' for k in ('H1','H2','delta')}
        return result

def export_outbox(pub,destination,max_bytes=5_000_000):
    """Small incremental Git bundle + fsynced scientific files; never archive Git credentials/config."""
    with pub.locked():
        journal=[json.loads(line) for line in pub.journal.read_text().splitlines() if line.strip()] if pub.journal.exists() else []
        verified=[e['commit_sha'] for e in journal if e['kind']=='remote_verified']
        base=verified[-1] if verified else git(pub.repo,'rev-parse','refs/remotes/origin/'+pub.branch)
        head=git(pub.repo,'rev-parse','HEAD');files={str(p.relative_to(pub.repo)):p.read_bytes() for p in pub.state.rglob('*.json')}
        manifest={'branch':pub.branch,'base_sha':base,'local_head':head,'files_sha256':{p:hashlib.sha256(body).hexdigest() for p,body in files.items()},'runtime_journal':journal}
        with tempfile.TemporaryDirectory(prefix='shadow-outbox-') as folder:
            bundle=Path(folder)/'pending.bundle'
            if head!=base:git(pub.repo,'bundle','create',bundle,'HEAD','^'+base)
            target=Path(destination)
            if target.exists():raise OpsError('outbox destination exists')
            with zipfile.ZipFile(target,'x',compression=zipfile.ZIP_DEFLATED) as z:
                z.writestr('manifest.json',json.dumps(manifest,sort_keys=True))
                for path,body in files.items():z.writestr(path,body)
                if bundle.exists():z.write(bundle,'pending.bundle')
            if target.stat().st_size>max_bytes:
                target.unlink();raise OpsError('outbox exceeds fixed free-storage budget')
        pub.log('outbox_exported',bytes=target.stat().st_size,head=head)
        return manifest


def restore_outbox(pub,archive,max_bytes=5_000_000):
    with pub.locked():
        path=Path(archive)
        if path.stat().st_size>max_bytes:raise OpsError('outbox too large')
        with zipfile.ZipFile(path) as z:
            if sum(x.file_size for x in z.infolist())>25_000_000:raise OpsError('outbox expanded size exceeds budget')
            manifest=json.loads(z.read('manifest.json'));files=manifest['files_sha256']
            allowed=set(files)|{'manifest.json','pending.bundle'}
            if set(z.namelist())-allowed or manifest['branch']!=pub.branch:raise OpsError('unexpected outbox entries/branch')
            if any(not p.startswith(str(ROOT)+'/') or '..' in Path(p).parts or Path(p).is_absolute() for p in files):raise OpsError('unsafe outbox path')
            bodies={p:z.read(p) for p in files}
            if any(hashlib.sha256(bodies[p]).hexdigest()!=expected for p,expected in files.items()):raise OpsError('outbox hash mismatch')
            head=git(pub.repo,'rev-parse','HEAD')
            if head not in (manifest['base_sha'],manifest['local_head']):
                # An old artifact is a no-op only when every saved ledger is already a prefix remotely.
                covered=True
                for p,body in bodies.items():
                    if '/ledgers/' not in p:continue
                    old=json.loads(body);new=read(pub.repo/p,{})
                    if new.get('events',[])[:len(old['events'])]!=old['events']:covered=False
                try:git(pub.repo,'merge-base','--is-ancestor',manifest['local_head'],'HEAD')
                except OpsError:covered=False
                if covered:return {'state':'already_published'}
                raise OpsError('outbox conflicts with advanced remote; retained for explicit review')
            if manifest['local_head']!=head:
                with tempfile.TemporaryDirectory(prefix='shadow-bundle-') as folder:
                    bundle=Path(folder)/'pending.bundle';bundle.write_bytes(z.read('pending.bundle'))
                    git(pub.repo,'bundle','verify',bundle);git(pub.repo,'fetch',bundle,manifest['local_head'])
                    git(pub.repo,'merge','--ff-only',manifest['local_head'])
            # Verify all ledgers against their content-addressed immutable protocols before touching the checkout.
            for p,body in bodies.items():
                if '/ledgers/' not in p:continue
                ledger=json.loads(body);ident=Path(p).stem;protocol=json.loads(bodies[str(ROOT/'protocols'/f'{ident}.json')])
                if shadow.digest(protocol)!=ident:raise OpsError('protocol content address mismatch')
                shadow.verify(protocol,ledger)
                prior=read(pub.repo/p,{})
                if ledger['events'][:len(prior.get('events',[]))]!=prior.get('events',[]):raise OpsError('outbox rewrites a ledger prefix')
            receipts_path=str(ROOT/'receipts.json')
            if receipts_path in bodies:
                existing=read(pub.state/'receipts.json',[])
                restored_receipts=json.loads(bodies[receipts_path])
                if restored_receipts[:len(existing)]!=existing:raise OpsError('outbox rewrites receipt history')
            for p,body in bodies.items():
                destination=pub.repo/p;destination.parent.mkdir(parents=True,exist_ok=True)
                with destination.open('wb') as f:f.write(body);f.flush();os.fsync(f.fileno())
            prior_lines=pub.journal.read_text().splitlines() if pub.journal.exists() else []
            existing_events={shadow.digest(json.loads(line)) for line in prior_lines if line.strip()}
            with pub.journal.open('a') as f:
                for event in manifest.get('runtime_journal',[]):
                    if shadow.digest(event) not in existing_events:f.write(json.dumps(event,sort_keys=True)+'\n')
                f.flush();os.fsync(f.fileno())
            pub.log('outbox_restored',head=manifest['local_head'])
            return {'state':'restored','head':manifest['local_head']}

def supervise(pub,source,authorization,seconds=480,poll=10,sleep=time.sleep,monotonic=time.monotonic):
    """Bounded sidecar for the existing collection job, no Telegram/API call or scheduler creation."""
    if not 5<=poll<=60 or not 0<seconds<=1080:raise OpsError('bounded free-runtime limits required')
    end=monotonic()+seconds;cycles=0
    while monotonic()<end:
        result=tick(pub,source,authorization);cycles+=1
        pub.log('heartbeat',state=result['state'],observations=result['observations'])
        if result['problems']:raise OpsError('publication or chronology quarantine; visible nonzero exit')
        sleep(min(poll,max(0,end-monotonic())))
    return {'state':'completed','cycles':cycles,'provider_requests':0,'continuous_monitoring':False}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['activate','tick','analyse','supervise','export-outbox','restore-outbox']);ap.add_argument('--repo',required=True)
    ap.add_argument('--source',required=True);ap.add_argument('--authorization',required=True)
    ap.add_argument('--historical',default='audits/open-game-shadow/protocol.json');ap.add_argument('--seconds',type=int,default=480);ap.add_argument('--poll-seconds',type=int,default=10);ap.add_argument('--outbox');a=ap.parse_args()
    try:
        pub=Publisher(a.repo)
        if a.action in ('export-outbox','restore-outbox'):
            if not a.outbox:ap.error('--outbox required')
            result=export_outbox(pub,a.outbox) if a.action=='export-outbox' else restore_outbox(pub,a.outbox)
        else:result=activate(pub,a.source,a.authorization,a.historical) if a.action=='activate' else (tick(pub,a.source,a.authorization) if a.action=='tick' else (evaluate(pub) if a.action=='analyse' else supervise(pub,a.source,a.authorization,a.seconds,a.poll_seconds)))
        print(json.dumps(result))
        if isinstance(result,dict) and (result.get('problems') or result.get('operational_publication_problems')):
            raise SystemExit(3)
    except (OpsError,ValueError,KeyError) as exc:
        if 'pub' in locals():pub.log('failure',error=type(exc).__name__)
        print('SHADOW_FAILED',type(exc).__name__);raise SystemExit(3)

if __name__=='__main__':main()
