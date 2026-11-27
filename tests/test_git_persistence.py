"""P1: falha silenciosa de persistência git (execução #814, passo `Commit data from the fast reads`, código 128).
P2: o passo das leituras rápidas deixa de ser tolerado (falha = execução vermelha) e tem uma só nova tentativa de rebase+push
quando o push é recusado porque o remoto avançou.

Corre os comandos REAIS dos passos `Commit data` e `Commit data from the fast reads`, lidos de .github/workflows/collect.yml,
contra repositórios git temporários: um repositório bare faz de GitHub, cada runner é um clone novo. Tudo offline (o remoto é
um caminho local), sem credenciais, sem tocar nos dados de produção (são só copiados para a pasta temporária).

O runner do GitHub não tem identidade git global e a deteção automática dá um nome vazio ("empty ident name"). Aqui o mesmo
estado é simulado de forma determinística em qualquer máquina: configuração global isolada com `user.useConfigOnly=true`
(nenhuma identidade fora do que o próprio passo configura) e todas as variáveis GIT_* do ambiente removidas.
"""
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from itertools import takewhile

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github' / 'workflows' / 'collect.yml'
IDENTITY = ('scangoal-collector', 'actions@users.noreply.github.com')
STEPS = ('Commit data', 'Commit data from the fast reads')
DATA = ('data/messages.jsonl', 'data/notify_log.jsonl', 'data/notify_state.json', 'data/state.json', 'docs/report.json', 'docs/bets.csv')

# Script dos dois passos no `main` antes da correção (f7a9853), copiado literalmente: os testes de CI fazem checkout
# superficial e não podem ler a versão antiga do histórico.
PRE_FIX = '''git add data docs/report.json docs/bets.csv
if git diff --cached --quiet; then echo "no changes"; exit 0; fi
git -c user.name="scangoal-collector" -c user.email="actions@users.noreply.github.com" commit -q -m "data: collect [skip ci]"
git pull -q --rebase origin "${GITHUB_REF_NAME}" && git push -q origin "HEAD:${GITHUB_REF_NAME}"
'''


# Envolve o git real só para observar e provocar a corrida: regista cada subcomando e cada push e, antes do push n, corre
# `before_push_<n>` (outro escritor publica no remoto), como se o remoto tivesse avançado entre o pull e o push.
SHIM = '''#!/bin/bash
argv=("$@")
while [[ "$1" = -C || "$1" = -c ]]; do shift 2; done
subcommand="$1"
echo "$subcommand" >> "$SHIM_DIR/subcommands"
if [ "$subcommand" = push ]; then
  n=$(( $(cat "$SHIM_DIR/pushes" 2>/dev/null || echo 0) + 1 )); echo $n > "$SHIM_DIR/pushes"
  echo "$*" >> "$SHIM_DIR/push_args"
  if [ -x "$SHIM_DIR/before_push_$n" ]; then "$SHIM_DIR/before_push_$n" >/dev/null 2>&1 || exit 99; fi
fi
exec "$REAL_GIT" "${argv[@]}"
'''


def step_script(name, text=None):
    """Return the exact command configured for the named workflow step."""
    lines = (text or WORKFLOW.read_text(encoding='utf-8')).splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == '- name: %s' % name)
    index = next(i for i in range(start + 1, len(lines)) if lines[i].startswith('        run: '))
    command = lines[index].removeprefix('        run: ')
    if command != '|':
        return command
    body = takewhile(lambda line: line.startswith('          '), lines[index + 1:])
    return '\n'.join(line[10:] for line in body)


class Sandbox:
    """Remoto bare + configuração global isolada sem identidade (como o runner) + ambiente sem GIT_* herdado."""

    def __init__(self, tmp):
        self.tmp = Path(tmp)
        self.home = self.tmp / 'home'
        self.home.mkdir()
        self.gitconfig = self.home / '.gitconfig'
        self.gitconfig.write_text('[user]\n\tuseConfigOnly = true\n[commit]\n\tgpgsign = false\n[init]\n\tdefaultBranch = main\n')
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_') and k not in ('EMAIL',)}
        self.env.update(HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / '.config'), GIT_CONFIG_GLOBAL=str(self.gitconfig),
                        GIT_CONFIG_NOSYSTEM='1', GIT_TERMINAL_PROMPT='0', GITHUB_REF_NAME='main', LC_ALL='C')
        self.origin = self.tmp / 'origin.git'
        self.raw('init', '-q', '--bare', '-b', 'main', str(self.origin), cwd=self.tmp)
        seed = self.clone('seed')
        for rel in DATA:                                   # dados reais copiados (só leitura em produção)
            src, dst = ROOT / rel, seed / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.exists():
                shutil.copyfile(src, dst)
            else:
                dst.write_text('')
        (seed / 'README.md').write_text('seed\n')
        self.human(seed, 'add', '-A')
        self.human(seed, 'commit', '-q', '-m', 'seed')
        self.human(seed, 'push', '-q', 'origin', 'HEAD:main')

    def raw(self, *args, cwd, check=True):
        r = subprocess.run(['git', *args], cwd=str(cwd), env=self.env, capture_output=True, text=True, timeout=60)
        if check and r.returncode:
            raise AssertionError('git %s: %s' % (' '.join(args), r.stderr))
        return r

    def human(self, repo, *args, check=True):
        """Outro escritor do remoto (merge de uma PR, por exemplo), com identidade própria só neste comando."""
        return self.raw('-c', 'user.name=other', '-c', 'user.email=other@invalid', *args, cwd=repo, check=check)

    def clone(self, name):
        self.raw('clone', '-q', str(self.origin), name, cwd=self.tmp)
        return self.tmp / name

    def run_step(self, repo, script, shim=False):
        env = dict(self.env, PYTHONPATH=str(ROOT))
        if shim:
            env = dict(env, SHIM_DIR=str(self.shim_dir), REAL_GIT=shutil.which('git', path=self.env['PATH']),
                       PATH=str(self.shim_dir / 'bin') + os.pathsep + self.env['PATH'])
        return subprocess.run(['bash', '-e', '-c', script], cwd=str(repo), env=env, capture_output=True, text=True, timeout=120)

    @property
    def shim_dir(self):
        d = self.tmp / 'shim'
        if not d.exists():
            (d / 'bin').mkdir(parents=True)
            (d / 'bin' / 'git').write_text(SHIM)
            (d / 'bin' / 'git').chmod(0o755)
        return d

    def race_before_push(self, n, rel, lines, name='racer'):
        """Antes do push n do passo, outro escritor acrescenta `lines` a `rel` e publica (o remoto avança)."""
        repo = self.tmp / name
        if not repo.exists():
            self.clone(name)
        git = '"$REAL_GIT" -C %s -c user.name=other -c user.email=other@invalid' % repo
        body = ''.join('printf %%s\\\\n %s >> %s\n' % (shlex.quote(l), shlex.quote(str(repo / rel))) for l in lines)
        hook = self.shim_dir / ('before_push_%d' % n)
        hook.write_text('#!/bin/bash\nset -e\n%s pull -q --rebase origin main\n%s%s add -A\n%s commit -q -m "race %d"\n'
                        '%s push -q origin HEAD:main\n' % (git, body, git, git, n, git))
        hook.chmod(0o755)

    def shim_log(self, name):
        p = self.shim_dir / name
        return p.read_text().split('\n')[:-1] if p.exists() else []

    def remote_head(self):
        return self.raw('rev-parse', 'main', cwd=self.origin).stdout.strip()

    def remote_file(self, rel, rev='main'):
        return self.raw('show', '%s:%s' % (rev, rel), cwd=self.origin).stdout

    def advance_remote(self, rel, mutate, name='other'):
        """Outro escritor publica um commit no remoto (o runner não o tem)."""
        repo = self.tmp / name
        if repo.exists():
            self.human(repo, 'pull', '-q', '--rebase', 'origin', 'main')
        else:
            repo = self.clone(name)
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(mutate(path.read_text() if path.exists() else ''))
        self.human(repo, 'add', '-A')
        self.human(repo, 'commit', '-q', '-m', 'remote: %s' % rel)
        self.human(repo, 'push', '-q', 'origin', 'HEAD:main')
        return self.remote_head()


def append(rel_lines):
    return lambda text: text + ''.join(l + '\n' for l in rel_lines)


def new_messages(tag, n=3):
    return [json.dumps({'id': 10_000_000 + i, 'tag': tag, 'text': 'msg %s %d' % (tag, i)}, sort_keys=True) for i in range(n)]


def collect(repo, tag):
    """O que uma recolha/leitura rápida escreve: novas mensagens, avisos e estado das notificações, relatório."""
    lines = new_messages(tag)
    with open(repo / 'data/messages.jsonl', 'a') as f:
        f.write(''.join(l + '\n' for l in lines))
    with open(repo / 'data/notify_log.jsonl', 'a') as f:
        f.write(json.dumps({'event': 'delivered', 'key': 'k-%s' % tag, 'v': 1}) + '\n')
    state = json.loads((repo / 'data/notify_state.json').read_text() or '{}')
    state['last_run_utc'] = 'run-%s' % tag
    (repo / 'data/notify_state.json').write_text(json.dumps(state, indent=1, ensure_ascii=False, sort_keys=True) + '\n')
    (repo / 'docs/report.json').write_text(json.dumps({'run': tag}) + '\n')
    return lines


class WorkflowScriptTests(unittest.TestCase):
    def test_both_steps_run_the_publisher(self):
        for name in STEPS:
            self.assertEqual(step_script(name), 'python -u -m scangoal.publication publish')
        self.assertIn('python -u -m scangoal.publication resume', WORKFLOW.read_text(encoding='utf-8'))

    def test_unchanged_step_guards(self):
        text = WORKFLOW.read_text(encoding='utf-8')
        main = text[text.index('- name: Commit data\n'):text.index('- name: Fast reads')]
        fast = text[text.index('- name: Commit data from the fast reads'):text.index('- name: Start the next collection')]
        self.assertNotIn('continue-on-error', main)          # a recolha principal falha de forma visível
        self.assertNotIn('continue-on-error', fast)          # P2: uma falha de persistência deixa a execução vermelha
        guard = fast.split('\n')[1]
        self.assertNotIn("steps.resume.outcome", guard)     # the data of the fast reads is published even after a failed resume
        notices = text[text.index("- name: Telegram notices through the user's bot"):text.index('- name: Commit data\n')]
        self.assertIn("steps.published.outcome == 'success'", notices)
        published = text[text.index('- name: Publish the report before sending notices'):text.index("- name: Telegram notices")]
        self.assertIn("steps.resume.outcome == 'success'", published)
        self.assertIn('group: scangoal-write\n', text)
        self.assertNotIn('DURABLE_URL', text)
        self.assertNotIn('DURABLE_TOKEN', text)
        self.assertIn('cancel-in-progress: false', text)
        self.assertIn("cron: '3,13,23,33,43,53 * * * *'", text)


class GitPersistenceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.sb = Sandbox(self._tmp.name)
        self.global_before = self.sb.gitconfig.read_text()

    def tearDown(self):
        self.assertEqual(self.sb.gitconfig.read_text(), self.global_before, 'global git configuration must never change')
        self._tmp.cleanup()

    def assert_published(self, runner, msgs_before, new_lines):
        """O remoto tem o commit do coletor, com a identidade técnica, e os ficheiros do runner byte a byte."""
        r = self.sb.raw('log', '-1', '--format=%an|%ae|%cn|%ce|%s', 'main', cwd=self.sb.origin).stdout.strip()
        self.assertEqual(r, '|'.join(IDENTITY * 2) + '|data: collect [skip ci]')
        for rel in ('data/messages.jsonl', 'data/notify_log.jsonl', 'data/notify_state.json', 'docs/report.json'):
            self.assertEqual(self.sb.remote_file(rel), (runner / rel).read_text(), rel)
        remote_msgs = self.sb.remote_file('data/messages.jsonl')
        self.assertTrue(remote_msgs.startswith(msgs_before))              # append-only: nada reescrito
        for l in new_lines:
            self.assertEqual(remote_msgs.count(l + '\n'), 1)
        json.loads(self.sb.remote_file('data/notify_state.json'))
        self.assertEqual(self.sb.raw('status', '--porcelain', cwd=runner).stdout, '')

    # 0. Reprodução do defeito no main anterior à correção.
    def test_pre_fix_reproduces_exit_128_when_rebase_must_replay(self):
        runner = self.sb.clone('runner')
        collect(runner, 'fast')
        before = self.sb.advance_remote('README.md', append(['merge of a PR']))
        r = self.sb.run_step(runner, PRE_FIX)
        self.assertEqual(r.returncode, 128, r.stderr)
        self.assertIn('Committer identity unknown', r.stderr)
        self.assertEqual(self.sb.remote_head(), before)                     # nada foi publicado: a falha da #814

    def test_pre_fix_passes_when_the_remote_did_not_move(self):
        runner = self.sb.clone('runner')
        collect(runner, 'main')
        r = self.sb.run_step(runner, PRE_FIX)
        self.assertEqual(r.returncode, 0, r.stderr)                         # por isso o defeito só aparece às vezes

    # 1. Commit normal sem configuração git global.
    def test_commit_without_global_identity(self):
        for name in STEPS:
            with self.subTest(step=name):
                runner = self.sb.clone('runner-' + name.replace(' ', '-'))
                msgs = (runner / 'data/messages.jsonl').read_text()
                lines = collect(runner, name)
                r = self.sb.run_step(runner, step_script(name))
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assert_published(runner, msgs, lines)
                cfg = self.sb.raw('config', '--local', '--get-regexp', '^user\\.', cwd=runner, check=False).stdout
                self.assertEqual(cfg, '')

    def test_no_changes_exits_zero_without_commit(self):
        runner = self.sb.clone('runner')
        before = self.sb.remote_head()
        for name in STEPS:
            r = self.sb.run_step(runner, step_script(name))
            self.assertEqual((r.returncode, r.stdout.strip()), (0, ''))
        self.assertEqual(self.sb.remote_head(), before)

    # 2. Rebase com commit remoto não conflitante (o caso exato da #814: merge de uma PR durante as leituras rápidas).
    def test_rebase_over_non_conflicting_remote_commit(self):
        for name in STEPS:
            with self.subTest(step=name):
                runner = self.sb.clone('runner-' + name.replace(' ', '-'))
                msgs = (runner / 'data/messages.jsonl').read_text()
                lines = collect(runner, name)
                remote = self.sb.advance_remote('docs/index.html', append(['<p>%s</p>' % name]))
                r = self.sb.run_step(runner, step_script(name))
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assert_published(runner, msgs, lines)
                self.assertEqual(self.sb.raw('rev-parse', 'main~1', cwd=self.sb.origin).stdout.strip(), remote)  # linear, sem merge
                self.assertIn('<p>%s</p>' % name, self.sb.remote_file('docs/index.html'))

    # Sequência completa da execução: recolha principal publica primeiro; durante as leituras rápidas a barreira de avisos
    # publica do próprio runner e outro escritor avança o remoto; o segundo passo tem de reaplicar o commit.
    def test_full_run_main_then_fast_reads_with_remote_advancing(self):
        runner = self.sb.clone('runner')
        msgs0 = (runner / 'data/messages.jsonl').read_text()
        main_lines = collect(runner, 'main')
        self.assertEqual(self.sb.run_step(runner, step_script(STEPS[0])).returncode, 0)
        # Barreira de avisos (GitPublisher, notify.py): commit só dos ficheiros de notificação + pull --rebase --autostash + push.
        with open(runner / 'data/notify_log.jsonl', 'a') as f:
            f.write(json.dumps({'event': 'in_flight', 'key': 'k-intent', 'v': 1}) + '\n')
        ident = ('-c', 'user.name=%s' % IDENTITY[0], '-c', 'user.email=%s' % IDENTITY[1])
        self.sb.raw(*ident, 'add', '--', 'data/notify_state.json', 'data/notify_log.jsonl', cwd=runner)
        self.sb.raw(*ident, 'commit', '-q', '-m', 'notify: intent x [skip ci]', '--', 'data/notify_state.json', 'data/notify_log.jsonl', cwd=runner)
        self.sb.raw(*ident, 'push', '-q', 'origin', 'HEAD:main', cwd=runner)
        fast_lines = collect(runner, 'fast')
        self.sb.advance_remote('docs/index.html', append(['<p>PR merged</p>']))
        r = self.sb.run_step(runner, step_script(STEPS[1]))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assert_published(runner, msgs0, main_lines + fast_lines)
        # 6. Histórico do notify_log preservado: cada versão publicada é prefixo da seguinte (append-only).
        revs = self.sb.raw('log', '--reverse', '--format=%H', 'main', '--', 'data/notify_log.jsonl', cwd=self.sb.origin).stdout.split()
        versions = [self.sb.remote_file('data/notify_log.jsonl', rev) for rev in revs]
        self.assertEqual(len(versions), 4)                                  # seed, recolha principal, intenção, leituras rápidas
        for a, b in zip(versions, versions[1:]):
            self.assertTrue(b.startswith(a))
        self.assertIn('"k-intent"', versions[-1])
        # 7. notify_state.json íntegro e igual ao do runner.
        self.assertEqual(json.loads(self.sb.remote_file('data/notify_state.json'))['last_run_utc'], 'run-fast')

    # Primeiro passo sem alterações ("no changes", sai antes de configurar): o segundo tem de configurar sozinho.
    def test_fast_step_works_when_main_step_had_nothing_to_commit(self):
        runner = self.sb.clone('runner')
        self.assertEqual(self.sb.run_step(runner, step_script(STEPS[0])).stdout.strip(), '')
        msgs = (runner / 'data/messages.jsonl').read_text()
        lines = collect(runner, 'fast')
        self.sb.advance_remote('README.md', append(['x']))
        r = self.sb.run_step(runner, step_script(STEPS[1]))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assert_published(runner, msgs, lines)

    # 3a. Alterações concorrentes a OUTROS ficheiros de dados: rebase limpo, nada perdido de nenhum lado.
    def test_concurrent_change_to_other_data_file(self):
        runner = self.sb.clone('runner')
        msgs = (runner / 'data/messages.jsonl').read_text()
        lines = collect(runner, 'fast')
        self.sb.advance_remote('data/state.json', lambda t: json.dumps({'remote': True}) + '\n')
        r = self.sb.run_step(runner, step_script(STEPS[1]))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assert_published(runner, msgs, lines)
        self.assertEqual(json.loads(self.sb.remote_file('data/state.json')), {'remote': True})

    # 3b. Conflito real (os dois lados acrescentam ao fim de messages.jsonl): falha visível, sem resolução destrutiva.
    def test_real_conflict_fails_visibly_and_discards_nothing(self):
        runner = self.sb.clone('runner')
        lines = collect(runner, 'fast')
        local_msgs = (runner / 'data/messages.jsonl').read_text()
        remote_lines = new_messages('remote')
        before = self.sb.advance_remote('data/messages.jsonl', append(remote_lines))
        remote_msgs = self.sb.remote_file('data/messages.jsonl')
        r = self.sb.run_step(runner, step_script(STEPS[1]))
        self.assertNotEqual(r.returncode, 0)
        self.assertNotEqual(r.returncode, 128, r.stderr)                    # não é um erro de identidade
        self.assertIn('PUBLICATION_DEGRADED', r.stdout)
        self.assertNotIn('identity unknown', r.stderr)
        self.assertEqual(self.sb.remote_head(), before)                     # nada publicado por cima
        self.assertEqual(self.sb.remote_file('data/messages.jsonl'), remote_msgs)
        # O commit local continua intacto no runner (ORIG_HEAD / rebase em curso), com todas as linhas novas.
        local = self.sb.raw('show', 'HEAD:data/messages.jsonl', cwd=runner).stdout
        self.assertEqual(local, local_msgs)
        for l in lines:
            self.assertIn(l, local)
        self.assertTrue((runner / '.git' / 'goal-roi-publication' / 'pending.bundle').exists())
        self.assertFalse((runner / '.git' / 'rebase-merge').exists())

    # 4. Push rejeitado (o remoto recusa/avançou entre o pull e o push): falha visível, commit local mantido.
    def test_push_rejected_keeps_local_commit(self):
        hook = self.sb.origin / 'hooks' / 'pre-receive'
        hook.write_text('#!/bin/sh\necho "rejected: remote moved" >&2\nexit 1\n')
        hook.chmod(0o755)
        runner = self.sb.clone('runner')
        lines = collect(runner, 'main')
        before = self.sb.remote_head()
        r = self.sb.run_step(runner, step_script(STEPS[0]))
        self.assertNotEqual(r.returncode, 0)
        self.assertNotEqual(r.returncode, 128)
        self.assertIn('PUBLICATION_', r.stdout)
        self.assertEqual(self.sb.remote_head(), before)
        head = self.sb.raw('log', '-1', '--format=%cn|%ce|%s', 'HEAD', cwd=runner).stdout.strip()
        self.assertEqual(head, '%s|%s|data: collect [skip ci]' % IDENTITY)
        local = self.sb.raw('show', 'HEAD:data/messages.jsonl', cwd=runner).stdout
        for l in lines:
            self.assertIn(l, local)
        self.assertEqual(self.sb.raw('status', '--porcelain', cwd=runner).stdout, '')


class FastReadsRetryTests(GitPersistenceTests):
    """P2: o remoto avança entre o pull e o push do passo das leituras rápidas (corrida real, provocada pelo envoltório)."""

    def run_fast(self, runner):
        return self.sb.run_step(runner, step_script(STEPS[1]), shim=True)

    def assert_only_git_plumbing(self):
        self.assertTrue(set(self.sb.shim_log('subcommands')) <= {'add', 'diff', 'commit', 'push', 'rev-parse',
                        'log', 'merge-base', 'cat-file', 'status', 'rebase', 'bundle', 'fetch', 'merge'})
        for args in self.sb.shim_log('push_args'):
            self.assertRegex(args, r'^push -q origin [0-9a-f]+:refs/heads/main$')                    # nunca --force, -f nem +ref

    # Push rejeitado por avanço concorrente do remoto + recuperação após uma nova tentativa.
    def test_push_rejected_by_concurrent_advance_recovers_after_one_retry(self):
        runner = self.sb.clone('runner')
        msgs = (runner / 'data/messages.jsonl').read_text()
        log = (runner / 'data/notify_log.jsonl').read_text()
        lines = collect(runner, 'fast')
        self.sb.race_before_push(1, 'docs/index.html', ['<p>PR merged during the fast reads</p>'])
        r = self.run_fast(runner)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.sb.shim_log('pushes'), ['2'])                         # 1 recusado + 1 aceite
        self.assertIn('PUBLICATION_COMPLETED', r.stdout)
        self.assertIn('PUBLICATION_', r.stdout)
        self.assert_only_git_plumbing()
        self.assert_published(runner, msgs, lines)
        self.assertIn('PR merged during the fast reads', self.sb.remote_file('docs/index.html'))
        self.assertEqual(self.sb.raw('rev-list', '--count', '--merges', 'main', cwd=self.sb.origin).stdout.strip(), '0')
        self.assertTrue(self.sb.remote_file('data/notify_log.jsonl').startswith(log))
        self.assertEqual(self.sb.shim_log('subcommands').count('commit'), 1)      # o mesmo commit, reaplicado

    # O outro escritor alterou outro ficheiro de dados: as duas alterações ficam.
    def test_retry_preserves_a_concurrent_change_to_another_data_file(self):
        runner = self.sb.clone('runner')
        msgs = (runner / 'data/messages.jsonl').read_text()
        lines = collect(runner, 'fast')
        self.sb.race_before_push(1, 'data/results_bsd.json', [' '])
        r = self.run_fast(runner)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assert_published(runner, msgs, lines)
        self.assertTrue(self.sb.remote_file('data/results_bsd.json').endswith(' \n'))

    # Conflito real na nova tentativa: falha visível, nenhuma resolução automática, nada perdido.
    def test_real_conflict_on_retry_fails_visibly_and_discards_nothing(self):
        runner = self.sb.clone('runner')
        lines = collect(runner, 'fast')
        local_msgs = (runner / 'data/messages.jsonl').read_text()
        local_state = (runner / 'data/notify_state.json').read_text()
        local_log = (runner / 'data/notify_log.jsonl').read_text()
        remote_lines = new_messages('racer')
        self.sb.race_before_push(1, 'data/messages.jsonl', remote_lines)
        r = self.run_fast(runner)
        self.assertNotIn(r.returncode, (0, 128), r.stderr)
        self.assertIn('PUBLICATION_DEGRADED', r.stdout)
        self.assertEqual(self.sb.shim_log('pushes'), ['1'])                         # sem push depois do conflito
        self.assert_only_git_plumbing()
        remote = self.sb.remote_file('data/messages.jsonl')
        for l in remote_lines:
            self.assertEqual(remote.count(l + '\n'), 1)                             # o remoto ficou com o outro escritor
        for l in lines:
            self.assertNotIn(l, remote)
        for rel, text in (('data/messages.jsonl', local_msgs), ('data/notify_state.json', local_state),
                          ('data/notify_log.jsonl', local_log)):
            self.assertEqual(self.sb.raw('show', 'HEAD:' + rel, cwd=runner).stdout, text, rel)   # commit local intacto
        self.assertTrue((runner / '.git' / 'goal-roi-publication' / 'pending.bundle').exists())
        self.assertFalse((runner / '.git' / 'rebase-merge').exists())

    # O remoto volta a avançar antes do segundo push: falha definitiva, sem terceira tentativa, commit local intacto.
    def test_definitive_failure_after_the_single_retry(self):
        runner = self.sb.clone('runner')
        lines = collect(runner, 'fast')
        local = {rel: (runner / rel).read_text() for rel in ('data/messages.jsonl', 'data/notify_log.jsonl', 'data/notify_state.json')}
        self.sb.race_before_push(1, 'docs/index.html', ['<p>one</p>'])
        self.sb.race_before_push(2, 'docs/index.html', ['<p>two</p>'])
        r = self.run_fast(runner)
        self.assertNotIn(r.returncode, (0, 128), r.stderr)
        self.assertEqual(self.sb.shim_log('pushes'), ['2'])                         # exatamente uma nova tentativa
        self.assertEqual(self.sb.shim_log('subcommands').count('fetch'), 3)
        self.assert_only_git_plumbing()
        self.assertNotIn(lines[0], self.sb.remote_file('data/messages.jsonl'))
        self.assertIn('<p>two</p>', self.sb.remote_file('docs/index.html'))
        for rel, text in local.items():
            self.assertEqual(self.sb.raw('show', 'HEAD:' + rel, cwd=runner).stdout, text, rel)
        self.assertEqual(self.sb.raw('status', '--porcelain', cwd=runner).stdout, '')
        head = self.sb.raw('log', '-1', '--format=%cn|%ce|%s', 'HEAD', cwd=runner).stdout.strip()
        self.assertEqual(head, '%s|%s|data: collect [skip ci]' % IDENTITY)

    # Push aceite mas dado como falhado (resposta perdida): a nova tentativa não duplica nada.
    def test_retry_after_a_push_that_actually_landed_is_a_no_op(self):
        runner = self.sb.clone('runner')
        msgs = (runner / 'data/messages.jsonl').read_text()
        lines = collect(runner, 'fast')
        hook = self.sb.origin / 'hooks' / 'post-receive'
        flag = self.sb.tmp / 'lost-once'
        hook.write_text('#!/bin/sh\n[ -e %s ] && exit 0\ntouch %s\nexit 0\n' % (flag, flag))
        hook.chmod(0o755)
        # O push 1 chega ao remoto, mas o envoltório devolve erro, como numa resposta perdida.
        (self.sb.shim_dir / 'bin' / 'git').write_text(SHIM.replace('exec "$REAL_GIT" "${argv[@]}"',
            '"$REAL_GIT" "${argv[@]}"; rc=$?\n[ "$subcommand" = push ] && [ "$(cat "$SHIM_DIR/pushes")" = 1 ] && exit 1\nexit $rc'))
        r = self.run_fast(runner)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.sb.shim_log('pushes'), ['1'])
        self.assert_published(runner, msgs, lines)
        self.assertEqual(self.sb.raw('log', '--format=%s', 'main', cwd=self.sb.origin).stdout.count('data: collect'), 1)

    def test_main_collection_recovers_after_one_retry(self):
        runner = self.sb.clone('runner')
        collect(runner, 'main')
        self.sb.race_before_push(1, 'docs/index.html', ['<p>x</p>'])
        r = self.sb.run_step(runner, step_script(STEPS[0]), shim=True)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(self.sb.shim_log('pushes'), ['2'])


def simulate_job(text, failing, shadow=True, authorization_source=None):
    """Avaliação das condições dos passos como no GitHub Actions (só as expressões usadas neste workflow): o passo `failing`
    falha; devolve (conclusão do job, passos executados, passos saltados)."""
    names, steps = re.findall(r'^      - name: (.+)$', text, re.M), {}
    for name in names:
        block = text.split('      - name: %s\n' % name, 1)[1].split('\n      - ', 1)[0]
        cond = re.search(r'^        if: \$\{\{ (.+) \}\}$', block, re.M)
        steps[name] = (cond.group(1) if cond else 'success()', '        continue-on-error: true' in block.split('\n'))
    failed, ran, skipped, outcome = False, [], [], {}
    resume_completed = failing != 'Resume pending publication'
    pending = failing in STEPS
    for name in names:
        cond, tolerated = steps[name]
        facts = {
            "steps.published.outcome == 'success'": outcome.get('Publish the report before sending notices') == 'success',
            'success()': not failed, 'failure()': failed, 'always()': True, '!cancelled()': True,
            "github.ref_name == 'main'": True, "steps.resume.outcome == 'success'": resume_completed,
            "steps.fast.outcome != 'skipped'": 'Fast reads every 2 minutes until the next collection (read-only)' in ran,
            "steps.publication.outputs.pending == 'true'": pending,
            "steps.publication.outputs.pending != 'true'": not pending,
            # P2 Polymarket (ativação): simula o observador ligado; os seus passos toleram falhas.
            "vars.GOAL_ROI_POLYMARKET_SHADOW == 'on'": shadow,
        }
        auth_condition = "steps.reader.outputs.authorization == 'lost' || steps.fast.outputs.authorization == 'lost'"
        facts["(failure() || " + auth_condition + ")"] = failed or authorization_source is not None
        go = all(facts[part] for part in cond.split(' && '))
        if not go:
            skipped.append(name)
            continue
        ran.append(name)
        outcome[name] = 'failure' if name == failing else 'success'
        if name == failing and not tolerated:
            failed = True
    return ('failure' if failed else 'success'), ran, skipped


class ChainContinuityTests(unittest.TestCase):
    FAST, NEXT, TELL = STEPS[1], 'Start the next collection', 'Tell the user when a collection fails (at most once an hour)'

    def test_red_fast_commit_fails_the_job_but_the_next_collection_is_launched(self):
        conclusion, ran, _ = simulate_job(WORKFLOW.read_text(encoding='utf-8'), self.FAST)
        self.assertEqual(conclusion, 'failure')
        self.assertLess(ran.index(self.FAST), ran.index(self.NEXT))
        self.assertIn('Preserve pending data and intent for the next runner', ran)
        self.assertNotIn(self.TELL, ran)

    NOTICES, READ = "Telegram notices through the user's bot (skipped without keys)", 'Read bot conversation (read-only)'

    def test_failed_resume_keeps_reading_blocks_notices_and_still_launches_next_job(self):
        conclusion, ran, skipped = simulate_job(WORKFLOW.read_text(encoding='utf-8'), 'Resume pending publication')
        self.assertEqual(conclusion, 'failure')
        for step in (self.READ, 'Report (fixed rules)', STEPS[0], 'Fast reads every 2 minutes until the next collection (read-only)'):
            self.assertIn(step, ran)
        self.assertIn('Publish the report before sending notices', skipped)
        self.assertIn(self.NOTICES, skipped)
        self.assertIn(self.NEXT, ran)

    def test_failed_report_publication_blocks_notices_only(self):
        conclusion, ran, skipped = simulate_job(WORKFLOW.read_text(encoding='utf-8'), 'Publish the report before sending notices')
        self.assertEqual(conclusion, 'failure')
        self.assertIn(self.NOTICES, skipped)
        self.assertIn(STEPS[0], ran)
        self.assertIn(self.NEXT, ran)

    def test_failed_telegram_read_still_publishes_and_continues(self):
        conclusion, ran, _ = simulate_job(WORKFLOW.read_text(encoding='utf-8'), self.READ)
        self.assertEqual(conclusion, 'failure')
        self.assertIn(STEPS[0], ran)
        self.assertIn(self.NEXT, ran)

    def test_authorization_loss_stays_green_but_notifies_after_publication_and_dispatch(self):
        workflow = WORKFLOW.read_text(encoding='utf-8')
        for source in ('reader', 'fast'):
            with self.subTest(source=source):
                failed = 'Fast reads every 2 minutes until the next collection (read-only)' if source == 'fast' else None
                conclusion, ran, _ = simulate_job(workflow, failed, authorization_source=source)
                self.assertEqual(conclusion, 'success')
                self.assertLess(ran.index(STEPS[1]), ran.index(self.NEXT))
                self.assertLess(ran.index(self.NEXT), ran.index(self.TELL))

    def test_reader_shell_tolerates_only_certified_authorization_exit(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            output, timeout = folder / 'output', folder / 'timeout'
            timeout.write_text("#!/bin/sh\nexit \"$SYNTHETIC_EXIT\"\n")
            timeout.chmod(0o755)
            for code, marker, expected in ((4, 'authorization=lost', 0), (4, '', 4),
                                            (1, 'authorization=lost', 1), (124, 'authorization=lost', 124), (0, '', 0)):
                with self.subTest(code=code, marker=marker):
                    output.write_text(marker + '\n')
                    env = {**os.environ, 'PATH': str(folder) + os.pathsep + os.environ['PATH'],
                           'SYNTHETIC_EXIT': str(code), 'GITHUB_OUTPUT': str(output)}
                    result = subprocess.run(['bash', '-eo', 'pipefail', '-c', step_script(self.READ)], env=env)
                    self.assertEqual(result.returncode, expected)

    def test_green_workflow_summary_shows_authorization_loss_even_with_recent_read(self):
        script = step_script('Check for an unpublished transaction')
        script = script.replace('${{ steps.resume.outcome }}', 'success').replace('${{ steps.fast.outcome }}', 'success')
        with tempfile.TemporaryDirectory() as folder:
            summary = Path(folder) / 'summary'
            env = {**os.environ, 'GOAL_ROI_PUBLICATION_DIR': folder, 'GITHUB_STEP_SUMMARY': str(summary),
                   'GITHUB_OUTPUT': str(Path(folder) / 'output'), 'TG_AUTHORIZATION_LOST': '1'}
            outcome = subprocess.run(['bash', '-eo', 'pipefail', '-c', script], env=env, capture_output=True, check=True)
            self.assertIn(b'::warning title=Telegram authorization lost::', outcome.stdout)
            self.assertIn('Telegram authorization lost. Collection is degraded.', summary.read_text())

    def test_authorization_alert_deduplicates_across_independent_git_checkouts(self):
        from datetime import datetime, timezone, timedelta
        from unittest.mock import patch
        from scangoal import notify
        at = datetime(2026, 11, 27, 18, 37, tzinfo=timezone.utc)
        env = {'TG_BOT_TOKEN': 'synthetic', 'TG_CHAT_ID': 'synthetic', 'TG_AUTHORIZATION_LOST': '1',
               'GITHUB_REF_NAME': 'main', 'NOTIFY_DURABLE': 'git'}
        sent = []
        with tempfile.TemporaryDirectory() as folder:
            sandbox = Sandbox(folder)
            first, stale = sandbox.clone('first'), sandbox.clone('stale')
            def send_now(checkout, now):
                with patch.dict(os.environ, SCANGOAL_DATA_DIR=str(checkout / 'data')):
                    return notify.failure(lambda *args: sent.append(args), now, env)
            self.assertTrue(send_now(first, at))
            self.assertFalse(send_now(stale, at + timedelta(minutes=2)))
            self.assertFalse(send_now(sandbox.clone('fresh'), at + timedelta(minutes=3)))
            self.assertEqual(len(sent), 1)
            self.assertIn('perda de autorização Telegram', sent[0][2])
            self.assertEqual(json.loads(sandbox.remote_file('data/notify_auth_health.json'))['claimed_at_utc'], at.isoformat())

    def test_a_successful_run_stays_green_and_does_not_warn(self):
        conclusion, ran, skipped = simulate_job(WORKFLOW.read_text(encoding='utf-8'), None)
        self.assertEqual(conclusion, 'success')
        self.assertIn(self.NEXT, ran)
        self.assertEqual(skipped, ['Preserve pending data and intent for the next runner', self.TELL])

    def test_telegram_warning_is_not_guaranteed_outside_the_first_ten_minutes(self):
        """O aviso existente (`notify.failure`, inalterado) só envia nos minutos 00–09 de cada hora, ou se o lançamento seguinte
        falhar. Uma falha deste passo fica sempre vermelha, mas o Telegram só chega se calhar nessa janela."""
        from datetime import datetime
        from unittest.mock import patch
        from scangoal import notify
        sent, env = [], {'TG_BOT_TOKEN': 't', 'TG_CHAT_ID': 'c'}
        sender = lambda *a: sent.append(a)
        at = lambda hm: datetime.fromisoformat('2026-11-17T%s:00+00:00' % hm)
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, SCANGOAL_DATA_DIR=folder):
            self.assertFalse(notify.failure(sender, at('12:31'), env))
            self.assertTrue(notify.failure(sender, at('12:31'), dict(env, NEXT_COLLECTION_FAILED='1')))
            self.assertFalse(notify.failure(sender, at('13:04'), env))
            self.assertTrue(notify.failure(sender, at('14:04'), env))
        self.assertEqual(len(sent), 2)


class ProductionDataUntouchedTests(unittest.TestCase):
    def test_sandbox_never_writes_into_the_repository(self):
        rels = [r for r in DATA if (ROOT / r).exists()]
        before = {r: (ROOT / r).read_bytes() for r in rels}
        with tempfile.TemporaryDirectory() as tmp:
            sb = Sandbox(tmp)
            runner = sb.clone('runner')
            collect(runner, 'x')
            sb.advance_remote('README.md', append(['y']))
            self.assertEqual(sb.run_step(runner, step_script(STEPS[1])).returncode, 0)
        self.assertEqual({r: (ROOT / r).read_bytes() for r in rels}, before)


if __name__ == '__main__':
    unittest.main()
