"""PR 5 mutation check: every protective rule of the notifier, removed one at a time, must break at least one test.

Each mutation is a textual replacement applied to a temporary copy of the repository (the checkout is never touched); the
Telegram tests (`test_telegram_presentation`, `test_notify_migration`, `test_scangoal`) run against the mutant. A mutant
whose tests still pass is a SURVIVOR and fails this check.

Usage (repository root): python3.11 audits/pr5-evidence/mutation_check.py [--json out.json]
"""
import json, shutil, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = 'scangoal/notify.py'
TESTS = ['test_telegram_presentation.py', 'test_notify_durability.py', 'test_notify_migration.py', 'test_scangoal.py']
WORKFLOW, CHECK = '.github/workflows/collect.yml', 'scripts/post_cutover_check.py'

MUTATIONS = [
    ('M01 no intent persisted before the send',
     "        rec['events'].append('intent')\n        store.save_state(state)\n        cp('after_intent_state')",
     "        rec['events'].append('intent')\n        cp('after_intent_state')"),
    ('M02 in-flight retried instead of uncertain',
     "            r['state'], r['reason'] = 'uncertain', 'interrupted'", "            r['state'], r['reason'] = 'pending', 'interrupted'"),
    ('M03 uncertain counted as delivered',
     "        except Exception:                 # qualquer outra exceção: não se sabe se saiu\n            outcome = 'uncertain'",
     "        except Exception:                 # qualquer outra exceção: não se sabe se saiu\n            outcome = 'delivered'"),
    ('M04 confirmed failures retried',
     "if recs.get(n.key, {}).get('state') in (None, 'pending')]", "if recs.get(n.key, {}).get('state') in (None, 'pending', 'failed')]"),
    ('M05 no event-key filter (effects only)',
     "if recs.get(n.key, {}).get('state') in (None, 'pending')]", "]"),
    ('M06 keep sending after a failure', "        if outcome != 'delivered':\n            print(", "        if False:\n            print("),
    ('M07 log not repaired from the state',
     "                missing.append(log_entry(now, key, ev, r, reconstructed=True", "                0 and missing.append(log_entry(now, key, ev, r, reconstructed=True"),
    ('M08 delivered keys of the log not restored', "        if isinstance(intent.get('effects'), list):\n            apply_effects(state, intent['effects'], now)",
     "        if False:\n            pass"),
    ('M09 old key per signal (date|market|selection)',
     "    return 'item|%s|%s|%s' % (game_key(w), w.get('market') or '', w.get('selection') or '')",
     "    return 'item|%s|%s|%s' % (w.get('date_utc'), w.get('market') or '', w.get('selection') or '')"),
    ('M10 legacy keys ignored', "        if ik in notified or legacy_key(w) in sent or legacy_hit(pairs, w):", "        if ik in notified or legacy_hit(pairs, w):"),
    ('M11 grouping off', "    out = equivalence.groups(items, TAU, GROUP_MIN_GAMES) + alone", "    out = [[h] for h, _, _ in items] + alone"),
    ('M12 single linkage (transitive chains)', "    out = equivalence.groups(items, TAU, GROUP_MIN_GAMES) + alone",
     "    out = _single_linkage(items) + alone"),
    ('M13 absence means back to observation', "        return 'em observação'\n    return None", "        return 'em observação'\n    return 'em observação'"),
    ('M14 health gate off', "        elif not healthy or not change_verified(report, hid, new):", "        elif not change_verified(report, hid, new):"),
    ('M15 pending status ignored', "    if not isinstance(row, dict) or row.get('pending_status') is not None:", "    if not isinstance(row, dict):"),
    ('M16 fixed change not required', "    return isinstance(last, dict) and last.get('to') == raw and raw in (new, 'forte', 'confirmada')", "    return True"),
    ('M17 anti-oscillation off', "                and now - at < timedelta(hours=OSCILLATION_HOURS)):", "                and False):"),
    ('M18 anti-oscillation hides critical changes', "        if (not critical and last and last.get('from') == new", "        if (last and last.get('from') == new"),
    ('M19 certification loss needs a healthy report', "        if old in VALID and new not in VALID:\n            pass", "        if False:\n            pass"),
    ('M20 vanished validated tactic stays silent', "            new = 'em observação'                         # validada", "            new = None                         # validada"),
    ('M21 guard_claims skipped by the engine', "        if not guard_claims(n.text, report, n.claims):\n            apply_effects", "        if False:\n            apply_effects"),
    ('M22 guard does not require the label', "        if not positive or label not in text:", "        if not positive:"),
    ('M23 claims not tied to their hypothesis', "        if positive_claims(line, report) and not any(line.rstrip().endswith(' ' + hid) for hid in verified):",
     "        if positive_claims(line, report) and not verified:"),
    ('M24 "forte" not a claim word', "CLAIM = re.compile(r'(valid|certific|confirm)[a-z]*|fortes?')", "CLAIM = re.compile(r'(valid|certific|confirm)[a-z]*')"),
    ('M25 negation anywhere in the line', "        if any(i >= len(neg) and tuple(toks[i - len(neg):i]) in forms for forms in NEGATED_BY for neg in forms[:1]):",
     "        if {'nao', 'sem', 'nunca', 'nenhuma'} & set(toks):"),
    ('M26 certificate without its own confirmation',
     "    if not isinstance(conf, dict) or conf.get('certifiable') is not True:\n        return None",
     "    if not isinstance(conf, dict):\n        conf = {'rank': 1, 'certified_utc': '2026-01-01T00:00:00+00:00', 'certifiable': True}"),
    ('M27 ledger validity not required', "    return (v.get('method') == VALIDATION_METHOD and v.get('state') == 'active' and v.get('ledger_valid') is True",
     "    return (v.get('method') == VALIDATION_METHOD and v.get('state') == 'active'"),
    ('M28 late alerts allowed', "        if level == 'alerta' and not still_bettable(w, now):\n            continue", "        if False:\n            continue"),
    ('M29 silent migration not seeded', "    state['announced_statuses'] = announced\n", "    state['announced_statuses'] = {}\n"),
    ('M30 watch_sent truncated to 400', "                ws.append(e[1])\n                ws.sort()", "                ws.append(e[1])\n                ws.sort()\n                del ws[:-400]"),
    ('M31 preview writes the state', "        store = MemoryStore(state, log_path.read_bytes() if log_path.exists() else b'')",
     "        store = FileStore(state_path.parent)"),
    ('M32 preview uses the real sender', "        def record(token, chat, text):\n            shown.append(text)",
     "        def record(token, chat, text):\n            shown.append(text)\n            send(token, chat, text)"),
    ('M33 summary shows unverified counts', "        'Hipóteses: %d em teste; %d promissoras (%d grupos de equivalentes), %d abandonadas.'\n        % (brain.get('hypotheses') or 0, c.get('promissora', 0),",
     "        'Hipóteses: %d em teste; %d promissoras (%d grupos de equivalentes), %d abandonadas, %d fortes.'\n        % (brain.get('hypotheses') or 0, c.get('promissora', 0),"),
    ('M34 scientific state not on top', "    lines = head + [title] + evidence_lines(report, state, rep, None, gr[1:])",
     "    lines = [title] + head + evidence_lines(report, state, rep, None, gr[1:])"),
    ('M35 log written without fsync of a newline separator', "                        data = b'\\n' + data      # linha cortada", "                        data = data      # linha cortada"),
    ('M37 no resync after a foreign writer', "    resync_after_foreign_writer(state, now)\n    out = []", "    out = []"),
    ('M38 P1: level back in the bet identity', "    return 'item|%s|%s|%s' % (game_key(w), w.get('market') or '', w.get('selection') or '')",
     "    return 'item|%s|%s|%s|%s' % (game_key(w), w.get('market') or '', w.get('selection') or '', item_level({}, w))"),
    ('M39 P1: legacy pairs only for hints', "        if ik in notified or legacy_key(w) in sent or legacy_hit(pairs, w):",
     "        if ik in notified or legacy_key(w) in sent or (level == 'indício' and legacy_hit(pairs, w)):"),
    ('M40 P1: update not labelled', "    update = ' (nova seleção num jogo já avisado)' if g.get('previous') else ''", "    update = ''"),
    ('M41 P2: missing state = empty history', "            raise StateMissing('notify_state.json missing')\n        return json.loads(self.state_path",
     "            return {}\n        return json.loads(self.state_path"),
    ('M42 P2: integrity check off', "    entries, bad = store.read_log()\n    check_integrity(state, entries)", "    entries, bad = store.read_log()"),
    ('M43 P2: init over an existing history', "    if store.state_path.exists() or (store.log_path.exists() and store.log_path.stat().st_size):",
     "    if False:"),
    ('M44 P0: send although the intent was not published', "            if not published:\n", "            if False:\n"),
    ('M45 P0: withdrawal keeps the effects', "                state.clear()\n                state.update(before)\n", ""),
    ('M46 P0: withdrawn intent treated as uncertain', "        if outcome is None or (r is not None and (r.get('events') or [''])[-1] == 'withdrawn' and not terminal):",
     "        if outcome is None:"),
    ('M47 P0: push failure reported as success', "                if self.git('push', '-q', 'origin', 'HEAD:%s' % self.branch):\n                    return True",
     "                self.git('push', '-q', 'origin', 'HEAD:%s' % self.branch)\n                return True"),
    ('M48 P0: misconfiguration falls back to local', "    if mode != 'git' or not branch:\n        raise NotifyError(",
     "    if mode != 'git' or not branch:\n        return None\n        raise NotifyError("),
    ('M49 P2-R: state without history or installation accepted', "    if not installed and not history:\n", "    if False:\n"),
    ('M50 P2-R: any installation marker accepted', "    installed = isinstance(state.get(INSTALLATION), dict) and state[INSTALLATION].get('method') == 'init-state'",
     "    installed = isinstance(state.get(INSTALLATION), dict)"),
    ('M51 P2-R: history without the PR 1 marker accepted', "               and STATUS_MIGRATION in migrations)", "               )"),
    ('M52 P2-R: --migrate-format migrates a reset state', "    check_integrity(state, parse_log(log.read_bytes() if log.exists() else b'')[0])", "    pass"),
    ('M53 P2-R: --init-state writes an unmarked state',
     "    save_state(store.state_path, {INSTALLATION: {'method': 'init-state', 'at_utc': iso(now or datetime.now(UTC))}})",
     "    save_state(store.state_path, {})"),
    ('M54 workflow: barrier off in the fast reads',
     "          TG_CHAT_ID: ${{ secrets.TG_CHAT_ID }}\n          NOTIFY_DURABLE: git\n        run: timeout",
     "          TG_CHAT_ID: ${{ secrets.TG_CHAT_ID }}\n        run: timeout", WORKFLOW),
    ('M55 workflow: barrier off in the notices step',
     "          NOTIFY_DURABLE: git\n        run: python -u -m scangoal.notify\n", "        run: python -u -m scangoal.notify\n", WORKFLOW),
    ('M56 check: intent-before-outcome always true', "        'pr5_intent_published_before_outcome': intent_before_outcome(root, before, after),",
     "        'pr5_intent_published_before_outcome': True,", CHECK),
    ('M36 sender HTTP 5xx counted as confirmed failure', "        if 400 <= exc.code < 500:", "        if 400 <= exc.code < 600:"),
]

SINGLE = '''

def _single_linkage(items):
    """Mutant helper: transitive chains (A~B, B~C => A, B, C together)."""
    out = []
    for hid, h, gs in items:
        hit = [g for g in out if any(equivalence.compatible(h, mh) and equivalence.jaccard(gs, mgs) >= TAU for _, mh, mgs in g)]
        merged = [(hid, h, gs)] + [x for g in hit for x in g]
        out = [g for g in out if g not in hit] + [merged]
    return [[x[0] for x in sorted(g, key=lambda x: [i[0] for i in items].index(x[0]))] for g in out]
'''


def run(tmp):
    r = subprocess.run([sys.executable, '-m', 'unittest', *[t[:-3] for t in TESTS]], cwd=tmp,
                       env={'PYTHONPATH': '%s:%s' % (tmp, tmp / 'tests'), 'PATH': '/usr/bin:/bin'}, capture_output=True, text=True, timeout=600)
    failed = sorted({line.split('(')[1].split(')')[0].split(' ')[0] for line in r.stderr.splitlines()
                     if line.startswith(('FAIL:', 'ERROR:')) and '(' in line})
    return r.returncode, failed


def main(argv):
    results, ok = [], True
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d) / 'repo'
        shutil.copytree(ROOT, tmp, ignore=shutil.ignore_patterns('.git', '__pycache__'))
        code, failed = run(tmp)
        assert code == 0, 'unmutated tests must pass: %s' % failed
        for mutation in MUTATIONS:
            name, old, new = mutation[:3]
            target = mutation[3] if len(mutation) > 3 else TARGET
            original = (tmp / target).read_text(encoding='utf-8')
            count = original.count(old)
            if count != 1:
                results.append({'mutation': name, 'status': 'NOT_APPLIED', 'occurrences': count})
                print('%-58s NOT_APPLIED (%d occurrences)' % (name, count), flush=True)
                ok = False
                continue
            mutant = original.replace(old, new) + (SINGLE if '_single_linkage' in new else '')
            (tmp / target).write_text(mutant, encoding='utf-8')
            code, failed = run(tmp)
            (tmp / target).write_text(original, encoding='utf-8')
            killed = code != 0
            ok &= killed
            results.append({'mutation': name, 'target': target, 'status': 'KILLED' if killed else 'SURVIVED', 'failing_tests': failed[:6],
                            'failing_count': len(failed)})
            print('%-58s %s (%d)' % (name, 'KILLED' if killed else 'SURVIVED', len(failed)), flush=True)
    summary = {'mutations': len(MUTATIONS), 'killed': sum(r['status'] == 'KILLED' for r in results), 'ok': ok, 'results': results}
    if '--json' in argv:
        Path(argv[argv.index('--json') + 1]).write_text(json.dumps(summary, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({k: summary[k] for k in ('mutations', 'killed', 'ok')}))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
