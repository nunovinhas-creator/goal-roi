# Kit do autor dos bancos (papel B)

Este kit é exportado como arquivo autónomo, com o hash publicado. O autor dos bancos recebe **apenas** este diretório. Não pode clonar, ler nem pesquisar o repositório `goal-roi`, o seu histórico, as PR, os relatórios ou o código de H, I, J ou K. O kit descreve só o formato dos eventos observáveis e não contém nenhum código de deteção.

## O que o autor dos bancos tem de entregar

1. **Gerador** em Python 3.11+, só com a biblioteca padrão, com a função `build(slot, role, index, seed)` descrita em [interface.json](interface.json) e ilustrada em [template.py](template.py).
2. **Oito famílias** `F1` a `F8`: `F1`–`F4` pré-jogo e `F5`–`F8` ao vivo. Os mecanismos são inéditos, ficam escritos só no gerador e seguem a gramática de `protocol.json`.
3. **Compromisso**, publicado antes da ronda do farol. É criado com `tools/commitment.py` (papel `bank_author`) e um sal de 32 bytes aleatórios guardado em privado. Publicam-se só o valor do compromisso e o número de ficheiros.
4. **Revelação**, depois de publicada a ronda: o gerador e o sal.

## Regras

- O gerador é uma função determinística das sementes recebidas. Toda a aleatoriedade, incluindo iscos, jogos sem relação e mudanças de escala, vem de `random.Random(seed)`.
- Nenhum mecanismo pode reutilizar famílias publicadas no repositório, que o autor não deve consultar. O avaliador D verifica a semelhança estrutural com as famílias anteriores e publica o resultado.
- É permitida no máximo uma correção por família, e só se a elegibilidade falhar antes de qualquer pontuação. A correção é publicada com o diff e um novo compromisso. As sementes não mudam.
- O autor não comunica mecanismos nem rótulos ao autor do algoritmo (C) antes da publicação do relatório.

## Elegibilidade observável, aplicada por D

Uma decisão só existe numa cotação com:
- preço pontual explícito ≥ 1,10;
- fase conhecida (pré-jogo antes do início do jogo; ao vivo com marcador e minuto inteiro);
- janela temporal do mercado ainda aberta;
- linha ainda não decidida pelo marcador visível.

Um alvo só conta se a sua cotação terminal cumprir estas condições no instante de receção.
