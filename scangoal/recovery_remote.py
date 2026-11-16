"""Remote Git journal and authenticated GitHub metadata; no sports transport.

No bootstrap, ref creation, force push or local fallback. Hosting protection and
account exclusivity remain activation prerequisites, not inferred from this code.
"""
import json
import os
import re
import subprocess
from urllib.request import Request, build_opener, HTTPRedirectHandler
from .recovery_trial import TrialBlocked
from .recovery_trial_journal import OfflineGitStore, REF

REPOSITORY = 'nunovinhas-creator/goal-roi'


class RemoteGitStore(OfflineGitStore):
    """Use an isolated bare object cache and a separately provisioned remote.

    A candidate must be a direct child of the expected head. Non-force receive-
    pack then uses Git's atomic ref transaction: competing children cannot both
    advance the ref. Every read fetches the remote; cached heads never authorize.
    """
    def __init__(self, path, remote, anchor):
        super().__init__(path)
        if not re.fullmatch('[0-9a-f]{40}', anchor):
            raise TrialBlocked('fixed_anchor_required')
        if not (remote == 'https://github.com/'+REPOSITORY+'.git' or remote.startswith('file:///')):
            raise TrialBlocked('remote_not_allowed')
        self.remote, self.anchor = remote, anchor

    def transfer(self, *args):
        env = dict(os.environ, GIT_TERMINAL_PROMPT='0')
        try:
            return subprocess.check_output(['git', '--git-dir='+str(self.path), *args],
                env=env, stderr=subprocess.DEVNULL, timeout=10)
        except Exception:
            raise TrialBlocked('remote_git_operation_uncertain') from None

    def head(self):
        self.transfer('fetch', '--no-tags', self.remote, REF)
        head = self.git('rev-parse', '--verify', 'FETCH_HEAD').decode().strip()
        cursor = head
        for _ in range(32):  # five reservations + five completions, no unbounded history
            parent, event = self.read(cursor)
            if cursor == self.anchor:
                if parent is not None or event.get('kind') != 'genesis':
                    break
                return head
            if parent is None:
                break
            cursor = parent
        raise TrialBlocked('remote_anchor_missing_or_replaced')

    def propose(self, parent, event):
        if parent is None:
            raise TrialBlocked('remote_bootstrap_forbidden')
        return super().propose(parent, event)

    def cas(self, previous, candidate):
        if previous is None or self.read(candidate)[0] != previous:
            raise TrialBlocked('remote_direct_child_required')
        if self.head() != previous:
            return False
        try:
            self.transfer('push', '--porcelain', self.remote, candidate+':'+REF)
        except TrialBlocked:
            # Confirm by authenticated fresh read; never retry the write or grant
            # send permission following an uncertain write, even if it committed.
            try:
                observed = self.head()
            except TrialBlocked:
                raise TrialBlocked('remote_write_and_confirmation_uncertain') from None
            if observed == candidate:
                raise TrialBlocked('remote_write_confirmed_slot_burned') from None
            raise TrialBlocked('remote_write_not_confirmed_no_send') from None
        if self.head() != candidate:
            raise TrialBlocked('remote_postwrite_confirmation_failed')
        return True


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class GitHubAttempts:
    """Read exact attempts with existing GitHub credential, never BSD credential.

    Exceptions and response bodies are not logged. No user-supplied proof blob is
    accepted by abandon(). Tests replace the HTTPS opener, not validation.
    """
    def __init__(self, token):
        if not isinstance(token, str) or not token.strip():
            raise TrialBlocked('github_auth_missing')
        self._token = token

    def read(self, run_id, attempt):
        if type(run_id) is not int or type(attempt) is not int or min(run_id, attempt) < 1:
            raise TrialBlocked('invalid_run_identity')
        url = f'https://api.github.com/repos/{REPOSITORY}/actions/runs/{run_id}/attempts/{attempt}'
        request = Request(url, headers={'Authorization':'Bearer '+self._token,
            'Accept':'application/vnd.github+json', 'X-GitHub-Api-Version':'2022-11-28'})
        try:
            with build_opener(NoRedirect()).open(request, timeout=10) as response:
                if response.status != 200 or response.geturl() != url:
                    raise ValueError()
                raw = response.read(1_000_001)
            if len(raw) > 1_000_000 or self._token.encode() in raw:
                raise ValueError()
            proof = json.loads(raw)
            if (proof['id'] != run_id or proof['run_attempt'] != attempt
                or proof['repository']['full_name'] != REPOSITORY
                or proof['head_repository']['full_name'] != REPOSITORY
                or proof['status'] != 'completed'
                or proof['path'] != '.github/workflows/collect.yml'
                or proof['head_branch'] != 'main' or not proof.get('conclusion')):
                raise ValueError()
            # Preserve only the identity fields; provider body cannot become an
            # arbitrary persisted secret or inject journal events.
            evidence = {k:proof[k] for k in ('id','run_attempt','status','path',
                'head_branch','conclusion')}
            evidence.update(repository={'full_name':REPOSITORY},
                            head_repository={'full_name':REPOSITORY})
            return json.dumps(evidence).encode()
        except Exception:
            raise TrialBlocked('authenticated_terminal_attempt_unproven') from None

    def abandon(self, slots, operation_id):
        _, state = slots.load()
        prior = state['attempts'].get(operation_id)
        if not prior or prior['outcome'] != 'reserved':
            raise TrialBlocked('no_uncertain_reservation')
        try:
            run_id, attempt = map(int, prior['owner'].split(':'))
        except (ValueError, AttributeError):
            raise TrialBlocked('invalid_run_owner') from None
        return slots.abandon(operation_id, self.read(run_id, attempt))
