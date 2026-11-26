# Diagnóstico offline de formatos ScanGoal

Utilitário de auditoria V7 para um checkout fixado. A classificação candidata identifica a estrutura das mensagens e conserva a categoria do parser em uso. Nenhuma das estruturas candidatas é admitida como aposta. O volume Betfair ao marcador fica separado do volume global. Mercados negados, primeira parte, total de equipa e apuramento permanecem distintos.

Da raiz de um checkout com o relatório gerado a partir do mesmo arquivo:

```sh
PYTHONPATH=. python3 audits/v7/parser/parser_audit.py --inventory /tmp/v7-inventory.csv
python3 -m unittest discover -s tests -p 'test_v7_parser_audit.py'
```

O inventário inclui numeração de linhas, tipos, datas e estados. Usa apenas os ficheiros locais. O diário, o protocolo, o processo de recolha e a Intelligence mantêm as interfaces atuais.
