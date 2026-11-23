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
- Leitura incremental: só os bytes acrescentados ao diário desde a última passagem (o diário só cresce); qualquer
  incoerência obriga a uma releitura completa. Mensagens já conhecidas no arranque, ou lidas antes dele, nunca entram.
- Prova de preço fresco: resposta recebida nos 120 s E livro alterado há no máximo FRESH_BOOK_MAX_AGE. Um livro parado
  (`stale_book`) fica registado, mas não prova um preço fresco.
- Cada execução regista o seu início e fim (data/polymarket_observer_runs.jsonl), mesmo sem observações: é a data de
  ativação verificável usada pelo relatório.
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
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import chronology, polymarket_shadow as ps
from .eligibility import eligible, game_identity
from .parser import URL, parse
from .store import data_dir, message_rows

FLAG = 'GOAL_ROI_POLYMARKET_SHADOW'
SCHEMA = 'goal-roi/polymarket-observation/v1'
JOURNAL = 'polymarket_observations.jsonl'
RUN_SCHEMA = 'goal-roi/polymarket-observer-run/v1'
RUNS_JOURNAL = 'polymarket_observer_runs.jsonl'
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
FRESH_BOOK_MAX_AGE = ps.QUIET_BOOK           # 300 s: o livro tem de ter mudado há no máximo isto, à receção
HISTORY_SKEW = timedelta(seconds=5)          # leitura anterior ao arranque (menos isto) = histórico, nunca observado
CURSOR_TAIL = 4096                           # bytes antes do cursor que têm de coincidir para ler só o acréscimo

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


def first_signal(row):
    """A 1.ª versão de uma mensagem, a partir só da sua 1.ª linha, com os mesmos campos que `report.load` usa.

    Equivalência verificada em testes contra `report.load` sobre o diário real (todas as mensagens POLYMARKET).
    `burst` só existe para Betfair em `report.load`; aqui fica False, o que só é exato para os outros tipos.
    """
    p = parse(row['text'])
    links = [*URL.finditer(row['text'])]
    for button in row.get('buttons') or ():
        if isinstance(button, (list, tuple)) and len(button) == 2 and isinstance(button[1], str):
            links.extend(URL.finditer(button[1]))
    ids = {link[1] for link in links}
    p['explicit_match_ids'] = sorted(ids)
    if len(ids) == 1:
        p['match_id'] = next(iter(ids))
    elif len(ids) > 1:
        p['match_id'] = None
    return dict(p, message_id=row['message_id'], date_utc=row['date_utc'], had_ft_at_first_sight='ft' in p,
                edited_at_first_sight=bool(row.get('edit_date_utc')), collected_at_utc=row.get('collected_at_utc'),
                burst=False)


def version_sha(row):
    """sha256 da versão, como `store.append_versions` o grava (texto em UTF-8)."""
    sha = row.get('sha256')
    return sha if isinstance(sha, str) and len(sha) == 64 else digest(row['text'].encode('utf-8'))


class JournalCursor:
    """Lê só o acréscimo de data/messages.jsonl. Devolve cada mensagem nova uma única vez (pela 1.ª linha).

    O diário é substituído de forma atómica com o conteúdo antigo + o novo, por isso o prefixo não muda. Se o ficheiro
    encolher ou os CURSOR_TAIL bytes antes do cursor mudarem (por exemplo um checkout a meio), relê tudo; as mensagens já
    vistas não voltam a sair. Uma linha incompleta no fim fica para a passagem seguinte. A leitura é feita linha a linha
    (memória limitada a uma linha) e do histórico só se guardam os IDs.
    """

    def __init__(self, path):
        self.path, self.offset, self.tail, self.seen = Path(path), 0, b'', set()
        self.full_reads = self.incremental_reads = 0

    @staticmethod
    def _row(line):
        try:
            row = json.loads(line)
        except ValueError:
            return None                                       # linha rasgada; o store escreve um marcador a seguir
        if isinstance(row, dict) and isinstance(row.get('message_id'), int) and isinstance(row.get('text'), str):
            return row
        return None

    def read(self, collect=True):
        """-> 1.ªs linhas de mensagens nunca vistas por este cursor (collect=False: só marca como vistas)."""
        try:
            size = self.path.stat().st_size
        except OSError:
            return []
        if size == self.offset:
            return []
        new = []
        with self.path.open('rb') as handle:
            start = max(0, self.offset - len(self.tail))
            incremental = size > self.offset and self.offset > 0
            if incremental:
                handle.seek(start)
                incremental = handle.read(self.offset - start) == self.tail
            if incremental:
                position = self.offset
                self.incremental_reads += 1
            else:
                handle.seek(0)
                position = 0
                self.full_reads += 1
            for line in handle:
                if not line.endswith(b'\n'):
                    break                                     # incompleta: fica para a próxima passagem
                position += len(line)
                if not line.strip():
                    continue
                row = self._row(line)
                if row is None or row['message_id'] in self.seen:
                    continue
                self.seen.add(row['message_id'])
                if collect:
                    new.append(row)
            self.offset = position
            handle.seek(max(0, position - CURSOR_TAIL))
            self.tail = handle.read(position - max(0, position - CURSOR_TAIL))
        return new


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
            and record['key'] == '%d:%s' % (record['message_id'], record.get('version_sha256'))
            # prova de preço só com resposta na janela E livro fresco
            and (record.get('evidence_for_signal') is not True
                 or (record['status'] == 'contemporaneous' and record.get('fresh_book_proven') is True)))


def valid_run(record):
    return (isinstance(record, dict) and record.get('schema') == RUN_SCHEMA and record.get('event') in ('start', 'stop')
            and isinstance(record.get('run_id'), str) and record.get('orders_sent') == 0
            and record.get('key') == '%s:%s' % (record['run_id'], record['event'])
            and chronology.parse_utc(record.get('at_utc')) is not None)


def _append(path, records, sort_key):
    """Acrescenta registos com chave nova. Nunca reescreve linhas; diário inválido = JournalCorrupt."""
    prior, rows = journal_rows(path)
    keys = {row['key'] for row in rows}
    new, duplicates = [], 0
    for record in records:
        if record['key'] in keys:
            duplicates += 1
            continue
        keys.add(record['key'])
        new.append(record)
    new.sort(key=sort_key)
    if new:
        atomic_write(path, prior + ''.join(json.dumps(r, ensure_ascii=False, sort_keys=True) + '\n'
                                           for r in new).encode('utf-8'))
    return len(new), duplicates, keys


def ingest(directory, spool):
    """Spool -> data/polymarket_observations.jsonl e data/polymarket_observer_runs.jsonl. Idempotente."""
    directory, spool = Path(directory), Path(spool)
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory.parent / ('.' + directory.name + '.polymarket.lock')
    stats = {'added': 0, 'duplicates': 0, 'rejected': 0, 'runs_added': 0}
    files = sorted(p for p in spool.glob('*.json') if p.is_file() and not p.is_symlink())
    observations, runs, invalid = [], [], []
    for item in files:
        try:
            record = json.loads(item.read_bytes())
        except (OSError, ValueError):
            record = None
        if valid_record(record):
            observations.append((item, record))
        elif valid_run(record):
            runs.append((item, record))
        else:
            invalid.append(item)
    stats['rejected'] = len(invalid)
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        added, duplicates, observation_keys = _append(directory / JOURNAL, [r for _, r in observations],
                                                      lambda r: (r.get('first_read_utc') or '', r['message_id']))
        stats['added'], stats['duplicates'] = added, duplicates
        added, duplicates, run_keys = _append(directory / RUNS_JOURNAL, [r for _, r in runs],
                                              lambda r: (r['at_utc'], r['key']))
        stats['runs_added'], stats['duplicates'] = added, stats['duplicates'] + duplicates
    for item, record in observations + runs:              # só sai do spool o que já está num diário
        if record['key'] in (observation_keys if record['schema'] == SCHEMA else run_keys):
            item.unlink(missing_ok=True)
    rejected = spool / 'rejected'
    for item in invalid:
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


def freshness(timing):
    """Duas provas distintas: resposta recebida na janela de 120 s, e livro alterado há <= FRESH_BOOK_MAX_AGE."""
    within = bool(timing) and timing.get('relation_to_signal') == 'contemporaneous'
    age = timing.get('source_age_seconds') if timing else None
    fresh = (within and isinstance(age, (int, float)) and not isinstance(age, bool)
             and -ps.CLOCK_SKEW.total_seconds() <= age <= FRESH_BOOK_MAX_AGE.total_seconds())
    return within, fresh


def build_record(m, version_sha, obs, status, reason, *, attempted, run):
    quote, timing, clob, contract = (obs.get(k) or {} for k in ('quote', 'timing', 'clob', 'contract'))
    first_read = chronology.parse_utc(m.get('collected_at_utc'))
    received = chronology.parse_utc(quote.get('received_utc'))
    within, fresh = freshness(timing)
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
        'received_within_window': within,
        'fresh_book_proven': fresh,
        'fresh_book_max_age_seconds': FRESH_BOOK_MAX_AGE.total_seconds(),
        'evidence_for_signal': status == 'contemporaneous' and fresh,
        'execution_proven': False, 'orders_sent': 0, 'credentials_used': False,
        'responses': obs.get('requests') or [],
    }


def run_event(run, event, **fields):
    return dict({'schema': RUN_SCHEMA, 'key': '%s:%s' % (run['run_id'], event), 'event': event,
                 'run_id': run['run_id'], 'orders_sent': 0}, **fields)


# --------------------------------------------------------------------------------------------- seguidor

class Follower:
    def __init__(self, directory, spool, *, get=None, clock=utc_now, aliases=None, run=None,
                 max_observations=MAX_OBSERVATIONS, max_requests=MAX_REQUESTS):
        self.directory, self.spool, self.clock = Path(directory), Path(spool), clock
        self.get = get or (lambda url: ps.http_get(url, timeout=REQUEST_TIMEOUT))
        self.aliases, self.run = aliases or {}, dict(run or {})
        self.budget = Budget(self.get, clock, max_requests=max_requests)
        self.max_observations, self.observations = max_observations, 0
        self.started = clock()
        self.run.setdefault('run_id', uuid.uuid4().hex)
        self.run.setdefault('started_utc', iso(self.started))
        self.cursor = JournalCursor(self.directory / 'messages.jsonl')
        self.cursor.read(collect=False)                       # histórico: só os IDs, nunca observado
        self.done = self.existing_keys()
        self.ticks = self.history_skipped = 0

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
        """Uma passagem: só o acréscimo do diário, só mensagens novas lidas depois do arranque. -> registos escritos."""
        self.ticks += 1
        written = 0
        for row in self.cursor.read():
            read = chronology.parse_utc(row.get('collected_at_utc'))
            if read is None or read < self.started - HISTORY_SKEW:
                self.history_skipped += 1                     # nunca admitir dados históricos retroativamente
                continue
            m = first_signal(row)
            if eligible_signal(m):
                written += self.consider(m, version_sha(row))
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


def peak_rss_mb():
    """Pico de memória deste processo (VmHWM). `ru_maxrss` não serve: em Linux herda o pico do processo pai."""
    try:
        with open('/proc/self/status', encoding='ascii') as status:
            for line in status:
                if line.startswith('VmHWM:'):
                    return round(int(line.split()[1]) / 1024, 1)
    except (OSError, ValueError, IndexError):
        pass
    return None


def follow(directory, spool, *, seconds=LIFETIME_SECONDS, sleep=time.sleep, monotonic=time.monotonic, **kwargs):
    start, cpu = monotonic(), time.process_time()
    follower = Follower(directory, spool, **kwargs)
    run = follower.run
    atomic_write(spool / ('run-%s-start.json' % run['run_id']), (canonical(run_event(
        run, 'start', at_utc=run['started_utc'], github_run_id=run.get('github_run_id'),
        github_run_attempt=run.get('github_run_attempt'),
        limits={'lifetime_seconds': seconds, 'max_observations': follower.max_observations,
                'max_requests': follower.budget.max_requests, 'window_seconds': WINDOW.total_seconds(),
                'fresh_book_max_age_seconds': FRESH_BOOK_MAX_AGE.total_seconds()})) + '\n').encode('utf-8'))
    pid = spool / 'follower.pid'
    atomic_write(pid, str(os.getpid()).encode())
    reason = 'lifetime'
    try:
        while monotonic() - start < seconds:
            if (spool / 'STOP').exists():
                reason = 'stop_requested'
                break
            try:
                follower.tick()
            except Exception as exc:                          # nunca parar por uma passagem falhada
                print('POLYMARKET_SHADOW_TICK_FAILED', type(exc).__name__)
            sleep(POLL_SECONDS)
    except BaseException:
        reason = 'terminated'
        raise
    finally:
        pid.unlink(missing_ok=True)
        try:
            atomic_write(spool / ('run-%s-stop.json' % run['run_id']), (canonical(run_event(
                run, 'stop', at_utc=iso(follower.clock()), reason=reason, ticks=follower.ticks,
                observations=follower.observations, requests=follower.budget.requests,
                circuit=follower.budget.open_reason, history_skipped=follower.history_skipped,
                incremental_reads=follower.cursor.incremental_reads, full_reads=follower.cursor.full_reads,
                cpu_seconds=round(time.process_time() - cpu, 3),
                peak_rss_mb=peak_rss_mb())) + '\n').encode('utf-8'))
        except Exception as exc:
            print('POLYMARKET_SHADOW_RUN_STOP_NOT_RECORDED', type(exc).__name__)
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
