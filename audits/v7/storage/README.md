# Protótipo offline de armazenamento segmentado

Protótipo V7 de objetos gzip imutáveis, manifestos com SHA-256, troca atómica da revisão e leitura da revisão anterior em caso de corrupção. Uma revisão concorrente precisa de apresentar o HEAD esperado. A revisão anterior só é devolvida inteira e desde que cumpra o limite mínimo solicitado.

Da raiz do checkout:

```sh
python3 -m unittest discover -s tests -p 'test_v7_segment_store.py'
python3 audits/v7/storage/storage_sim.py --repo . --days 1825
```

O script `pages_preview.py` cria um tar gzip em disco a partir de um checkout offline da PR #98 e de um corte do relatório. Exige uma saída fora dos dois checkouts. A recolha, o Pages e os indicadores em produção conservam a configuração atual.
