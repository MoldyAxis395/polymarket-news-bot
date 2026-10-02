"""Decide: does this headline move a market, which side, how sure.

Pluggable. RuleBrain = free keyword heuristics. LLMBrain = any OpenAI-compatible API.
"""
import json
import os
import re
from dataclasses import dataclass

import requests

from . import config
from .matcher import tokens


@dataclass
class Signal:
    market: object
    outcome: int          # index into market.outcomes to BUY
    confidence: float
    reason: str


NEG = [
    "injured", "injury", "ruled out", "out for", "sidelined", "will miss", "to miss", "misses",
    "suspended", "suspension", "banned", "resigns", "resigned", "steps down", "stepping down",
    "fired", "sacked", "arrested", "indicted", "charged", "dies", "died", "dead", "withdraws",
    "withdrawn", "drops out", "quits", "collapses", "plunges", "tumbles", "crashes", "loses",
    "defeated", "eliminated", "knocked out", "scandal", "rejected", "blocked", "delayed",
    "postponed", "cancelled", "canceled", "hospitalized", "hospitalised", "illness", "doubtful", "in doubt", "questionable",
    "fails", "failed", "downgrade", "recall", "sanctions",
]
POS = [
    "win", "wins", "won", "victory", "beat", "beats", "defeat", "defeats", "clinches", "cleared to play", "returns", "fit to play",
    "back in training", "available", "approved", "passes", "signs", "elected", "leads",
    "surges", "soars", "rallies", "record high", "deal reached", "agreement reached",
    "endorses", "qualifies", "advances", "confirmed", "recovers", "boost", "upgraded",
    "will play", "expected to play", "full participant", "full practice", "activated", "cleared",
]
# Questions where "bad thing happens" = YES
Q_NEG = [
    "resign", "out as", "fired", "leave office", "removed", "impeach", "arrested", "indicted",
    "die", "lose", "drop out", "withdraw", "recession", "default", "shutdown", "banned",
    "delist", "fall below", "dip", "crash", "miss", "suspended", "out by",
]
VS = re.compile(r"\b(beat|vs\.?|v\.?|against|over|defeat)\b", re.I)


def _norm(text):
    t = text.lower().replace("’", "'")
    # "injury report/update" headlines are neutral containers, not bad news by themselves
    return re.sub(r"\bwon't\b|\bwin or lose\b|\binjury (?:report|update|news|status)s?\b", " ", t)


def _count(text, words):
    t = f" {_norm(text)} "
    return [w for w in words if re.search(rf"\b{re.escape(w)}\b", t)]


def _words(text):
    return [w[:-2] if w.endswith("'s") else w for w in re.findall(r"[a-z0-9']+", _norm(text))]


def _near(title, matched, kws, window=6):
    """Is a sentiment keyword within `window` words of a matched entity?"""
    words = _words(title)
    ent = [i for i, w in enumerate(words) if w in matched]
    for k in kws:
        kw = k.split()
        for i in range(len(words) - len(kw) + 1):
            if words[i:i + len(kw)] == kw and any(abs(i - e) <= window for e in ent):
                return True
    return False


LOSER_AFTER = re.compile(r"\b(?:beat|beats|defeat|defeats|win over|victory over|wins over|against|over|past|edge|edges|rout|routs|stun|stuns)\s+(?:the\s+)?([a-z0-9' ]{3,40})")


def _is_loser(title, matched):
    """'Bears win over the Eagles' -> eagles is the loser."""
    for mt in LOSER_AFTER.finditer(_norm(title)):
        after = set(_words(mt.group(1))[:3])
        if after & matched:
            return True
    return False


OPP_AFTER = re.compile(r"\b(?:vs\.?|v\.?|versus|against|at|facing)\s+(?:the\s+)?([a-z0-9' ]{3,40})")


def _is_opponent(title, matched):
    """'Hall ruled out vs. Bears' -> the bad news is for the Bears' opponent."""
    for mt in OPP_AFTER.finditer(_norm(title)):
        if set(_words(mt.group(1))[:3]) & matched:
            return True
    return False


class RuleBrain:
    name = "rules"

    def analyze(self, item, candidates, index=None):
        neg, pos = _count(item.title, NEG), _count(item.title, POS)
        p = len(pos) - len(neg)
        if p == 0:
            return []
        out = []
        for m, score, matched in candidates[:3]:
            pol = 1 if p > 0 else -1
            if not _near(item.title, matched, pos if pol > 0 else neg):
                continue
            if not m.is_binary_yes_no:
                # both teams named: the one after "vs/against/over" is the opponent, the other the subject
                hits = [o for o in m.outcomes if set(tokens(o)) & matched]
                if len(hits) == 2:
                    after = [o for o in hits if (_is_loser if pol > 0 else _is_opponent)(item.title, set(tokens(o)))]
                    if len(after) != 1:
                        continue
                    subject = hits[1 - hits.index(after[0])]
                    matched = set(tokens(subject))
                    side = m.outcomes.index(subject) if pol > 0 else 1 - m.outcomes.index(subject)
                    conf = min(0.95, 0.30 + 0.12 * min(abs(p), 3) + min(score, 12) / 40)
                    kw = ",".join(pos if pol > 0 else neg)
                    out.append(Signal(m, side, round(conf, 2),
                                      f"rules: {'+' if pol > 0 else '-'}[{kw}] subject '{subject}' vs '{after[0]}'"))
                    continue
            if pol > 0 and _is_loser(item.title, matched):
                pol = -1
            elif pol < 0 and _is_opponent(item.title, matched):
                pol = 1
            side, why = self._side(m, matched, pol)
            if side is None:
                continue
            conf = min(0.95, 0.30 + 0.12 * min(abs(p), 3) + min(score, 12) / 40)
            kw = ",".join(pos if pol > 0 else neg)
            out.append(Signal(m, side, round(conf, 2),
                              f"rules: {'+' if pol > 0 else '-'}[{kw}] match[{','.join(sorted(matched))}] {why}"))
        return out

    def _side(self, m, matched, pol):
        if m.is_binary_yes_no:
            q = m.question
            q_neg = bool(_count(q, Q_NEG))
            # is the matched entity the opponent ("Will A beat B?" and news is about B)?
            parts = VS.split(q, maxsplit=1)
            opponent = False
            if len(parts) >= 3:
                after = set(tokens(parts[-1]))
                before = set(tokens(parts[0]))
                if matched & after and not matched & before:
                    opponent = True
            good_for_yes = pol > 0
            if q_neg:
                good_for_yes = not good_for_yes
            if opponent:
                good_for_yes = not good_for_yes
            return (0 if good_for_yes else 1), f"yes/no q_neg={q_neg} opp={opponent}"
        # named outcomes, e.g. ["Argentina", "Croatia"]
        hits = [i for i, o in enumerate(m.outcomes) if set(tokens(o)) & matched]
        if len(hits) != 1:
            return None, ""
        i = hits[0]
        return (i if pol > 0 else 1 - i), f"outcome '{m.outcomes[i]}' {'good' if pol > 0 else 'bad'}"


def make_brain():
    if config.BRAIN == "llm":
        from .llm import LLMBrain
        return LLMBrain(RuleBrain())
    return RuleBrain()
