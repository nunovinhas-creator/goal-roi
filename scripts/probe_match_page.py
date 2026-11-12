"""Diagnóstico manual: a página de jogo do scangoal.com mostra o resultado final a um pedido simples?

Faz UM pedido a UMA página /match/ (permitida pelo robots.txt) e imprime só um resumo. Não guarda nada.
Uso: python scripts/probe_match_page.py barnsley-vs-oldham-20261111
"""
import re, sys, urllib.error, urllib.request

slug = sys.argv[1] if len(sys.argv) > 1 else 'barnsley-vs-oldham-20261111'
req = urllib.request.Request('https://scangoal.com/match/%s?lang=en' % slug,
                             headers={'User-Agent': 'goal-roi-probe/1.0 (single manual request)', 'Accept': 'text/html'})
try:
    with urllib.request.urlopen(req, timeout=25) as r:
        status, body = r.status, r.read(600_000).decode('utf-8', 'replace')
except urllib.error.HTTPError as e:
    status, body = e.code, e.read(20_000).decode('utf-8', 'replace')
except Exception as e:
    print('PROBE_FAILED', type(e).__name__)
    sys.exit(0)
text = re.sub(r'<script[\s\S]*?</script>|<style[\s\S]*?</style>|<[^>]+>', ' ', body)
text = re.sub(r'\s+', ' ', text)
title = re.search(r'<title>(.*?)</title>', body, re.S)
print('PROBE status', status, 'bytes', len(body))
print('PROBE title', title[1].strip()[:120] if title else None)
print('PROBE has_next_data', '__NEXT_DATA__' in body, 'json_ld', 'application/ld+json' in body)
print('PROBE score_like', re.findall(r'\b\d{1,2}\s?[:\-–]\s?\d{1,2}\b', text)[:12])
print('PROBE status_words', sorted(set(w.lower() for w in re.findall(r'\b(FT|Full[ -]?time|Finished|Final|Ended|HT)\b', text, re.I)))[:8])
print('PROBE text_sample', text[:400])
