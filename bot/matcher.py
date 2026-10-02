"""Find which Polymarket markets a headline is about (token overlap weighted by rarity)."""
import math
import re
from collections import defaultdict

_WORD = re.compile(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'\.\-]+")
STOP = set("""
the a an and or of to in on at by for with from as is are was were be been will would can could
should may might this that these those it its his her their our your what who whom which when where
why how not no yes than then there here into over under after before about up down out off new
says said say vs v versus win wins won match game games team teams vs. season week day today
year years month world first last next more most less top end price above below between
amid after report reports update live news latest video watch
""".split())
MONTHS = set("january february march april may june july august september october november december".split())


def tokens(text):
    out = []
    for w in _WORD.findall(text):
        lw = w.lower().strip(".'-")
        if len(lw) < 3 or lw in STOP or lw in MONTHS:
            continue
        if lw.endswith("'s"):
            lw = lw[:-2]
        out.append(lw)
    return out


def proper_tokens(text):
    """Capitalised words not at the start of the headline: likely names/teams/countries."""
    words = _WORD.findall(text)
    res = set()
    for i, w in enumerate(words):
        if w[0].isupper() and (i > 0 or len(w) > 3):
            lw = w.lower().strip(".'-")
            if lw.endswith("'s"):
                lw = lw[:-2]
            if len(lw) >= 3 and lw not in STOP and lw not in MONTHS:
                res.add(lw)
    return res


class MarketIndex:
    def __init__(self, markets):
        self.markets = markets
        self.inv = defaultdict(set)
        self.mtoks = []
        for i, m in enumerate(markets):
            text = f"{m.question} {m.event_title} " + ("" if m.is_binary_yes_no else " ".join(m.outcomes))
            t = set(tokens(text))
            self.mtoks.append(t)
            for w in t:
                self.inv[w].add(i)
        n = max(len(markets), 1)
        self.idf = {w: math.log(n / len(ix)) for w, ix in self.inv.items()}

    def candidates(self, headline, k=8):
        """Returns [(market, score, matched_tokens)] best first."""
        ht = set(tokens(headline))
        proper = proper_tokens(headline)
        scores = defaultdict(float)
        matched = defaultdict(set)
        for w in ht:
            for i in self.inv.get(w, ()):
                scores[i] += self.idf[w]
                matched[i].add(w)
        res = []
        for i, s in scores.items():
            mt = matched[i]
            # only proper nouns that are rare across markets count (names, teams, places)
            m = self.markets[i]
            strong = {w for w in mt & proper if self.idf[w] >= 3.0}
            strength = sum(self.idf[w] for w in strong)
            if m.is_binary_yes_no:
                ok = len(strong) >= 2 and strength >= 7
            else:  # named outcomes: a full outcome name must appear ("Penn State", not just "State")
                ok = any(set(tokens(o)) and set(tokens(o)) <= ht and set(tokens(o)) & proper
                         for o in m.outcomes)
                strong = strong | {w for o in m.outcomes if set(tokens(o)) <= ht for w in tokens(o)}
            if ok:
                res.append((m, strength, strong))
        res.sort(key=lambda x: -x[1])
        return res[:k]
