"""P1: falha silenciosa de persistência git (execução #814, passo `Commit data from the fast reads`, código 128).

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
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

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


def step_script(name, text=None):
    """Bloco `run: |` do passo `name` (sem dependência de PyYAML, que o workflow de testes não instala)."""
    lines = (text or WORKFLOW.read_text(encoding='utf-8')).split('\n')
    start = next(i for i, l in enumerate(lines) if l.strip() == '- name: %s' % name)
    run = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == 'run: |')
    indent = len(lines[run]) - len(lines[run].lstrip()) + 2
    body = []
    for l in lines[run + 1:]:
        if l.strip() and len(l) - len(l.lstrip()) < indent:
            break
        body.append(l[indent:])
    return '\n'.join(body).rstrip('\n') + '\n'


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

    def run_step(self, repo, script):
        return subprocess.run(['bash', '-e', '-c', script], cwd=str(repo), env=self.env, capture_output=True, text=True, timeout=120)

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
    def test_both_steps_set_a_repository_local_identity_before_commit_and_rebase(self):
        for name in STEPS:
            script = step_script(name)
            self.assertIn('git config user.name "scangoal-collector"', script, name)
            self.assertIn('git config user.email "actions@users.noreply.github.com"', script, name)
            order = [script.index(s) for s in ('git config user.name', 'git config user.email', 'git commit', 'git pull -q --rebase')]
            self.assertEqual(order, sorted(order), name)
            self.assertNotIn('--global', script, name)
            self.assertNotIn('--system', script, name)
            self.assertNotRegex(script, r'--force|push\s+-f|\+HEAD', name)
            self.assertNotRegex(script, r'-X\s*(ours|theirs)|--strategy|reset --hard|checkout --(ours|theirs)', name)

    def test_only_the_identity_lines_changed(self):
        fixed = step_script(STEPS[0])
        undo = re.sub(r'# Identidade local[^\n]*\ngit config user.name "scangoal-collector"\ngit config user.email '
                      r'"actions@users.noreply.github.com"\ngit commit -q', 'git -c user.name="scangoal-collector" -c '
                      r'user.email="actions@users.noreply.github.com" commit -q', fixed)
        self.assertEqual(undo, PRE_FIX)
        self.assertEqual(step_script(STEPS[0]), step_script(STEPS[1]))

    def test_unchanged_step_guards(self):
        text = WORKFLOW.read_text(encoding='utf-8')
        main = text[text.index('- name: Commit data\n'):text.index('- name: Fast reads')]
        fast = text[text.index('- name: Commit data from the fast reads'):text.index('- name: Start the next collection')]
        self.assertNotIn('continue-on-error', main)          # a recolha principal falha de forma visível
        self.assertIn('continue-on-error: true', fast)       # comportamento atual mantido (risco documentado na PR)
        self.assertIn("if: ${{ !cancelled() && github.ref_name == 'main' }}", fast)
        self.assertIn('group: scangoal-write', text)
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
                cfg = self.sb.raw('config', '--local', '--get-regexp', '^user\\.', cwd=runner).stdout
                self.assertEqual(cfg, 'user.name scangoal-collector\nuser.email actions@users.noreply.github.com\n')

    def test_no_changes_exits_zero_without_commit(self):
        runner = self.sb.clone('runner')
        before = self.sb.remote_head()
        for name in STEPS:
            r = self.sb.run_step(runner, step_script(name))
            self.assertEqual((r.returncode, r.stdout.strip()), (0, 'no changes'))
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
        self.assertEqual(self.sb.run_step(runner, step_script(STEPS[0])).stdout.strip(), 'no changes')
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
        self.assertIn('CONFLICT', r.stdout + r.stderr)
        self.assertNotIn('identity unknown', r.stderr)
        self.assertEqual(self.sb.remote_head(), before)                     # nada publicado por cima
        self.assertEqual(self.sb.remote_file('data/messages.jsonl'), remote_msgs)
        # O commit local continua intacto no runner (ORIG_HEAD / rebase em curso), com todas as linhas novas.
        local = self.sb.raw('show', 'ORIG_HEAD:data/messages.jsonl', cwd=runner).stdout
        self.assertEqual(local, local_msgs)
        for l in lines:
            self.assertIn(l, local)
        self.assertTrue((runner / '.git' / 'rebase-merge').exists() or (runner / '.git' / 'rebase-apply').exists())

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
        self.assertIn('rejected', r.stderr)
        self.assertEqual(self.sb.remote_head(), before)
        head = self.sb.raw('log', '-1', '--format=%cn|%ce|%s', 'HEAD', cwd=runner).stdout.strip()
        self.assertEqual(head, '%s|%s|data: collect [skip ci]' % IDENTITY)
        local = self.sb.raw('show', 'HEAD:data/messages.jsonl', cwd=runner).stdout
        for l in lines:
            self.assertIn(l, local)
        self.assertEqual(self.sb.raw('status', '--porcelain', cwd=runner).stdout, '')


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
