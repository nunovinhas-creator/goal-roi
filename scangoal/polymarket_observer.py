"""P2 — observação automática, prospetiva e só leitura de preços Polymarket (DESATIVADA por defeito).

Objetivo: para cada sinal elegível (Polymarket, pré-jogo, Asian Total, Over 2.5, jogo inteiro, 90 minutos), ler o livro
público até 120 s depois da PRIMEIRA leitura do sinal e antes do início, e guardar a evidência. Não aposta, não envia
ordens, não faz POST, não usa credenciais. Uma cotação contemporânea não prova que a aposta fosse executável.

Isolamento (ver audits/polymarket-p2/README.md):
- `follow` corre em segundo plano e só LÊ data/messages.jsonl. Escreve apenas num spool FORA do repositório (um ficheiro
  por observação, escrito de forma atómica). Nunca toca em data/ nem no Git, por isso não pode sujar a árvore durante uma
  publicação.
- `ingest` corre como passo sequencial, antes dos commits já existentes: copia o spool para
  data/polymarket_observations.jsonl (só acrescenta; chave única por versão de mensagem; escrita atómica).
- Qualquer erro termina com código 0 e uma linha de diagnóstico: o observador nunca pára a recolha.
- Duas chaves: a variável de repositório no workflow e GOAL_ROI_POLYMARKET_SHADOW=on no processo.
"""

import argparse
import fcntl
import hashlib
import json
import os
import signal as signals
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import chronology, polymarket_shadow as ps
from .eligibility import eligible, game_identity
from .store import data_dir, message_rows

FLAG = 'GOAL_ROI_POLYMARKET_SHADOW'
SCHEMA = 'goal-roi/polymarket-observation/v1'
JOURNAL = 'polymarket_observations.jsonl'
ROOT = Path(__file__).resolve().parents[1]

WINDOW = ps.MAX_DECISION_LAG                 # 120 s depois da primeira leitura
POLL_SECONDS = 5
LIFETIME_SECONDS = 840
MAX_OBSERVATIONS = 12                        # por execução do workflow
MAX_REQUESTS = 80                            # por execução (cada observação usa até 6 GET)
REQUEST_TIMEOUT = 8
OBSERVATION_SECONDS = 30
BREAKER_FAILURES = 2                         # falhas técnicas seguidas que fecham a rede até ao fim da execução
BREAKER_HTTP = {403, 429}                    # estes fecham logo à primeira

STATUSES = ('contemporaneous', 'outside_window', 'stale_book', 'contract_unavailable', 'identity_unproven',
            'technical_failure')
IDENTITY = {'identity_not_found', 'identity_ambiguous', 'child_event_not_linked', 'child_event_identity_mismatch',
            'contract_ambiguous', 'kickoff_mismatch', 'clob_kickoff_mismatch', 'clob_condition_mismatch',
            'clob_tokens_mismatch', 'outcomes_invalid', 'token_invalid', 'condition_id_invalid',
            'rules_not_regulation_over25', 'neg_risk_market', 'book_identity_mismatch', 'invalid_time'}
UNAVAILABLE = {'contract_not_found', 'market_not_accepting_orders', 'book_no_asks'}
OUTSIDE = {'after_kickoff', 'deadline_exceeded'}


def enabled(environ=None):
    return (environ if environ is not None else os.environ).get(FLAG, '').strip() == 'on'


def utc_now():
    return datetime.now(timezone.utc)


def iso(value):
    return ps.iso(value)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


# --------------------------------------------------------------------------------------------- elegibilidade

def eligible_signal(m):
    """Mesmo critério de origem do aviso PR #55, sem os cortes de quota e valor: primeiro registo comprovado."""
    if not ps.in_scope(m):
        return False
    original = m.get('bet') is True and all(
        m.get(k) is False for k in ('had_ft_at_first_sight', 'edited_at_first_sight', 'burst'))
    return original and eligible(dict(m, in_roi=True)) and chronology.parse_utc(m.get('kickoff_utc')) is not None


SIGNAL_FIELDS = ('message_id', 'match_id', 'teams', 'league', 'kickoff_utc', 'type', 'phase', 'market', 'selection',
                 'market_scope', 'period', 'odds', 'share', 'total', 'date_utc', 'collected_at_utc')


def signal_hash(m):
    return digest(canonical({k: m.get(k) for k in SIGNAL_FIELDS}).encode('utf-8'))


def first_versions(path):
    """message_id -> (sha256 da primeira versão, collected_at_utc dessa versão). Só leitura."""
    first = {}
    for _, row in message_rows(path):
        mid = row.get('message_id')
        if isinstance(mid, int) and mid not in first:
            first[mid] = (row.get('sha256'), row.get('collected_at_utc'))
    return first


# --------------------------------------------------------------------------------------------- caminhos seguros

def inside(target, directory):
    directory = Path(directory).resolve()
    return target == directory or directory in target.parents


def safe_spool(path):
    target = Path(path).expanduser().absolute().resolve()
    if inside(target, ROOT) or inside(target, data_dir()):
        raise ValueError('spool must be outside the repository and the data directory')
    target.mkdir(parents=True, exist_ok=True)
    return target


def atomic_write(path, data):
    path = Path(path)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.' + path.name + '.', suffix='.tmp', delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)


def key_file(key):
    return digest(key.encode('utf-8'))[:40] + '.json'


# --------------------------------------------------------------------------------------------- diário append-only

class JournalCorrupt(RuntimeError):
    pass


def journal_rows(path):
    """Linhas do diário. Qualquer linha inválida ou final sem quebra de linha = corrupção (nunca se reescreve)."""
    path = Path(path)
    if not path.exists():
        return b'', []
    raw = path.read_bytes()
    if raw and not raw.endswith(b'\n'):
        raise JournalCorrupt('unterminated tail')
    rows = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            raise JournalCorrupt('invalid line') from None
        if not isinstance(row, dict) or not isinstance(row.get('key'), str):
            raise JournalCorrupt('row without key')
        rows.append(row)
    return raw, rows


def valid_record(record):
    return (isinstance(record, dict) and record.get('schema') == SCHEMA and isinstance(record.get('key'), str)
            and record.get('status') in STATUSES and isinstance(record.get('message_id'), int)
            and record.get('execution_proven') is False and record.get('orders_sent') == 0
            and record['key'] == '%d:%s' % (record['message_id'], record.get('version_sha256')))


def ingest(directory, spool):
    """Spool -> data/polymarket_observations.jsonl. Idempotente; nunca altera linhas existentes."""
    directory, spool = Path(directory), Path(spool)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / JOURNAL
    lock_path = directory.parent / ('.' + directory.name + '.polymarket.lock')
    stats = {'added': 0, 'duplicates': 0, 'rejected': 0}
    files = sorted(p for p in spool.glob('*.json') if p.is_file() and not p.is_symlink())
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        prior, rows = journal_rows(path)
        keys = {row['key'] for row in rows}
        new = []
        for item in files:
            try:
                record = json.loads(item.read_bytes())
            except (OSError, ValueError):
                record = None
            if not valid_record(record):
                stats['rejected'] += 1
                continue
            if record['key'] in keys:
                stats['duplicates'] += 1
                continue
            keys.add(record['key'])
            new.append(record)
        new.sort(key=lambda r: (r.get('first_read_utc') or '', r['message_id']))
        if new:
            extra = ''.join(json.dumps(r, ensure_ascii=False, sort_keys=True) + '\n' for r in new).encode('utf-8')
            atomic_write(path, prior + extra)
            stats['added'] = len(new)
    rejected = spool / 'rejected'
    for item in files:
        try:
            record = json.loads(item.read_bytes())
        except (OSError, ValueError):
            record = None
        if valid_record(record):
            item.unlink(missing_ok=True)          # já está no diário (agora ou antes)
        else:
            rejected.mkdir(exist_ok=True)
            item.replace(rejected / item.name)
    return stats


# --------------------------------------------------------------------------------------------- classificação

def classify(obs):
    if obs.get('status') == 'observed':
        timing = obs['timing']
        if timing['relation_to_signal'] != 'contemporaneous':
            return 'outside_window', timing['relation_to_signal']
        if timing['quiet_book']:
            return 'stale_book', 'source_age_over_%ds' % ps.QUIET_BOOK.total_seconds()
        return 'contemporaneous', None
    reason = obs.get('reason')
    if reason in IDENTITY:
        return 'identity_unproven', reason
    if reason in UNAVAILABLE:
        return 'contract_unavailable', reason
    if reason in OUTSIDE:
        return 'outside_window', reason
    return 'technical_failure', reason


class Budget:
    """Pedidos, prazo por observação e disjuntor. Recusa antes de tocar na rede."""

    def __init__(self, get, clock, *, max_requests=MAX_REQUESTS, timeout=REQUEST_TIMEOUT):
        self.get, self.clock = get, clock
        self.requests, self.max_requests, self.timeout = 0, max_requests, timeout
        self.failures, self.open_reason, self.deadline = 0, None, None

    def __call__(self, url):
        if self.open_reason:
            raise ps.Refusal('circuit_open', self.open_reason)
        if self.requests >= self.max_requests:
            raise ps.Refusal('budget_exhausted')
        if self.deadline is not None and self.clock() >= self.deadline:
            raise ps.Refusal('deadline_exceeded')
        self.requests += 1
        return self.get(url)

    def after(self, obs):
        if obs.get('status') != 'unavailable':
            if obs.get('reason') not in ('circuit_open', 'budget_exhausted', 'deadline_exceeded'):
                self.failures = 0
            return
        self.failures += 1
        detail = obs.get('detail')
        if (obs.get('reason') == 'http_error' and isinstance(detail, int)
                and (detail in BREAKER_HTTP or detail >= 500)) or self.failures >= BREAKER_FAILURES:
            self.open_reason = '%s:%s' % (obs.get('reason'), detail)


def build_record(m, version_sha, obs, status, reason, *, attempted, run):
    quote, timing, clob, contract = (obs.get(k) or {} for k in ('quote', 'timing', 'clob', 'contract'))
    first_read = chronology.parse_utc(m.get('collected_at_utc'))
    received = chronology.parse_utc(quote.get('received_utc'))
    return {
        'schema': SCHEMA, 'key': '%d:%s' % (m['message_id'], version_sha), 'run': run,
        'message_id': m['message_id'], 'version_sha256': version_sha, 'signal_sha256': signal_hash(m),
        'match_id': game_identity(m), 'teams': m.get('teams'), 'league': m.get('league'),
        'kickoff_utc': m.get('kickoff_utc'), 'signal_sent_utc': m.get('date_utc'),
        'first_read_utc': m.get('collected_at_utc'), 'signal_odds': m.get('odds'),
        'share': m.get('share'), 'total': m.get('total'),
        'attempted_utc': iso(attempted), 'status': status, 'reason': reason, 'detail': obs.get('detail'),
        'condition_id': contract.get('condition_id'), 'token_over': contract.get('token_over'),
        'market_slug': contract.get('slug'), 'rules_sha256': contract.get('rules_sha256'),
        'fee': (dict(clob['fee'], source='clob /clob-markets fd (taker only)') if clob.get('fee') else None),
        'quote': ({k: quote.get(k) for k in ('best_ask', 'best_ask_size', 'best_bid', 'book_hash', 'source_utc',
                                             'received_utc', 'ask_depth_usdc_top5', 'mirror_consistent', 'fills')}
                  if quote else None),
        'lag_after_first_read_seconds': (round((received - first_read).total_seconds(), 3)
                                         if received and first_read else None),
        'source_age_seconds': timing.get('source_age_seconds'),
        'seconds_to_kickoff': timing.get('seconds_to_kickoff'),
        'evidence_for_signal': status in ('contemporaneous', 'stale_book') and bool(timing.get('evidence_for_signal')),
        'execution_proven': False, 'orders_sent': 0, 'credentials_used': False,
        'responses': obs.get('requests') or [],
    }


# --------------------------------------------------------------------------------------------- seguidor

class Follower:
    def __init__(self, directory, spool, *, get=None, clock=utc_now, load=None, aliases=None, run=None,
                 max_observations=MAX_OBSERVATIONS, max_requests=MAX_REQUESTS):
        self.directory, self.spool, self.clock = Path(directory), Path(spool), clock
        self.get = get or (lambda url: ps.http_get(url, timeout=REQUEST_TIMEOUT))
        self.load = load or self.report_load
        self.aliases, self.run = aliases or {}, run or {}
        self.budget = Budget(self.get, clock, max_requests=max_requests)
        self.max_observations, self.observations = max_observations, 0
        self.started = clock()
        self.run.setdefault('started_utc', iso(self.started))
        self.messages = self.directory / 'messages.jsonl'
        self.known = set(first_versions(self.messages))       # histórico: nunca observado
        self.done = self.existing_keys()
        self.stamp = None

    @staticmethod
    def report_load(now):
        from . import report
        messages, *_ = report.load(result_observations={}, as_of=now)
        return messages

    def existing_keys(self):
        keys = set()
        try:
            keys |= {row['key'] for row in journal_rows(self.directory / JOURNAL)[1]}
        except (JournalCorrupt, OSError):
            pass
        for item in self.spool.glob('*.json'):
            try:
                keys.add(json.loads(item.read_bytes())['key'])
            except (OSError, ValueError, KeyError, TypeError):
                pass
        return keys

    def tick(self):
        """Uma passagem: só processa mensagens novas desde o arranque. -> número de registos escritos."""
        try:
            stat = self.messages.stat()
        except OSError:
            return 0
        stamp = (stat.st_size, stat.st_mtime_ns)
        if stamp == self.stamp:
            return 0
        first = first_versions(self.messages)                 # pode falhar a meio de um checkout: tenta no próximo
        new = sorted(set(first) - self.known)
        if not new:
            self.stamp = stamp
            return 0
        now = self.clock()
        by_id = {m.get('message_id'): m for m in self.load(now)}
        written = 0
        for mid in new:
            m = by_id.get(mid)
            if m is None:
                if mid in first and first[mid][1] and chronology.parse_utc(first[mid][1]) and \
                        now - chronology.parse_utc(first[mid][1]) > WINDOW:
                    self.known.add(mid)                       # nunca vai ser um sinal a tempo
                continue
            self.known.add(mid)
            if eligible_signal(m):
                written += self.consider(m, first[mid][0])
        self.stamp = stamp if all(mid in self.known for mid in new) else None
        return written

    def consider(self, m, version_sha):
        key = '%d:%s' % (m['message_id'], version_sha)
        if not version_sha or key in self.done:
            return 0
        attempted = self.clock()
        first_read = chronology.parse_utc(m.get('collected_at_utc'))
        kickoff = chronology.parse_utc(m.get('kickoff_utc'))
        if first_read is None:
            obs, status, reason = {}, 'outside_window', 'first_read_unproven'
        elif attempted - first_read > WINDOW:
            obs, status, reason = {}, 'outside_window', 'detected_late'
        elif kickoff is None or attempted >= kickoff:
            obs, status, reason = {}, 'outside_window', 'after_kickoff'
        elif self.observations >= self.max_observations:
            obs, status, reason = {}, 'technical_failure', 'observation_cap_reached'
        elif self.budget.open_reason:
            obs, status, reason = {'detail': self.budget.open_reason}, 'technical_failure', 'circuit_open'
        else:
            self.observations += 1
            self.budget.deadline = min(first_read + WINDOW, kickoff, attempted + timedelta(seconds=OBSERVATION_SECONDS))
            try:
                obs = ps.observe(m, get=self.budget, aliases=self.aliases)
            except Exception as exc:                      # nunca repetir a mesma chave por um erro interno
                obs = {'status': 'unavailable', 'reason': 'internal_error', 'detail': type(exc).__name__}
            finally:
                self.budget.deadline = None
            self.budget.after(obs)
            status, reason = classify(obs)
        record = build_record(m, version_sha, obs, status, reason, attempted=attempted, run=self.run)
        atomic_write(self.spool / key_file(key), (canonical(record) + '\n').encode('utf-8'))
        self.done.add(key)
        print('POLYMARKET_SHADOW_RECORD', m['message_id'], status, reason or '')
        return 1


def follow(directory, spool, *, seconds=LIFETIME_SECONDS, sleep=time.sleep, monotonic=time.monotonic, **kwargs):
    start = monotonic()
    follower = Follower(directory, spool, **kwargs)
    pid = spool / 'follower.pid'
    atomic_write(pid, str(os.getpid()).encode())
    try:
        while monotonic() - start < seconds and not (spool / 'STOP').exists():
            try:
                follower.tick()
            except Exception as exc:                          # nunca parar por uma passagem falhada
                print('POLYMARKET_SHADOW_TICK_FAILED', type(exc).__name__)
            sleep(POLL_SECONDS)
    finally:
        pid.unlink(missing_ok=True)
    return follower


def stop(spool, *, wait=8.0, sleep=time.sleep, monotonic=time.monotonic):
    atomic_write(spool / 'STOP', b'stop\n')
    start = monotonic()
    while (spool / 'follower.pid').exists() and monotonic() - start < wait:
        sleep(0.2)
    return not (spool / 'follower.pid').exists()


def load_aliases(directory):
    try:
        raw = json.loads((Path(directory) / 'team_aliases.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in raw.items() if isinstance(v, list)} if isinstance(raw, dict) else {}


def main(argv=None, environ=None):
    """Sai sempre com 0: o observador nunca altera o resultado da recolha."""
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=('follow', 'ingest', 'stop'))
    parser.add_argument('--spool', required=True)
    parser.add_argument('--seconds', type=int, default=LIFETIME_SECONDS)
    parser.add_argument('--stop-first', action='store_true')
    args = parser.parse_args(argv)
    if not enabled(environ):
        print('POLYMARKET_SHADOW_DISABLED')
        return 0
    try:
        spool = safe_spool(args.spool)
        directory = data_dir()
        if args.operation == 'follow':
            signals.signal(signals.SIGTERM, lambda *_: sys.exit(0))
            run = {'github_run_id': os.environ.get('GITHUB_RUN_ID'), 'github_run_attempt': os.environ.get('GITHUB_RUN_ATTEMPT')}
            follower = follow(directory, spool, seconds=max(0, min(args.seconds, LIFETIME_SECONDS)),
                              aliases=load_aliases(directory), run=run)
            print('POLYMARKET_SHADOW_FOLLOW_DONE observations', follower.observations, 'requests', follower.budget.requests)
        elif args.operation == 'stop':
            print('POLYMARKET_SHADOW_STOPPED', stop(spool))
        else:
            if args.stop_first:
                print('POLYMARKET_SHADOW_STOPPED', stop(spool))
            print('POLYMARKET_SHADOW_INGEST', canonical(ingest(directory, spool)))
    except SystemExit:
        pass
    except Exception as exc:
        print('POLYMARKET_SHADOW_FAILED', args.operation, type(exc).__name__)
    return 0


if __name__ == '__main__':
    sys.exit(main())
