# Recibos independentes para compromissos científicos

O verificador usa RFC 3161 e a raiz pública do FreeTSA. O recibo vincula o SHA-256 de um objeto JSON canónico ao número de série e à hora assinados pela autoridade. O domínio e o repositório fazem parte do objeto. A validação é local com OpenSSL e rejeita recibos fora do corte. [RFC 3161](https://www.rfc-editor.org/rfc/rfc3161) · [FreeTSA](https://freetsa.org/)

| Domínio | Objeto | Ordem exigida |
| --- | --- | --- |
| `certification-ledger` | Registo completo até ao prefixo em causa, incluindo o génesis | Recibos em ordem estrita e até 30 minutos depois do instante declarado. A seleção só produz efeitos a partir do recibo. |
| `certification-admission` | Hipótese com o hash das condições e compromisso com sequência, encontro, mercado, período, seleção, preço e prazo | Depois da decisão e antes do início do encontro ou da primeira observação do desfecho, conforme o que ocorrer primeiro. |
| `certification-outcome` | Compromisso, hipótese, resultado, hora disponível e lucro | Depois da admissão, entre a disponibilidade e o prazo. Para uma cadeia já liquidada, o relógio de fecho usa a hora testemunhada. |
| `llm-protocol` | SHA Git da revisão e SHA-256 do texto | A janela parte da hora assinada. A avaliação v5 permanece descritiva enquanto não houver prova independente da coorte e dos desfechos. |

Para preparar um recibo, guardar o objeto do domínio em `artefacto.json` e executar os comandos abaixo. O pedido transmite o resumo criptográfico. O par objeto–recibo deve ficar acessível na publicação de auditoria, com o recibo em `data/scientific_receipts/<hash>.tsr` para a avaliação local.

```sh
imprint=$(python -m scangoal.scientific_receipts DOMINIO artefacto.json)
openssl ts -query -sha256 -cert -digest "$imprint" -out request.tsq
curl --fail --silent --show-error -H 'Content-Type: application/timestamp-query' \
  --data-binary @request.tsq https://freetsa.org/tsr -o "$imprint.tsr"
python -m scangoal.scientific_receipts DOMINIO artefacto.json --verify-from .
```

O recibo atesta a existência de conteúdo numa hora externa. A completude de uma série publicada e a exatidão das fontes desportivas continuam a exigir verificação independente. A recolha habitual não emite recibos nem instala um registo de certificação. O recibo incluído nos testes vincula exclusivamente uma cadeia sintética.
