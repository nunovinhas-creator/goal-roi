"""Duplicados, redundância extensional e grupos de hipóteses equivalentes (PR 4). Puro, sem rede e sem resultados.

Só usa a PERTENÇA de sinais e jogos às hipóteses (as características conhecidas), nunca os resultados. Por isso nada aqui
pode transportar informação sobre o desempenho futuro de uma hipótese.

- Redundância extensional (rejeição de propostas, decisão do proprietário de 15/11/2026): no instante da proposta, o conjunto
  de sinais elegíveis abrangidos é igual ao de uma hipótese existente da mesma família, ou ao de uma sub-combinação das suas
  condições (incluindo a família inteira). Nunca se rejeita por semelhança (Jaccard).
- Grupos (só para avaliação e apresentação, nunca para rejeitar): ligação completa com regras de compatibilidade, para que
  não haja cadeias transitivas A~B~C que juntem A e C incompatíveis.
"""
import itertools

from . import brain, insights
from .eligibility import game_identity


def _key(conds):
    return tuple(sorted(tuple(c) for c in conds))


class Index:
    """Conjuntos de sinais como inteiros (bitsets) por família e característica, sobre uma lista fixa de sinais."""

    def __init__(self, signals):
        self.signals = list(signals)
        multi, earlier = brain.multi_matches(self.signals), brain.earlier_index(self.signals)
        self.fam = {}
        for i, b in enumerate(self.signals):
            d = self.fam.setdefault(insights.family(b), {})
            bit = 1 << i
            d[None] = d.get(None, 0) | bit
            for at in set(brain.atoms(b, multi, earlier)):
                d[at] = d.get(at, 0) | bit

    def mask(self, family, conds):
        d = self.fam.get(family) or {}
        m = d.get(None, 0)
        for c in conds:
            m &= d.get(tuple(c), 0)
            if not m:
                break
        return m

    def games(self, family, conds):
        """Jogos (identidade comprovável) de todos os sinais abrangidos, com ou sem resultado."""
        m, out, i = self.mask(family, conds), set(), 0
        while m:
            if m & 1:
                g = game_identity(self.signals[i])
                if g is not None:
                    out.add(g)
            m >>= 1
            i += 1
        return frozenset(out)


class Redundancy:
    """Verificador de redundância extensional no instante da proposta (registo e sinais desse instante)."""

    def __init__(self, signals, registry):
        self.index = Index(signals)
        self.by_family = {}
        for hid in sorted(registry):
            h = registry[hid]
            fam, conds = h.get('family'), h.get('conditions')
            if not isinstance(fam, str) or not isinstance(conds, list):
                continue
            try:
                m = self.index.mask(fam, conds)
            except TypeError:
                continue
            self.by_family.setdefault(fam, {}).setdefault(m, hid)

    def register(self, family, conds, hid):
        """Uma proposta aceite passa a existir para as seguintes (na mesma resposta do modelo)."""
        self.by_family.setdefault(family, {}).setdefault(self.index.mask(family, conds), hid)

    def reason(self, family, conds):
        """None (não redundante) ou o motivo: ('same_signals_as', id) ou ('condition_adds_nothing', sub-combinação)."""
        conds = _key(conds)
        m = self.index.mask(family, conds)
        for k in range(len(conds)):
            for sub in itertools.combinations(conds, k):
                if self.index.mask(family, sub) == m:
                    return ('condition_adds_nothing', brain.hyp_id(family, sub) if sub else family)
        other = (self.by_family.get(family) or {}).get(m)
        if other is not None and other != brain.hyp_id(family, conds):
            return ('same_signals_as', other)
        return None


def expectation(h):
    return 'abaixo' if h.get('expect') == 'abaixo' else 'acima'


def compatible(a, b):
    """Mesma família, mesma expectativa e nenhuma dimensão com valores diferentes."""
    if a['family'] != b['family'] or expectation(a) != expectation(b):
        return False
    da, db = dict(map(tuple, a['conditions'])), dict(map(tuple, b['conditions']))
    return all(db[d] == v for d, v in da.items() if d in db)


def jaccard(x, y):
    u = len(x | y)
    return len(x & y) / u if u else 0.0


def groups(items, tau, min_games=8):
    """items: [(id, hipótese, conjunto de jogos)] já pela ordem de prioridade (o primeiro de cada grupo é o representante).

    Ligação completa: um item entra no primeiro grupo em que é compatível com TODOS os membros e tem Jaccard >= tau com
    TODOS (ambos com >= min_games jogos); senão abre um grupo novo. -> [[id, ...], ...] na ordem de criação.
    """
    out = []
    for hid, h, gs in items:
        placed = False
        if len(gs) >= min_games:
            for g in out:
                if all(len(mgs) >= min_games and compatible(h, mh) and jaccard(gs, mgs) >= tau for _, mh, mgs in g):
                    g.append((hid, h, gs))
                    placed = True
                    break
        if not placed:
            out.append([(hid, h, gs)])
    return [[hid for hid, _, _ in g] for g in out]
